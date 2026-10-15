# 03 · 实现

本文回答「实际落了什么」：文件、端点、表与行为，以及**已知限制**。
需求见 [01-requirements.md](01-requirements.md)，技术选型与分层理由见 [02-design.md](02-design.md)，
测试覆盖见 [04-testing.md](04-testing.md)。

## 1. 落点总览

### 1.1 后端新增（10 个模块）

| 模块 | 层 | 职责 | 不做什么 |
| --- | --- | --- | --- |
| `backend/app/bili_policy.py` | L5 纯工具 | 文案表、状态折合、失败分类 | 不含任何判定逻辑 |
| `backend/app/bili_meta.py` | L5 纯工具 | 模板渲染、标签派生、**版权与来源的收口（`normalize_copyright` / `effective_source`）**、平台硬限校验 | 不碰进程、不碰网络 |
| `backend/app/bili_http.py` | L4 基础设施 | 唯一 HTTP 接缝（`Transport` 协议 + 标准库实现） | 不含业务码判断 |
| `backend/app/bili_auth.py` | L3 领域 | 扫码登录会话、**TV 端三步令牌交换**、cookie 文件读写与摘要 | 不落盘策略（由 manager 决定） |
| `backend/app/bili_api.py` | L3 领域 | 会员态接口：对账、稿件详情、合集、置顶评论 | 不实现上传封面（见 §5.2） |
| `backend/app/bili_bin.py` | L4 基础设施 | `biliup` 探测/命令组装/执行、ffmpeg 抽帧 | 不判断「投稿成功没成功」 |
| `backend/app/bili_upload.py` | L3 领域 | 单次上传的领域逻辑（纯函数式） | **不写库、不发事件、不读全局配置** |
| `backend/app/bili_read_model.py` | L2 编排 | 两张表 → 界面形状；徽标聚合 | 不写库、不改状态 |
| `backend/app/bili_manager.py` | L2 编排 | 队列、串行与间隔、状态落账、事件、重启收敛 | 不直接调 `biliup`，不解析平台结构 |
| `backend/app/routers/bilibili.py` | L0 入口 | 取参 → 调辅助 → 定状态码 | 不含领域规则 |

分层由 `scripts/check_layers.py` 强制（解析 `docs/design.md` 的模块职责矩阵）。

### 1.2 后端改动（既有文件）

| 文件 | 改了什么 |
| --- | --- |
| `config.py` | `biliup_path` / `bili_cookie_filename` / `bili_upload_gap_seconds`（默认 20.0）+ `bili_cookie_path` / `bili_cover_dir` 两个派生路径 |
| `models.py` | `BiliUploadStatus`（8 态）、`BiliSeasonStatus`（4 态）、`BiliUpload` / `BiliSeason` 两张表。**`BiliUpload` 另含 `copyright` / `source` 两列** —— 重投时靠它们重建要提交的元数据，不存就会出现「重试一次，稿件从转载变成了自制」这种没人发现得了的偏差。 |
| `db.py` | `_ensure_columns` 给 `bili_upload` 补 `make_cover` / `comment` 两列 |
| `schemas.py` | 整段 B站 线上模型（含 `BiliPrefillRead.season_desc`；`BiliMetadata` 含 `copyright` / `source`） |
| `log_safety.py` | `_SECRET_NAMES` 扩展 B站 凭据（`sessdata` / `bili_jct` / `dedeuserid` / `buvid3` / `csrf` …） |
| `api_context.py` / `routers/__init__.py` / `main.py` | 装配 `ctx.bili`；lifespan 里 `bili.start()` + `resume_interrupted()` |
| `job_read_model.py` | `_elapsed_seconds` → `elapsed_seconds`（公开名，两个读模型共用一条「未结束时取当前时间」的规则） |
| `ytdlp_service.py` | 暴露 `ffmpeg_executable()`（抽封面复用它，避免「诊断面板说可用、抽封面却失败」） |
| `pyproject.toml` | 新增 `qrcode>=8.0`（本次唯一新增运行时依赖） |

### 1.3 前端

**新增组件 5 个**（`frontend/src/components/`）：

- `BiliIcon.tsx` —— 内联 SVG 图标，`currentColor` 上色，随 tone 变化；
- `BiliUploadButton.tsx` —— 「B站图标 + 上传」按钮，5 档 tone（idle / busy / ok / warn / bad）；
- `BiliUploadDialog.tsx` —— 预填表单弹窗，含逐条预览与本地校验。**含「自制 / 转载」两档单选与来源输入框**：默认转载；来源框只在「转载 + 单视频」时出现（合集的逐条来源由服务端渲染时各填各的，见 [02-design §7.1](02-design.md)）。
- `BiliLoginSection.tsx` —— 设置面板里的登录区（扫码 + 导入 cookies）；
- `BiliJobStatus.tsx` —— 条目级状态行（含「重新核对」「重试上传」入口）。

**改动**：`App.tsx`（3 个 state + 4 个 handler + SSE 回调并列刷新）、`SettingsPanel.tsx`（挂登录区、
新增 `onError`）、`JobQueue.tsx`（传 5 个新 props）、`api.ts`、`types.ts`、`styles.css`（末尾整节
「B站 上传」+ 一个 `@media (max-width: 680px)` 块）。

文案一律直白：按钮是「上传到 B站」，状态是「未上传 / 上传中 / 已上传 / 待核对 / 部分失败 / 失败」，
不出现 `season` / `tid` / `csrf` 这类术语。

## 2. 接口（13 个 operation）

状态码口径与既有 `cookie_import_status_code` 一致：
**409 = 前置条件不满足**，**400 = 请求本身不对**，**404 = 记录不存在**，**502 = 上游取不到数据**。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/bilibili/status` | 环境体检；`probe_account=false` 可做纯离线体检 |
| POST | `/api/bilibili/tools/repair` | 修复 WinGet 下的 0 字节 `biliup.exe` 空壳，再体检 |
| POST | `/api/bilibili/login/qrcode` | 生成登录二维码（返回 **SVG 文本**） |
| GET | `/api/bilibili/login/poll` | 轮询扫码状态；**只有 `confirmed` 才落盘** |
| POST | `/api/bilibili/cookies` | 导入已有登录文件（**不校验格式**，交由体检结论说明） |
| DELETE | `/api/bilibili/cookies` | 清除登录态（幂等） |
| GET | `/api/bilibili/prefill` | 预填：单视频给一组值，合集给「模板 + 逐条预览 + problems」 |
| GET | `/api/bilibili/uploads/summary` | 所有**有记录**的任务的徽标汇总 |
| GET | `/api/bilibili/uploads` | 按任务读回：任务级徽标 + 条目明细 + 合集记录 |
| POST | `/api/bilibili/uploads` | 启动单视频上传，返回**整个任务**的视图 |
| POST | `/api/bilibili/collections` | 启动合集上传（逐条投稿 + 全部结束后自动归集） |
| POST | `/api/bilibili/uploads/{id}/retry` | 重试一条稿件（三种终态三种动作） |
| POST | `/api/bilibili/collections/{id}/group` | 重试归集（**不会重复建合集**） |

契约漂移由 `scripts/check_api_contract.py` 卡住：`types.ts` 按 `SCHEMA_PAIRS` 逐接口比对字段名集合，
`docs/openapi.yaml` 比对「路径 × 方法」集合。

## 3. 数据

两张表，**刻意不合并**：稿件的成败与合集的成败是两个独立生命周期 ——
稿件可以全部成功而归集失败，塞进同一行会让「合集图标状态」与「单条视频状态」互相污染。

- `bili_upload`：一次投稿一行，**行是 upsert 的**（`(job_id, item_id)` 唯一）。
  同时保存「实际提交的元数据快照」（用于对账与复现）与平台返回的标识（`aid` / `bvid` / `cid`）。
- `bili_season`：一次归集一行。`upload_ids_json` **保序**，既是重试的输入也是界面顺序的来源。

稿件状态 8 档，**`unverified`（待核对）是刻意留出的一档**：`biliup` 命令返回 0 只说明命令跑完了，
平台上有没有这条稿件只能靠对账回答，查不到就停在这里，绝不折进 `published`。

## 4. 行为

### 4.1 队列与间隔

- **单 worker、串行上传**，`bili_upload_gap_seconds`（默认 20s）是抗账号级频控（错误码 601）的唯一手段。
- 间隔按「距上次投稿结束」**现算**，所以队列里的第一条不会白等 20 秒。
- 同一任务里还有在途记录时，新的提交返回 409（`in_flight`）—— 两个 worker 抢同一个任务只会互相踩状态。

### 4.2 投稿流程（单条）

「校验前置条件」这一格里最容易漏掉的是**投稿令牌**：`biliup` 的 `login_by_cookies` 在发第一个
投稿请求之前会**无条件**拿 `token_info.access_token` 去 `oauth2/info` 验一次，验不过就以 `Err` 退出，
**一个字节都不会发出去**。所以前置条件里有一条是「cookie 文件里必须有 access_token」，
体检也会把它单列成一档阻塞项（详见 [011](../../bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)）。

```
校验前置条件（含投稿令牌）→ 落一行 queued → 入队
  worker: 置 uploading → （可选）抽封面 → 跑 biliup upload（`--submit` 照传但不起作用，见 01 的 F6′）
    ├─ 命令失败 → 原文按 `biliup 输出` 前缀逐行写进 app.log
    │            → 按输出分类成（结论, 下一步）→ failed（**不查对账**）
    └─ 命令成功 → 对账（标题前缀匹配 + 退避重试）
         ├─ 查不到 → unverified（带「重新核对」的下一步）
         └─ 查到 → 取 aid/bvid/状态 → 若 status ∈ {published, reviewing} 且 aid+cid 齐 → 发并置顶评论
```

失败时把原文写进日志是 011 的修复项之一：原先 `UploadContext.on_log` 定义了、也调了，
但**全仓库没有调用方**，于是失败提示让人去 `app.log` 搜 `BILI` 时，那里根本没有 `biliup` 的原话。

### 4.3 合集流程

逐条投稿（单条失败**不中断**整批），全部结束后自动归集：
`create_season` → `add_episodes`（**一次请求带上全部条目**，逐条调用会被频控，也会在「第 3 条失败」时留下半个合集）。

归集只收纳**同时拿到 aid 与 cid** 的条目 —— 缺 cid 的加进去会被平台拒绝。
已有 `season_id` 时跳过 `create_season` 只补加：否则一次「合集建好了但归集超时」会让重试产出第二个同名合集。

### 4.4 重试的三种动作

| 当前状态 | 动作 | 理由 |
| --- | --- | --- |
| `queued` / `uploading` / `submitted` | `none` | 正在跑，等它结束 |
| `published` / `reviewing` | `none` | 已经成功，**绝不重复投稿** |
| `unverified` | `reverify` | 只重新对账，**不重跑 `biliup`** |
| `failed` / `rejected` | `reupload` | 重新入队 |

把它们合成一条「重试」路径，最轻的后果是同一个视频在 B站 上出现两次。

### 4.5 重启收敛

进程被杀时 `uploading` 的记录**必然**会留下（那一刻没有任何代码能收尾）。
`resume_interrupted()` 把它折成 `failed` + 「这次上传被中断了」+ 「点重试重新上传这一条」，
而**不是**重新入队 —— 我们无法知道那次投稿在平台上到底成没成，擅自重投可能产生重复稿件。

### 4.6 界面状态由后端算

`derive_badge(uploads, season)` 是唯一一处「多条记录 → 一档状态」的聚合，放在后端有两个理由：
任务行与条目行必须显示**同一个**结论；这条规则有真实分支，纯函数才好被参数化测试逐档覆盖。

两条容易写错的分支：
- **只要还有一条在跑，结论就是「上传中」** —— 8 条成功 2 条在跑时说「已上传」会让用户关掉页面；
- **合集归集失败算一次失败**：视频传上去了、合集没建成，界面必须体现出来，
  否则用户会以为一切都好了，直到自己去 B站 发现没有合集。

## 5. 已知限制

### 5.1 前端

- 单视频与合集的弹窗共用同一个表单组件，标题/简介在两种形态下语义不同（一组值 vs 模板），
  靠占位符提示区分；没有做更细的形态化措辞。
- 封面只做「抽第 1 秒那一帧」，没有让用户挑帧或裁剪。

### 5.2 不做上传封面

合集封面复用第一条稿件的**平台封面 URL**（`/x/web-interface/view` 的 `data.pic`）。
收益是少写一个 multipart 实现、也少一个会随平台变更的端点；代价是合集封面不能独立于稿件封面。

### 5.3 平台侧不可控

- **B站 对合集有人工审核**：本功能能承诺的终态是「已提交归集」，不是「合集已通过」。
- 稿件列表有几十秒到几分钟的延迟，所以「待核对」是正常中间态，不是错误。
- 对账按标题前 20 字前缀匹配：两条稿件前 20 字完全相同时无法区分（已是实测中最稳的判据）。

### 5.4 本机相关

- `biliup` 只经 `bili_bin` 访问，且每一级命中都验 PE 头 —— 绕开 WinGet 在未开开发者模式时
  留下的 0 字节空壳（那个空壳在 PATH 里优先级最高，`shutil.which` 找得到、`CreateProcess` 拒绝执行）。
- 抽封面依赖 `ytdlp_service.ffmpeg_executable()`；探不到时**不生成封面**，不影响投稿。

### 5.5 删任务不连带删上传记录

`JobManager.delete()` / `delete_items()` 只删 `JobEvent` / `JobItem` / `Job` 三张表，
**不删 `bili_upload` / `bili_season`**。所以删掉一个已上传过的任务之后：

- `GET /api/bilibili/uploads/summary` 仍会为这个已不存在的 `job_id` 回一条记录
  （`read_all_job_uploads` 的 job 集合就是从这两张表里取并集得来的）；
- 表里的行会无限累积，只是界面上看不见（前端按 `/api/jobs` 里的 job 渲染徽标）。

不修的原因是**正确的修法不止一行删表**：投稿是在另一个 worker 上跑的，删任务时可能正有一条
在途，删完再写回就等于白删。要做到「删干净」得让 `BiliManager` 支持按 job 取消在途上传
（当前只有 `stop()` 整体停 worker，没有按 job 的取消集合）。这属于下载队列的删除语义，
已登记为 `ai/bug-fix` 候选，等排期。

## 7. 归集方式（新建 / 加入已有 / 不归集）

改动落在这些文件（`group_mode` 是贯穿线）：

| 位置 | 做了什么 |
| --- | --- |
| `bili_meta.py` | `GROUP_MODE_*` 常量、`normalize_group_mode`、`normalize_season_option`、`validate_group_choice` |
| `schemas.py` | `BiliSeasonOption` / `BiliSeasonListRead`；`BiliCollectionCreate` 增 `group_mode` / `season_id` |
| `bili_manager.py` | `account_seasons()`；`submit_collection` 收 `group_mode` / `season_id`；`_upsert_season` 收 `pin_season_id` / `skipped` |
| `routers/bilibili.py` | `GET /api/bilibili/seasons`；`POST /collections` 先校验归集决策再校验模板 |
| `models.py` / `bili_policy.py` | 新终态 `BiliSeasonStatus.skipped` 与它的界面文案 |
| `BiliUploadDialog.tsx` | 「归集方式」单选 + 合集名下拉 / 新建输入框 |
| `App.tsx` / `api.ts` / `types.ts` | 开窗前并行读合集列表；提交时按方式决定带不带编号 |

三处值得单独说的实现细节：

**1. `season_id` 预置就是全部的「加入已有」。** `_upsert_season` 收到 `pin_season_id` 时直接写进
`season.season_id`；`_group_work` 本来就读它当 `existing_season_id` 并据此跳过 `create_season`。
所以新逻辑只有一行赋值，「不新建、只补加」由既有机制自动成立。

**2. `pin_season_id` 是 `season_id or season.season_id` 里的第一个。**
既有的「不清空」规则是为了**重试**不重复建合集；新选择必须覆盖，否则「从加入 A 改成加入 B」
会静默继续往 A 里加。用户显式选择 > 上一轮遗留。

**3. `skipped` 的记录照建。** 原先第一版实现是「不归集就什么都不落库」，
被自己的测试判成缺陷：不落库时界面上什么都不显示，用户分不清是「没归集」还是「还没开始归」。
改成**建记录 + 直接落终态**，两个问题同时解决 —— 不会自动归集（因为 `_maybe_group` 只认 `pending`），
也有一句明确的「未归集（投稿时选了不归集）」可显示。

前端侧一处顺带修掉的缺陷：`App.tsx` 原本把合集名硬传 `prefill.job_title`，
所以弹窗里新加的「合集名」输入框**填了也不会生效**。新增的改名用例第一次跑就红在这里。
