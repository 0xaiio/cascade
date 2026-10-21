# 04 · 测试

本文回答「凭什么算测过了」：自动测试覆盖了什么、**没覆盖什么**、手工验收怎么做。
实现见 [03-implementation.md](03-implementation.md)。

## 1. 结论

| 层 | 文件 | 用例数 |
| --- | --- | --- |
| 纯工具（文案/状态折合） | `backend/tests/test_bili_policy.py` | 28 |
| 纯工具（模板/标签/硬限/**版权与来源**/**归集方式**） | `backend/tests/test_bili_meta.py` | 71 |
| 基础设施（HTTP 接缝、扫码会话） | `backend/tests/test_bili_auth.py` | 59 |
| 基础设施（`biliup` 探测/命令/进度/执行） | `backend/tests/test_bili_bin.py` | 44 |
| 领域（会员态接口） | `backend/tests/test_bili_api.py` | 89 |
| 领域（单次上传编排） | `backend/tests/test_bili_upload.py` | 69 |
| 编排（队列/落账/收敛/预填/**归集决策**） | `backend/tests/test_bili_manager.py` | 82 |
| 编排（读模型与徽标聚合） | `backend/tests/test_bili_read_model.py` | 60 |
| 入口（13 个 operation 的状态码） | `backend/tests/test_api_bilibili.py` | 55 |
| 共享替身 | `backend/tests/bili_fakes.py` | — |
| **合计** | **9 个文件** | **557** |

数字取自 `pytest --collect-only`（逐文件），与 `backend/tests` 全集减去既有基线相符：
**557 + 356 = 913**，而 `backend/tests` 全集正是 **913**。
（那 356 是 `main` 的既有 354 加 [`011`](../../bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)
给 `test_log_safety.py` 加的 2 项 —— **B站 之外的部分一行没动**。）

**这张表 2026-10-15 重新逐文件实测过，此前少记了 25。** 表里停在 `467` 时对应的全集是
`467 + 356 = 823`，也就是「自制 / 转载 + 原视频链接」那一轮**之前**的状态：那一轮的 +25
（`test_bili_manager.py` +6、`test_api_bilibili.py` +9、`test_bili_meta.py` +10）
只写进了下面的正文，没同步进这张表。本轮「归集方式」再 +15（`test_bili_manager.py` +6、
`test_api_bilibili.py` +7、`test_bili_meta.py` +2）= **507**。两轮的增量现在都在表里了。
**教训：逐文件表与正文的「+N」是两份，改完一轮要一起核** —— 否则它会安静地停在某个历史快照上，
而它恰恰是「凭什么算测过了」最容易被引用的一份。

**紧随其后的「质量提升轮」再 +36 = 543**（`test_bili_auth.py` +3、`test_bili_api.py` +14、
`test_bili_manager.py` +6、`test_bili_read_model.py` +13，其余五个文件一行没动）。这轮的增量集中在**边界**：
建合集回包 `id` 的四种平台形态与 10 种坏形状、合集 `upload_ids` 列的七种内容形态加 `null` 列、
两处「条目列表」解析必须逐字一致、`limits()` 每个字段都要与常量一致
（`tag_preview` 漏了前端就会自己写死 12，见 [012](../../bug-fix/012-preflight-contradicts-itself-and-the-server.md)），
以及「成功 / 失败 / 在途三组集合**恰好归类一次**」的不相交性。
**核对等式：表内合计 + 356 = `backend/tests` 全集**（356 是 `main` 的既有基线）。

**归集缺陷轮再 +14 = 557**（`test_bili_api.py` +12、`test_bili_upload.py` +2，
`test_bili_manager.py` 三条用例只换了夹具、条数没动）。增量全在**「合集 id ≠ 分区 id」**
这条判据上（[013](../../bug-fix/013-season-section-id-confusion.md)）：
合集列表里 `sections.sections[]` 的三种形态（有默认分区 / 只有自定义分区 / 一个都没有）、
新合集暂未进列表时的退避重试、超过一页时的翻页、脏分区行、以及
**「读不到分区就一个归集请求都不发」**。等式仍是 557 + 356 = 913。

前端同步在涨：这一轮从 12 个文件 / 119 passed 到 **15 个文件 / 172 passed**，
新增的三份全是**不挂载组件**的纯函数测试（`biliForm.test.ts` 35、`biliStatus.test.ts` 14、
`errors.test.ts` 4）—— 判断被抽成纯函数之后，边界才测得动。

**「按钮点下去做什么」这两轮只动前端**（见 §10、§11）：后端一行未改，上面那张表与等式
（557 + 356 = 913）保持不动；前端从 **208 passed** 到 **216**（`uploaded` 档改行为，+8）再到
**222**（`unverified` / `partial` / `uploading` 三档定语义，+6），`tsc --noEmit` 退出码 0。
文件数是 **18 个**（172 → 208 这一段是字幕那几轮涨出来的，不在本功能账上）。

`test_bili_meta.py` 里与版权 / 来源有关的那批集中在三组判据：`normalize_copyright` 的回退方向
（读不出合法值时**回退转载而不是自制** —— 标成自制是替用户声明一件他没说过的事）、
`effective_source`（自制稿即便带着来源也不提交）、`validate` 对「转载缺来源」的拦截。
`test_bili_bin.py` 的那 2 条锁的是**实发参数**：`--copyright` 跟着元数据走、
`--source` 只在转载时出现 —— 这两条错了会让落库快照与实际提交不一致。

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

### 3.1.1 「自制 / 转载 + 原视频链接」这一轮新发现的两条

| # | 缺陷 | 后果 | 判据 |
| --- | --- | --- | --- |
| 7 | **`BiliMetadata` 加字段后，两个精确断言用例没跟** | 提交体少了 `copyright` / `source`，而服务端靠它们重建元数据 —— 重投时稿件可能从转载悄悄变成自制 | `vitest` 报 2 例失败。这两条用例本来是 `toEqual` 精确比对整个请求体，正好是锁定「前端原样回传」的地方；改成 `expect.objectContaining` 就能过，但那样它们再也不会发现这类偏差 |
| 8 | **新用例写成 `.metadata.copyright`，而 harness 的 body 类型是 `Record<string, unknown>`** | 运行时全绿、`tsc --noEmit` 报 `TS2571: Object is of type 'unknown'` —— 断言过了但类型是错的 | 先取出 `metadata` 再断言字段。**这一条只有类型检查能发现，vitest 一路绿灯** |


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

「自制 / 转载」那两档单选的选中态靠 `.bili-choice-option:has(input:checked)` 上色。
这里有一条**纠正过一次**的判断（写错过，记下来免得再错一遍）：初稿以为「jsdom 不支持 `:has()`，
所以配色无法断言」。实测（`vite.config.ts` 已开 `test.css = true`，用例 `import "./styles.css"`）：

```
stylesheets = 1
matches(':has(input:checked)') = true
borderColor      = "rgb(251, 114, 153)"   ← #fb7299
backgroundColor  = "rgb(255, 241, 245)"   ← #fff1f5
```

所以 `:has()` 的选中态**可以**用 `getComputedStyle` 断言，那条用例已加进去。
（探针第一次跑出空值，是因为那次没 import 样式，`document.styleSheets.length = 0` ——
**「读不到样式」与「选择器不支持」是完全不同的两件事**，前者只说明样式表没加载。）
不变的边界仍然是 §4.2 那条：jsdom 不做布局，所以「两档是否共线」「边框有没有被挤变形」测不了。

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
3b. **已上传之后按钮换了件事做**：鼠标悬停在那个绿色「已上传」上，提示必须是
   **「B 站已上传；点击进入 B 站」**；点它应**新开一个 B站 稿件页**，且**不弹出上传表单**。
   （若提示仍是「点击重新上传」，说明这条记录本地没记下 `bvid` —— 那是回落路径，不是缺陷，
   见 §10 第 4 条。）
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

## 7. 归集方式的测试

后端 **848 passed**（823 → 848，+25）、前端 **114 passed**（105 → 114，+9），
`tsc --noEmit` 退出码 0。四项检查（`check_layers` / `check_api_contract` / `docs.py check` /
`check_doc_anchors`）全过。

新增用例钉的判据，按「写错了会不会被用户发现」排：

| 用例 | 钉住什么 |
| --- | --- |
| `test_join_an_existing_collection_never_creates_a_new_one` | `season/add` **一次都不能调** |
| `test_creating_a_collection_still_leaves_season_id_empty...` | 反向：提交后 `season_id` 仍为 `None`（预置成 0 就会跳过建集） |
| `test_switching_the_target_collection_overrides_the_previous_one` | 两次 `sectionId` 依次是 111 / 222 |
| `test_choosing_no_collection_leaves_nothing_pending` | 稿件照投、**一次都不建集**、记录落终态 |
| `test_switching_to_no_collection_stops_a_previous_pending_grouping` | 上一轮的 `pending` 被停掉 |
| `test_list_seasons_reports_the_login_problem_...` | 「读不到」与「真的没有」是两句话 |
| `test_a_new_collection_must_not_carry_a_season_id` 等三条 | 矛盾各自报错，不静默取其一 |
| 前端 `never sends a stale season id alongside 新建合集` | 切回新建时编号必须被撤掉 |
| `test_manager_itself_refuses_to_join_without_an_id` | `submit_collection` 是**公开方法**，路由那道 400 不是唯一防线 |

### 三处「实测推翻假设」

**1. 路由里先归一再校验，等于把校验废掉了。**
`test_an_unknown_group_mode_is_400_not_silently_corrected` 第一次跑是红的（200 不是 400）。
原因是路由先 `mode = normalize_group_mode(payload.group_mode)` 把非法值修成 `new`，
再拿 `mode` 去校验 —— 校验永远看不到非法值。**修正：校验用原始输入，归一只用于通过校验之后
真正执行的那一步。** 这条教训可复用：任何「先修正再校验」的顺序都会让校验失效。

**2. `skipped` 我先实现成了「不落库」，被自己的测试判成缺陷。**
第一版想的是「不归集就别建记录，省得留个挂着的徽标」，于是 `test_choosing_no_collection_...`
里 `harness.season("job2")` 直接 `NoResultFound`。想清楚之后改了：不落库时界面上什么都不显示，
用户分不清是「没归集」还是「还没开始归」。**终态记录 + 明确文案** 才同时解决两件事 ——
不会自动归集（`_maybe_group` 只认 `pending`），也有一句能显示的话。

**3. 测试辅助函数的「顺手重置」会悄悄吃掉调用方的参数。**
新增的合集方式用例要先造一份「条目全部就绪」的预填（默认夹具刻意留一条未就绪的，
提交按钮一直是灰的）。我第一版把这份预填写在辅助函数里，而辅助函数内部又无条件
`mockState.biliPrefill = biliPrefillCollectionPayload` —— 于是 4 个用例全红，
症状是「找不到『开始上传全部』按钮」，看着像组件坏了。
试过 `??=` 也不对：`mockState` 每用例被 harness 重置成**单视频**预填，
「保留调用方设过的值」会让所有既有用例开成单视频表单。
**最终改成显式传参**（`openCollectionDialog(prefill)`），并在函数里写明为什么不能做小聪明。

### 未验证（与本文档其他部分同一条纪律）

真实账号上的**三条路径都还没跑过**：加入已有合集、新建合集、不归集。
B站 对合集有人工审核，所以归集类断言的自动化边界止于「发出了哪些请求、
落了什么状态」，「平台侧是否接受」需要有真实账号才能验。

## 8. 单视频归集方式的测试

后端 **863 passed**（848 → 863，+15：manager +6、路由 +7、meta +2）、
前端 **117 passed**（114 → 117，其中一条是**把旧断言反过来**）。
`tsc --noEmit` 退出码 0。四项检查（`check_layers` / `check_api_contract` / `docs.py check` /
`check_doc_anchors`）全过。

新增用例钉的判据，按「写错了会不会被用户发现」排：

| 用例 | 钉住什么 |
| --- | --- |
| `test_single_submit_joins_an_existing_collection` | 单视频投完真的走完归集；`season/add` **一次都不调** |
| `test_single_submit_creates_a_collection_when_asked` | 合集名用的是**用户填的**那个，不是任务名 |
| `test_single_submit_without_grouping_leaves_no_season_record` | 默认路径**一条记录都不落**（`bili_season` 查询为空） |
| `test_single_submit_to_no_grouping_stops_a_previous_pending_season` | 上一轮的 `pending` 被停成 `skipped`，且**不动它的标题** |
| `test_single_submit_refuses_existing_without_an_id` / `..._new_without_a_title` | manager 层的两道闸（公开方法不能只靠路由防） |
| 路由 `..._defaults_to_no_grouping_and_stores_no_season` | 不传 `group_mode` → `season` 读模型是 `None` |
| 路由 `..._rejects_an_unknown_group_mode_with_400` | 非法值 400 而不是被静默修正 |
| 路由 `test_prefill_reports_the_default_group_mode_per_kind` | 默认值随预填下发，两种 `kind` 不同 |
| 前端 `offers 归集方式 on a single video, defaulting to 不归集` | 控件**对单视频可见**、默认是不归集、且列表没回来时仍可提交 |
| 前端 `never sends a stale season id alongside 新建合集 on a single video` | 切回新建时编号必须被撤掉（且合集名要真的被提交） |
| 前端 `still uploads when the collection list cannot be read` | 列表拉失败不影响投稿；提示是**结论**而不是「正在读取…」 |

### 三处「实测推翻假设」

**1. 用户报的「看不到任何与合集相关的 UI」，根因是可达性而不是缺陷。**
我先后排除了三件事：产物是否旧（`frontend/dist/assets/*.js` 建于 19:47，晚于提交的 13:05，
且 `grep` 得到「归集方式 / 新建合集 / 加入已有合集 / 不归集」四个字符串都在）、
CSS 是否缺（`.bili-choice` 在源与产物里都有）、以及链路是否断（`App` 的
`seasons` 确实传进了弹窗）。最后查数据库才看到关键事实：**本机 12 个任务的
`total_items` 全是 1**，`bili_season` 0 行 —— 也就是**没有任何一个 playlist 任务**，
而控件当时挂在 `isCollection &&` 上。
**教训：用户说「界面上没有 X」时，先确认「X 的渲染条件在他的数据上成立吗」，
不要先去查「X 是不是坏了」。** 这是我第一次在这类反馈上先花时间验证产物与样式。

**2. 旧测试把「只对合集开放」写成了断言，于是它现在必须反过来。**
`never renders the 归集方式 controls for a single video` 里写着
「单视频没有『归集』这回事 —— 加上去只会让用户去为一个不存在的概念做选择」。
这句话在**接口与产品两个层面都不成立**（B站 允许把单条视频放进合集；`create_season`
不需要先有稿件；归集阶段不看条数）。**测试把设计假设固化下来的代价是：改设计时要先推翻一条绿测试**，
而那正是它该起的作用 —— 这次它确实让人停了一下、去复核那条假设，复核的结果是假设错了。

**3. `getByLabelText` 找不到「加进哪个合集」，暴露了三处门禁只放开了一处。**
我只把单选组外面的 `isCollection &&` 去掉，却漏了「合集名输入框」与「合集下拉」两处同类门禁。
症状是单视频下选了「加入已有合集」**什么都没出现**。三个 `isCollection &&` 是同一件事的三份，
**改这类门禁必须一次全部搜出来**（`grep -n "{isCollection &&"` 一次列全，再逐条判断
哪一条是「合集专属」、哪一条只是「当前场景不需要」）。

### 未验证（与本文档其他部分同一条纪律）

真实账号上单视频归集的三条路径（加入已有 / 新建 / 不归集）**都没跑过**，
合集那三条同样没跑过。B站 对合集有人工审核，所以归集类断言的自动化边界止于
「发出了哪些请求、落了什么状态」，「平台侧是否接受」需要有真实账号才能验。

## 9. 按需读合集列表的测试

后端 **863 passed**（本轮没动后端）；前端 **119 passed**（117 → 119）；
`tsc --noEmit` 退出码 0。四项检查全过。

### 9.1 单测钉住的两件事

| 用例 | 钉住什么 |
| --- | --- |
| 前端 `does not read the collection list until 加入已有合集 is chosen` | 开窗 **0 次**请求；选中「加入已有」**恰好 1 次**；在三个选项之间来回切**仍是 1 次** |
| 前端 `blocks the submit on a single video when 加入已有合集 is picked but nothing is chosen` | 单视频与合集**同一条规则**：没挑合集就别放行；挑了就放行 |

第一条比它看起来重要：原先那几条「列表读不到也能投稿」的用例**在回归发生时照样会绿**
（它们只是不选那一项）。把请求挪回开窗路径，症状只是「开窗多一次真实往返」，
没有任何断言会失败 —— 所以专门加了一个请求计数器（`mockState.biliSeasonReads`）来钉它。
**「安静地多做一件事」是测试最难抓的一类回归，得给它一个显式的计数量。**

### 9.2 真实浏览器探针：`tmp_acceptance/ui_single_group.py`（23 条断言，全过）

跑法：后端 `python -m app --port 8010`（单端口托管 `frontend/dist`），
用 managed `envs/default` 解释器跑探针（需 `websocket-client`）。

新增/修好的断言：

| 断言 | 为什么非要在真浏览器里量 |
| --- | --- |
| 开窗至今读合集列表 **0 次** | jsdom 里 `fetch` 是假的，「开窗到底发没发请求」量不出来。探针在**开弹窗之前**装一个 `window.fetch` 记录器（`api.ts` 里是 `fetch(url, ...)`，调用时才查全局，所以装得上）。 |
| 选中「加入已有」后**恰好 1 次**、来回切**仍 1 次** | 去重依赖 React 的渲染时序，`useRef` 在真实重渲染下的行为只有在真环境里才算验过。 |
| 那一格**落定**（不是停在「正在读取…」） | 这条只有真浏览器能验，且**恰好在本机失败路径上验到了**（见下）。 |
| 说明文案主语是「这条视频」/ 单视频表单标题 | 组件在真实 `prefill.kind` 下渲染出来的东西，与单测里手搓的 props 不是一回事。 |

### 9.3 三处「实测推翻假设」

**1. 探针的选择器串到了另一组单选上，而那条断言恒假。**
`dlg.querySelector('[role=radiogroup]')` 返回的是**第一个**匹配 —— 这张表单上有两组：
先是「转载 / 自制」（版权），后才是「归集方式」。于是「三个选项齐全」永远拿的是
「转载 / 自制」那两项。**它不是「偶尔失败」，是恒假**，只是一直没人细看那行输出。
修法是按 `aria-labelledby` 限定：`[role="radiogroup"][aria-labelledby="bili-group-label"]`。
**教训：探针里用 `querySelector` 取「那个控件」时，先数一遍页面上同 `role` 的元素有几个。**

**2. 「正在读取…」不是推理出来的，是量出来的，而且比预想的严重得多。**
我原本按代码推理「DNS 卡住 → 界面挂在正在读取」。实测是
`curl --max-time 40 http://127.0.0.1:8010/api/bilibili/seasons` → `http=000 time=40.003s` ——
**40 秒不返回**。这解释了为什么网络层那个 `timeout` 形同虚设：
它用的是 `socket` 超时，**不覆盖 DNS 解析**。所以 12 秒的客户端兜底是**必需品**而不是保险，
探针里那句结论（「可能是登录已失效或网络不通…」）就是它给的。

**3. 「落定」这个词本身需要定义，否则探针会写成一个假断言。**
第一版探针写的是 `time.sleep(0.5)` 然后断言「不是『正在读取』」—— 在**本机**这条会失败
（请求 40 秒不回，0.5 秒时必然还在读）。改成轮询「那一格已经不是『正在读取』」并给 16 秒上限，
断言的对象才是设计意图（**最终**落成一个结论），而不是「500 毫秒内落定」这个没人承诺过的时限。

### 9.4 未验证

- 关窗 / 提交后的 `resetSeasonList()`（即「下一轮重新读」）只有单测覆盖，探针没走关窗再开窗。
- `SEASON_LIST_TIMEOUT_MS` 这条路径**在本机是被真实验到的**（请求 40s 不回），但
  「真结果迟到并覆盖兜底结论」这条只在单测里构造过，真实环境没出现（本机压根读不到）。
- B站 侧对归集的接受度：与前几节同一条边界，需要真实账号。

## 10. 「已上传」按钮改为进入 B 站

需求原话是「鼠标移到绿着的『已上传』上，提示却说『点击重新上传』，这不太合理」。改动只有一处语义：
**`uploaded` 这一档的点击与 tooltip 换掉，外观与文案一个字没动**（FR-B31）。

前端 **208 → 216 passed**（+8），`tsc --noEmit` 退出码 0；后端一行未改，未重跑。文件数不变（18）。

### 10.1 单测钉住的四件「写错了会被用户发现」的事

| 用例 | 钉住什么 |
| --- | --- |
| `makes 已上传 an entry point to B站 instead of a second upload` | tooltip **恰好**是「B 站已上传；点击进入 B 站」；点击只调一次 `window.open`（目标 URL 正确），**没有** `dialog`、**没有**预填请求、**没有**提交请求 |
| `still opens B站 when the local video file is gone` | `output_path` 为空时按钮仍可点并仍打开稿件页 —— 「视频还没下载好」这条理由只约束**上传** |
| `falls back to the upload path when the record has no link at all` | `bvid`/`video_url` 都为空时退回老路（tooltip 仍是「点击重新上传」且确实打开表单） |
| `an uploaded collection item opens its own page, not the first one` | 条目级只打开**这一条**的链接（BV1two），不拿任务级的第一条顶替 |
| `biliStatus.test.ts` 的四条 | `uploadedTargetUrl` 取「第一条有链接的」；`null` / `[]` / `undefined` / 全空串一律返回 `null`；空串等同「没有」 |

`title` 与 `aria-label` 共用同一个常量（`BiliUploadButton.HINTS`）：视觉与读屏说同一句话，
两处各写一份必然漂移 —— 这也是为什么测试断言的是**整句相等**而不是「包含『进入 B 站』」。

### 10.2 真实浏览器：`tmp_acceptance/ui_bili_uploaded_button.py`（11 条断言，全过）

`Fetch` 在**请求阶段**把 `/api/bilibili/uploads/summary` 换成一条合成的「已上传」记录
（任务列表是真的，从 `GET /api/jobs` 现取 25 条里第一条已下载好的），其余请求照旧打到真实后端。
两种形态各跑一遍，11/11 通过：

- 有链接：tooltip 与无障碍名都是约定那句、按钮文字仍是「已上传」、`tone-ok` 仍是绿、可点；
  点击后 **`Target.targetCreated` 真的开出了 `bilibili.com/video/BV1xx411c7mD/`**，且页面上没有 `[role=dialog]`。
- 无链接：tooltip 退回「B站：已上传 · 点击重新上传」，点击打开上传表单。

**三条探针自身的坑（都当场修了）**：

1. **按标题子串找按钮会撞上明细链接。** 第一版用 `button[aria-label*="<稿件标题>"]`，结果命中
   `[在 B 站查看]` 那枚链接（`aria-label="在 B 站打开 …"`），于是断言一路「徽标没变绿」，
   而界面其实完全正常。判据要收窄到 `button.bili-button` **并且**按主语 `endsWith` 匹配。
2. **`send()` 必须带事件队列。** `Fetch.requestPaused` 若被「等自己的应答」那一步吞掉，
   请求永久挂起、summary 永远不返回，症状同样是「徽标不变」，与界面缺陷长得一样。
3. **`window.open` 要用 `userGesture: true` 触发**，否则 headless 会当成弹窗拦截，看不到新 target。

### 10.3 未验证

- 任务级「已上传」在**多稿件**（合集）情形下打开的是第一个稿件 —— 真实浏览器里只验了单稿件形态
  （`item_id` 为空）；多稿件只在单测里构造过。
- 合集页（`collectiondetail?sid=`）**没有做入口**：它要账号 `mid`，为一个次要入口多引一次账号请求
  不划算，见 [02-design.md](02-design.md) §10。

## 11. 非终态档位：在跑就等、没核对就核对、只补没成功的

HX 追问三件事：为什么「上传中」还能点着「重新上传」？「待核对」到底是什么状态、是不是已经传上去了？
如果是，它也不该重投。这一轮把**所有非终态档位**的点击语义一次定清（FR-B32 / FR-B40）。

### 11.1 先回答那两个问题

**「上传中」本来就点不动** —— `disabledReason`（「正在上传中，等这批跑完」）一直把它禁用着。
真正错的是**文案**：一个灰着的按钮还写着「点击重新上传」，等于许诺一个不会发生的动作。
本轮把这一档改成徽标驱动（`waiting` 优先于一切），提示换成为什么不能点：
「正在上传中；跑完会自动变成「已上传」或「失败」」。**上一轮 02-design §10 那张对照表是缺陷源**：
它只列了「档位 → 点击」，没有「禁用」这一维，读的人自然会以为「上传中」可以点。表已重写。

**「待核对」= 投稿命令成功过，但对账没在平台列表里查到它**（`bili_upload.unverified`）。
所以它**很可能已经传上去了**，只是 B站 的稿件列表有几十秒到几分钟延迟 —— 这正是它不能重投的原因。
它原本的点击是「打开投稿表单」，也就是「重投一条可能已经在平台上的稿件」：本功能里最贵的一类误操作。
现在点它 = 调 `retry_upload`，服务端对 `unverified` 只走 `_reverify_work`（回读平台列表，不发投稿）。

`partial`（部分失败）同理：任务级按钮原本打开**整批**合集表单，而 `submit_collection` 会把每一条
都 upsert 成 `queued` —— 已经发布的那几条会被重新投一遍。现在只挑 `retryableUploadIds()` 那几条，
逐条交给服务端按各自终态决定「重新排队」还是「只核对」。

### 11.2 用例（前端 216 → **222 passed**，+6；`tsc --noEmit` 退出码 0；后端未改，未重跑）

| 用例 | 钉住什么 |
| --- | --- |
| `stays disabled while an upload is in flight, and promises nothing` | 禁用 + 提示**整句相等** + 提示里**不含「点击」** + 点了不产生预填请求/表单 |
| `待核对 只重新核对，绝不重新投稿` | 只打 `.../retry` 一次（服务端据此走核对）；`biliPrefillQueries` 与 `biliUploadBodies` 都是空；页顶回执「已重新核对。」 |
| `部分失败 只补没成功的那几条，不碰已经上传的` | `biliRetryUploadIds === ["bili-bad"]` —— 已发布的 `bili-ok` **一次都没被提交** |
| `失败 仍然走投稿表单（没有记录在平台上，元数据可能还要改）` | `failed` 档没有被「一刀切」成重试：仍然预填 + 弹表单 |
| `biliStatus.test.ts` 三条 | `retryableUploadIds` 只挑 `failed`/`rejected`/`unverified`，已成功与在途一条不进；空值一律 `[]`；全成功返回 `[]`（那一档走「打开页面」） |

### 11.3 真实浏览器：同一个探针扩到四种形态（**22 条断言，全过**）

`tmp_acceptance/ui_bili_uploaded_button.py` 现在覆盖 `已上传（有链接）` / `已上传（无链接）` /
`上传中` / `待核对` 四种形态，`Fetch` 同时拦截 `summary` 与 `.../retry`：

- 上传中：`disabled=True`、提示整句正确、提示里没有「点击」、点一下不出现 `[role=dialog]`。
- 待核对：恰好 **1 次** `POST .../uploads/bili-probe/retry`、没有表单、页顶回执「已重新核对」。
- 已上传（有链接）：真的开出了 `bilibili.com/video/BV1xx411c7mD/` 新标签页。

**探针自身又踩了一个坑**：`pump()` 原先会把 `Target.targetCreated` 一并吃掉，
症状是「点了但没看到新标签页」而浏览器其实开了。修法是把非 `Fetch` 事件**放回队列**
（`events.extendleft(reversed(kept))`）—— 与「CDP 类必须带事件队列」是同一条教训的下半段：
**队列要有，而且不能在中间环节顺手清空。**

### 11.4 未验证

- `partial` 与多稿件`待核对` 的真实浏览器形态没跑（探针的合成记录只有一个 `uploads` 元素）；
  这两条只有 App 级集成测试覆盖。
- 「批量补投」的逐条回执合并（`X 条已重新排队，Y 条已重新核对`）只在单条路径被断言过，
  多条并存的文案没有专门用例 —— 它只影响提示措辞，不影响状态。

## 12. 上传进度：为什么是四段、怎么验的

HX 指出：点了上传之后按钮变成「上传中」，然后**什么都没有** —— 一次投稿可能几分钟到十几分钟
（含网络传输与平台审核），界面必须让人看得出「在跑」「跑到哪」「还要不要等」。

### 12.1 先把「能测什么」查清楚

`biliup upload --help` 里**没有**任何进度参数；实测日志里一次 14 MB 的投稿只有三行可用输出：

```
pre_upload: {...,"size":14266098,...}
Upload completed: ... => cost 481.85s, 0.03 MB/s.
APP接口投稿成功 (code 0)
```

也就是说**没有字节级进度**，而且它是在管道（非 TTY）里跑的，连原生进度条都被关掉了。
服务端 `_PROGRESS_STAGES` 早已把这三种行折成 0.2 / 0.6 / 0.95 —— 那正是**三个闸门**，不是比例。
**结论：不画百分比条。** 一根停在 20% 长达 481 秒的条，比没有条更让人以为卡死。

### 12.2 于是给的是「到哪一步 + 等了多久」

四段轨迹（排队 / 传输 / 提交 / 核对）+ 当前段扫光动效 + 每秒推进的「已用/已提交」。
映射与聚合抽成纯函数 `biliProgress.ts`：`uploadStageView` / `jobUploadProgress` / `uploadElapsedLabel`。
审核不是第五步 —— 它是「核对之后等平台」，四段全亮、写「已提交，B 站 审核中」，并且明确不给百分比。

### 12.3 数据怎么动起来

后端只在**闸门真的走过去**时发一条 `bili_upload_progress`（`_store_progress` 里判 `after > before`）。
`on_progress` 是每读一行输出回调一次，不判就会灌出几百条同值事件。前端沿用「收到任何一条事件就重拉」
的既有口径，因此**没有**为进度新增合并逻辑，也没有第二份状态来源。

### 12.4 用例（前端 222 → **241 passed / 19 文件**，+19；后端 1069 → **1072 passed**）

| 层 | 用例 | 钉住什么 |
| --- | --- | --- |
| 纯函数（`biliProgress.test.ts`，15） | `uploadStageView` 的表驱动 | 八档状态各落在哪一段、是否 settled、是否 broken；0/0.19/0.2/0.59/0.6/0.95 的分界 |
| 纯函数 | `jobUploadProgress` | 合集选**正在跑的那条**而不是第一条；`done` 只数已确认的；全成功/全失败/待核对都 `waiting=false`；空数组 → `null` |
| 纯函数 | `uploadElapsedLabel` | 在跑说「已用」、等审核说「已提交」；起点缺失或坏值 → `null`（不显示 0:00）；时钟倒挂不出负数 |
| 界面（`bili-upload.test.tsx`，+4） | 上传中 | 「上传进度」出现、当前段 `is-active`、前序段 `is-done`、`已用 01:0x` |
| 界面 | 合集 | 「第 2/2 条 · Part two」+「已有 1/2 条确认在平台上」 |
| 界面 | 审核中 | 四段全 `is-done`、没有 active、「已提交 12:0x」 |
| 界面 | 已发布 / 失败 / 待核对 | **不渲染**进度块 |
| 后端（`test_bili_manager.py`，+3） | `test_a_real_upload_walks_the_progress_gates` | 走真实回调链（替身回放 biliup 三类行）→ 事件恰好 `[0.2, 0.6, 0.95]`，一次投稿最多 3 条 |
| 后端 | `test_progress_events_only_fire_when_the_gate_moves` | 同值重放**不**发、倒退既不写库也不发 |
| 后端 | `test_progress_is_ignored_once_the_record_is_no_longer_uploading` | 结论落账后迟到的回调不把进度改回去 |

### 12.5 真实浏览器：`ui_bili_uploaded_button.py` 扩到 **32 条断言，全过**

新增 `[3b]` 一组（合成一条 `uploading` + `started_at` 65 秒前的记录）：

- DOM：`.bili-progress` 存在、`.bili-step` 恰好 4 个、标签正好是「排队/传输/提交/核对」；
- 状态：`排队` 是 `is-done`、`传输` 是 `is-active`、其余不是；详情「正在传输视频文件」；
- 时钟：「已用 01:0x」（说明每秒推进的计时真的在跑）；
- 动效：当前段的 `getComputedStyle(...).animationName === "bili-progress-sweep"` —— **它是「还在动」的
  凭据**，传输段可能几分钟没有事件；
- 版式：块高 93px、宽 853px（jsdom 量不出来，只能在这里看）；
- 截图留证：`tmp_acceptance/shot_bili_progress.png`（进度块）与 `shot_bili_card.png`（整张任务卡片）。

### 12.6 未验证

- **真实 biliup 跑一次投稿**的进度事件序列：本地这三条后端用例用的是替身回放的三类行。
  真机上「三个闸门是否严格按 0.2 → 0.6 → 0.95 到达」尚未实测（那需要真的投一条稿）。
- 12 分钟以上的长传输下「已用」的显示（`formatClock` 过 1 小时后变 `1:05:03`）只在纯函数层测过。
- 条目行（展开后的单条）没有各自的进度条：合集只显示「当前第几条」。若以后要逐条显示，
  `jobUploadProgress` 已经能按条算，缺的只是布局。

## 13. 长文件名 / 失败分类 / 体检按钮（018、ui 004）

### 13.1 用**真实日志**当夹具

这两个缺陷的证据都来自本机真实数据，所以用例直接拿它们做夹具，而不是造一段「看起来像」的输出：

- `test_bili_policy.py` 里的 `REAL_21104_TAIL` 是那条失败投稿的 `log_tail` 逐字抄本（含
  「验证cookie / 通过cookie登录」这些**成功**行）—— 它同时验证了「21104 要认出来」与
  「成功日志不能把结论带回登录失效」。
- `test_bili_upload.py` 的文件名用例按库里那张对照表取 `stem=85`（Ep 105）与正常名。

### 13.2 用例（后端 **1072 → 1081 passed**，+9；前端 241 → **248 passed / 20 文件**，+7）

| 层 | 用例 | 钉住什么 |
| --- | --- | --- |
| 分类（+6） | `test_the_title_error_is_not_mistaken_for_a_login_failure` | 结论里必须出现 21104 或「文件名」，且不等于登录失效 |
| 分类 | `test_a_benign_login_line_alone_is_not_a_failure` | 只有成功日志时不得判成登录失效 |
| 分类 | `test_real_auth_failures_are_still_recognized`（4 例） | 收紧 needle 后真登录失效仍认得出 |
| 上传（+3） | `test_a_long_file_name_is_uploaded_through_a_short_hard_link` | 传给 biliup 的是 `cascade-*.mp4`、`≤80`、内容大小与源一致、结束后删除、原文件仍在 |
| 上传 | `test_a_normal_file_name_is_passed_through_untouched` | 正常文件名不造链接 |
| 上传 | `test_a_failed_hard_link_falls_back_to_the_original_name` | 硬链接失败 ⇒ 退回原名 + warning，不复制 |
| 前端（+2） | `重新体检真的会再查一次，并留下体检时间` | 再发一次 `probe_account=true`，页面出现 `上次体检 HH:MM:SS` |
| 前端 | `体检进行中按钮不可点，并写明「体检中…」` | 加载态存在（用稳定句柄 + `waitFor`，不按名字抓瞬时元素） |
| 前端（+5） | `biliHealth.test.ts` | 按时返回 / 超时退回离线 / 网络错误照旧抛 / 离线也失败时抛 / 时限是 12 秒 |

### 13.3 真实账号验证（这一轮的判据不在测试里）

**修复后对同一条失败记录执行一次真实重试**，结果：

```
action=reupload  message=已重新排队上传
uploading 0.0 → 0.2 → 0.95 → reviewing
FINAL: reviewing | bvid=BV1iunv61Eu4 | error=None
```

账号稿件列表里出现了这条稿件（标题仍是 71 字符，`state=-30` 已提交待审核）；
`downloads/` 下没有残留 `cascade-*` 链接。

**注意**：这条重试是我代执行的（用户原话是「多次失败…仍然失败」），它产生的稿件是公开的。

### 13.4 「重新体检」的真实浏览器验证

`tmp_acceptance/probe_recheck_button.py`（**11 条断言全过**）：

```
点之前   text=重新体检  disabled=False  stamp=上次体检 12:20:14
点一下   text=体检中…   disabled=True   stamp=上次体检 12:20:14
结束后   text=重新体检  disabled=False  stamp=上次体检 12:20:17   ← 换了
```

### 13.5 探针/测试自身的两个坑

1. **按名字抓「体检中…」会抓到快照**。60ms 的窗口里元素会换回「重新体检」，断言拿到的已经是旧节点 ——
   改成先取稳定句柄再 `waitFor`。这与「按标题子串找控件会撞上别的同类元素」是同一类错误。
2. **界面测试里用假定时器会毒死同文件的其它用例**：`vi.useFakeTimers()` 之后 RTL 的 `waitFor` 不再推进，
   后面 8 条用例全部 5 秒超时。修法不是加 `advanceTimers`，而是**把判定挪进纯模块**（`biliHealth`），
   用 20ms 的真实时限测 —— 这也是更该有的分层。

### 13.6 未覆盖 / 如实说明

- **硬链接只在本机 NTFS 上验过**；exFAT / 网络盘 / 跨卷的退回路径只有单测（monkeypatch `os.link`）。
- 平台侧「80 字符」是**实测归纳**（stem=80 通过、85 失败），没有逐个测 81..84；取 80 作界更保守。
- `21104` 的另一种成因（**标题本身**真的超过 80）没有真实样本 —— 表单侧本来就会拦下，
  所以这条分类在真机上只走过「文件名超限」这一支。
- 「重新体检」的 12 秒超时路径在真实浏览器里没有稳定复现（后端通常 0.7~3 秒返回），只有单测覆盖。
- 合集（多条）场景下的文件名超限没有实测；合集走同一个 `upload_one`，逻辑上同一条路径。

## 14. 标题长度校验：合集量的是渲染后的每一条（019）

### 14.1 先回答「需不需要加」

用户的问法是「要不要在投稿页的标题区域加标题长度验证」。查完之后答案是**一半已经有了，一半完全没有**：

| 场景 | 改动前 | 缺口 |
| --- | --- | --- |
| 单视频 | 问题列表里有「标题超出 N 个字」 | 标题框旁只有 `71/80` 一个数字 —— 超限与合规是同一档灰字，得自己相减 |
| 合集 | 预检量的是**模板** | `{title}` 只有 7 个字 ⇒ **永远合规**，超限要等提交才被拒 |

合集这条是**从来没生效过**的校验，所以真正要补的是它（服务端 `validate_collection` 一直是逐条渲染后
校验的，前端却量模板）。记录见 [ai/bug-fix/019](../../../bug-fix/019-the-title-length-check-never-fires-for-collections.md)。

### 14.2 分工：可观察性归前端，权威归服务端

- 前端只回答「超没超、超多少、是哪一条」：`charBudget`（把 `length/limit/over/label/warning`
  合成一个结构体，**超没超必须和数字一起算出来**）+ `collectionTitleBudget`（渲染后的每一条）。
- **模板那一档显示但不参与判据**：服务端不校验模板本身。反例是 `{index}`（7 字）渲染成 `"01"`
  （2 字）—— 拿模板拦提交会制造「界面说超了、服务端说没事」。
- 判据与逐条预览共用 `renderItemTitle`，否则会出现「预览 79 字、判据 85 字」。

### 14.3 用例（前端 **248 → 264 passed / 20 文件**，+16；后端未改，未重跑）

| 层 | 用例 | 钉住什么 |
| --- | --- | --- |
| 纯函数（+11） | 模板合规、渲染后超限 —— 必须报出来，并点名是哪一条 | 判据量的是渲染结果（`第 2 条` + `超出 2 个字`） |
| 纯函数 | 只量模板就会给出假的通过 | 反证：同一模板在 `items: []` 下「没问题」，有 items 就有 1 条问题 |
| 纯函数 | 模板自己超长、渲染后合规 —— 不拦 | 模板那一档不参与判据 |
| 纯函数 | 多条超限：先说有几条，再点名超出最多的那条 | 报的是 `over` 最大的那条，不是先遇到的那条 |
| 纯函数 | 单视频量的仍然是标题本身 —— 传了 items 也不参与 | 合集改动不能把单视频那条改坏 |
| 纯函数 | 条目还没读到时不报假警 | 空 `items` 不是「全都合规」的相反面 |
| 纯函数 | `longest` 等长时取先出现的那条 | 界面上「第 X 条」的指向稳定 |
| 纯函数 | 判据与预览用的是同一个渲染结果 | 消除「预览 79 字、判据 85 字」 |
| 纯函数 | `charBudget`：超没超与数字一起算 / `over` 永不为负 | 「-3」会被读成「还剩 3」 |
| 组件（+4） | checks the length of every rendered title, not the length of the template | 弹窗里改模板后**即时**出现「渲染后最长…」+ 按钮禁用；改回短模板放行 |
| 组件 | a template that fits everywhere leaves the rendered line unmarked | 合规时**不带** `is-over`、文案里没有「超出」（否定式断言要有 0 值支撑） |
| 组件 | says how many characters over right next to the title box | 单视频框旁 `90/80 · 超出 10 个字` + `aria-invalid` |
| 组件 | a title within the limit carries no warning at all | 合规时不带 `is-over`，且单视频**没有**「渲染后」那一行 |
| 样式源文（+1） | gives the over-limit character count its own tone | `.char-count.is-over` 必须存在、且只改颜色与字重（位置由 `.field` 的 grid 决定，写在这里会与上一档重复 —— 正是 ui/001 那类病的入口） |

### 14.4 承重测试

把 `titleProblems()` 的合集分支退化成只量模板（`if (true)`），新加的 4 条纯函数用例立刻转红
（`4 failed | 42 passed`），还原后全绿 —— 说明它们测的是**判据**，不是「代码跑得通」。
界面用例同理：`getByText("渲染后最长 …")` 找不到元素就会失败，不存在静默通过的空断言。

### 14.4 真实浏览器：`tmp_acceptance/ui_bili_title_len.py`（**41 条断言，全过**）

合成一个 2 条目 playlist（第 2 条原标题明显更长）+ 合成合集预填，其余请求照旧打真实后端
（单视频那一半用**真实** Ep 105 任务与真实预填）。这一轮非用它不可的理由：
**jsdom 不加载样式表** —— `.char-count.is-over` 若只在 TSX 里加类名而 CSS 漏了这一档，
组件用例照样全绿，用户看到的却是灰字。

```
渲染后最长 159/80（第 2 条，超出 79 个字）   color=rgb(180, 35, 24)  weight=700  可见（宽高 > 0）
问题列表：有 2 条渲染后的标题超限，最严重的是第 2 条「…」（超出 79 个字）
提交按钮：disabled → 模板改成 {title} 后 enabled，那一行回到 rgb(51, 65, 85) 且文案无「超出」
单视频：90/80 · 超出 10 个字 + rgb(180, 35, 24) + aria-invalid=true + 输入框描边染红，输入未被截断
```

两个探针自身的坑（都已修）：

1. **`Fetch.enable` 不能拦 Document**：导航请求本身也是 `Fetch.requestPaused`，
   在 `Page.navigate` 等应答期间挂住它，这条命令永远不返回。收窄成 `resourceType: XHR|Fetch` 即可。
2. **合规档的灰字不是 `.char-count` 里写的 `#657187`**：`.field > span`（0,1,1）压过
   `.char-count`（0,1,0），实测是 `#334155`。超限档（0,2,0）压得赢所以不受影响；
   合规档那一档是**既有**的死声明，已记进 [bug-fix README](../../../bug-fix/README.md) 未处理清单第 26 条，
   没有顺手改（改它会动到所有字段的既有配色）。

### 14.5 未覆盖 / 如实说明

- **「渲染后最长」那一行只量了 1440×1000 一个视口**；窄窗口下会不会换行挤坏布局没有量。
- **没有在真账号上投一个「渲染后超限」的合集**去验证服务端那条 400 —— 代价是一堆不可撤销的稿件。
  本轮只保证判据同源。
- **服务端预填时给的 `prefill.problems` 是旧模板的快照**，与本地预检可能同时出现、措辞不同
  （服务端点名原始标题、前端点名渲染结果）。本轮没有去重 —— 去重会把「服务端认为有问题」这个事实藏起来。

## 15. 重投不重填：预填复用上一次投稿（FR-B37）

### 15.1 为什么这件事值得单独一轮

输入是一句体验反馈：「上传失败后点重新上传，之前填的信息没保存，很沮丧」。
查完之后发现**后端早就有这些数据**：`bili_upload` 每一行都存着标题/简介/标签/分区/版权/来源/
封面开关/置顶评论（理由就写在模型注释里 —— 投稿是异步的，worker 要能在用户关掉页面之后照着重投）。
缺的只是**表单打开时把它读回来**。于是这一轮几乎没有新概念，只有一条新数据（合集模板原文）。

### 15.2 用例

| 层 | 用例 | 钉住什么 |
| --- | --- | --- |
| manager（+7） | `test_prefill_reuses_the_previous_submission_for_a_single_video` | **全部字段**复用：标题/简介/标签/分区/版权/来源/评论/封面，少一个用户就得重填那一格 |
| manager | `test_prefill_falls_back_to_defaults_when_nothing_was_submitted` | 没投过就是默认值，且 `reused=False`（否则界面提示一句假话） |
| manager | `test_prefill_reuse_survives_opening_the_dialog_from_the_other_entry` | 从任务行投过、带条目 id 来问也找得到（两个入口的键不同） |
| manager | `test_prefill_reports_the_reused_values_when_they_break_a_limit` | 复用的值超限时照实报错、不截断（值仍是完整 90 字） |
| manager | `test_collection_prefill_reuses_the_stored_template_and_collection_name` | 模板原文/合集名/简介/评论/封面全回来，逐条预览按**上次那个模板**重算 |
| manager | `test_collection_prefill_without_a_previous_submission_is_all_defaults` | 没投过时模板是默认的、`season_title` 空 |
| manager | `test_collection_prefill_survives_a_corrupt_stored_template` | 模板列读不回来 ⇒ 退回默认模板、`reused=False`、**不抛异常** |
| manager | `test_a_single_video_submission_does_not_wipe_the_stored_template` | 单视频投稿不清空合集那边存的模板（共用一个归集记录） |
| manager | `test_resubmitting_updates_the_stored_template` | 改过的模板覆盖旧的 —— 复用上一次不等于永远复用第一次 |
| api（+2） | `test_prefill_says_it_is_not_reusing_anything_by_default` / `test_prefill_reuses_the_last_submission_over_http` | 走完整条 HTTP 链：中间任何一层吞了字段就这里红 |
| 前端（+4） | 提示出现/不出现（单视频与合集各一）、合集名复用与退回任务名 | 界面真的把「复用了」说出来，且合集名优先用上次那个 |

### 15.3 一个只有真跑一遍才发现的坑

合集分支第一版写的是 `_previous_upload(session, job.id, None)` —— 找「整任务一条」的那一行。
**合集根本没有那样的行**（每条各有一行），于是评论与封面开关永远拿不到，而复用看起来是好的
（模板与合集名都回来了）。单测第一轮就红在 `prefill.comment` 上：
`'本视频由个人工作流同步上传…' == '上次的置顶评论'`。

教训与 [018](../../../bug-fix/018-a-long-file-name-looks-like-a-login-failure.md) 同形：
**「大部分复用了」和「没复用」在用户眼里是同一种沮丧**，所以用例必须逐字段断言，
不能只断言「标题对了」。

### 15.4 真实浏览器：`tmp_acceptance/ui_bili_reuse.py`（**23 条断言，全过**）

合成一个 playlist + 合成预填（`reused: true` 带上次那一份），其余请求照旧打真实后端：

```
合集：提示可见（宽高 > 0）；标题模板 = 上次那个；合集名 = 上次起的那个（不是任务名）；
      置顶评论 = 上次写的；提交按钮可点（复用的值合法，复用不能把一次重投变成一次 400）
对照：reused=False ⇒ 没有那句提示，合集名退回任务名
单视频：标题框里是上次那个（不是按视频标题现算的默认值），提示同样可见
```

探针自身的两个坑（都已修，都 worth 记住）：

1. **夹具必须自己改成合集的口径**。第一版从单视频预填改造时漏了 `default_group_mode`
   （仍是 `none`）与 `job_title`（仍是那条 71 字的 Ep 105 标题）⇒ 「合集名」输入框
   根本不渲染（断言拿到 `null`），且模板渲染后超限让按钮被**正确地**禁用。
   两个 FAIL 都是夹具不真实，不是产品坏 —— 但若当时顺手把断言改松，就会漏掉真问题。
2. 与 §14.4 同一条：`Fetch.enable` 必须收窄 `resourceType: XHR|Fetch`，拦 Document 会让
   `Page.navigate` 永远不返回。

### 15.5 未覆盖 / 如实说明

- **「失败徽标 → 点按钮 → 表单里是上次那一段」这条路径只走到「徽标为未上传」**：
  探针把 `uploads/summary` 拦成空列表，所以按钮走的是 `none` 档。`failed` 档点下去
  走的也是同一个 `handleBiliUpload`（FR-B32），但**没有真的构造一条 failed 记录去点**。
- **归集方式不复用是有意的取舍**，没有验证「用户第二次想改归集方式时会不会困惑」——
  界面上它每次回到默认值，这一点写进了用户手册。
- **合集模板只在本轮新提交之后才有**：升级前投过的合集没有 `template_json`，
  那些任务第一次重投时仍会看到默认模板（已如实降级，不会报错）。

## 16. 上传进度的时间凭空多出 8 小时（020）

### 16.1 输入是一句「肯定是存在问题的」

Ep 125 上传时进度条显示「已提交 7:59:59」，跑的过程中出现过 `8:00:xx`。
库里那条记录的 `elapsed_seconds = 52` —— **同一个事实，两处相差 8 小时**，
这本身就是最强的证据：不是计时逻辑算错，而是**两处对「那串数字代表哪个时刻」的理解不同**。

真因：服务端发的是 naive 的 UTC 墙上时间（SQLite 不存时区），而 `Date.parse`
按 ECMAScript 规定把**不带偏移量**的串当本地时间解析。本机 UTC+8 ⇒ 每个时长 +8 小时。
附带一条：审核期秒表在 `running` 变 false 的那一刻就冻结了，于是出现「8 小时 - 1 秒」。

### 16.2 为什么它躲过了全部用例

`biliProgress.test.ts` 与 `appFixtures.ts` 里的时间戳**全部带 `Z`**，而服务端从来不发带 `Z` 的。
契约里明明写着 `format: date-time`，但 `check_api_contract.py` **只比字段名**。

**判据：夹具里出现的格式必须与服务端实际发出的格式一致，否则「测过」等于「没测」。**

### 16.3 用例

| 层 | 用例 | 钉住什么 |
| --- | --- | --- |
| api（+1） | `test_upload_timestamps_carry_a_timezone` | 串的**形状**（末尾有 `Z`）；`elapsed_seconds` 仍是 52 |
| biliProgress（+4） | naive 起点按 UTC / 审核期同样 / 真实样本 Ep 125 / 带偏移量照旧 | **服务端真正发出来的那种格式** |
| formatting（+7） | `parseServerTime` 四条 + `formatDateTime` 三条 | 无偏移量 ⇒ UTC；有 ⇒ 按偏移量；空/坏 ⇒ `null`（不是 0）；显示按本地时区 |
| bili-upload（+1） | 审核中：时钟继续走 | 首帧 `已提交 00:00`，随后必须前进 |

### 16.4 两次承重，以及被承重拆穿的一条用例

- 把 `uploadElapsedLabel` 退回 `Date.parse` ⇒ **4 条转红**（`4 failed | 15 passed`）。
- 把推进条件退回 `if (!view?.running)` ⇒ 「时钟继续走」**转红**（5.2 s 超时）。
- **但这条用例第一版是错的**：起点放两秒前，首帧已经是 `00:02`，断言「它变了」在冻结时也照样通过。
  是承重测试当场把它拆穿的 —— 改成从 `00:00` 起步才真正钉住。
  教训与 [018](../../../bug-fix/018-a-long-file-name-looks-like-a-login-failure.md) 同形：
  **断言必须落在「修复前会变的东西」上**，否则它只是一次冒烟。

另外，任务中心那条用例原本断言固定的 `"2026-05-15 10:00:00"` —— 在非 UTC 时区上，
它断言的**正是缺陷本身**。已改成在测试里独立算一份本地时刻（不复用被测实现）。

### 16.5 未覆盖 / 如实说明

- **没有在真实浏览器里量过那行字**：接口返回的串与纯函数输出都验了，但真实渲染没跑 CDP 探针。
- **下载侧（`JobItemRead` / `JobRead`）的时间字段同样改成了带 `Z`**，界面「开始/结束」的显示值随之改变；
  这一条只被任务中心那条用例间接覆盖，没有专门的时区用例。

## 17. 传输层断线：用真实失败输出当夹具（021）

### 17.1 夹具就是库里的 `log_tail`

`test_bili_policy.py` 的 `REAL_EP130_DROP` 是 Ep 130 那条失败记录 `log_tail` 的逐字抄本
（含「验证cookie / 无需更新cookie / 通过cookie登录」这些**成功**行）。
它与 018 的 `REAL_21104_TAIL` 是一对，正好覆盖两个方向：

- 018：needle **太宽**，成功日志把真实原因盖掉；
- 021：表里**缺一类**，真实原因一条都命中不了。

### 17.2 用例（后端 +8）

| 层 | 用例 | 钉住什么 |
| --- | --- | --- |
| 分类（+7） | `test_the_transport_drop_that_started_this_is_no_longer_unrecognized` | 真实输出必须落在传输那两条常量之一，不许是兜底；且不许说「登录」「找不到」 |
| 分类 | `test_a_drop_inside_the_cdn_transfer_promises_retry_is_safe` | 断在 CDN 段 ⇒ 结论 `UPLOAD_CDN_DROPPED`，文案里有「不会多出一条重复稿件」 |
| 分类 | `test_a_drop_outside_the_cdn_transfer_does_not_promise_retry_is_safe` | 断在提交接口 ⇒ 换成通用结论，**不许**承诺不投重，且指向创作中心 |
| 分类 | `test_transport_failure_wording_variants_are_all_recognized`（7 例） | 7 种说法（重置 / 管道 / 拒绝 / 对端关闭 / 中文 errno / MITM 插话）都不许沉默 |
| 分类 | `test_every_transport_needle_is_a_failure_statement` | needle 全小写、无首尾空白、长度 ≥ 6 —— 挡「裸词」回归 |
| 分类 | `test_a_cdn_side_http_error_is_not_read_as_a_missing_local_file` | CDN 侧 `404 Not Found` 不许被读成「本地找不到文件」（次序缺陷） |
| 分类 | `test_a_successful_upload_output_is_not_classified_as_a_drop` | 成功日志里没有传输失败语句 |
| 上传（+1） | `test_run_single_reports_a_transport_drop_as_a_drop_not_as_an_unknown` | **接线**：原始输出 → 结论/下一步落到 `UploadResult`；命令失败时不去对账 |

文案硬约束也一并覆盖：`UPLOAD_LINK_DROPPED` / `UPLOAD_CDN_DROPPED` 已登记进 `_advice_pairs_grouped()`，
于是「每条结论都要给下一步」与「不许出现 `timed out` / `reset` / `403` 裸词」两条既有用例
会自动管到这两条新文案。**这一步是必须的**：它们不在 `FAILURE_PATTERNS` 里成对出现
（CDN 那条是细分出来的），不登记就等于两条约束都漏掉。

### 17.3 承重：把 needle 换成 `()` 之后

把 `FAILURE_PATTERNS` 里那条传输项的 needle 临时改成 `()`（等价于回到修复前）：

```
12 failed, 116 passed
```

12 条里包括核心回归、两条承诺措辞用例、7 条语料参数化、CDN 404 次序、以及 `run_single` 那条。
还原后 `test_bili_policy.py + test_bili_upload.py` **128 passed**；后端全量 **1107 passed**（176.7s）。

### 17.4 未覆盖 / 如实说明

- **没有真实重投**（会产出公开稿件，且这次失败多半与网络路径有关）。真实判据仍是
  「同一条记录点重试 → `uploading 0.2 → … → reviewing`」。
- **没有测到 biliup 到 CDN 的可达性**：`curl` 探测走的是本机代理（`remote_ip=127.0.0.1`），
  与 biliup 直连不是同一条路径，不作证据。
- CDN 侧 HTTP 4xx/5xx 两支没有真实样本，只有单测。

## 18. 字幕段数与文案：两条判据各配守护用例（022）

### 18.1 夹具来自真实滚动字幕

`ROLLING_VTT` 是 Ep 130 的 `.en.vtt` 前四块**逐字**抄本（滚屏条 / 10ms 过渡条 / 滚屏条 / 过渡条）。
唯一改动是去掉原文件里那些「只有一个空格」的行 —— 它们本来就被 `strip()` 过滤，结果等价，
但保留会让本文件带上行尾空白、`git diff --check` 会红。

### 18.2 用例（`test_bili_subtitle.py` +14，共 52）

| 用例 | 钉住什么 |
| --- | --- |
| `test_rolling_transition_cues_are_dropped` | 4 段 → 2 段，留下的正是「一句一条」 |
| `test_a_short_cue_with_unique_text_is_kept` | 30ms 但文字独一份 ⇒ 保留（挡「只按时长删」） |
| `test_a_short_line_is_kept_when_the_same_text_only_appears_far_away` | 文字在别处出现、不在**紧邻** ⇒ 保留（挡「只按内容出现过删」） |
| `test_the_transition_threshold_stays_below_a_visible_frame` | 阈值 ∈ [0.01, 0.05]（实测 0.010，上限 3 帧） |
| `test_load_bcc_drops_the_transitions_end_to_end` | **接线**：规则必须挂在 `load_bcc` 这条唯一入口上 |
| `test_language_display_never_leaks_a_platform_code`（8 例） | `en→英文`…；空/未知不猜 |
| `test_the_success_message_says_what_happened_not_how_many_cues` | 文案逐字；不含 `en`、不含「条」；段数仍在 `cue_count` |

### 18.3 四个方向的承重

| 把什么改坏 | 结果 |
| --- | --- |
| `load_bcc` 去掉 `drop_transition_cues(...)` | 1 红（接线） |
| `drop_transition_cues` 直接 `return cues` | 2 红 |
| 判据只留时长 | 2 红（两条守护用例） |
| 阈值放大到 10s | 3 红 |

**这里有一个反例值得记下来**：最初写的「同一句话说两次不算过渡条」用的是两条 **2 秒**的 cue，
阈值放大到 10s 时它照样绿 —— 因为时长判据本就不覆盖 2 秒的 cue。是承重把这条用例拆穿的：
改成「**30 毫秒**且文字只在远处出现」之后，它才真正守住「紧邻」这条判据。

### 18.4 全语料校验（不是推断）

`downloads/` 里 28 个 `.vtt` 跑一遍：20,385 → 10,973；被删的 100% ≤0.02s；
0 文字丢失；0 个文件末条结束时间改变。**量级比单样本本身更有说服力**：删掉的是 46% 的 cue，
而一句台词都没少。

### 18.5 未覆盖

- 没有真实重传字幕（会覆盖线上稿件）；真实判据是重传后平台 `body` 段数 ≈ 句数。
- 「换行处闪一帧」只在数据层确认（10ms 不足一帧，肉眼难判）。
- 字幕仍是「上一行 + 新一行」的滚动窗口形态，没有重排成单行。
