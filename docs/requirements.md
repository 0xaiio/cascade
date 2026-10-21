# 需求分析

适用读者：产品维护者、测试者和需要判断变更是否符合项目边界的开发者。

## 目标

本项目提供一个本机单用户 YouTube 下载控制台，使用户能够解析公开视频或 playlist，选择清晰度和字幕选项，将下载任务加入队列，并在任务中心观察状态、失败原因和最终输出信息。下载完成的视频还可以进一步**投稿到 B站**：单视频走一张预填表单，playlist 走模板批量投稿并自动归入一个 B站 合集。B站 投稿是一条独立链路（独立账号、独立凭据文件、独立队列与状态），下载链路不认识它。

系统范围由 [routers/](../backend/app/routers/__init__.py)、[API schema](../backend/app/schemas.py#L14) 和 [前端类型](../frontend/src/types.ts#L1) 共同定义。

## 用户角色

| 角色 | 目标 |
| --- | --- |
| 普通用户 | 在本机下载有权访问的视频或 playlist，处理 cookies、清晰度和字幕选项。 |
| 维护者 | 更新下载策略、API、任务状态和排障文档，保持文档与代码同步。 |
| 测试者 | 验证解析、下载队列、任务中心、cookies 导入、清晰度降级和错误显示。 |

## 功能需求

| 编号 | 需求 | 代码依据 |
| --- | --- | --- |
| FR-1 | 支持单视频和 playlist 元数据解析。 | [`POST /api/analyze`](../backend/app/routers/analyze.py#L16)、[`AnalyzeResponse`](../backend/app/schemas.py#L69) |
| FR-2 | 支持选择 playlist 子项并创建下载任务。 | [`selected_entries`](../backend/app/api_support.py#L63)、[`CreateJobRequest`](../backend/app/schemas.py#L101) |
| FR-3 | 支持视频+字幕、仅视频、仅字幕三种模式。 | [`DownloadMode`](../backend/app/schemas.py#L7) |
| FR-4 | 用户只选择清晰度，默认 `1440p`；后端自动选择具体格式。 | [`format_selector`](../backend/app/ytdlp_formats.py#L9) |
| FR-5 | 下载前预检测计划分辨率、格式和视频大小，任务中心展示实际值。 | [`prepare_download`](../backend/app/ytdlp_service.py#L395)、[`_apply_download_preparation`](../backend/app/job_manager.py#L1032) |
| FR-6 | 支持明确的分辨率降级原因和重启建议。 | [`fallback_policy.py`](../backend/app/fallback_policy.py#L4)、[`resolution_decisions.py`](../backend/app/resolution_decisions.py#L87) |
| FR-7 | 支持任务暂停、重启、删除、playlist 子视频删除、批量操作，以及本地播放/打开文件夹。 | [`batch_job_action`](../backend/app/routers/jobs.py#L80)、[`system_open.py`](../backend/app/system_open.py) |
| FR-8 | 默认请求人工字幕和自动字幕；缺少某类字幕时 fallback 到另一类可用字幕，并显示来源与格式。 | [`DownloadOptions`](../backend/app/schemas.py#L82)、[`DownloadOptionsPanel`](../frontend/src/components/DownloadOptionsPanel.tsx#L26) |
| FR-9 | 支持 cookies 上传、浏览器导入和清除。 | [`/api/cookies`](../backend/app/routers/cookies.py#L60)、[`BrowserCookieImporter`](../backend/app/browser_cookies.py#L67) |
| FR-10 | 支持并发、限速、重试次数和单视频并发下载数（aria2c）作为运行时设置即时保存；限速/重试变更会让当前视频断点续传重启以应用新参数。 | [`SettingsUpdate`](../backend/app/schemas.py#L229)、[`set_runtime_download_defaults`](../backend/app/job_manager.py#L138) |
| FR-11 | 支持 SSE 事件流和任务轮询。 | [`/api/events`](../backend/app/routers/events.py#L15)、[`EventBroker`](../backend/app/events.py#L7) |
| FR-12 | 支持诊断依赖状态、cookies 状态和稳定性参数。 | [`/api/diagnostics`](../backend/app/routers/diagnostics.py#L29)、[`get_dependency_status`](../backend/app/ytdlp_service.py#L296) |
| FR-13 | 下载停滞可观测：在可配置窗口内没有新增字节时主动判为失败，而不是永久 `running`。 | [`StallGuard`](../backend/app/stall_guard.py#L32)、[`_download_once`](../backend/app/ytdlp_service.py#L637) |
| FR-14 | 媒体流失败时在错误文案中带上当前 cookies 状态，便于区分「未配置 cookies」与网络问题。 | [`_media_stream_failure_message`](../backend/app/job_manager.py#L1161) |
| FR-15 | 代理可作为配置项读写（留空=自动、`direct`=强制直连、其余按 URL），并能**真的发一次请求**验证连通性，返回状态码、耗时、字节数与原始异常。 | [`proxy.py`](../backend/app/proxy.py#L169)、[`POST /api/proxy/test`](../backend/app/routers/diagnostics.py#L65) |
| FR-16 | 已配置的 cookies 可被校验：离线检查格式/域名/鉴权项/过期，可选联网确认登录态，输出「未配置 / 未登录 / 已登录」三档结论与证据。 | [`cookie_health.py`](../backend/app/cookie_health.py#L234)、[`POST /api/cookies/verify`](../backend/app/routers/cookies.py#L24) |
| FR-17 | 依赖与 JS 运行时状态可在不重启应用的前提下重新探测，并把结论回显到界面。 | [`POST /api/diagnostics/runtime`](../backend/app/routers/diagnostics.py#L52)、[`runtime_env.py`](../backend/app/runtime_env.py#L58) |
| FR-18 | 界面上的解释性内容默认收起，挂在其所解释的对象旁边，按需展开；展开时不抢占输入焦点、不阻塞其他操作，钉住状态可记忆。 | [`HelpPopover`](../frontend/src/components/HelpPopover.tsx#L42)、[用户手册：辅助说明的查看方式](user-manual.md#辅助说明的查看方式) |
| FR-19 | 支持把**已下载完成**的视频投稿到 B站：点任务行/条目行上的 B站 上传按钮弹出表单，默认值由后端预填（标题、分区、标签、简介），用户可改后确认。 | [`bili_create_upload`](../backend/app/routers/bilibili.py#L185)、[`BiliUploadDialog`](../frontend/src/components/BiliUploadDialog.tsx#L65) |
| FR-20 | 支持把整个 playlist 任务按**模板**批量投稿：标题/简介可用 `{title}` `{index}` `{count}` `{job}` 占位符，后端为每条视频各渲染一份，逐条串行投稿，全部确认发布后自动归入同一个 B站 合集。 | [`MetadataTemplate`](../backend/app/bili_meta.py#L264)、[`bili_create_collection`](../backend/app/routers/bilibili.py#L244) |
| FR-21 | 提交前校验平台硬限（标题 ≤ 80、简介 ≤ 250、标签总长 < 200 且最多 10 个、单个标签 ≤ 20、评论 ≤ 1000）；**超限只报错不截断**，合集要把问题定位到第几条。 | [`validate`](../backend/app/bili_meta.py#L354)、[`limits`](../backend/app/bili_meta.py#L339) |
| FR-22 | 提供 B站 环境体检（投稿工具是否可用、ffmpeg、登录态、缺项清单），每条结论文案必须自带「下一步」；并支持修复投稿工具、扫码登录、导入已有登录文件三种前置动作。 | [`bili_status`](../backend/app/routers/bilibili.py#L67)、[`BiliLoginSection`](../frontend/src/components/BiliLoginSection.tsx#L32) |
| FR-23 | 投稿命令返回成功**不等于**已发布：必须回读账号稿件列表对账；对不上的记录进入「待核对」而非「已发布」或「失败」，且两者的推荐动作不同。 | [`find_archive`](../backend/app/bili_api.py#L127)、[`UPLOAD_UNVERIFIED`](../backend/app/bili_policy.py#L368) |
| FR-24 | 任务行与条目行各有 B站 上传图标，状态由**后端**按该任务的全部记录聚合出一档徽标（未上传/上传中/已上传/待核对/部分失败/失败），前端不自行推导。 | [`derive_badge`](../backend/app/bili_read_model.py#L60)、[`BiliUploadButton`](../frontend/src/components/BiliUploadButton.tsx#L42) |
| FR-25 | 失败与待核对可单条重试，动作按终态三选一：重新投稿 / **只**重新核对（不重投）/ 什么都不做；合集归集失败可单独重试，且**不会重复建合集**。 | [`BiliRetryRead`](../backend/app/schemas.py#L607)、[`bili_retry_season`](../backend/app/routers/bilibili.py#L311) |
| FR-26 | 投稿可自动生成封面（ffmpeg 抽帧）并自动发送、置顶一条评论；评论发送失败不影响稿件本身的状态。 | [`extract_cover_frame`](../backend/app/bili_bin.py#L288)、[`send_and_top_comment`](../backend/app/bili_api.py#L441) |
| FR-27 | B站 凭据只以文件形式保存，API 与界面**只回传名字与计数**（不返回值、不返回掩码前缀）；登录文件被 git 跟踪时单独作为一条阻断项报出。 | [`cookie_summary`](../backend/app/bili_auth.py#L379)、[`is_cookie_tracked_by_git`](../backend/app/bili_auth.py#L390) |
| FR-28 | 字幕只处理**中文与英文**两族，其余语种一律忽略（粤语 `yue` 不算中文）。族判定只有一个入口，界面列出与下载选取共用同一份过滤。 | [`language_family`](../backend/app/subtitle_plan.py#L109)、[`_map_subtitles`](../backend/app/ytdlp_service.py#L1193) |
| FR-29 | 按视频**主语言**决定下载目标：主语言判据是 yt-dlp 给原始语音 ASR 轨加的 `-orig` 后缀（平台信号，不是猜标题/频道），判不出时退回英文这一有界默认。 | [`detect_primary_language`](../backend/app/subtitle_plan.py#L140)、[`resolve_subtitle_plan`](../backend/app/subtitle_plan.py#L200) |
| FR-30 | 同一语言内**人工/官方字幕优先**，缺失才降级到机器生成（ASR）；两者都没有则**跳过字幕、不中断下载**。「跳过」必须请求一个匹配不到任何轨的语言代码实现，不得退化成空列表或 `["all"]`。 | [resolve_subtitle_plan](../backend/app/subtitle_plan.py#L200)、[`_options_with_subtitle_plan`](../backend/app/job_manager.py#L1019) |
| FR-31 | 下载结论要记录**语言 + 来源类型（人工 / 自动生成）+ 文件路径**，供上传环节消费；落库判据是**磁盘上真的有那个文件**，不是下载前的计划。 | [`_record_subtitle_result`](../backend/app/job_manager.py#L1062)、[`find_subtitle_file`](../backend/app/output_paths.py#L162) |
| FR-32 | 视频投稿成功后把已下载的字幕**一并提交为可关闭的 CC 字幕**（字幕轨而非硬字幕）：SRT/VTT 转成 BCC，挂在分P 的 `cid` 上，语言标识用**平台白名单里的值**。 | [`upload_subtitle`](../backend/app/bili_subtitle.py#L277)、[`save_subtitle`](../backend/app/bili_api.py#L366)、[`_attach_subtitle`](../backend/app/bili_upload.py#L345) |
| FR-33 | 无可用字幕时**只跳过字幕上传**，视频投稿流程照常完成；字幕这一步失败**不把已发布的稿件翻成失败**，且提供**只重传字幕**（绝不重投视频）的重试。**「跳过」与「没做成」必须分开**：稿件在审核/转码中时平台还查不到分P编号，这是「有字幕但挂不上」，要记成失败并允许补传 —— 记成跳过等于那条字幕永久丢失（见 [016](../ai/bug-fix/016-subtitle-lost-while-the-video-is-in-review.md)）。 | [`SubtitleOutcome`](../backend/app/bili_subtitle.py#L268)、[`retry_upload`](../backend/app/bili_manager.py#L1019)、[`_attach_subtitle`](../backend/app/bili_upload.py#L345) |
| FR-34 | 投稿表单在**提交前**告诉用户每条视频会带什么字幕（如 `en · 人工`、`无字幕`）；这句文案由服务端拼好，前端不自己拼一遍。 | [`subtitle_summary`](../backend/app/subtitle_plan.py#L92)、[`BiliPrefillItem.subtitle`](../backend/app/schemas.py#L430) |
| FR-35 | 任务中心每个已下到字幕的视频条目都能**用本机的程序打开字幕文件**：按钮位于「打开视频所在文件夹」右侧、图标是字幕（CC）、悬停提示「打开字幕文件；可选择打开方式」。**一颗按钮就是一个动作**：点它直接弹系统的「打开方式」窗口，由用户当场挑程序（不再有右侧的下拉箭头）。**不自己维护编辑器清单、不自己记「上次用的程序」**——那两件事的所有者是操作系统。 | [`SubtitleOpenButton`](../frontend/src/components/SubtitleOpenButton.tsx#L19)、[`open_path_with_chooser`](../backend/app/system_open.py#L61)、[`subtitle_file`](../backend/app/job_artifacts.py#L56) |
| FR-36 | 没有字幕文件的条目，该按钮**必须置灰**并说明原因；后端对这种情况返回 `409`，而不是 204 空转。按钮的可点性只看读模型给的 `subtitle_path`（真的存在才给值）。 | [`_project_subtitle_path`](../backend/app/job_read_model.py#L110)、[`existing_subtitle_file`](../backend/app/output_paths.py#L184) |
| FR-37 | 「打开字幕」不接收前端传入的路径；`mode` 只有 `default` / `chooser` 两个取值，其余一律 `422`。**接口缺省是 `chooser`**（与界面上那颗按钮同一个动作），`default` 只留给接口调用方；界面调用不带 `mode`，免得同一个决定在两处各写一遍。打开失败的原因显示在**对应那一行**，不进全局提示区。 | [`SubtitleOpenMode`](../backend/app/routers/job_files.py#L23)、[`runLocalFileAction`](../frontend/src/components/JobQueue.tsx#L132) |
| FR-38 | 字幕这一步的结论**必须在任务卡片里看得见、失败时点得到**：`posted` / `failed` 渲染一行「CC 字幕」+ 服务端那句文案，`failed` 还给「重传字幕」；`skipped` 不渲染（表单里已写过「无字幕」）。与稿件行**分两行**，互不冒充。 | [`BiliJobStatus`](../frontend/src/components/BiliJobStatus.tsx#L34)、[`isBadSubtitle`](../frontend/src/biliStatus.ts#L83)、[`isNotableUpload`](../frontend/src/biliStatus.ts#L160) |
| FR-39 | 「已上传」那一档的按钮**不再重新投稿**：点击打开该稿件在 B站 的页面，悬停提示「B 站已上传；点击进入 B 站」。任务级打开该任务的第一个稿件，条目级**只**打开这一条（不拿别的顶替）；本地没记下 `bvid` 时退回上传入口，不给一个点下去什么都不发生的绿按钮。该档的可点性**不受本地视频文件是否存在影响**。 | [`uploadedTargetUrl`](../frontend/src/biliStatus.ts#L129)、[`BiliUploadButton`](../frontend/src/components/BiliUploadButton.tsx#L42) |
| FR-40 | 上传入口的其余档位按「这一步到底缺什么」分流：`uploading` **禁用**且提示不承诺任何点击；`unverified`（待核对）点击**只重新核对**（提示明说不会重投）—— 它的投稿命令已经成功过一次；`partial` 点击**只补没成功的那几条**，绝不重投已发布的；只有 `failed` 走投稿表单。批量补投逐条串行、动作由服务端按各自终态决定，回执按实际动作分别计数。 | [`retryableUploadIds`](../frontend/src/biliStatus.ts#L147)、[`retry_upload`](../backend/app/bili_manager.py#L1019) |
| FR-41 | 上传过程中必须能看出「到哪一步了」：任务卡片里给一条**四段轨迹**（排队 / 传输 / 提交 / 核对），当前段有进行中的动效，并显示**已用时间**（按秒推进，起点取服务端的 `started_at`）；合集要写明第几条、已有几条确认在平台上；审核中四段走完并说明**审核没有百分比可读**。**不显示百分比**：投稿工具在后台运行时不输出已传字节数（`parse_progress` 只有三个闸门值），编一个精确的百分比就是谎报。已发布 / 失败 / 待核对不再显示进度块。 | [`jobUploadProgress`](../frontend/src/biliProgress.ts#L104)、[`BiliUploadProgress`](../frontend/src/components/BiliUploadProgress.tsx#L20) |

| FR-42 | 投稿失败必须按**失败语句**分类，不能按成功日志里也会出现的名词分类（`登录` / `cookie` 这类裸词曾在「通过cookie登录」上命中，把标题错误报成登录失效）。平台错误码 `21104`（标题/文件名超过 80 字符）单独成一条；B站 的 App 接口把**视频文件名**（不含扩展名）也按 80 字符校验，超过时用一个同目录**硬链接**换短名上传，原文件不动、也不复制；硬链接不可用时退回原名并如实说明。**传输层失败**（`error sending request` / `connection error` / 对端未发 `close_notify` 就断开这类**没有业务码**的输出）同样要给出结论；只有在能证明「字节还没传完」（失败那一行的 URL 落在上传 CDN 上）时才允许写「重试不会多出一条重复稿件」，否则必须要求用户先去创作中心核对。 | [`classify_failure`](../backend/app/bili_policy.py#L351)、[`_short_name_video`](../backend/app/bili_upload.py#L228) |
| FR-43 | 「重新体检」必须有可观察的反馈：点下去立刻禁用并显示「体检中…」，结束后写入「上次体检 HH:MM:SS」；账号探测 12 秒未返回就退回**离线**体检（工具与登录文件的存在性）并说明原因，不允许停在无响应状态。 | [`runHealthCheck`](../frontend/src/biliHealth.ts#L48)、[`BiliLoginSection`](../frontend/src/components/BiliLoginSection.tsx#L32) |
| FR-44 | 投稿表单要能**在提交前**就看出标题超没超：字段旁给 `已用/上限`，超限时追加「超出 N 个字」并标 `aria-invalid`，输入**不截断**。合集量的是**渲染后的每一条**（不是模板本身 —— `{title}` 这种 7 个字的模板永远「合规」，只量模板等于什么都没查），另给一行「渲染后最长 N/上限（第 X 条，超出 M 个字）」；多条超限时先说有几条、再点名超出最多的那条；条目还没读到时不报假警。上限取自 `prefill.limits`，**服务端 `validate` 仍是唯一权威**。 | [`charBudget`](../frontend/src/biliForm.ts#L129)、[`collectionTitleBudget`](../frontend/src/biliForm.ts#L180)、[`BiliUploadDialog`](../frontend/src/components/BiliUploadDialog.tsx#L65) |
| FR-45 | **重投不重填**：投稿表单打开时，如果这个任务/条目上一次投过，就把上一次提交的**原样**填回来（标题、简介、标签、分区、版权与来源、封面开关、置顶评论；合集外加模板原文与合集名），并明说「已填入上一次投稿的内容」。没有上一次才按视频标题现算默认值。复用的值**照样过校验**（超限只报错、不截断），版权原样带回（上次选了自制，重投不能悄悄变回转载）。归集方式与合集编号**不复用**：它们依赖平台实时状态，拿一个上次的编号去投会撞上「选中的合集已经不在列表里」。 | [`build_prefill`](../backend/app/bili_manager.py#L224)、[`_previous_upload`](../backend/app/bili_manager.py#L329)、[`template_to_json`](../backend/app/bili_meta.py#L407)、[`BiliUploadDialog`](../frontend/src/components/BiliUploadDialog.tsx#L65) |
| FR-49 | **跨接口的时间必须自带偏移量**：所有对外的时间戳是 UTC 且以 `Z` 结尾。少了偏移量时，前端 `Date.parse` 会把同一串数字按**本地**时间解析（ECMAScript 规定），在本机 UTC+8 上让每一个时长**凭空多出 8 小时**（实测一条 52 秒的上传显示成「已提交 8:00:52」）。界面上把时刻显示给用户时按**本地时区**渲染，不把 UTC 墙上时间当本地时间摆出来。上传进度的秒表在**审核期**继续按秒推进（判据是「在等」而不是「在跑」）—— 停住的时钟会被读成「卡住了」。 | [`UtcDateTime`](../backend/app/schemas.py#L30)、[`parseServerTime`](../frontend/src/formatting.ts#L48)、[`BiliUploadProgress`](../frontend/src/components/BiliUploadProgress.tsx#L20) |
| FR-46 | 任务中心为每个视频显示**时长**（`时:分:秒`，不到一小时也保留时位，与「已用/剩余」的 `formatClock` 刻意不同）：建任务时从解析结果写入，任务一进队列即可见；平台没给时长的显示 `未知`；合集任务行按子视频求和、部分未知时前缀「已知」（与「大小」同一套折法）。 | [`duration_from_info_dict`](../backend/app/ytdlp_formats.py)、[`routers/jobs.py`](../backend/app/routers/jobs.py#L57)、[`formatMediaDuration`](../frontend/src/formatting.ts#L15)、[`formatJobDuration`](../frontend/src/components/JobQueue.tsx#L587) |
| FR-47 | **进程退出不丢任务**：启动时先把上次退出残留在 `queued`/`running` 的条目收敛掉，**然后**才允许 worker 消费队列。默认 `resume`（重新排队 + 断点续传，下载幂等可续传，重排队最坏只是多下一遍）；`YTDL_ORPHAN_RECOVERY=fail` 是逃生阀（标失败并写明原因，适合清掉陈年积压）；用户按过的 `paused` 任何策略下都不恢复；有已取消条目的任务视为「正在被取消」。每次恢复写一条 `jobs_recovered` 事件。 | [`recover_orphaned_work`](../backend/app/job_manager.py#L83)、[`orphan_recovery.recover_orphaned_items`](../backend/app/orphan_recovery.py#L77)、[015](../ai/bug-fix/015-orphan-items-never-resume.md) |
| FR-48 | **任务完成提醒**：任务到达终态（`succeeded`/`failed`，`cancelled` 不算）时可提醒一次。方式由全局设置 `completion_notification` 决定（`off`/`sound`/`toast`/`both`，默认 `both`），**要不要**由逐任务 `notify_on_complete` 决定（默认开，否则「在设置里选了方式」是空话）。提醒在浏览器里发生：提示音由 Web Audio 现场合成（无音频文件入库），浮层是右下角 DOM（约 6s 自动缩回、悬停暂停、**标签页在后台时不倒计时**）。触发条件是**状态跃迁**而非「当前状态」，否则一进来就已跑完的历史任务会被反复播报；非法取值一律收敛回 `both`。 | [`notifications.ts`](../frontend/src/notifications.ts)、[`CompletionToasts`](../frontend/src/components/CompletionToasts.tsx#L21)、[`_notify_on_complete`](../backend/app/job_read_model.py#L44)、[014](../ai/bug-fix/014-a-dead-notify-switch.md) |
| FR-50 | 字幕上传的结论只讲**发生了什么 + 观众能做什么**：语言说人话（「英文」而不是平台码 `en`），**不出现转换器的中间量**（段数）。滚动字幕的 10 毫秒过渡条在转换时丢掉（时长 ≤0.05s **且**文字被**紧邻** cue 完整覆盖），否则段数会虚高一倍（Ep 130：239 段 / 122 句）、换行处还会闪一帧；「一句一条」的段数留给日志排障，不进界面（见 [022](../ai/bug-fix/022-a-subtitle-count-nobody-can-read.md)）。 | [`drop_transition_cues`](../backend/app/bili_subtitle.py#L211)、[`language_display`](../backend/app/bili_subtitle.py#L113)、[`upload_subtitle`](../backend/app/bili_subtitle.py#L277) |

## 非功能需求

| 类别 | 要求 |
| --- | --- |
| 可维护性 | 下载策略、降级原因、读模型、cookies 导入和格式选择拆分到独立模块；文档工具链可复现并检查本地链接与 UML 产物，详见 [实现文档](implementation.md) 和 [文档写作与生成环境](documentation-workflow.md)。 |
| 稳定性 | 默认稳定性基线见 [PLAN.md](../ai/perf/PLAN.md)：关闭单流节流守卫（`YTDL_THROTTLED_RATE_KBPS=0`），用 90 秒停滞看门狗兜底，保留断点续传、`http_chunk_size=16 MiB` 和同清晰度多 profile 重试；aria2c 多连接默认关闭。支持把并发设为 1 的稳定优先运行方式。 |
| 可观测性 | 任务中心显示进度、速度、视频大小、视频时长、ETA、实际分辨率、实际格式和错误原因；诊断接口返回依赖状态与稳定性参数；停滞与媒体流失败都有可读原因。失败日志落盘 `data/logs/app.log` 并带 `category=` 与「诊断 / 原因 / 建议」结构，见 [实现文档](implementation.md#日志落盘)。代理与 cookies 各有一次「真的发请求」的自检，避免只报结论不给证据。任务完成提醒（提示音 + 浮层）与启动时的孤儿条目恢复也都留痕（后者写 `jobs_recovered` 事件），前者见 [FR-48](#功能需求)。 |
| 安全性 | 不在 UI 或日志回显 cookies、token、敏感 URL query；日志清洗见 [log_safety.py](../backend/app/log_safety.py#L20)；安全审查结论见 [安全审计报告](safety-review.md)。 |
| 本地化 | 当前 UI 和主要错误信息面向中文用户。 |
| 可测试性 | 后端使用 pytest，前端使用 Vitest/jsdom，默认不依赖真实 YouTube 下载；并发与节流行为有可离线复现的基准脚本。B站 上传的领域逻辑（模板渲染、硬限校验、状态折合、对账匹配）全部做成可注入 `transport` / 命令执行器 / sleeper 的纯函数，因此整链也能离线单测，不需要真实账号。字幕同理：**主语言判定与轨偏好是不联网、不读库、不 import yt-dlp 的纯函数**，SRT/VTT → BCC 的转换与一次字幕上传也都可注入替身，因此「人工优先 / 降级 ASR / 跳过不中断」这三条分工可以全离线验证。 |

## 约束与边界

- 本项目是本机工具，不是公网多用户服务。
- 不绕过 DRM、会员、地区、年龄、私有视频等权限限制。
- 不允许用户传入任意 yt-dlp 参数；API 只暴露项目定义的安全选项。
- 下载稳定性策略不能保证所有 YouTube 媒体流可下载，尤其是账号态、地区、会员或 PO token 强制场景。
- `aria2c` 是显式启用的可选 fallback，不是默认依赖；配置详见 [开发文档](development.md#环境变量)。
- 停滞看门狗的触发依赖 yt-dlp 的 progress 回调；完全阻塞在 socket 读时不会触发，此时仍由 `socket_timeout=30` 兜底。
- B站 投稿要求本机已装投稿工具 `biliup`、且用户已登录（扫码或导入登录文件）—— 两者都不由应用代装/代登，缺任一项都会在体检里作为阻断项报出。
- B站 合集（season）接口是页面接口而非稳定开放 API，且平台对合集有人工审核；应用只能保证「已提交归集」，不能承诺合集已上线。
- 投稿是否成功只能靠「回读最近稿件列表 + 标题前缀匹配」对账，因此**改名后无法对账**，这类记录会停在「待核对」并需要人工确认。
- 账号级上传频控无法在本地消解。单 worker 串行 + 可配置投稿间隔只能降低触发概率，触发后按平台提示等待重试，不做自动硬刷。
- 主语言判定依赖平台**提供** `-orig` 原声轨标记。平台不给时退化为「人工字幕里的中/英」，再退化为英文 —— 这是有界默认，不是精确判定。
- B站 的 CC 字幕只接受**白名单内**的语言标识：实测接受 `zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` / `ko` / `yue` / `fr`，而 **`zh-CN` / `en-US` / `zh-Hans` / `en-GB` 一律被拒**（`code=79011 不合法的语言`）。需求里举的 `zh-CN` / `en-US` 在平台侧是非法值，实现按实测映射而不是按字面。
- 字幕上传是**尽力而为**的附加步骤：它不改变稿件「有没有投上去」的结论，因此也不进任务行的徽标聚合。

## 验收口径

功能变更必须同时满足：

- 对应自动测试通过，命令见 [测试文档](testing.md#自动测试命令)。
- API、配置、下载策略或任务字段变化已同步更新 [API 文档](api.md)、[技术文档](technical.md) 和相关 UML 图。
- 文档变更已运行 `python scripts\docs.py check`（本地链接与 UML 产物一致）与 `python scripts\check_doc_anchors.py`（代码行锚点无漂移）。
- 界面变更已重拍 `docs/assets/screenshots/` 下受影响的截图，且截图不含本机用户名路径等个人信息。
- README 仍保持入口页定位，详细说明不回流到 README。
- 新增的失败路径必须给出可执行的下一步，只报「失败」不给下一步的提示视为未完成。
