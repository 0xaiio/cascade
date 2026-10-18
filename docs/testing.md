# 测试文档

适用读者：需要验证功能、回归下载策略或审查文档同步情况的开发者和测试者。

本文只维护验证命令和验收范围；应用启动方式已统一为仓库根 `init/` 下的一键脚本，用法见 [README 快速启动](../README.md#快速启动)（前端热更新开发模式见 [开发文档](development.md#本地运行)）。

## 自动测试命令

后端语法检查：

```powershell
python -m compileall backend\app
```

后端测试（当前基线：**913 passed**，31 个 `test_*.py`）：

```powershell
python -m pytest backend\tests -q
```

> **在 WorkBuddy 沙箱里请分块跑。** 本仓库没有 `pytest.ini`，所以 pytest 会把临时目录建在系统 Temp、收尾时再清理；
> 沙箱的批量删除守卫是**累积计数**的（一轮命令内累计到阈值就整条拒掉），于是 913 个用例放在一个进程里跑会在中途被拦下。
> 现象很好认：**大量 `at setup of ...` 的 error，而 `FAILED` 一个都没有** —— 那是环境问题，不是代码问题。
>
> 按下面的分块跑，六块相加应恰好等于 913（各块 82～262 项；若某块仍被拦下，继续拆小即可）：
>
> ```powershell
> python -m pytest backend\tests\test_bili_policy.py backend\tests\test_bili_meta.py backend\tests\test_bili_auth.py backend\tests\test_bili_bin.py backend\tests\test_bili_read_model.py -q -p no:cacheprovider --basetemp=tmp_pytest\a   # 262
> python -m pytest backend\tests\test_bili_api.py backend\tests\test_bili_upload.py backend\tests\test_api_bilibili.py -q -p no:cacheprovider --basetemp=tmp_pytest\b                                                            # 213
> python -m pytest backend\tests\test_bili_manager.py -q -p no:cacheprovider --basetemp=tmp_pytest\c                                                                                                                   #  82
> python -m pytest backend\tests\test_ytdlp_service.py -q -p no:cacheprovider --basetemp=tmp_pytest\d                                                                                                                  #  73
> python -m pytest backend\tests\test_api.py backend\tests\test_resolution_decisions.py backend\tests\test_output_paths.py backend\tests\test_safe_delete.py -q -p no:cacheprovider --basetemp=tmp_pytest\e                # 146
> python -m pytest backend\tests\test_dev_server.py backend\tests\test_proxy.py backend\tests\test_cookie_health.py backend\tests\test_stall_guard.py backend\tests\test_error_advice.py backend\tests\test_connectivity.py backend\tests\test_download_progress.py backend\tests\test_runtime_env.py backend\tests\test_job_manager_finally.py backend\tests\test_finally_guards.py backend\tests\test_system_open.py backend\tests\test_logging_setup.py backend\tests\test_db.py backend\tests\test_paths.py backend\tests\test_transfer_stats.py backend\tests\test_progress_persist.py backend\tests\test_log_safety.py -q -p no:cacheprovider --basetemp=tmp_pytest\f   # 137
> ```
>
> 上面的块内数字不用手数：`python -m pytest backend\tests --collect-only -q -p no:cacheprovider` 会逐条打印用例 id，
> 按 `::` 前的文件名归并即可（`--collect-only` 不建临时目录，不受沙箱守卫影响）。
>
> `--basetemp` 指向的目录**必须不存在**，否则 pytest 会先 `rmtree` 它、同样会触发守卫（`tmp_pytest\` 已在 `.gitignore` 里）。

前端测试和构建（当前基线：**172 passed**，15 个文件；`tsc --noEmit` 与 `vite build` 通过）：

```powershell
cd frontend
npm test
npm run build
```

空白检查：

```powershell
git diff --check
```

文档本地链接和 UML 产物一致性检查：

```powershell
python scripts\docs.py check
```

文档里的代码行锚点（Markdown 链接里带 `#Lnn` 的那些）会随代码漂移，可用脚本核对：

```powershell
python scripts\check_doc_anchors.py          # 只报告，有漂移时退出码 1
python scripts\check_doc_anchors.py --fix    # 能唯一确定符号的锚点直接重算
```

它只改「标签就是符号名」的锚点；标签是文件名或散文的（例如 `[main.py](../backend/app/main.py#L127)`）会列进「待人工复核」。

首次执行前如尚未初始化文档工具，请先阅读 [文档写作与生成环境](documentation-workflow.md)。用例数会随功能变化，本文档记录的是当前基线值；改动测试后请同步更新这里。

## 离线基准

下载并发与节流行为有两个可离线复现的基准脚本，不需要真实 YouTube 连通性：

```powershell
python scripts\bench_concurrency.py <临时目录>
python scripts\bench_throttle_guard.py <临时目录> 64   # 节流守卫开启时的中断-重提取循环
python scripts\bench_throttle_guard.py <临时目录> 0    # 关闭后的连续下载
```

判定标准与历史基线见 [PLAN.md](../ai/perf/PLAN.md) 第 6 节。修改下载调度、并发或节流相关参数后，应至少重跑与改动相关的基准，确认未劣化。

## 真实网络验收

`scripts/acceptance_real.py` 启动真实应用（真实 `YtDlpService` + 真实 yt-dlp），对公开播放列表跑多轮并发并输出逐项 CSV。
脚本会自动读取 Windows 系统代理（WinINet），因此**不需要单独配置代理环境变量**。

```powershell
python scripts\acceptance_real.py --items 1-12 --concurrency 1,3,5 --mode subtitles_only --subtitles en
python scripts\acceptance_real.py --mode video_only --items 1-6    # 需要 data/cookies.txt，否则媒体流 403
```

`scripts/acceptance_network.py` 是**网络与账号链路**的专项验收（代理 / cookies / 环境变量），
它对着真实 `YtDlpService` 跑，并把每一步的原始证据写成 JSON：

```powershell
python scripts\acceptance_network.py                          # 报告写到 tmp_acceptance/network-acceptance.json
python scripts\acceptance_network.py --skip-end-to-end        # 只跑连通性矩阵与 cookies 体检
python scripts\acceptance_network.py --skip-env-experiment    # 跳过 NODE_OPTIONS A/B（不想动环境变量时）
python scripts\acceptance_network.py --video <url> --timeout 30
```

它包含四段：启动环境快照、代理探测矩阵（自动 / 强制直连 / 显式 / 指向不存在的端口）、
cookies 体检与 `LOGGED_IN` 探针、端到端元数据提取（带与不带 cookies），
以及一段**环境净化 A/B**：注入 `NODE_OPTIONS=--require=<空补丁>` 后跑一次（预期失败），
再摘除该变量跑一次（预期成功）——这条 A/B 就是 JS challenge 那个缺陷的复现条件。
退出码 0 表示全部通过。

注意事项：

- 无 cookies 时 YouTube 媒体流一律 403，视频本体无法下载；字幕与元数据路径不受影响。
- 不要用 `--subtitles all`：空语言列表会被解释为下载**全部**字幕轨，触发 HTTP 429 并使整个 item 失败。
- 本地基准走 127.0.0.1，脚本已强制设置 `NO_PROXY`，避免被系统代理的 `<-loopback>` 规则拦截。
- 代理探测矩阵**故意包含两个失败用例**（强制直连、指向 `127.0.0.1:1`）：它们失败才说明代理参数真的被应用了。

最近一次执行结果与口径说明见 [PLAN.md](../ai/perf/PLAN.md) 第 9.1 节。

## 后端测试范围

后端测试位于 [backend/tests](../backend/tests/)（31 个 `test_*.py` + [fakes.py](../backend/tests/fakes.py) / [bili_fakes.py](../backend/tests/bili_fakes.py)，当前 **913 passed**）：

| 文件 | 重点 |
| --- | --- |
| [test_api.py](../backend/tests/test_api.py) | API 行为、任务创建、重启、删除、cookies、设置、诊断、代理自检、cookies 体检、运行环境自检刷新，合集子视频按并发并行下载，happy-path 不再二次 extract_metadata，以及**探针抛异常时降级仍要真的执行并开始下载**。 |
| [test_db.py](../backend/tests/test_db.py) | SQLite WAL、`busy_timeout`、`synchronous=NORMAL` 与 WAL checkpoint。 |
| [test_ytdlp_service.py](../backend/tests/test_ytdlp_service.py) | yt-dlp 参数、profile、PO token、aria2c、格式选择和错误识别；**预检把「选不出格式」归一成 `is_selectable=False` 而不是抛出去，且无关异常仍要原样抛出**；JS 运行时探测（跳过坏候选、原样报错、探测本身不抛异常、诊断暴露失败原因）。 |
| [test_download_progress.py](../backend/tests/test_download_progress.py) | 多子流进度聚合：字幕/chunk 不锁死在 99.9%，分离音视频不把已下载字节重置为 0。 |
| [test_progress_persist.py](../backend/tests/test_progress_persist.py) | 进度 SQLite/SSE 写入节流：首次、终态、时间间隔和进度跳变。 |
| [test_transfer_stats.py](../backend/tests/test_transfer_stats.py) | 平均速度计算。 |
| [test_stall_guard.py](../backend/tests/test_stall_guard.py) | 停滞看门狗：字节推进不触发、零增长超时触发、节流振荡（峰值不刷新）触发、合并格式的流切换不被误判。 |
| [test_paths.py](../backend/tests/test_paths.py) | 安全路径名。 |
| [test_log_safety.py](../backend/tests/test_log_safety.py) | 日志敏感信息清洗：`name=value` 与 JSON 的 `"name":"value"` 两种形态都要盖到，且**不误伤**同一行里的正常字段。 |
| [test_logging_setup.py](../backend/tests/test_logging_setup.py) | 日志落盘：文件被创建、格式含时间/级别/模块、重复调用不叠加 handler、只读目录降级不抛异常、uvicorn logger 也被接上。 |
| [test_runtime_env.py](../backend/tests/test_runtime_env.py) | 宿主环境净化：只摘命中强加载开关的 `NODE_OPTIONS`、其它值原样保留、返回值可用于留痕、幂等。 |
| [test_error_advice.py](../backend/tests/test_error_advice.py) | 异常翻译层：JS challenge / cookies / 代理 / 媒体流四类分类顺序、异常链展开不因环状引用死循环、无法归类时返回 `None` 而不硬凑。 |
| [test_connectivity.py](../backend/tests/test_connectivity.py) | 代理探针：直连与走代理分别构造正确的 opener、HTTP 错误与网络异常都变成可展示证据、失败必带 `next_steps`、`direct` 失败文案说明「可能预期」。 |
| [test_cookie_health.py](../backend/tests/test_cookie_health.py) | cookies 体检：格式、域名分布、鉴权项命中/缺失、过期、`LOGGED_IN` 三态（真/假/未知）、结论与下一步。 |
| [test_resolution_decisions.py](../backend/tests/test_resolution_decisions.py) | 降级**判定**的纯单测（31 例，0.12 s）：四种降级原因各自的输入条件、"该不该找降级候选"的两个早退条件、两句失败文案不可互换、"元数据不可得"与"零个格式"必须区分、每个 reason 都能被 `fallback_policy` 翻成非空文案。 |
| [test_output_paths.py](../backend/tests/test_output_paths.py) | 产物位置计算：候选链「记录值优先 + 去重」、成品定位与 sidecar 排除；含一条**与重构前实现逐项对照**的用例（见 [ai/refactor/004](../ai/refactor/004-artifact-paths.md)）。 |
| [test_safe_delete.py](../backend/tests/test_safe_delete.py) | 删除白名单的路径安全判定：根目录与子孙放行，父目录、无关绝对路径、以及**共享字符串前缀的兄弟目录**一律拒绝，Windows 大小写差异视为同一根（见 [ai/refactor/005](../ai/refactor/005-safe-delete.md)）。 |
| [test_proxy.py](../backend/tests/test_proxy.py) | 代理**解析**：`direct` 哨兵、显式 > 系统 > 环境变量的优先级、环境变量只报告不落盘、`no_proxy` 绕过与凭据脱敏。 |
| [test_dev_server.py](../backend/tests/test_dev_server.py) | 端口预检与「谁占了这个端口」：`netstat` / `tasklist` 输出解析、可用端口向后查找、`YTDL_API_PORT` 默认值与 `.env` 锚定仓库根（[009](../ai/bug-fix/009-local-dev-port-is-occupied-and-unconfigurable.md)）。 |
| [test_system_open.py](../backend/tests/test_system_open.py) | 本地打开：Windows 目录开新窗口、文件走默认关联、有可用播放器时优先、无可用播放器时如实报告缺失。 |
| [test_finally_guards.py](../backend/tests/test_finally_guards.py) | **守护测试**：扫源码禁止 `finally` 里出现 `return`；自带「违规能被抓到」「嵌套作用域里的 `return` 不算违规」两条自检。 |
| [test_job_manager_finally.py](../backend/tests/test_job_manager_finally.py) | 行为层回归：条目被删除时收尾不再吞掉逃逸中的异常，崩溃的条目被判 `failed` 而不是永久停在 `running`（[008](../ai/bug-fix/008-return-in-finally-swallows-the-real-error.md)）。 |
| [fakes.py](../backend/tests/fakes.py) | API 测试的 fake service 和辅助对象，其中 `RaisingUnselectableProbeService` 刻意让 `prepare_download` **抛异常**而不是返回 `is_selectable=False` —— 复现真实 yt-dlp 的行为，防止降级分支再次退化成死代码。 |
| [test_bili_policy.py](../backend/tests/test_bili_policy.py) | 平台状态码 → 记录状态 → 徽标的折合（含「命令成功但查不到」必须落到待核对）、错误输出 → （结论 + 下一步）的分类，以及**每一档徽标都有配色与文案**（漏一档会让界面渲染 `undefined`）。 |
| [test_bili_meta.py](../backend/tests/test_bili_meta.py) | 模板渲染**不用 `str.format`**（用户标题里的大括号必须原样保留）、未知占位符不静默吞掉、标签派生与收敛、分区词表、硬限逐项（标题/简介/标签总长/标签个数/单标签/评论/**回显截断长度 `tag_preview`**）与**超限只报错不截断**、**`limits()` 的每个字段都必须与常量一致**（漏一个前端就只能自己写死常量，见 [ai/bug-fix/012](../ai/bug-fix/012-preflight-contradicts-itself-and-the-server.md)）；**版权与来源**（`normalize_copyright` 的回退方向、`effective_source` 只在转载时给来源、`validate` 拦截「转载缺来源」）。 |
| [test_bili_auth.py](../backend/tests/test_bili_auth.py) | 登录文件四形态兼容、**两处「条目列表」解析必须逐字一致**（扁平的 `dict` 与内层 `list` 走同一条 `_jar_from_pairs`）、条目缺 `name`/`value` 要跳过、内层列表为空要回落到扁平那条、**凭据摘要只回名字与计数（不含值、不含前缀）**、是否被 git 跟踪、扫码挑战与轮询状态映射；**投稿令牌交换**（签名字面值冻结 —— 签名错会被 B站 伪装成「凭据坏了」、两组参数风格相反（appkey 轮次签名 / h5 确认走用户的 `csrf`）、`86039` 重试但其它码立刻报出来、`Cookie` 头只带两项不夹带回调 URL 上的查询参数名）、令牌摘要只回布尔与长度。 |
| [test_bili_bin.py](../backend/tests/test_bili_bin.py) | 投稿工具探测：**0 字节空壳与「没装」必须分开**（`is_pe` 校验 PE 头）、三级查找优先级、空壳识别与修复；命令组装含 `--submit`（**注意它在本版并不起作用**，实际提交走 App 接口，见 [011](../ai/bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)）与 **`--copyright` / `--source` 跟随元数据**（自制稿不带 `--source`，落库快照与实发参数因此不会不一致）；进度解析单调不回退；`run_command` 超时会 kill 并写超时标记、回调抛异常不影响上传；抽封面四种降级。 |
| [test_bili_api.py](../backend/tests/test_bili_api.py) | B站 客户端的请求形状与错误边界：member/web referer 与 `Content-Type`、`BiliApiError` 与 `HttpError` 不混用、对账的前缀匹配**两侧都归一空白**与退避次数、建合集（`csrf` 在 form）/ 归集（`csrf` 在 **query**、body 用 **camelCase**）、**建合集回包里取 `id` 的四种平台形态**（`777` / `"777"` / `{"id": 777}` / `{"id": "777"}`）与 10 种坏形状一律报「缺 id」而不是抛 `TypeError`、发并置顶评论的幂等（重复码 `12051` 后换时间序重找、只置顶失败时保住 `rpid`）。 |
| [test_bili_upload.py](../backend/tests/test_bili_upload.py) | 单次上传的领域逻辑：状态折合、封面优先级、**失败时把 biliup 的原始输出按固定前缀逐行进日志、成功时不刷屏**（`caplog`）、**对账查不到时是「待核对」不是「失败」且不重投**、日志尾部清洗凭据、评论（无 `aid` 或**只有 `aid` 没有 `cid`** 都不发、评论失败不污染稿件状态）、模板批量渲染与逐条校验、整批编排（条间等间隔、单条失败不中断、只归集已发布的、全军覆没时不建空合集）。 |
| [test_bili_read_model.py](../backend/tests/test_bili_read_model.py) | 「多条记录 → 一档徽标」的**19 行真值表**参数化：8 成功 2 在跑 → 上传中、归集算在途、归集失败不等于全成功、全部待核对不报成功、被拒算失败；外加「成功 / 失败 / 在途三组集合**恰好归类一次**」的不相交性（唯一允许落在三组之外的是 `unverified`）；连同任务级/条目级投影、孤儿记录排最后、汇总按 job 排序；`season_upload_ids` 对合集 `upload_ids` 列的**七种内容形态加 `null` 列**容错（合法列表、空列表、空串、坏 JSON、**JSON 对象** —— `{"a": 1}` 必须当成空而不是 `["a"]`、非字符串元素 `[1, 2]` 折成字符串、`[null]` 不吞条目）、`latest_season` 取最新一行、`list_seasons` 的定序保证选择稳定。 |
| [test_bili_manager.py](../backend/tests/test_bili_manager.py) | 队列与编排：重投复用同一行并作废旧 `bvid`/`aid`、六种前置条件各自报错（含 **`missing_upload_token`**）、登录落盘时补令牌 / 交换失败仍留住 cookies、导入路径补令牌且**不重复换**、体检把缺令牌报成 blocker、重试三分支（已发布→什么都不做 / 在途→什么都不做 / 待核对→**只重新核对且不发命令** / 失败→重新投稿）、重启收敛（`uploading` 与 `queued` 都折成可重试失败、幂等）、worker 崩溃可读且不带走 worker、体检（含 0 字节空壳与「没装」分开）、投稿间隔。 |
| [test_api_bilibili.py](../backend/tests/test_api_bilibili.py) | 路由层：真 `BiliManager` + `TestClient` + 按数据库状态等完成（**不用队列空作完成信号**：worker 一 `get()` 走队列就空了，那时它还在跑）。覆盖体检 / 扫码（只有 `confirmed` 才落盘、`%2C` 解码）/ 导入清除 / 预填（单视频与合集、404、422）/ 读回 / 单视频与合集投稿（400 超限定位于第几条、409 列出未下载的、404）/ 重试与重试归集。 |
| [bili_fakes.py](../backend/tests/bili_fakes.py) | B站 测试的共享替身：`FakeBiliTransport`（覆盖 13 个端点，含 TV 令牌交换那三个；记录 `calls`/`comments`/`topped`、未知 URL 直接断言失败）、`StubTransport`、脚本化 runner/sleeper、`write_video()`。**`UNSET` 哨兵**是必须的：用 `None` 兼作「没传参数」时，`account=None`（未登录）与 `video=None`（查不到稿件）都会被当成「用默认值」，那两条用例根本测不到目标分支、只会绿着过去。 |

默认自动测试不依赖真实 YouTube 下载，避免网络、地区、cookies 和 YouTube 风控导致不稳定。

## 前端测试范围

前端组件测试是对 `App` 的**集成测试**，按功能面拆成 `frontend/src/*.test.tsx` 多个文件：解析链接与下载选项（`analyzer.test.tsx`，11）、cookies 导入与校验（7）、设置面板与代理检测（10）、任务中心（控制/删除/展开 11、本地文件操作与链接 9、进度与详情呈现 6）、清晰度降级（4）、自检信息（2）、帮助浮层（3）、**B站 上传（`bili-upload.test.tsx`，50 例）**、版面与文案不变式（3），以及只读样式源文的 `styles.test.ts`（3）。当前 **15 个文件 / 172 passed**。

另有三份**不挂载任何组件**的纯函数测试（`frontend/src/*.test.ts`，不经过 `App`）：B站 表单词汇表与本地预检 `biliForm.test.ts`（35 例）、B站 状态谓词 `biliStatus.test.ts`（14 例）、错误取值兜底 `errors.test.ts`（4 例）。它们把原先内联在 `BiliUploadDialog.tsx` / `App.tsx` 里的判断抽成纯函数后再测，所以能在不渲染界面的前提下覆盖边界（例如「列表还在读」与「还没选合集」不能同时报出来，见 [ai/bug-fix/012](../ai/bug-fix/012-preflight-contradicts-itself-and-the-server.md)）。

逐文件用例数可用 `npx vitest run --reporter=json --outputFile=<临时路径>` 复算（JSON 报告的 `testResults[].assertionResults` 长度即该文件用例数）。

跨文件共享的东西只有三处，都在 [frontend/src/test](../frontend/src/test/)：测试台 [appHarness.ts](../frontend/src/test/appHarness.ts)（后端 `fetch` 替身与前后置钩子）、夹具 [appFixtures.ts](../frontend/src/test/appFixtures.ts)、样式源文解析 [cssRules.ts](../frontend/src/test/cssRules.ts)。

每个文件都必须能**单独**跑绿（`npx vitest run --environment jsdom src/<文件名>`）。拆分前 69 个用例同处一个文件，个别断言实际上是靠同文件里先跑过的用例才成立的（详见 [ai/refactor/008](../ai/refactor/008-split-app-tests.md)）；只按整包绿来看是看不出来的。

重点覆盖：

- 链接解析和 playlist 条目选择。
- 下载选项默认值、全局运行时设置保存和提交请求体，包括默认 `1440p`、默认“两者都要”字幕来源、字幕来源 fallback、限速和重试次数。
- cookies 上传、浏览器导入、Edge 锁库提示。
- 任务中心状态、进度、速度、视频大小、实际分辨率、实际格式和显式删除入口展示。
- 任务中心播放已下载单视频和 playlist 子视频，打开视频所在文件夹和 playlist 文件夹，复制任务/子视频源链接，跳转 YouTube 页面；`output_path` 缺失但下载目录存在时，打开文件夹仍应可用；本地文件操作失败或找不到合适播放器时在对应任务行附近显示错误。
- Playlist 子视频单个删除、多选删除和删除文件确认。
- 旧的全局“删除任务时同时删除已下载视频”复选框应不存在。
- 分辨率降级提示和重启按钮。
- 代理自检：检测失败时显示下一步建议、行内给出代理来源与耗时、以及「先试输入框里的地址」**不会**把地址写进设置。
- 代理端口对照：点浮层里的端口会填进输入框（而不是立刻保存）。
- 运行环境自检：JS 运行时不可用时显示原始报错与「The page needs to be reloaded.」的关联说明、日志文件路径可复制、「重新自检」会打到刷新接口。
- cookies 校验：联网校验、离线体检、以及「域名不对，等于没配」与「google.com-only」两档结论的解释文案。
- 辅助说明浮层：四个入口（获取方式 / 结论解读 / 常用端口 / 排查顺序）默认收起，悬停或点击可读，`Esc`、点击空白处、右上角 × 都能关闭，展开状态写入 `localStorage` 并在刷新后恢复。
- 字幕语言标签：选中几种就**逐个列出**几种语言代码（不许改成「已选 N 项」式的 JS 侧截断 —— 标签太长时正确的解法是让它换行，见 [ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)）。
- B站 上传入口：视频没下载好时按钮**禁用并给出原因**（单视频与合集两种措辞）、点开后用该任务的 `job_id`/`item_id` 去预填（合集传 `item_id=null`）、上传中禁用、徽标文案与配色完全照搬服务端给的值（前端不自己判「算不算已上传」）。
- B站 上传表单：字段、**硬限数字**与**回显时的截断长度**都来自 `prefill.limits`（前端不写死常量，否则会出现「界面说没超、提交回来 400」，或「短到能完整显示」那个分支永远走不到 —— 见 [ai/bug-fix/012](../ai/bug-fix/012-preflight-contradicts-itself-and-the-server.md)）；超限**只报错不截断**（输入框里仍是用户输入的完整内容）；提交的请求体就是用户在表单里填的；被拒（409）时表单**不关闭**、错误留在表单里；合集显示逐条预览且改模板即时重算、未就绪的条目按序号点名并挡住提交。
- B站 稿件明细：失败给「重试」、待核对给「**重新核对**」（措辞必须分开，否则用户会去重投一条已经投上去的稿件）、已发布的条目**不给重试按钮**只给「在 B 站查看」、合集归集失败单独给「重试归集」。页顶「跑完了」的提示**只在徽标真的变化且落到终态时**才播报（首次拉到状态与没变化时都不能播报）。
- B站 设置区：三项体检各自的措辞与下一步；**0 字节空壳**要显示成不通过并给出「修复」按钮（`found=true` 不等于能用）；登录文件只报数量、不显示值**也不列名字**；被 git 跟踪单独红字；半截登录文件报出缺哪几项；扫码二维码内联渲染、`confirmed` 之前不刷新体检、`scanned` 继续等待。
- 样式源文不变式（**jsdom 不做布局，这是布局类缺陷唯一能进单测的部分**）：文件上传控件（`.file-button`）的基类不得声明纵向外边距与宽度（[ai/ui/001](../ai/ui/001-cookie-buttons-not-on-the-same-baseline.md)）；右栏（`.side-column`）必须声明 `overflow-wrap: anywhere`，且全表不得出现 `overflow-wrap: normal`（[ai/ui/002](../ai/ui/002-side-column-overflow-breaks-the-page.md)）；语言选择器标签（`.select-trigger span`）必须允许换行，且不得声明 `overflow:` 或 `text-overflow:`（「藏起来」不等于「放得下」，[ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)）。「两个按钮是否共线」「标签是否真的折了行」只能靠真实浏览器量，见 [ai/ui](../ai/ui/README.md)。

## 手动验收

每次修改下载策略、cookies、任务中心或 API 时，建议执行：

1. 用仓库根 `init/` 一键脚本启动单端口应用（`bash init/start.sh` 或 `init\start.ps1`）；若正在开发前端，加 `dev` 参数进入 [开发模式](development.md#前端热更新开发模式)。
2. 解析一个公开单视频，确认可显示标题、封面、清晰度和字幕信息。
3. 创建默认 1440p 下载任务，确认任务中心在下载前或下载开始后很快显示实际分辨率、格式和视频大小；如果源视频没有 1440p，应显示明确降级原因。
4. 下载过程中修改限速或重试次数，确认界面显示“保存中...”“已保存”或失败时的“保存失败”；保存成功后当前视频会短暂重启并继续断点续传，后续任务使用新值。
5. 解析一个小 playlist，选择多个条目，确认单项进度、失败原因和聚合状态。
6. 清除 cookies 后解析需要登录态的视频，确认错误提示可理解；重新导入 cookies 后重试。
7. 对失败任务执行指定清晰度重启，确认请求体和任务中心提示符合 [API 文档](api.md#endpoint)。
8. 下载完成后确认速度仍显示平均值。
9. 对单任务分别点击“仅删除任务”和“删除任务并删除已下载文件”，确认第二个入口会弹出确认框。
10. 展开 playlist 任务，分别验证单个子视频删除和多选子视频删除；删除最后一个子视频时父任务应消失。
11. 在任务中心多选任务，分别验证“批量删除任务”和“批量删除任务和已下载文件”的请求体。
12. 下载完成后点击播放按钮，确认应用选择可解码播放器打开对应文件；点击视频/合集文件夹按钮，确认文件管理器打开对应目录。Windows 下播放器或文件夹窗口应尽量弹出到前台，而不是只在任务栏闪烁；若故意移动文件或卸载可用播放器，错误应显示在对应任务行或子视频行附近，并包含当前格式和建议播放器。对旧任务或运行中任务，可手动清空 `output_path` 后确认后端仍能按文件名中的 YouTube id 发现最终视频或打开任务目录。
13. 点击复制按钮，确认剪贴板内容为对应单视频、playlist 或子视频链接。
14. 点击外链按钮，确认单视频、playlist 和子视频会打开对应 YouTube 页面。
15. 清除 cookies 后在受限视频上触发媒体流失败，确认错误文案以「当前 cookies 状态：未配置」开头，并给出重试建议。
16. 修改设置面板的「单视频并发下载数（1–4）」，确认保存成功且 `/api/diagnostics` 的 `aria2c_connections` 同步变化；确认未设置 `YTDL_ARIA2C_ENABLED=true` 时下载链路不会使用 aria2c。这一项旁的「怎么启用」说明浮层要**默认收起**、点击展开，且展开时不越出视口、内部无横向溢出（探针 `tmp_acceptance/ui_aria2c_help.py`，23 项断言）。
17. 设置面板点「检测代理」：通过时应看到 HTTP 200 与耗时；把端口改成 `1` 再点，应看到「积极拒绝」并给出下一步建议。随后在输入框里填一个地址后点「先试输入框里的地址」，确认**没有**触发 `PUT /api/settings`（试地址不等于保存）。
18. 点代理输入框旁的「常用端口」说明，在浮层里点一行地址，确认它只填进输入框、不立即保存。
19. 把 `cookies_enabled=false`（清除 cookies）后点「校验 cookies」，确认结论是「未配置」，并可从「获取方式」说明看到三种途径；导入 cookies 后再点一次，确认结论与「域上条数 / 命中的鉴权项 / 联网校验」三行证据一致。
20. 辅助说明浮层（页面加载后默认全部收起）：悬停「获取方式」「常用端口」应即看即收；点击后应固定展开，且**光标仍在原来的输入框里**（不抢焦点）。**记忆**：点击展开后直接刷新页面，应保持展开；按 `Esc`（或点空白处、点右上角 ×）关闭后再刷新，应保持收起——关闭会同时清掉这条记忆。
21. 用 DevTools 把视口切到 390px 宽（或设备模拟）：同一个说明入口应变成贴底的**底部抽屉**，可滚动，且抽屉内部**没有横向滚动条**（说明正文必须换行，不能继承触发位置的 `white-space: nowrap`）。
22. 检查 `data/logs/app.log`：启动时应有 `proxy resolved` / `js runtime ready`（或 `js runtime unavailable`）/ `dependencies ready` 三行；故意制造一次失败后，应有 `category=` 结构与紧随其后的 `诊断 / 原因 / 建议N` 块。
23. 设置面板点「重新自检」，确认 JS 运行时状态会被重新探测（装上 Node/Deno 后无需重启应用）。
24. 在桌面宽度（≥1280px）下看解析面板的 cookies 操作行：「选择 cookies」与「清除 cookies」必须**同顶同底**，右侧「自动检测浏览器 / 从浏览器导入」也在同一基线。这一行曾因文件上传控件的基类声明了 `margin-top` 而整体下沉 8px（等宽同高、就是没对齐），见 [ai/ui/001](../ai/ui/001-cookie-buttons-not-on-the-same-baseline.md)。
25. 把窗口宽度依次改成 1280 / 1366 / 1440 / 1520 / 1600，**页面底部不应出现横向滚动条**，右栏（下载选项 / 设置）应完整落在视口内。右栏是固定 390px 的网格轨道，里面任何一个「不能断行的长 token」（自检回显的 Windows 路径最典型）都会把整页撑宽 —— 这一条曾漏到 ≤1600px 时溢出 44px，见 [ai/ui/002](../ai/ui/002-side-column-overflow-breaks-the-page.md)。
26. 解析一个有较多字幕轨道的视频，在「字幕语言」里多勾几种（12 种即可稳定复现）：标签应**就地折成多行**，选择器随之变高，页面**不得**出现横向滚动条，且标签里的语言代码**一个都不能少**（不允许被裁成省略号）。标签是 `已选 N 项：en, zh-Hans, …`、N 无上界，它曾经被 `white-space: nowrap` 锁成一行 → 不是被裁而是把整页顶宽，见 [ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)。

修改 B站 上传时，另需：

27. 设置面板的「B站 上传」区先看一次体检：没装投稿工具时，条目必须显示**为什么不行 + 该怎么做**（要么一条 `winget install …` 命令，要么一个按钮），只报「没找到」不算通过。用 `where.exe biliup` 确认本机的那个 `biliup.exe` **不是 0 字节**（`Get-Item … | Select Length`）；若是 0 字节，界面应显示成**不通过**并给出「修复」按钮（`found=true` 不代表能用）。
28. 点「扫码登录」：二维码应是**内联 SVG** 且大小正常（服务端返回的 SVG 单位是**毫米**，前端必须显式覆盖宽高，否则会跟着宿主字号变形）。用手机扫码后先只扫不确认，状态应停在「已扫码，请在手机上确认」且**不刷新**体检；在手机上确认后才变成已登录并显示账号名。
29. 用「导入 cookies.json」导入一份有效的登录文件：界面应只显示「登录文件里有 N 项凭据（只显示数量，不显示内容）」与账号名 —— **不出现任何凭据值、掩码前缀或名字列表**。故意把该文件纳入 git（`git add -f`）后重新体检，应出现一条红字说明它与 `git rm --cached` 的处理方式。
30. 下载完一个单视频后点「上传到 B站」：表单的标题/分区/标签/简介应已预填，「N/80」这类计数的上限来自服务端。把标题改到超过 80 字，应就地报「标题超出 N 个字」、提交按钮变灰，且**输入框里仍是完整内容**（不截断）。提交后页顶出现「已开始上传《…》，跑完这里会变成「已上传」。」，任务行的按钮变成「上传中」。
31. 等上传跑完再看那条记录：**确认按钮文案与徽标一致**（「已上传」/「待核对」/「上传失败」三档的措辞不同）。「待核对」必须给的是「重新核对」而不是「重试」，并且点它**不会**再向 B站 投一次稿（可在 `data/logs/app.log` 里确认没有第二次 `biliup` 调用）；「已上传」的条目不给任何重试按钮。去 B站 创作中心确认稿件列表里真的有了这条稿件。
32. 用一个 2 条以上、**全部下载完**的 playlist 任务点「上传到 B站」：应打开合集表单（标题写的是「把整个合集上传到 B 站」），带占位符说明与逐条预览表；改模板标题后预览表**当场重算**。故意留一条没下载好，该行应标注「还没下载好」、底部按序号点名、提交按钮变灰。全部就绪后提交：应按模板逐条投稿，全部确认发布后自动归集，最终合集行显示「已提交归集（B站 需人工审核）」。若归集失败，任务卡片上应出现「重试归集」，重试**不会**重复建合集（B站 创作中心的合集列表里只有一条）。

## 高风险回归点

- YouTube 页面或媒体流变化导致 `yt-dlp` 解析或下载参数失效。
- 并发只按任务占用 worker，导致合集子视频无法并行下载。
- 字幕、HTTP chunk 或分离音视频流把运行中进度锁在 99.9%，或把已下载字节重置为 0。
- 媒体流 403/连接重置被错误地自动降清晰度重下。
- 720p 自动降级底线失效。
- 单视频失败原因被任务级聚合错误覆盖。
- `throttledratelimit` 被重新默认打开，导致「每约 5 秒中断并重新 extract」的性能回退，见 [PLAN.md](../ai/perf/PLAN.md) §3.1。
- 停滞看门狗的文案被改动后误命中 [is_media_stream_blocked_error](../backend/app/ytdlp_service.py#L682)，把停滞误分类成媒体流阻塞。
- aria2c 多连接在默认配置下被启用，推高 403 率。
- Cookies 导入暴露敏感信息或擅自关闭浏览器。
- JS 运行时探测退化成「只看文件是否存在」：那样「检测到 node 却解不出 n challenge」会重新变成不可诊断的状态。
- 宿主环境变量净化被扩大化：只应摘掉会强加载外部脚本的 `NODE_OPTIONS`，而不是顺手清掉用户的其它环境变量。
- 日志重新退回「只往 stderr 打」：`data/logs/app.log` 消失会让用户失去唯一的取证手段。
- 「先试输入框里的地址」重新变成「先保存再试」：一个填错的端口会被落盘。
- **把「命令返回码 0」当成「已发布」**：B站 的稿件列表有延迟，不看对账就会把一次未确认的投稿报成成功，用户便永远不会去核对。
- **把「待核对」显示成失败**（或共用一个重试按钮）：用户会去重投一条已经投上去的稿件，平台上出现重复稿件。
- **重试一条 `unverified` 的记录时真的重投**：正确行为是只重读平台列表、一条命令都不发。
- **`biliup` 探测退化成「文件存在即可用」**：WinGet 的 0 字节空壳会被当成装好了，用户按报错字面重装也修不好；空壳与「没装」必须分开报。
- **体检结论文案丢掉「下一步」**：只报「没找到投稿工具」等于把问题丢回给用户，这是本功能的验收口径。
- **B站 凭据被回显**：API 或界面一旦输出凭据的值（哪怕是掩码前缀），它就会出现在截图与 bug 报告里。
- **上传前不校验硬限、或超限时静默截断**：前者跑到一半才失败，后者让用户以为发出去的是完整内容。
- **把合集归集失败当成整批失败**：稿件其实已经发布了，正确做法是让合集记录单独可重试，且重试不重复建合集。
- **投稿间隔被去掉或并发上传**：会触发平台侧的账号级频控，且硬刷会把临时限流升级成账号风险。
- README 与 `docs/` 重复，导致后续维护分叉。

## 文档验收

文档变更也应验证：

- 所有新增 Markdown 链接指向存在文件。
- 每个 SVG 图都由同名 `.puml` 生成，并嵌入至少一份文档。
- README 保持入口页，不重新复制 API、技术策略或排障长文。
- `python scripts\docs.py check` 输出「文档检查通过」，即本地链接有效且 SVG 与 `.puml` 源一致。
- `git diff --check` 无输出。
