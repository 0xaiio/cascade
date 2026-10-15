# 需求分析：视频自动上传 bilibili

适用读者：产品维护者、测试者，以及需要判断这项变更是否符合项目边界的开发者。

## 1. 背景

本仓库现状是一个**本机单用户 YouTube 下载控制台**：解析链接 → 建下载任务 → 队列下载 → 任务中心观察状态。
视频落地之后就没有下文了 —— 用户要把它发到 B站，只能手动打开网页、逐个拖文件、手填标题分区标签。

本功能把「下载完 → 发 B站」这最后一段接上：在任务中心为已下载的视频提供 B站 上传入口，
并把 B站 侧的状态（在传 / 已上传 / 失败）回显到同一处。

## 2. 技术事实（结论的依据，逐条可复核）

这一节刻意区分**事实**（有出处、可复核）与**判断**（我们的选择）。下面的选择全部建立在事实之上，
其中 F4 与 F9 直接决定了本功能的能力边界，务必先读。

| 编号 | 事实 | 出处 / 复核方式 |
| --- | --- | --- |
| F1 | B站 开放平台（`openhome.bilibili.com`）的投稿接口要求**企业资质 + 应用审核 + 实名认证**，官方明确「暂未开通个人开发者的申请入驻」。 | arXiv-daily 项目的 `bili-auto-upload/SKILL.md`；判断：个人账号只能走非官方工具。 |
| F2 | 本机已装 `biliup-rs 0.2.4`（winget `ForgQi.biliup-rs`），`biliup.exe --version` 输出 `biliup-cli 0.2.4`。 | 实测命令。 |
| F3 | `biliup-rs 0.2.4` 的 `upload` 支持 `--submit {client,app,web}`、`--tid/--title/--desc/--tag/--cover/--copyright/--dtime/--limit/--line/--extra-fields`。 | `biliup upload --help` 实测。 |
| F4 | `biliup-rs 0.2.4` **不支持合集（season)归集**：二进制内 `season_id` 字符串 0 命中，`--help` 无任何 season 选项。 | 二进制字符串扫描 + `--help` 实测。 |
| F5 | winget 源只有 `ForgQi.biliup-rs 0.2.4` 与 `ForgQi.biliup-app 0.6.7`；继任仓库 `biliup/biliup` 的 CLI（含 `season add`）**没有 winget 包**。 | `winget search biliup` 实测。 |
| F6 | ~~cookie 文件登录必须走 `--submit web`~~ **这条结论是错的，已由 F6′ 取代。** | 原出处：arXiv `_bili_upload_exec3.log`。 |
| F6′ | `--submit` 在 0.2.4 里**不起作用**：上游 `uploader.rs` 的 `match submit` 两个分支都调 `submit_by_app`（web 分支被注释），且 `biliup.exe` 里没有 `x/vu/web/add` 这个串（只有 `x/vu/app/add`）。 | `python -c "print(b'x/vu/web/add' in open(EXE,'rb').read())"` → `False`；上游源码。 |
| F6″ | **cookie 文件登录的真正门槛是启动时那次令牌校验**：`login_by_cookies` 无条件先调 `oauth2/info?access_key=<token_info.access_token>`；`access_token` 为空串时 B站 返回 `{"code":-400,"message":"请求错误"}`，biliup 随即以 `Err` 退出，**一个投稿请求都不会发出**。所以登录必须一路拿到令牌。 | 源码 `credential.rs::validate_tokens` + 直接探测该端点：空串 → `-400`，非空假值 → `61000 登录状态异常`。完整证据见 [011](../../bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)。 |
| F6‴ | 拿令牌的自动化路径是 **TV 端三步交换**（`passport-tv-login/qrcode/auth_code` → `h5/qrcode/confirm`（带用户自己的 SESSDATA+bili_jct）→ `qrcode/poll`），返回 `token_info{access_token, refresh_token, expires_in=15552000, mid}`。 | 复刻 biliup 的 `credential.rs::login_by_web_cookies`；本机实测换到 32 位令牌，`expires_in=15552000`（180 天）。 |
| F7 | **`upload` 返回码 0 ≠ 已发布**，必须用 `GET https://member.bilibili.com/x/web/archives?pn=1&ps=20` 对账读回。 | arXiv 脚本 `cmd_verify` 与实测日志。 |
| F8 | `code 601 您上传视频过快` 是**账号侧临时频控**，不是脚本 bug；正确处置是等几分钟原样重投，不能改参数硬刷。 | arXiv 实测日志与 SKILL.md 已知坑。 |
| F9 | 创建合集 `POST https://member.bilibili.com/x2/creative/web/season/add`（form-urlencoded：`title/desc/cover/season_price/csrf`）返回 `data` = season_id；**该操作有人工审核**，合集状态会先处于审核中。 | bilibili-API-collect `docs/creativecenter/season.md`。 |
| F10 | 添加稿件到合集 `POST https://member.bilibili.com/x2/creative/web/season/section/episodes/add?csrf=...`，JSON body `{"sectionId": <int>, "episodes": [{"aid","cid","title","charging_pay"}]}`。**参数名是 `episodes` / `sectionId`**，文档表格里的 `episode` / `section_id` 是笔误（社区 issue #1436 证实）。 | bilibili-API-collect 同页 + issue #1436。 |
| F11 | 封面图 URL 由 `https://member.bilibili.com/x/vu/web/cover/up` 返回（形如 `https://archive.biliimg.com/bfs/archive/<hash>.jpg`）。 | biliup 二进制字符串 + arXiv 投递日志。 |
| F12 | 评论发送用 `POST /x/v2/reply/add`、置顶用 `POST /x/v2/reply/top`；`/x/v2/reply/send` 已下线（恒 404）。 | arXiv 脚本 2026-10-06 实测注释。 |
| F13 | 字段硬限制：`title` ≤ 80 字、`desc` ≤ 250 字、`tag` 逗号分隔总长 < 200。 | arXiv SKILL.md 字段表。 |
| F14 | 多 P 投稿在 **Web 端受账号权重限制**（等级 > 3 且粉丝 > 1000 才不限分 P 数），不满足条件时只能靠 client/app 提交接口。 | biliup 官方 README 的 TIPS 段。 |
| F15 | `copyright` 是**必填**字段（`1` 原创/自制、`2` 转载），取值同 `biliup --copyright 1-自制 2-转载`；`biliup upload` 自己的默认值是 **`1`（自制）**。 | B站 开放平台《视频稿件提交》字段表 + `biliup upload --help` 实测。 |
| F16 | `source`（转载来源）是**条件必填**：`copyright = 2` 时必填，缺了会被拒，错误码 **`21004 Missing source for reprint`**（同一批还有 `21003 Invalid copyright value`）。 | B站 开放平台「转载时 source 必填」+ bilibili-API-collect 的错误码表。 |

## 3. 目标与非目标

**目标**

1. 已下载成功的视频，能在任务中心就地发起 B站 投稿，不必离开应用。
2. 单视频：点开就能看到预填好的标题/分区/标签/简介，改完确认即上传。
3. 视频合集：填一份模板，系统按模板为每个视频生成元数据并**逐个投稿**，全部完成后**自动归集到一个 B站 合集**。
4. 上传结果**如实回显**：成功就说成功，没核实就说没核实，失败要给下一句能动手的话。

**非目标（明确不做）**

| 不做 | 原因 |
| --- | --- |
| 多 B站 账号管理与切换 | 本机单用户；多账号会引入凭据隔离问题，收益不抵复杂度。 |
| 视频转码 / 压制 / 二次剪辑 | 与下载控制台职责无关，且会引入新的重量级依赖。 |
| 承诺「已发布」 | 平台侧有转码与人工审核，任何工具都只能承诺「已提交」并给出对账结论。见 F7、F9。 |
| 「上传中途取消」 | `biliup` 上传过程中无安全的阻断点，伪取消会造成「服务端已收部分分片、本地以为没发」的不一致。宁可让它在后台跑完。 |
| 删除 B站 已投稿件 | 对外不可逆动作，误删代价高；需要时请去创作中心手工操作。 |
| 绕过风控 / 命中 601 后硬刷 | 见 F8：硬刷会把临时频控升级为账号风险。正确处置是等待后原样重投。 |
| 定时发布（`--dtime`）的界面开关 | 本功能第一版不需要；字段保留在命令组装层，后续要加不必改契约。 |

## 4. 用户角色

| 角色 | 目标 |
| --- | --- |
| 普通用户 | 把刚下载好的视频或整个 playlist 发到 B站，尽量少手填。 |
| 维护者 | B站 接口或 `biliup` 行为变化时能定位到单一模块并更新文档。 |
| 测试者 | 在不联网、不真投的前提下验证元数据生成、状态机与失败路径。 |

## 5. 用户场景

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| US-1 | 单视频任务下载完成，点该任务的 B站 上传按钮 | 弹出表单，标题/分区/标签/简介已预填，可直接改；确认后开始上传。 |
| US-2 | 上传进行中 | 该任务的 B站 图标显示「上传中」，进度可从任务中心看到。 |
| US-3 | 上传结束并通过对账 | 提示成功，B站 图标变为「已上传」；同一视频再次上传前有明确提示（避免重复投稿）。 |
| US-4 | 单视频上传失败（未登录 / 频控 / 网络） | 图标显示「失败」，给出可读原因与**下一步**（如「先扫码登录」「等 5 分钟原样重投」）。 |
| US-5 | playlist 任务（合集）下载完成，点合集的 B站 上传按钮 | 弹出模板表单（一份模板 + 逐条预览）；确认后系统逐个上传，全部成功后自动创建并归集 B站 合集，合集图标变「已上传」。 |
| US-6 | 合集里某个视频上传失败 | 其余继续；该视频标失败并可单独重试；合集整体标「部分失败」，不谎报成功。 |
| US-7 | 首次使用，未登录 B站 | 界面引导扫码登录或导入已有 cookie 文件；未登录时上传按钮禁用并说明原因。 |

## 6. 功能需求

| 编号 | 需求 | 依据 |
| --- | --- | --- |
| FR-B1 | 单视频任务与其合集任务的条目行，各有一个 B站 图标 + 「上传」按钮；按钮文案不含专业术语。 | US-1、US-5 |
| FR-B2 | 单视频上传前弹出表单，预填标题、分区、标签、简介，另加版权（原视频链接随该条视频预填），全部可编辑后再确认。 | US-1、F13、FR-B19 |
| FR-B3 | 元数据在**生成侧**就按平台硬限校验（标题 80 / 简介 250 / 标签 200），超限给出可读提示而不是把错误留给 B站。 | F13 |
| FR-B4 | 标题与简介按模板从视频标题派生；模板支持占位符（合集序号、单条标题），界面能看到逐条预览。 | US-5 |
| FR-B5 | 可自动从视频抽一帧作为封面并随稿件提交；也可选择不提交（交由 B站 自动生成）。 | F11 |
| FR-B6 | 上传默认串行执行，且相邻两次投稿之间留可配置的间隔，用于规避频控。 | F8、F14 |
| FR-B7 | 投稿完成后**必须对账**（member archives 读回），把「已发布 / 审核中 / 审核不通过 / 未核实」如实回显。 | F7 |
| FR-B8 | 合集上传：全部稿件投稿成功后，自动创建 B站 合集并把稿件（aid + cid + 单集标题）归集进去；创建结果是「已提交审核」而不是「已通过」。 | F9、F10 |
| FR-B9 | 合集归集失败不吞掉：稿件仍是「已上传」，合集层单独标「待归集」并可重试。 | F10 |
| FR-B10 | 支持扫码登录（应用内展示二维码并轮询），登录态落到本机 cookie 文件。 | US-7 |
| FR-B11 | 支持导入已有 cookies.json（兼容 `biliup` LoginInfo、扁平 name→value、Netscape 三种格式）。 | US-7 |
| FR-B12 | cookie 文件不得进入版本控制；任何界面、日志、接口响应都不得回显 `SESSDATA` / `bili_jct` 等凭据原文。 | 安全边界 |
| FR-B13 | 提供环境体检：`biliup` 可执行文件、`ffmpeg`、cookie 文件、登录态，四项结论都要给出可操作的一步。 | US-7 |
| FR-B14 | 上传记录持久化：谁（哪条任务/条目）、什么时候、投了什么、结果如何、BV 号，都要可查。 | US-3、US-4 |
| FR-B15 | 失败的上传可重试，且重试是幂等的（不因为「上次其实成功了」而重复投稿）。 | US-6 |
| FR-B16 | 上传进度经既有 SSE 通道推送，前端不需要额外轮询或新增长连接。 | 可观测性 |
| FR-B17 | 已上传的对象，其 B站 按钮状态变为「已上传」并在重启后保持。 | US-3 |
| FR-B18 | 命中账号频控（601）时，文案必须说明「这是账号侧临时限流，等待后原样重投即可」，不得引导用户改参数硬刷。 | F8 |
| FR-B19 | 投稿表单可选择**自制 / 转载**，**默认转载**，并给出原视频链接。单视频场景下选「转载」必须填来源（留空就地拦住，不让用户点了提交才吃 400）；选「自制」不提交来源。 | F15、F16 |
| FR-B20 | 合集模板**只含版权、不含共用来源**：合集里每条视频的出处各不相同，来源由服务端逐条渲染时各取该条视频的原始链接。 | F15 |

## 7. 非功能需求

| 类别 | 要求 |
| --- | --- |
| 可维护性 | 上传能力落在独立领域模块；所有对外副作用（`biliup` 进程、B站 HTTP）经**可注入接缝**，与既有 `YtDlpService` 的注入方式一致。新增模块必须登记进 [docs/design.md](../../../docs/design.md) 的模块职责矩阵，否则分层校验会红。 |
| 安全性 | cookie 文件路径默认落在 `data/`（已在 `.gitignore` 覆盖范围内），并在体检里校验其**未被 git 跟踪**；日志经既有 `log_safety` 清洗；接口响应只回传凭据的**存在性与摘要**，不回传原文。 |
| 可测试性 | 全部自动测试**离线**：`biliup` 调用与 B站 HTTP 都走 fake，不联网、不真投、不需要账号。真实扫码与真实投稿属于人工验收，见 [04-testing.md](04-testing.md)。 |
| 稳定性 | 上传是长任务（实测 30 MB 约 5 秒上传 + 平台侧提交，大文件可达数分钟），必须跑在工作线程上，不得阻塞事件循环与 SSE；单条上传超时上限可配置。 |
| 可观测性 | 每条上传记录保存结果摘要与日志尾部（脱敏后），失败分类到可执行建议；成功与失败都要落 `data/logs/app.log`。 |
| 本地化 | 界面与提示面向中文用户，且**不使用「稿件」「分 P」「season」等术语**，改用「上传」「视频」「合集」这类直白说法。 |
| 兼容性 | 运行时依赖只新增一个纯 Python 的 `qrcode>=8.0`（仅用其 SVG 工厂，不牵入 Pillow），理由见 [02-design.md §12](02-design.md#12-为什么为二维码破例加了一个依赖)；HTTP 用标准库 `urllib`，不依赖传递进来的 `requests`。 |

## 8. 约束与边界

- 本功能是**本机单用户**能力，不是公网服务；不做鉴权、不做多租户。
- B站 侧「合集」由人工审核（F9）。因此**合集对应的最终状态是「已提交审核」**，本功能不谎报「已发布」。
- 合集归集要求稿件在 B站 侧已就绪才能拿到 `cid` 并被归集（F10）。若平台尚未就绪，归集步骤进入「待归集」并可重试。
- 依赖非公开接口（F9~F12）。B站 变更接口会让这些步骤失效 —— 失效时以 `biliup` 原始报错为准，不猜测。
- 视频必须**已经下载完成且文件存在**才允许上传；仅字幕任务、未完成任务的按钮为禁用态并说明原因。

## 9. 验收口径

功能变更必须同时满足：

- 第 6 节每一条 FR 都有对应的自动测试或明确标注为人工验收项（见 [04-testing.md](04-testing.md)）。
- 后端 `pytest` 全量与前端 `vitest` 全量通过；`npx tsc --noEmit` 无错。
- `python scripts/check_layers.py`（模块已登记、依赖方向正确）、
  `python scripts/check_api_contract.py`（`types.ts` 与 `openapi.yaml` 无漂移）、
  `python scripts/docs.py check`、`python scripts/check_doc_anchors.py`、`git diff --check` 全部通过。
- [docs/requirements.md](../../../docs/requirements.md)、[docs/design.md](../../../docs/design.md)、
  [docs/api.md](../../../docs/api.md)、[docs/openapi.yaml](../../../docs/openapi.yaml)、
  [docs/implementation.md](../../../docs/implementation.md)、[docs/testing.md](../../../docs/testing.md)
  已同步本次新增的端点、配置项、模块与测试命令。
- 新增的失败路径都给出可执行的下一步 —— 只报「失败」不给下一步的提示视为未完成（沿用仓库既有验收口径）。

## 10. 风险与未决项

| 编号 | 风险 | 现状与处置 |
| --- | --- | --- |
| R1 | 合集创建需人工审核，无法自动确认「已通过」。 | 已知，不回填成成功；界面与文档一律表述为「已提交审核」。 |
| R2 | 稿件刚投稿时可能尚未就绪，`episodes/add` 会失败。 | 归集步骤独立于投稿步骤，失败标「待归集」并支持重试。 |
| R3 | 非公开接口随 B站 变更失效。 | 单点收敛在 `bili_api` 模块，失效时改一处；错误原文透传不猜测。 |
| R4 | 账号频控（601）与权重限制（多 P 上限）。 | 串行 + 间隔；本功能按 F10 逐个投稿为**独立稿件**，绕开多 P 权重限制。 |
| R5 | 真实投稿无法在自动测试里验证（需真实账号 + 网络）。 | 明确列为人工验收项，不假装自动测试覆盖了它。 |
| R6 | 沙箱无 TTY，`biliup login` 的交互式登录不可用。 | 本功能改走 HTTP 扫码（自建轮询），从设计上避开 TTY 依赖。 |
