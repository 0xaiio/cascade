# 015 · 进程重启后，孤儿任务永远不动

- 状态：已修复
- 提交：`fix(job_manager): 启动时收敛孤儿条目，重启后没下完的任务自己接着下`
- 影响面：**任何一次进程退出**（强杀、崩溃、正常关闭都算）—— 当时在队列里或正在下载的条目
  全部变成永久卡死。合集会成批命中：一次退出就能留下几十上百条。
- 严重度：高。这是**存储面损伤**而不是某次下载失败：任务不会报错、不会重试、日志里一行都没有，
  用户只能一条条手动删。生产库曾累积 **332 个 `queued` + 3 个 `running`**（[`ai/perf/PLAN.md`](../perf/PLAN.md) §8）。

## 问题

关掉应用再打开，任务中心里那些没下完的任务还在：状态写着「排队中」或「下载中」，
进度、速度、剩余时间停在原来的数字上，**永远不动**。

点「暂停」再「继续」、改并发、重启单条 —— 都没用（重启单条会把它重新入队，所以这条「能修好」的
假象反而让病因更难看出来：同一批里有几条动了，其余几十条还在原地）。日志里什么都没有：
没有 `WARNING`、没有异常、连一行信息都没有。

## 原因

### 队列只活在内存里，而库里留着一个假装「还在跑」的状态

`JobManager` 的队列是一个 `asyncio.Queue`，成员是 `JobItem` 的 id：

```python
self._queue: asyncio.Queue[str | None] | None = None      # job_manager.py:57
```

进程一退，这个队列连同里面的 id 一起消失；而 `jobitem.status` 仍然是 `queued` / `running`。
把这些 id 重新放回队列的代码**只有三处**：新建任务（[enqueue](../backend/app/download/job_manager.py#L220)）、
整任务重启（[restart](../backend/app/download/job_manager.py#L220)）、单条重启（[restart_item](../backend/app/download/job_manager.py#L271)）。
三处都由**用户动作**触发，启动路径上一处也没有 —— [start()](../backend/app/download/job_manager.py#L68)
只做了两件事：建队列、按并发起 worker。

于是这些条目既不在队列里（没人会来取），也不在终态（界面不会把它们算成结束）：
它们悬在中间，且因为「没人取」不是「取失败了」，**不会产生任何日志或错误**。

> 判别依据是代码事实而不是推测：`grep -rn "JobItem.status == JobStatus.queued" backend/app`
> 只命中 `_queued_item_ids()`（被上面三个用户动作调用）与条目认领前的校验。
> 仓库里确实存在一个 `item_requeued` 事件（当时在 `job_manager.py:684`，今 739），但它属于
> **运行期**改限速/重试后的重排队，与「进程重启」无关 —— 这也是本缺陷最初被估成「有重排队机制」的原因。

### 第二个坑：用户点过的「取消」根本不在数据库里

`cancel()` 只把**排队中**的任务状态改成 `cancelled`；任务正在跑时，它只在内存里记一笔：

```python
async def cancel(self, job_id: str) -> None:
    self._cancelled.add(job_id)                      # 只活到进程结束
    ...
    if job and job.status == JobStatus.queued.value:
        job.status = JobStatus.cancelled.value
```

所以「这个任务正在被取消」在重启后是**不可见**的。如果恢复逻辑只看 `job.status`，
一个用户明明取消过、刚取消到一半就被关掉的任务，重启后会接着把剩下的条目下完 ——
把「用户意图」覆盖掉，比原来的缺陷更糟。

## 修复方案

新增 [orphan_recovery.py](../backend/app/download/orphan_recovery.py)（L2 编排），由
[recover_orphaned_work](../backend/app/download/job_manager.py#L83) 在 lifespan 里、`start()` **之前**调用：

1. **先改库、再启动 worker**。扫描跑在 `asyncio.to_thread` 里，此刻一个 worker 都不存在，
   不存在「worker 与该行并发读写」的窗口 —— 用顺序消掉风险，而不是加锁。
2. **只让 `orphan_recovery` 改条目状态**，任务终态回到运行期同一个
   [_maybe_finish_job](../backend/app/download/job_manager.py#L541)。否则「什么算结束」会出现两份定义。
3. 准入规则全部落在**库里可见的事实**上（内存集合随进程消失，靠不住）：

   | 条目所在处 | 结果 |
   | --- | --- |
   | 任务 `paused` | 折回 `paused`，任何策略下都不恢复 |
   | 任务 `cancelled`，或该任务**已有** `cancelled` 条目 | 折成 `cancelled`（后者就是上面第二个坑的判据） |
   | 所属任务不存在 | 折成 `cancelled` 并写明原因 |
   | 其余 | 按 `YTDL_ORPHAN_RECOVERY`：`resume`（默认）重新排队 + 清空进度字段；`fail` 标失败并写明「上次退出时正在下载 / 还在排队」 |

4. 恢复顺序按「任务创建时间 → 条目序号」，不是 id 字典序 —— 332 条的积压要按当初的下单顺序跑。
5. 每个被恢复的任务写一条 `jobs_recovered` 事件（`JobEvent` + SSE）：
   「这个任务为什么自己动起来了」在事件表里有答案。这是本次修复的**正面证据**，
   没有它就只能从「状态变成 succeeded」倒推。

### 为什么默认是 `resume` 而不是 `cancelled`

候选池里这条的建议写的是「默认保守（`cancelled`）」。改成 `resume` 的理由：

- 下载**幂等且可续传**（`build_download_options` 里 `continuedl=True`，`.part` 接着用），
  重排队的最坏情况只是多下一遍，没有数据被破坏的可能；
- `cancelled` 要用户**逐条**点「重启」才能恢复 —— 那正是本条要消灭的痛点，等于把
  「永远不动」换成「永远显示已取消」；
- B站 上传那边选「折成失败」的理由（重投会在平台留下重复稿件）在下载侧**不成立**。
  同一条原则（重做一次代价多大）导出两个相反的默认值，别把其中一个当通用规则。

`fail` 保留为逃生阀：积压几百条陈年任务时，可以一次性全标失败而不是让它们全部重新开跑。

### 被否决的方案

- **启动时把所有孤儿一律标 `cancelled`**（见上）。
- **让 `stop()` 在退出前把 `running` 反写成 `queued`**：`asyncio.to_thread` 取消不了线程，
  优雅关闭时那条线程可能**还在写库**，反写会与它竞争；而且这个方案只覆盖「优雅关闭」一条路径，
  崩溃与强杀（本缺陷的主要来源）照样漏。启动扫描不依赖关闭路径走完没有 —— 实测三种退出方式都留在库里。
- **把 `resume` / `fail` 做成设置界面的选项**：它只在「已经崩过一次」之后才有意义，
  平时是个没人会去找的开关；做成环境变量 + 排障文档更合适（`YTDL_ORPHAN_RECOVERY`）。
- **只按 `job.status` 判定，不看条目**：会复活用户已取消的任务（第二个坑）。
- **在 `_worker` 启动时去库里捞一条**（惰性拉取）：相当于把队列换成数据库轮询，改动面远超一个缺陷。

## 效果

| | 修复前 | 修复后 |
| --- | --- | --- |
| 重启后孤儿条目 | 永远 `queued`/`running`，无日志 | 自动重新排队并续传，日志 `启动恢复：重新排队 N 个条目` |
| 已暂停的任务 | 条目可能停在 `running` | 保持 `paused` |
| 正被取消的任务 | 重启后接着下完 | 保持取消 |
| `fail` 策略 | 不存在 | 标失败 + 逐条写明来源（正在下载 / 还在排队），**一次下载都不发起** |

## 未覆盖 · 如实说明

1. **332 + 3 是历史快照，本轮没有复现原始现场**：`perf/PLAN.md` §8 那份数据早已被清掉
   （当前库只剩 18 条 `succeeded`）。我用「人为造出孤儿条目」的方式验证，两者是同一形状，
   但不是同一份数据。
2. **没有验「两个实例同时启动」**：默认端口预检让第二个实例起不来，但 `--auto-port` 可以绕过，
   此时两个进程会同时恢复同一批条目、同时往同一个 `.part` 写。修复前也存在同类风险（任何两人同时点下载），
   本次没有加锁。
3. **`fail` 策略下 `.part` 文件会留着**，不做清理：那是用户磁盘上的东西，且下次重下时还能被续用。
4. **「所属任务不存在」这条分支没有生产数据支撑**：它是兜底规则（那种条目在旧版本里同样永远不动），
   测试里通过手工插入孤立行覆盖。
5. **恢复没有做分批/限速**：一次启动可能立刻把几百条塞进队列。队列本身受并发限制，
   但「一开应用就开始跑几十 GB」这件事对用户是突然的 —— 这是策略选择，不是遗漏（见 `fail` 逃生阀）。
6. **事件没有聚合**：300 个任务被恢复就写 300 条 `jobs_recovered` 行。

## 验证

**A/B（把启动接线还原成 `await manager.start()`，其余代码不动）**：3 条端到端全部失败，
报错与生产同形 —— `AssertionError: Job job-orphan did not reach succeeded: running`。修复后 3 条全绿。

```text
tests/test_orphan_recovery.py::test_startup_alone_makes_orphaned_items_download_again  FAILED
tests/test_orphan_recovery.py::test_startup_with_fail_policy_never_touches_the_downloader  FAILED
tests/test_orphan_recovery.py::test_startup_leaves_paused_work_paused  FAILED
3 failed, 8 deselected
```

**真实设备验收**（真实 yt-dlp + 真实 URL `hTdSU7q5WCo` + 本机 cookies，脚本跑完即删）：

```text
[1] 基准下载完成：On Vibe Coding [hTdSU7q5WCo].mp4 = 32693962 字节，耗时 41.1s
[2] 已删除成品，只留下截断的 .part：保留 16346981 / 32693962 字节
启动恢复：上次退出留下 1 个未完成条目（重新排队 1 / 标记失败 0 / 取消 0 / 暂停 0，策略=resume）
[3] 启动恢复：重新排队 ['job-orphan-real-item']，涉及任务 ['job-orphan-real']
[4] 条目终态 = succeeded，任务终态 = succeeded，error = None
    首个进度回调 downloaded_bytes = 16348005（.part 里原有 16346981 字节）
    成品文件：[('On Vibe Coding [hTdSU7q5WCo].mp4', 32693962)]
    .part 残留：False
```

三条关键读数：① 首帧进度从 **16,348,005** 字节起算，与 `.part` 里留下的 16,346,981 只差
一个 chunk 的记账量 —— 这是**真的续传**，不是重下；② 成品字节数与基准**完全一致**（32,693,962）；
③ `.part` 被清掉。这正是候选池里点名要验的风险「`.part` 能否续传」。

**新增用例**：11 条（`backend/tests/test_orphan_recovery.py`），覆盖 8 条规则 + 3 条 lifespan 端到端。
后端全量 **1039 passed**（本轮改动前，37 个测试文件）→ 加完新用例 **1054 passed**。

> 数字要打个补丁：本轮期间**另一个会话正在同一个工作区里加用例**，三次测量得到 1039 / 1054 / 1066
> 三个收集数（`pytest --co` 实测），差额不是回归，是别人的新增。所以上面只主张两件能自己核对的事：
> 新文件 11 条全绿；把启动接线还原后其中 3 条会红。

**文档工具**：`docs.py check`、`check_api_contract.py`、`check_layers.py` 通过；
`check_doc_anchors.py` 里由本轮位移造成的 38 处已全部重算并逐条核对指向（24 个行号，+42 或 +1），
剩余 36 处指向另一个会话当时**未提交**的 `schemas.py` / `JobQueue.tsx` / `api.ts` / `output_paths.py`，
按原样留着，理由见 [`README.md`](README.md) 的「已知但未处理」第 24 条。

## 风险与回滚

- **回滚**：`git revert` 本提交即可回到「孤儿永远不动」的旧行为；已经恢复并跑起来的任务不受影响
  （它们已经离开 `queued`/`running`）。
- **新建任务不受影响**：`recover_orphaned_work` 只在 lifespan 里跑一次，创建任务的路径没有变化。
- **`.part` 损坏**会怎样：yt-dlp 续传失败 → 条目 `failed` + 真实错误文本，用户点「重启」从零重下。
  不会静默：这一层是 yt-dlp 的既有行为，本次没有改动它。
- **策略写错一个字**（如 `YTDL_ORPHAN_RECOVERY=fial`）：按 `resume` 处理并打一条 warning，
  不让配置拼写错误变成「应用起不来」。

## 关联

- 候选池 [D-01](../plan/brainstorming.md)（本次落地并改状态）
- 与 [B站 上传的中断收敛](../backend/app/bilibili/bili_manager.py#L375) 是同一问题的两个相反答案，对照见
  [架构文档](../docs/architecture.md)「进程重启后」两段
- 与 [008](008-return-in-finally-swallows-the-real-error.md) / [010](010-unselectable-probe-raise-skips-the-fallback.md)
  同属一个主题：**都是「代码什么都没做，而界面/日志看起来已经做过了」**。本轮据此把判据写进验证方法：
  一条分支是否真的执行过，必须给出**正面证据**（这里是 `jobs_recovered` 事件与首帧字节数），
  「没有报错」不能证明它跑过。
