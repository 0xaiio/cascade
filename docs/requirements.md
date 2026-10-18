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
| FR-1 | 支持单视频和 playlist 元数据解析。 | [`POST /api/analyze`](../backend/app/routers/analyze.py#L16)、[`AnalyzeResponse`](../backend/app/schemas.py#L50) |
| FR-2 | 支持选择 playlist 子项并创建下载任务。 | [`selected_entries`](../backend/app/api_support.py#L63)、[`CreateJobRequest`](../backend/app/schemas.py#L82) |
| FR-3 | 支持视频+字幕、仅视频、仅字幕三种模式。 | [`DownloadMode`](../backend/app/schemas.py#L7) |
| FR-4 | 用户只选择清晰度，默认 `1440p`；后端自动选择具体格式。 | [`format_selector`](../backend/app/ytdlp_formats.py#L9) |
| FR-5 | 下载前预检测计划分辨率、格式和视频大小，任务中心展示实际值。 | [`prepare_download`](../backend/app/ytdlp_service.py#L387)、[`_apply_download_preparation`](../backend/app/job_manager.py#L954) |
| FR-6 | 支持明确的分辨率降级原因和重启建议。 | [`fallback_policy.py`](../backend/app/fallback_policy.py#L4)、[`resolution_decisions.py`](../backend/app/resolution_decisions.py#L87) |
| FR-7 | 支持任务暂停、重启、删除、playlist 子视频删除、批量操作，以及本地播放/打开文件夹。 | [`batch_job_action`](../backend/app/routers/jobs.py#L79)、[`system_open.py`](../backend/app/system_open.py) |
| FR-8 | 默认请求人工字幕和自动字幕；缺少某类字幕时 fallback 到另一类可用字幕，并显示来源与格式。 | [`DownloadOptions`](../backend/app/schemas.py#L63)、[`DownloadOptionsPanel`](../frontend/src/components/DownloadOptionsPanel.tsx#L26) |
| FR-9 | 支持 cookies 上传、浏览器导入和清除。 | [`/api/cookies`](../backend/app/routers/cookies.py#L60)、[`BrowserCookieImporter`](../backend/app/browser_cookies.py#L67) |
| FR-10 | 支持并发、限速、重试次数和单视频并发下载数（aria2c）作为运行时设置即时保存；限速/重试变更会让当前视频断点续传重启以应用新参数。 | [`SettingsUpdate`](../backend/app/schemas.py#L199)、[`set_runtime_download_defaults`](../backend/app/job_manager.py#L90) |
| FR-11 | 支持 SSE 事件流和任务轮询。 | [`/api/events`](../backend/app/routers/events.py#L15)、[`EventBroker`](../backend/app/events.py#L7) |
| FR-12 | 支持诊断依赖状态、cookies 状态和稳定性参数。 | [`/api/diagnostics`](../backend/app/routers/diagnostics.py#L29)、[`get_dependency_status`](../backend/app/ytdlp_service.py#L288) |
| FR-13 | 下载停滞可观测：在可配置窗口内没有新增字节时主动判为失败，而不是永久 `running`。 | [`StallGuard`](../backend/app/stall_guard.py#L32)、[`_download_once`](../backend/app/ytdlp_service.py#L582) |
| FR-14 | 媒体流失败时在错误文案中带上当前 cookies 状态，便于区分「未配置 cookies」与网络问题。 | [`_media_stream_failure_message`](../backend/app/job_manager.py#L1041) |
| FR-15 | 代理可作为配置项读写（留空=自动、`direct`=强制直连、其余按 URL），并能**真的发一次请求**验证连通性，返回状态码、耗时、字节数与原始异常。 | [`proxy.py`](../backend/app/proxy.py#L169)、[`POST /api/proxy/test`](../backend/app/routers/diagnostics.py#L65) |
| FR-16 | 已配置的 cookies 可被校验：离线检查格式/域名/鉴权项/过期，可选联网确认登录态，输出「未配置 / 未登录 / 已登录」三档结论与证据。 | [`cookie_health.py`](../backend/app/cookie_health.py#L234)、[`POST /api/cookies/verify`](../backend/app/routers/cookies.py#L24) |
| FR-17 | 依赖与 JS 运行时状态可在不重启应用的前提下重新探测，并把结论回显到界面。 | [`POST /api/diagnostics/runtime`](../backend/app/routers/diagnostics.py#L52)、[`runtime_env.py`](../backend/app/runtime_env.py#L58) |
| FR-18 | 界面上的解释性内容默认收起，挂在其所解释的对象旁边，按需展开；展开时不抢占输入焦点、不阻塞其他操作，钉住状态可记忆。 | [`HelpPopover`](../frontend/src/components/HelpPopover.tsx#L42)、[用户手册：辅助说明的查看方式](user-manual.md#辅助说明的查看方式) |
| FR-19 | 支持把**已下载完成**的视频投稿到 B站：点任务行/条目行上的 B站 上传按钮弹出表单，默认值由后端预填（标题、分区、标签、简介），用户可改后确认。 | [`bili_create_upload`](../backend/app/routers/bilibili.py#L185)、[`BiliUploadDialog`](../frontend/src/components/BiliUploadDialog.tsx#L63) |
| FR-20 | 支持把整个 playlist 任务按**模板**批量投稿：标题/简介可用 `{title}` `{index}` `{count}` `{job}` 占位符，后端为每条视频各渲染一份，逐条串行投稿，全部确认发布后自动归入同一个 B站 合集。 | [`MetadataTemplate`](../backend/app/bili_meta.py#L258)、[`bili_create_collection`](../backend/app/routers/bilibili.py#L244) |
| FR-21 | 提交前校验平台硬限（标题 ≤ 80、简介 ≤ 250、标签总长 < 200 且最多 10 个、单个标签 ≤ 20、评论 ≤ 1000）；**超限只报错不截断**，合集要把问题定位到第几条。 | [`validate`](../backend/app/bili_meta.py#L348)、[`limits`](../backend/app/bili_meta.py#L333) |
| FR-22 | 提供 B站 环境体检（投稿工具是否可用、ffmpeg、登录态、缺项清单），每条结论文案必须自带「下一步」；并支持修复投稿工具、扫码登录、导入已有登录文件三种前置动作。 | [`bili_status`](../backend/app/routers/bilibili.py#L67)、[`BiliLoginSection`](../frontend/src/components/BiliLoginSection.tsx#L29) |
| FR-23 | 投稿命令返回成功**不等于**已发布：必须回读账号稿件列表对账；对不上的记录进入「待核对」而非「已发布」或「失败」，且两者的推荐动作不同。 | [`find_archive`](../backend/app/bili_api.py#L124)、[`UPLOAD_UNVERIFIED`](../backend/app/bili_policy.py#L267) |
| FR-24 | 任务行与条目行各有 B站 上传图标，状态由**后端**按该任务的全部记录聚合出一档徽标（未上传/上传中/已上传/待核对/部分失败/失败），前端不自行推导。 | [`derive_badge`](../backend/app/bili_read_model.py#L60)、[`BiliUploadButton`](../frontend/src/components/BiliUploadButton.tsx#L19) |
| FR-25 | 失败与待核对可单条重试，动作按终态三选一：重新投稿 / **只**重新核对（不重投）/ 什么都不做；合集归集失败可单独重试，且**不会重复建合集**。 | [`BiliRetryRead`](../backend/app/schemas.py#L559)、[`bili_retry_season`](../backend/app/routers/bilibili.py#L311) |
| FR-26 | 投稿可自动生成封面（ffmpeg 抽帧）并自动发送、置顶一条评论；评论发送失败不影响稿件本身的状态。 | [`extract_cover_frame`](../backend/app/bili_bin.py#L288)、[`send_and_top_comment`](../backend/app/bili_api.py#L388) |
| FR-27 | B站 凭据只以文件形式保存，API 与界面**只回传名字与计数**（不返回值、不返回掩码前缀）；登录文件被 git 跟踪时单独作为一条阻断项报出。 | [`cookie_summary`](../backend/app/bili_auth.py#L379)、[`is_cookie_tracked_by_git`](../backend/app/bili_auth.py#L390) |

## 非功能需求

| 类别 | 要求 |
| --- | --- |
| 可维护性 | 下载策略、降级原因、读模型、cookies 导入和格式选择拆分到独立模块；文档工具链可复现并检查本地链接与 UML 产物，详见 [实现文档](implementation.md) 和 [文档写作与生成环境](documentation-workflow.md)。 |
| 稳定性 | 默认稳定性基线见 [PLAN.md](../ai/perf/PLAN.md)：关闭单流节流守卫（`YTDL_THROTTLED_RATE_KBPS=0`），用 90 秒停滞看门狗兜底，保留断点续传、`http_chunk_size=16 MiB` 和同清晰度多 profile 重试；aria2c 多连接默认关闭。支持把并发设为 1 的稳定优先运行方式。 |
| 可观测性 | 任务中心显示进度、速度、视频大小、ETA、实际分辨率、实际格式和错误原因；诊断接口返回依赖状态与稳定性参数；停滞与媒体流失败都有可读原因。失败日志落盘 `data/logs/app.log` 并带 `category=` 与「诊断 / 原因 / 建议」结构，见 [实现文档](implementation.md#日志落盘)。代理与 cookies 各有一次「真的发请求」的自检，避免只报结论不给证据。 |
| 安全性 | 不在 UI 或日志回显 cookies、token、敏感 URL query；日志清洗见 [log_safety.py](../backend/app/log_safety.py#L20)；安全审查结论见 [安全审计报告](safety-review.md)。 |
| 本地化 | 当前 UI 和主要错误信息面向中文用户。 |
| 可测试性 | 后端使用 pytest，前端使用 Vitest/jsdom，默认不依赖真实 YouTube 下载；并发与节流行为有可离线复现的基准脚本。B站 上传的领域逻辑（模板渲染、硬限校验、状态折合、对账匹配）全部做成可注入 `transport` / 命令执行器 / sleeper 的纯函数，因此整链也能离线单测，不需要真实账号。 |

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

## 验收口径

功能变更必须同时满足：

- 对应自动测试通过，命令见 [测试文档](testing.md#自动测试命令)。
- API、配置、下载策略或任务字段变化已同步更新 [API 文档](api.md)、[技术文档](technical.md) 和相关 UML 图。
- 文档变更已运行 `python scripts\docs.py check`（本地链接与 UML 产物一致）与 `python scripts\check_doc_anchors.py`（代码行锚点无漂移）。
- 界面变更已重拍 `docs/assets/screenshots/` 下受影响的截图，且截图不含本机用户名路径等个人信息。
- README 仍保持入口页定位，详细说明不回流到 README。
- 新增的失败路径必须给出可执行的下一步，只报「失败」不给下一步的提示视为未完成。
