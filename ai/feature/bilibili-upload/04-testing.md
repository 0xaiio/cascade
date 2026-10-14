# 04 · 测试

本文回答「凭什么算测过了」：自动测试覆盖了什么、**没覆盖什么**、手工验收怎么做。
实现见 [03-implementation.md](03-implementation.md)。

## 1. 结论

| 层 | 文件 | 用例数 |
| --- | --- | --- |
| 纯工具（文案/状态折合） | `backend/tests/test_bili_policy.py` | 28 |
| 纯工具（模板/标签/硬限） | `backend/tests/test_bili_meta.py` | 44 |
| 基础设施（HTTP 接缝、扫码会话） | `backend/tests/test_bili_auth.py` | 56 |
| 基础设施（`biliup` 探测/命令/进度/执行） | `backend/tests/test_bili_bin.py` | 42 |
| 领域（会员态接口） | `backend/tests/test_bili_api.py` | 63 |
| 领域（单次上传编排） | `backend/tests/test_bili_upload.py` | 65 |
| 编排（队列/落账/收敛/预填） | `backend/tests/test_bili_manager.py` | 64 |
| 编排（读模型与徽标聚合） | `backend/tests/test_bili_read_model.py` | 47 |
| 入口（13 个 operation 的状态码） | `backend/tests/test_api_bilibili.py` | 39 |
| 共享替身 | `backend/tests/bili_fakes.py` | — |
| **合计** | **9 个文件** | **448** |

数字取自 `pytest --collect-only`（逐文件），与 `backend/tests` 全集 **804** 减去既有 **356** 相符。
缺陷 [`011`](../../bug-fix/011-biliup-refuses-a-token-less-cookie-file.md) 修复后本表由 420 更新为 448：
`test_bili_auth.py` **+17**（TV 三步令牌交换、签名冻结值、`build_login_info`/`read_login_token`）、
`test_bili_manager.py` **+6**（投稿令牌前置条件与体检档）、`test_bili_policy.py` **+3**（`-400`/`61000` 分类）、
`test_bili_upload.py` **+2**（失败原文进日志）。

本功能合入前后端既有 **354** 项（22 个文件）保持全绿、前端 **69** 项（11 个文件）保持全绿；
两处随后各自增长（011 给 `test_log_safety.py` 加了 2 项，本功能给前端加了 `bili-upload.test.tsx`），
现状见 [docs/testing.md](../../../docs/testing.md) 的基线数字。
跑法见 §5。

## 2. 覆盖策略：把「离线可测」当成设计约束

`bili_upload` 被刻意写成纯函数式（不写库、不发事件、不读全局配置），并把三个外部接缝
（`runner` 跑子进程、`transport` 发 HTTP、`sleeper` 等待）全部显式注入。于是最贵、也最难在
真实账号上复现的分支可以在毫秒级跑出来：

| 难复现的场景 | 怎么造 |
| --- | --- |
| `biliup` 成功但对账查不到 | `FakeBiliTransport(archives=[])` |
| 601 频控 | `command_runner(returncode=1, stdout="code 601 …")` |
| 稿件列表延迟，第二次才读到 | `archives_error=…, archives_ok_after=1` |
| 合集建好但归集失败 | `episodes_error=HttpError(…)` |
| 平台判重（12051）后按时间序重找 | `reply_pages={2: [own_reply(…)]}, comment_code=12051` |
| 投稿命令超时 / worker 崩溃 | 让 runner 抛异常或跑一个真的 `sleep` |
| 进程重启留下的 `uploading` | 直接把行改成 `uploading` 再调 `resume_interrupted()` |

**替身遇到不认识的 URL 一律 `AssertionError`**，不返回空响应 —— 那样「某处多打了一个接口」
会立刻暴露，而不是被当成「这个接口返回空」静静吞掉。

三条替身自身的纪律（都是踩过才补的）：

1. **`None` 不能兼作「用默认值」的哨兵。** 早先 `FakeBiliTransport(account=None)` 会被当成
   「用默认账号」，于是「未登录」那条用例根本没测到目标分支、绿着过去。现改用 `UNSET` 哨兵。
2. **别让开发机的状态进断言。** `detect_broken_stub` 会先用 `find_biliup` 摸本机 PATH；
   本机恰好装了 `biliup` 时，用例里「应该用我造的替身来修」会变成「用本机那个真二进制来修」——
   断言跟着开发机走。`test_bili_bin.py` 用 autouse 夹具切断模块内部对 `find_biliup` 的引用。
3. **判据要真的到达。** 构造「合集重试不新建」时，如果所有稿件都停在「待核对」，
   归集根本不会发生，用例就在验证一件没被执行的事（假绿）。所以那条用例让稿件真的成功。

## 3. 自动测试发现并修掉的缺陷

### 3.1 真缺陷（实现改动了）

| # | 缺陷 | 后果 | 判据 |
| --- | --- | --- | --- |
| 1 | **`run_command` 的 `timeout` 形同虚设** | 一个不打印任何输出就挂住的投稿会让记录永远停在「上传中」——正是这个超时唯一要防的场景 | 实测：30 秒的 `sleep` 跑满全程、返回码 0、没有超时标记。原因是「主线程 `for line in stdout` 读完再 `wait(timeout)`」，阻塞在管道上时那段 `wait` 根本执行不到。改为**读取放后台线程、`wait(timeout)` 放主线程** |
| 2 | **对账不归一标题空白** | 用户在手填表单里多打一个尾随空格，对账就永远查不到自己刚投的稿件，一次成功投稿被标成「待核对」 | 模块注释写了「B站 会对标题做首尾空白处理」，但实现两侧都没归一 —— 注释给了理由，没有对应动作。现在**两侧都 `strip()`**（平台保留空白也能对上） |
| 3 | `validate()` 不查**单项标签长度** | 一个 150 字的标签能通过全部校验、直达平台才被拒；而 `limits()` 一直对外公布 `tag_item = 20` | 与标题一样：**报错，不截断** |
| 4 | 报错回显阈值的分支**不可达** | 「完整显示这个超长标签」是死代码（阈值 12 < 单项上限 20，超限的标签至少有 21 字） | 引入 `TAG_PREVIEW_MAX = 40`（必须 > `TAG_ITEM_MAX`）并覆盖两个分支 |
| 5 | 合集简介一直是**空串**发给平台 | 简介是必填项，留空等于把「能不能过」交给平台猜；界面上又没有这个字段 | 补 `default_season_desc()` + schema + `types.ts` + 前端回传 |
| 6 | 弹窗焦点副作用依赖 `onCancel` | SSE 每来一条事件 App 就换一次函数身份 → 用户打字途中被全选 | 改为挂载时只跑一次（`cancelRef` 承载回调） |

### 3.2 测试自身的缺陷（用例改了）

- **「显式传 None」无法表达**（§2 第 1 条）；
- **本机状态污染断言**（§2 第 2 条）；
- **`elapsed_seconds` 的语义**：误以为还在跑时返回 `None`，实际返回「已经跑了多久」
  （起止时间缺一个才给 `0`）。这是**更好的**行为，用例的断言错了。

## 4. 没有覆盖的部分（如实说明）

### 4.1 真实账号上的端到端

一条都没有。`biliup` 与 B站 的所有调用都被替身换掉了 —— 这是为了让 448 个用例能在 1 分钟级跑完，
代价是**「我们的请求形状与平台真实要求一致」没有被自动验证**。为此做了两件补偿：

- 所有端点与参数名都取自 `bilibili-API-collect` 的实测记录，不是猜的
  （尤其 `episodes` / `sectionId` 是 camelCase，文档表格里那对下划线命名是笔误）；
- `ai/feature/bilibili-upload/01-requirements.md` 的「技术事实」一节记录了每条结论的来源。
  其中三条来自 arXiv 项目的实测：`upload` 返回 0 ≠ 已发布（**必须对账**）、601 是账号侧频控、
  封面 URL 由 `cover/up` 返回。另外两条在本功能上线后被**推翻**并改掉：
  F6「cookie 登录必须走 `--submit web`」是错的（F6′），真正卡住投稿的是启动时那次令牌校验（F6″）。

> **这套替身有一个系统性盲区，已经在真实投稿上付过代价**：替身只验证「我们发出的请求长什么样」，
> 不验证「`biliup` 拿到我们的文件后会怎么反应」。011 那个缺陷（cookie 文件缺投稿令牌 → `biliup`
> 一个请求都不发就退出）在 448 个用例里全程绿灯 —— 因为替身从来不会「拒绝」我们。
> 教训：`biliup` 自己的**输入契约**（cookie 文件必须带哪些字段）也要当成待测契约，
> 用冻结的字面值把签名与文件形状钉住，而不是只测我们这一侧的组装。

**首次真实投稿必须手工验收**（见 §6）。

### 4.2 前端布局

前端自动测试是 `vitest + jsdom`，而 **jsdom 不做布局**（`getBoundingClientRect()` 一律返回 0）。
所以「两个盒子是否共线」「文本有没有溢出」这类判据**永远无法单测**，自动化上限是
「样式源文不变式」（见 `frontend/src/styles.test.ts`）。真判据是 CDP 探针 + 截图。

### 4.3 平台侧时序

「稿件列表延迟多久」「合集人工审核要几天」只能等真实账号给出。对账的退避重试次数
（`DEFAULT_VERIFY_ATTEMPTS = 3`、间隔 5 秒）是按经验定的，**没有实测数据支撑**，
真实环境里可能需要调大。

## 5. 怎么跑

```bash
# 后端（仓库根，用仓库自带的虚拟环境）
./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider \
  --basetemp=tmp_pytest/runNNN          # 目录**必须不存在**

# 只跑本功能
./.venv/Scripts/python.exe -m pytest backend/tests/test_bili_*.py backend/tests/test_api_bilibili.py \
  -q -p no:cacheprovider --basetemp=tmp_pytest/biliNNN

# 前端
cd frontend && npx vitest run --environment jsdom
npx tsc --noEmit
```

### 5.1 本机沙箱：全量一次跑不完

**把全部用例放在一个 pytest 进程里跑（011 合入后共 804 项），会在第 50 次「删除」时被沙箱的
批量删除守卫拦下**（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）。第一个 error 是守卫的 `SystemExit: 1`，
之后整个进程被"毒化" —— 后续每个 `tmp_path` 夹具都在 setup 阶段报 `AssertionError`。
实测结果（当时全集 774 项）：`644 passed, 130 errors`，**0 个 `FAILED`**，报错文件全是既有测试
（`test_ytdlp_service` / `test_output_paths` / `test_safe_delete` …），与本次改动无关。

**判据：先看有没有 `FAILED`。** 全是「at setup of」+ `SAFE_DELETE` 一律是环境问题，不是代码问题。
可行的替代做法是按文件**分块**跑（每个进程各自计数，实测每块 150 项上下都安全）：

```bash
# 既有 354 项（三块）
… test_ytdlp_service.py                                       #  73
… test_api.py test_connectivity.py test_cookie_health.py test_db.py \
  test_dev_server.py test_download_progress.py test_error_advice.py \
  test_finally_guards.py test_job_manager_finally.py test_log_safety.py   # 155
… test_logging_setup.py test_output_paths.py test_paths.py test_progress_persist.py \
  test_proxy.py test_resolution_decisions.py test_runtime_env.py test_safe_delete.py \
  test_stall_guard.py test_system_open.py test_transfer_stats.py          # 126
```

## 6. 手工验收清单（首次真实投稿）

按顺序做，每步都有可观察的判据：

1. **体检**：设置面板里填好 `biliup` 路径（或让它自己找到）→ 点「重新检查」→
   工具、ffmpeg、登录三项都要有结论；缺哪项要同时给出**下一步动作**。
2. **登录**：点「扫码登录」→ 二维码能显示（`<svg>` 有实际宽高，不是 0×0）→
   手机确认后变成「已登录（账号名）」。
3. **单视频**：下载一个 1 分钟以内的小视频 → 点 B站 上传 → 弹窗里标题/分区/标签/简介**已预填** →
   随便改一个字 → 确认 → 条目状态从「排队中」→「上传中」→「已上传」。
4. **去 B站 创作中心核对**：稿件在不在、标题对不对、分区对不对、封面是不是第 1 秒那一帧。
5. **置顶评论**：稿件评论区里应该有一条已置顶的评论，内容是弹窗里那段。
6. **对账延迟**：若状态停在「待核对」，等 1–2 分钟点「重新核对」→ 应变成「已上传」。
   **这里绝对不许出现「重新上传」。**
7. **合集**：选一个 3 条以上的任务 → 点合集上传 → 弹窗里能看到逐条预览（标题/简介/标签各不同）→
   确认 → 三条依次跑完 → 状态变「已上传」。
8. **去 B站 核对合集**：创作中心的「合集管理」里应有一个同名合集，且三条稿件都在里面。
9. **幂等**：对第 3 步那条已上传的稿件点「重试上传」→ 提示「已经上传成功，没有重复投稿」，
   且 B站 上**没有**多出第二条。

任何一步的界面文案里出现 `season` / `tid` / `csrf` / `unverified` 这类术语，都算文案不合格。
