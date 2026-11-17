# YouTube 下载器实现计划

## 2026-11-17 20:25 +08:00 - 中英双语字幕（同屏双行、逐条同步、符合 B 站 BCC）计划

> 状态：**已实现**。用户已确认两个未决项：默认英上中下；保留 `en` + `zh` 单语轨，
> 双语作为第三种呈现。提交：步骤 1 `2f15b71` / 步骤 2 `d33b80a` / 步骤 3 `cec696f` / 步骤 4 本次。

### Summary

在已实现的「非中文字幕 → 中文字幕」之上增加**同屏中英双语**：一条 `zh` CC 轨里，
每条 cue 的 `content` 写成「英文原文 + 换行 + 中文译文」，两行共用同一个 `from`/`to`，
因此**天然同步**；上传到 B站 后观众看到的是两行字幕，仍然可以在播放器里关掉。

**为什么不是两条轨**：已有的 `en` + `zh` 两条轨是**可切换**方案 —— 播放器一次只显示一条，
做不到"同屏双语"。要同时看到中英两行，必须把两行放进**同一条 cue 的 content**。
两条轨仍保留给只想看单语的观众；本计划增加的是第三种呈现模式。

成功判据（可量化）：

1. **同步**：每条双语 cue 仍有且只有一个 `from`/`to`；条数 = 源字幕条数；时间逐值相等。
   "两行不同步"在本方案里结构上不可能 —— 它们没有各自的时间戳。
2. **格式**：BCC 的 `font_size` / `font_color` / `background_alpha` / `background_color` /
   `Stroke` / `body[].location=2` / `from`/`to`（**秒**）/ `content` 齐全；
   `lan=zh`（平台白名单，不是 `zh-CN`）、`oid=cid`、`type=1`、`submit="true"`（小写字面量）。
3. **可关闭**：上传的是 CC 字幕轨而不是烧进画面的硬字幕；播放器可关。
4. **可追溯**：原字幕 SHA-256 不变；纯中文轨、双语轨、对照文件各自独立，互不覆盖。

### 1. 界面方案（在现有翻译抽屉上加一层，不另开页面）

#### 1.1 入口与位置

沿用现有两个入口（任务中心字幕按钮旁、B站 投稿预览表字幕列）。打开抽屉后，
在顶部 `源：en · 目标：简体中文（zh）` 那一行右侧增加一个**二选一的小分段控件**：

```
呈现方式： [ 仅中文 ]  [ 中英双语（同屏） ]
```

默认「仅中文」（与当前行为一致，不改变老用户习惯）。选「中英双语」后：
对照区每条卡片从"上灰原文 / 下黑译文"变为**B站 实际渲染形态的预览**：
两行都黑字、同行的时间码、上方小字标注「B站 将显示：第 1 行英文 / 第 2 行中文」。
预览就是 BCC content 的原样呈现，不给用户"看起来和平台不一样"的错觉。

#### 1.2 操作步骤

1. 翻译完成（或打开已完成的翻译任务）→ 切到「中英双语」；
2. 检查逐条预览：英文行非空、中文行非空、时间码与原字幕一致；
3. 点「导出双语 SRT / BCC」得到本地文件（供核对或备用）；
4. 点「上传中英双语到 B站」→ 以 `lan=zh` 覆盖同一条中文 CC 轨（同 lan 再传是覆盖，不是多一条轨）；
5. 在播放器里确认两行同时出现、可关闭；不满意可切回「仅中文」再传一次。

**不新增向导、不新增页面**：这个功能是"同一份译文的另一种排版"，不是新流程。

### 2. 实现方案

#### 2.1 数据：两行进同一条 cue

| 层 | 改动 | 不做什么 |
| --- | --- | --- |
| `translation/render` | 新增 `bilingual_content(source, translation)`：原文在前、译文在后，中间一个 `
`；保留两行各自已断好的行，不再二次包装 | 不改 `from`/`to`、不拆条、不新增轨 |
| `translation/render` | 新增 `render_bilingual_bcc(cues)`：把双语 content 按既有 BCC 结构序列化；**复用 `bili_subtitle.to_bcc` 的常量与形状**，不写第二份 JSON 结构 | 不另造语言映射、不另写端点参数 |
| `translation/service` | 新增 `mode` 概念（`zh` / `bilingual`），决定导出与上传时取 `cue.translation` 还是 `bilingual_content(...)`；模式存在翻译任务上，默认 `zh` | 不把模式塞进 URL 让前端传路径 |
| `api/routers/translation` | 导出接口增加可选 `mode`；上传接口复用现有 `upload-bili`，按任务上的模式构造内容 | 不新增"第三条轨" |
| `frontend/src/translation/` | 分段控件、双行预览、两个按钮 | 不自己拼 BCC、不算时间 |

#### 2.2 同步判据（每一层都要挡）

1. `bilingual_content` 的输入是**同一条** `AlignedCue.source` 与 `.translation` ——
   没有"另一条字幕的时间轴"可供错位；
2. 导出前仍然跑 `validate_alignment(source_cues, aligned)`：条数、序号、时间逐值、译文非空；
3. 双语内容额外校验：恰好两段非空文本（原文段、译文段），中间一个 `
`；
   原文自己带换行时先规整成单行再拼（否则播放器会显示三行以上）；
4. BCC 校验：`from < to`、时间为秒、`location=2`、`content` 非空；
   任一不过就 409，**不写出半份双语文件**。

#### 2.3 B站 格式要求的落点

全部复用 `bili_subtitle` / `bili_api.save_subtitle`，不复制常量：

| 项 | 值 | 来源 |
| --- | --- | --- |
| 端点 | `POST /x/v2/dm/subtitle/draft/save` | `bili_api.SUBTITLE_SAVE_URL` |
| `oid` | **cid**（不是 aid） | `save_subtitle` 现有实现 |
| `lan` | `zh` | 白名单实测（`zh-CN` 会被 79011 拒） |
| `data` | BCC JSON 字符串 | `to_bcc` 的 shape |
| `type` / `sign` / `submit` | `1` / `"false"` / `"true"` | 现有实现，布尔用小写字面量 |
| 行内换行 | BCC `content` 里的 `
` | 现有中文断行已用同一机制 |
| 可关闭 | 只上传 CC 轨，不烧录 | 平台行为 |

**同 lan 覆盖**：切到双语后上传会覆盖现有 `zh` 轨（字幕 id 会换），不是多一条轨。
上传后一段时间 `player/v2` 可能查不到该轨或 `subtitle_url` 为空（缓存/处理中），
判据是"拿到 URL 且 body 稳定"；匿名请求拿不到不能判定失败。

#### 2.4 滚动字幕与长句

- 翻译态仍**不合并** YouTube 自动字幕的 10ms 过渡条（逐条对齐优先）；
- 若上传前为了平台观感要合并，必须**成对合并**：相邻两条 content 完全相同（原文+译文都相同）
  才把前一条的 `to` 延到后一条，条数与时间同步变化；合并只发生在 BCC 导出路径；
- 中文行沿用现有 `wrap_chinese`（16–20 字断行），英文行只做空白规整、不改词序；
- 平台是否有单条 content 长度上限**未实测**：超过阈值（本地观测：单文件最大约 494 KiB /
  4674 条）只警告，不硬拦，失败时如实回传平台错误码。

### 3. 异常处理与验收标准

| 异常 | 用户看到 | 系统行为 |
| --- | --- | --- |
| 某条译文为空 | 「第 N 条没有译文，不能生成双语」 | 409，不导出、不上传 |
| 原文含硬换行 | 自动规整为单行后再拼 | 只改排版，不改词与时间 |
| BCC 校验失败 | 「第 N 条时间/内容不合法」 | 409，不写文件 |
| 模式切换后重传 | 「同一语言轨会被覆盖」 | 明确提示，不让用户以为会多一条轨 |
| 平台拒收 | 平台原始错误码 + 文案 | 不把已发布稿件翻成失败，沿用三档结论 |
| 上传后查不到轨 | 「平台可能还在处理」 | 不据此判失败，等 body 稳定 |

验收：

1. **自动**：新增用例断言双语 content 形如 `英文
中文`、每条只有一个 `from`/`to`、
   条数/时间与源逐值相等、BCC 字段齐全、`lan=zh`、`oid=cid`、`submit="true"`；
   用假 transport 抓真实表单，不只看函数返回值。
2. **变异测试**：把 `bilingual_content` 改成两条 cue（各自一个时间）→ 同步断言必须红；
   把 `
` 去掉 → 双行断言红；把 `lan` 改成 `zh-CN` → 白名单断言红。
3. **真机**：本地模型跑一条真实英文字幕 → 生成双语 SRT/BCC → 检查两行与时间戳；
   上传需在真实 B站 账号人工确认（本计划不自动改线上稿件）。
4. **回归**：后端/前端全量、`tsc --noEmit`、六道文档闸门、契约三处同步。

### 4. 实施顺序（每步独立验收、独立提交）

1. 纯渲染层：`bilingual_content` / `render_bilingual_bcc` + 用例（不碰接口）。
2. 任务模式与导出：`mode` 落库/三处契约同步 + 导出接口 + 前端分段控件与双行预览。
3. 上传路径：复用 `upload-bili` 传双语内容 + 覆盖语义提示 + 平台错误分类用例。
4. 文档同步：`docs/` 全册 + `ai/feature/subtitle-translation/` 增量 + 计划状态回写。

### 5. 未决项（开工前确认）

1. **行序**：默认「英文上 / 中文下」还是「中文上 / 英文下」？计划默认英文上（与原字幕对照顺序一致），可在设置里切换。
2. **是否保留两条单语轨**：计划默认保留（`en` + `zh` 可切换），双语是第三条呈现；若用户只要同屏双语，可在界面上隐藏单语入口，但**不删已上传的轨**。

### 6. 与既有实现的关系

已具备：逐条对齐、原子 JSON、人工修订锁定、只补失败块、`zh` 轨上传与双轨并存。
本计划**只增加"一条 cue 里放两行"的呈现模式**，不重做翻译链路、不新写 BCC 转换器、
不引入新的模型或依赖。

## 2026-11-17 18:08 +08:00 - 非中文字幕转中文字幕（双轨保留、逐条对齐、可上 B 站 CC）计划

> 状态：**仅计划，本轮不写实现代码**。开工前先按下文 §9 确认三个未决项。

### Summary

在已下载的字幕上增加「译为中文」：导入非中文原始字幕（`.srt` / `.vtt`），逐条翻译成中文，产出可上传 B 站的可关闭 CC 中文字幕；**原始字幕一份不改、原文与译文逐条一一对应、时间轴原样保留**，并提供原/中对照供人工核对。

三条成功判据（可量化，验收时逐条取证）：

1. **对齐**：译文条数 = 原文条数，每条 `index` 一一对应，`start` / `end` 与原文**逐字节相同**（不做时间重算、不拆条、不合条）。
2. **保留**：源字幕文件的 SHA-256 前后一致；同时产出纯中文轨与「原上译下」对照轨；删除翻译记录不删原文件。
3. **可用**：中文字幕能以 `lan=zh` 上传为 CC 字幕；与原 `en` 轨**并存**（不同语言是两条轨），观众可自行开关；导出经本地校验零错误。

**为什么 `zh` 不是 `zh-CN`**：这是既有实测结论，不是风格选择。B 站 字幕接口拒绝 `zh-CN` / `en-US` / `zh-Hans` / `en-GB`（`code=79011 不合法的语言`），只认白名单 `zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` / `ko` / `yue` / `fr`。见 `docs/technical.md` 的「CC 字幕上传」与 `ai/bug-fix` 之前记录的实测表。

### 1. 范围与不做项

**做**：已下载到本地的非中文字幕；人工与 ASR 来源都支持；简体中文目标；逐条校对；导出 SRT / VTT / 对照 SRT；传到 B 站 作为独立 CC 轨；断点续跑与失败重试。

**不做**（首期）：

- 不翻译视频音频、不生成硬字幕/烧录画面（那会直接违背「观众可关」）；
- 不改原始字幕文件，不把译文写回 `.en.vtt`；
- 不做整站批量翻译（一次一条字幕；批量是二期）；
- 不做繁体目标、不做双向互译、不做语音识别；
- 不自动在投稿时偷偷上传译文 —— 上传必须是一次明确的用户动作；
- 不把 API Key 写进数据库或前端契约（见 §5.6）。

### 2. 界面方案（简洁、清爽、明晰）

设计原则：**不新增页面、不做向导、不加图标堆**。复用现有 `.modal-backdrop` / `.modal` 弹层体系（`frontend/src/styles.css` 已有），做成**一个抽屉式弹窗**：打开即看见原文与译文，三步内完成「翻译 → 校对 → 导出/上传」。

#### 2.1 入口（主入口一处，副入口一处）

| 位置 | 呈现 | 出现条件 |
| --- | --- | --- |
| **主入口**：任务中心条目的字幕信息处（`JobQueue` 里显示 `en · 人工` 的那一行） | 文字按钮「译为中文」，紧跟字幕信息，**不是又一个图标按钮** | 有 `subtitle_path` 且 `subtitle_language` 判定为非中文族；已有翻译时变成「查看译文」并显示进度徽标 |
| **副入口**：B 站 投稿预览表「字幕」列（`BiliUploadDialog`） | 同一文字入口；若该条已存在中文译文，列里显示 `en · 人工 → zh · 已译` | 同上；让用户在投稿前就发现可带中文字幕 |

**刻意不做**的入口：工具栏总按钮、右键菜单、键盘快捷键。这个功能是「对某一条字幕做事」，入口必须贴在那一条上；放远了会让用户以为要对整个任务操作。

#### 2.2 主要界面：翻译抽屉

```
┌ 字幕翻译 · <视频标题>                                      ✕ ┐
│ 源：English · 人工字幕 · 328 条 · 18:42    目标：简体中文（zh，固定）│
│ 服务：OpenAI 兼容 · 已配置 ●                    [连接测试]        │
├───────────────────────────────────────────────────────────┤
│ ▓▓▓▓▓▓▓▓▓░░░░░  186 / 328 条 · 正在译第 187 条（分块 6/11）      │
│ [暂停] [仅看失败 3] [仅看已改 2] [搜索…]                        │
├───────────────────────────────────────────────────────────┤
│ 00:01:02,400 → 00:01:04,800                            #187   │
│   We begin with the fundamentals of query execution.           │
│   我们从查询执行的基础知识开始。                          ✎ 已改  │
│ ─────────────────────────────────────────────────────────── │
│ 00:01:04,800 → 00:01:06,100                            #188   │
│   Next, the optimizer rewrites the plan.                       │
│    ⚠ 翻译失败：请求超时 · [重试这一条]                          │
├───────────────────────────────────────────────────────────┤
│ [取消]        [导出中文 SRT] [导出对照 SRT] [上传到 B 站（zh）] │
└───────────────────────────────────────────────────────────┘
```

- 宽度约 `860px`、`max-height: 80vh`，表格区独立滚动；沿用既有按钮类（`primary-button` / `ghost-button`）与配色，**不引入新色板**；
- 顶部只留一行元信息 + 一行服务状态；设置详情折叠在「连接测试」旁的小齿轮里（跳到设置页的翻译分区），不在抽屉里堆字段；
- 源语言只读（后端给），目标语言固定简体中文，不给用户一个误以为能选的目标下拉。

#### 2.3 操作步骤

1. 在任务中心点「译为中文」→ 抽屉打开，显示源字幕元信息与已存译文（若有）；
2. 首次使用且服务未配置 → 顶部显示一句「还没配置翻译服务」+ 指向设置页的链接；配置后回来点「开始翻译」；
3. 翻译过程中**逐条实时填入**，可随时滚动、暂停、或关闭抽屉（后台继续，重开接着看）；
4. 校对：直接点译文编辑；人工改过的条目标「已改」，重跑/重试**永不覆盖**；
5. 导出中文 SRT（可传 B 站）/ 对照 SRT（原上译下，供人工核对）/ 上传 B 站 CC 轨；关闭时若有未导出的改动，给一次确认。

#### 2.4 进度与结果预览

- 进度用**条数**而不是百分比小数：`已译 186 / 328`，并显示当前分块序号 `6/11`；长字幕按分块推进，避免「卡了 3 分钟但看起来没动」；
- 三种状态各自可见：待译（灰）/ 翻译中（转圈，仅当前块）/ 失败（红，带原因与单条重试）/ 已改（黄点）；
- 失败不阻断整体：某一分块失败只标该块，其余继续；顶部出现「重试全部失败项」；
- 通过 SSE 推进度事件（`subtitle_translation_progress`），前端收到后**只重拉当前翻译详情**，不整页 `listJobs()`（与现有 `App.tsx` 的 SSE 处理区分开，避免每条事件都全量刷新）；
- 结果预览不需要「先全部完成再看」：每条译文一落盘就能看到，这是校对体验的关键。

#### 2.5 原字幕与中文字幕对照

- 每条一张卡片：**上灰下黑**两行（原文只读、译文可编辑），右侧灰字显示时间码与序号；
- 原文不可编辑（它不是产物，是证据）；译文编辑后立刻落盘，并标「已改」；
- 筛选器：`全部` / `仅看失败` / `仅看已改` / 文本搜索；不提供花哨的并排布局 —— 窄屏下并排会把时间码挤没；
- 对照导出为 `.bilingual.srt`（序号沿用原序号，原文一行、译文一行），**只给用户看，不上传 B 站**。

### 3. 总体架构与文件落点

| 落点（⚠ 这一节是**待创建**的模块，故写目录 + 模块名而不是完整路径；`check_doc_paths.py` 只校验已存在的文件） | 职责 | 硬约束 |
| --- | --- | --- |
| 新包 `backend/app/translation/` · 模块 `provider` | 翻译服务抽象：`TranslationProvider` Protocol + OpenAI 兼容实现 + 测试用的假实现 | 可注入 `transport`，**离线可测**；不发请求的测试不允许真联网 |
| `backend/app/translation/` · 模块 `align` | 分块、序号回填、逐条对齐校验、中文断行 | 只处理文本与序号，**绝不改时间轴** |
| `backend/app/translation/` · 模块 `service` | 编排：解析 → 分块 → 调用 → 校验 → 落盘 → 进度/取消/重试 | 不写 HTTP 状态码、不拼路径、不直接操作 DB session |
| `backend/app/translation/` · 模块 `store` | 翻译记录（DB）与逐条译文（JSON 文件）的读写、原子替换 | 一处真相：逐条数据在 JSON，DB 只存索引与进度 |
| `backend/app/api/routers/` · 新路由 `translations` | 路由：开始 / 查详情 / 编辑单条 / 重试 / 取消 / 导出 / 删除 | 只做「取参 → 调服务 → 定状态码」；挂进 `API_ROUTERS`，**不改 `main.py` 的路由注册**（既有约定） |
| `backend/app/api/api_context.py` / `backend/app/main.py` | 注入 `translation_service`（含 provider） | 沿用 `open_with_chooser` 的注入纪律：测试替换 provider 即声明「这次不联网」 |
| `backend/app/contracts/models.py` | 新表 `SubtitleTranslation` | 明细不进 DB，避免一处真相两处写 |
| `backend/app/contracts/schemas.py` | `SubtitleTranslationRead` / `SubtitleCueRead` / 请求模型 | **三处同步**：`schemas.py` ↔ `frontend/src/contracts/types.ts` ↔ `docs/openapi.yaml`，`check_api_contract.py` 会拦 |
| 新目录 `frontend/src/translation/` · 组件 `SubtitleTranslateDialog` | 翻译抽屉、对照表、编辑、筛选 | 新功能一个目录，不往 `task-center` 里继续堆 |
| `frontend/src/translation/` · API 封装 `translation` | API 封装与状态派生 | 进度不自己算，用后端给的 `translated_count` / `total` |
| `frontend/src/task-center/JobQueue.tsx` / `frontend/src/bilibili/BiliUploadDialog.tsx` | 两个入口 | 只传 `job_id` / `item_id`，不拼字幕路径 |

### 4. 翻译流程

1. **触发与前置校验**：由 `subtitle_path` 定位源文件（复用 `job_artifacts.subtitle_file` 的路径规则，不自己发现第二遍）；确认非中文族、文件可读、服务已配置；同一条已有进行中的翻译任务则直接返回该任务（幂等入口）。
2. **解析**：复用 `bili_subtitle.parse_subtitle_text`（SRT/VTT 共用一套解析，已处理 `WEBVTT` 抬头、`NOTE` 块、`<c>` 行内标签、逗号/点两种时间戳）。**翻译态不做 `merge_repeated_cues`**：ASR 的滚动重复条也必须逐条对应原文；合并只发生在导出 BCC 的那一步，且是另一条代码路径。
3. **分块**：按条数（建议 20–40）与字符预算（建议 ≤ 4000 字符/块）双约束切块，边界只落在整条 cue 上；带 1 条上下文给模型理解语气，但**上下文不参与回填、不重复计数**。
4. **调用**：一次请求发一个 JSON 数组 `[{i, text}]`，要求回复同长度数组 `[{i, text}]`；低温度（0–0.2）；提示词明确禁止：改序号、拆条、合条、输出时间码、输出解释性文字；专有名词/数字/代码标识符原样保留。术语一致性靠「已译块的术语小表随后续请求带上」（可选开关），不靠模型记忆。
5. **回填与校验**（见 §5）： replies 逐块校验后才落盘；校验失败的块重试（指数退避，最多 3 次），仍失败标该块 `failed`，其它块继续。
6. **续跑与幂等**：`POST .../retry` 只补未译/失败块；`edited_by_user=true` 的条目视为锁定，任何重跑都不覆盖；源字幕 SHA-256 变化时把整条记录标 `stale`，界面提示「源字幕已变，请重新校对」而不是自动重译（自动重译会丢人工修订）。
7. **取消**：`POST .../cancel` 置取消标记，在块边界检查；当前在途块允许完成后落盘再停（不浪费已付费的 token），随后不再发新请求。

### 5. 字幕解析与对齐（本功能最贵的一条）

**判据：译文不是「一份新的字幕」，而是「原文每条的翻译属性」。** 因此时间轴绝不参与翻译，只参与复制。

| 环节 | 规则 | 违反时的行为 |
| --- | --- | --- |
| 条数 | 输出条数必须等于输入条数 | 整块重试，3 次后标 `failed`，**绝不部分回填** |
| 序号 | 输出 `i` 集合必须与输入完全相同，无重复、无越界 | 同上；错位回填会毁掉整条时间轴，这是不可接受的失败模式 |
| 时间 | `Cue.start` / `Cue.end` 原样从原文复制，不四舍五入、不重算 | 代码层面不提供改时间的入口；测试断言逐值相等 |
| 空文本 | 原文空 → 译文空（保留条目）；原文非空 → 译文必须非空 | 视为校验失败 |
| 换行 | 中文译文单行过长时插 `\n`（建议 16–20 个汉字），**仍是一条 cue** | 不改 `from`/`to`，不拆条 |
| 编码 | 读写统一 `utf-8-sig`，与既有字幕转换一致 | 避免 BOM 让第一条的原文多出不可见字符 |
| 变异测试 | 故意把某条译文的 `i` 改成相邻值 | 对齐校验**必须**红；红了才说明断言钉在序号上，而不是碰巧通过 |

### 6. 保留原字幕机制

- **只读原文件**：翻译链路对 `subtitle_path` 只有读权限的调用约定；输出文件独立命名：
  - `<base>.zh.srt` —— 纯中文轨，用于上传 B 站或给用户单独使用；
  - `<base>.bilingual.srt` —— 原上译下，仅供对照，**不上传**；
  - `<base>.zh.vtt` —— 只在源文件是 VTT 时产出（可选，二期）；
  - `data/translations/<id>.json` —— 逐条数据（原文、译文、时间、序号、`edited_by_user`、状态、错误）。
- **指纹**：记录创建时保存源字幕 SHA-256 与大小；每次打开抽屉/开始续跑前复核，变化则标 `stale` 并要求用户确认，不静默重译。
- **删除语义**：删除翻译记录只删 `data/translations/<id>.json` 与派生 `.zh` / `.bilingual` 文件（若用户确认），**永不删原始字幕**；删除下载任务时按既有规则处理原字幕（既有「删除任务并删除文件」链路不变）。
- **原子写**：逐条译文先写临时文件再 `os.replace`，避免进程中断留下半截 JSON；进度事件在落盘成功后才发（顺序反了会出现「界面说完成、文件还没写」）。
- **重跑保护**：人工修订条目标记锁定；`retry` 与「重新翻译」都跳过锁定条目，除非用户显式点「放弃我的修订」。

### 7. B 站字幕格式兼容与导出校验

复用既有 `bili_subtitle.to_bcc`（BCC JSON，时间单位**秒**，`body[].content` 支持 `\n`），不另写一套转换器。

- **双轨并存**：`lan=en` 的原轨与 `lan=zh` 的译轨是两条独立 CC 字幕；同一 `lan` 再传是**覆盖**（字幕 id 会换），不是新增。因此：
  - 首次投稿时若已有译文 → 一次投稿后依次传 `en` 与 `zh` 两轨；
  - 稿件已发布后才翻译 → 用「只补字幕、不重投视频」的路径补 `zh` 轨（复用 [016](../bug-fix/016-subtitle-lost-while-the-video-is-in-review.md) 修好的 `reverify` 语义）；**绝不为了字幕重投视频**；
  - 上传后一段时间 `player/v2` 里可能查不到该轨或 `subtitle_url` 为空（平台缓存/处理中），判据是「拿到 URL 且 body 稳定」；匿名请求拿不到不能判定被删（需登录态/buvid）。
- **上传校验**：`oid=cid`（不是 aid）、`lan=zh`（白名单值）、`sign="false"`、`submit="true"` 小写字面量 —— 与 `bili_api.save_subtitle` 现有实现一致，不新增字段。
- **导出前置校验**（本地可做，不联网）：

| 检查项 | 期望 |
| --- | --- |
| 条数 / 序号 | 与源字幕一致，序号 1..N 连续 |
| 时间字段 | 每条 `from`/`to` 与源逐值相等；`from < to` |
| SRT 语法 | `HH:MM:SS,mmm`；块间空行；无裸 `WEBVTT` 混进 SRT |
| VTT 语法 | `WEBVTT` 抬头；时间用 `.` 毫秒；设置项不写进译文 |
| BCC | JSON 可解析；`font_size` / `body` 结构齐全；时间为秒；内容非空 |
| 编码 | UTF-8（-sig 可接受），无替换字符 `\ufffd` |
| 语言 | 目标恒为 `zh`；禁止把 `zh-CN` 写进请求 |
| 体积/条数 | 超过本地观测上限（约 500 KiB / 4700 条）只**警告**，不硬拦 —— 平台侧上限未实测，不能编造 |

### 8. 异常处理与验收标准

#### 8.1 异常处理表

| 异常 | 用户看到 | 系统行为 |
| --- | --- | --- |
| 服务未配置 / Key 缺失 | 「还没配置翻译服务」+ 设置页链接 | 不发请求；任务不创建 |
| 401 / 403 | 「服务拒绝了凭据，检查 API Key」 | 整条停止，已完成条目保留 |
| 429 / 5xx / 超时 | 「第 N 块失败：<原因>，可重试」 | 指数退避重试 3 次；其它块继续 |
| 返回非 JSON / 条数不符 / 序号错位 | 「译文与原文没对齐，已丢弃这一块」 | **整块丢弃重来**，绝不部分回填 |
| 生成空译文 | 「第 N 条没译出来」 | 标该块失败 |
| 源文件被删 / 改动 | 「原字幕已变，需要重新确认」 | 标 `stale`，暂停续跑 |
| 磁盘写入失败 | 「保存失败，检查磁盘空间」 | 不推进进度；不丢已落盘条目 |
| 用户取消 | 停在块边界，保留已译条目 | 不再发新请求 |
| B 站 拒收 / 稿件审核中拿不到 cid | 复用既有字幕三档结论（`failed` +「重传字幕」） | 不把已发布稿件翻成失败；补传只传字幕 |
| 进程被杀 | 重开后进度从 JSON 恢复 | 未落盘块视为未译，续跑只补这些块 |

#### 8.2 验收标准（逐条可执行）

1. **对齐（自动）**：构造 100 条样本（含空行、VTT 标签、滚动重复条、超长单行）跑假 provider，断言译文 100 条、序号一一对应、每条 `start`/`end` 与源逐值相等、无时间字段被改写。
2. **保留（自动）**：源字幕 SHA-256 前后一致；`.zh.srt` 与 `.bilingual.srt` 存在且内容符合 §7；删除翻译记录后源字幕仍在。
3. **失败与续跑（自动）**：provider 在第 3 块返回错位 JSON → 该块标失败、其它块成功；`retry` 只补失败块；人工改过的条目在两次重跑后文字不变；取消后不再发新请求。
4. **翻译质量（人工抽检）**：取一条真实英文技术演讲 ASR 字幕，翻译后抽 20 条对照：专有名词/数字/代码标识符无错译、无漏译、无把说明文字混进译文；不承诺 100% 准确，只承诺「可校对、可修订、修订不被覆盖」。
5. **B 站（手工 + 接口检查）**：测试稿件上 `player/v2` 能看到 `en` 与 `zh` 两条轨（登录态、等 body 稳定）；播放器可关字幕；再次传 `zh` 后仍只有一条 `zh` 轨（id 可变）；`aid`/`bvid` 在补传前后不变。
6. **回归闸门**：后端全量测试、`npm test`（必须 `--environment jsdom`）、`npx tsc --noEmit`、`check_api_contract.py`（三处同步）、`check_doc_anchors.py`、`docs.py check`、`check_layers.py`、`git diff --check` 全绿。
7. **真机 UI**：用 CDP 探针量抽屉宽度/滚动、对照两行同条对齐、失败按钮可点、进度数字与后端一致；jsdom 不做布局，不能只靠单测宣称「清爽」。

### 9. 未决项（开工前确认，不要边做边猜）

1. **翻译服务**：推荐「OpenAI 兼容 HTTP 接口」为默认（标准协议、易注入假 transport、Ollama 本地服务也兼容同一协议，只是 `base_url` 不同）。备选不同供应商 SDK 会各自引入依赖与错误形态，首期不做。需确认用户是否接受把字幕文本发给第三方服务；若不接受，默认改成 Ollama 本地模型，质量与耗时另评。
2. **凭据存放**：推荐 API Key **只从环境变量 / `.env` 读取**（`YTDL_` 前缀，与现有 `AppSettings` 一致），非敏感项（`base_url` / `model` / 术语表开关）存 `Setting` 表；界面只显示「已配置 / 未配置」，不提供明文回显。这样契约与日志里都不会出现密钥。需确认这一取舍可接受。
3. **依赖**：现有 `backend/pyproject.toml` 的 `httpx` 只在 `dev` 里；推荐用标准库 `urllib.request`（现有 `bili_http` 就是这条路）实现 provider，避免为一次调用新增运行时依赖。若确认要多供应商并发，再评估引入 `httpx`。

### 10. 实施顺序（每步都可独立验收、独立提交）

1. **纯领域层**：`translation/align.py` + 假 provider + 对齐/分块/断行测试（不碰 HTTP、不碰 DB）。
2. **provider 与配置**：OpenAI 兼容实现 + `Settings` 三处同步 + 连接测试接口；离线用假 transport 覆盖 401/429/错位 JSON。
3. **持久化与路由**：新表、JSON store、开始/详情/编辑/重试/取消/导出接口 + SSE 事件；前端抽屉与两个入口。
4. **B 站 双轨**：翻译完成后传 `zh` 轨、与 `en` 并存；补传路径复用既有「只补字幕」语义。
5. **文档同步**：`docs/requirements.md`（新增 FR）、`docs/api.md` + `openapi.yaml`、`docs/design.md` / `technical.md` / `implementation.md` / `user-manual.md` / `troubleshooting.md` / `testing.md`、`ai/feature/` 新建一册并回写 `ai/plan/brainstorming.md` 的状态。**闸门全绿不等于覆盖新功能**：按提交清单逐条核对文档，不能只跑脚本就宣布同步完成。

## 2026-05-17 +08:00 - 修复清晰度选择静默降级为 360p 计划

### Summary
- 已定位根因：当前环境找不到系统 `ffmpeg/ffprobe`，后端因此走“单文件格式”选择器；YouTube 目标视频的 1440p/1080p 都是 video-only，只有 `format_id=18` 是带音频的 360p mp4，所以 1080p 请求被静默降级为 360p。
- 修复目标：用户选择 1440p/1080p/360p 时，实际输出视频高度必须匹配所选清晰度；如果无法做到，任务必须失败并给出明确原因，绝不静默下载低清。
- 执行时先按本标题追加到 `ai/plan.md`，修复后同步更新 `README.md`，完成验证后 `git commit` + `git push origin main`。

### Key Changes
- 新增 `imageio-ffmpeg` 作为后备 ffmpeg provider；系统 PATH 找不到 ffmpeg 时，自动使用包内 ffmpeg，并把路径传给 yt-dlp 的 `ffmpeg_location`。
- `get_ffmpeg_status()` 以“系统 ffmpeg 或内置 ffmpeg 任一可用”为 `ffmpeg=True`；`ffprobe` 仍按系统可用性报告。
- 高分辨率格式选择改为精确高度优先：例如 1080p 使用 `height=1080` 的 video-only + audio 组合，不再通过 `/best` 静默降级到 360p。
- 对 `best` 保留最佳可用策略；对用户明确选择的 `1440p/1080p/360p`，要求输出高度匹配。若 ffmpeg 完全不可用且无法合并，抛出清晰错误。
- 具体 `format_id` 选择继续保留，但优先与最佳音频合并；有 ffmpeg 时不降级到 `best`。
- 前端下载选项附近在 ffmpeg 不可用时显示明确警告；README 说明内置 ffmpeg fallback 和不再静默降级。

### Test Plan
- 后端新增测试：无系统 ffmpeg 但 `imageio-ffmpeg` 可用时，`build_download_options()` 设置 `ffmpeg_location`，并对 `1080p` 生成精确高度合并选择器。
- 后端新增测试：`1080p` 选择器不得包含会降级到任意低清的裸 `/best` fallback。
- 后端新增测试：ffmpeg 完全不可用且请求高分辨率时抛出明确错误。
- 自动回归：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。
- 真实下载验收：使用 `https://youtu.be/NReDubvNjRg?si=glPVWHBZaB91s36W` 分别下载 `1440p`、`1080p`、`360p` 到 `downloads/resolution-verification/`，并确认输出高度为 1440、1080、360。

### Assumptions
- 采用“内置后备 ffmpeg”方案，允许新增 `imageio-ffmpeg` 依赖。
- 目标行为是“匹配所选清晰度或失败”，不再接受静默降级到低清。
- 真实下载测试文件位于已忽略的 `downloads/` 下，不进入 Git。

## Summary
实现一个本机单用户 Web UI：FastAPI 后端 + React/Vite 前端，使用 yt-dlp Python API 执行 YouTube 单视频、playlist、字幕和多分辨率下载。首版不做公网部署，不做账号系统；支持用户上传 cookies 文件以处理需要登录态的视频。参考 yt-dlp 官方能力：格式选择、字幕选项、Python 嵌入和 progress hooks（https://github.com/yt-dlp/yt-dlp）。

## Key Changes
- 新建项目结构：
  - `backend/`：FastAPI API、任务队列、SQLite 状态库、yt-dlp 封装、配置管理。
  - `frontend/`：React + TypeScript + Vite 本地 Web UI。
  - `ai/plan.md`：保存本计划。
  - `.gitignore`：覆盖 Python、Node、下载产物、cookies、数据库、日志和编辑器文件。
- 后端核心能力：
  - `POST /api/analyze`：解析单视频或 playlist，返回标题、封面、时长、playlist 条目、可选分辨率、格式、字幕语言和自动字幕语言。
  - `POST /api/jobs`：创建下载任务，支持单视频或 playlist 批量；模式包括视频+字幕、仅视频、仅字幕。
  - `GET /api/jobs`、`GET /api/jobs/{id}`：查看任务列表和详情。
  - `POST /api/jobs/{id}/cancel`：取消未完成任务。
  - `GET /api/events`：SSE 推送任务进度、速度、ETA、当前文件、成功/失败状态。
  - `GET/PUT /api/settings`：配置默认下载目录、并发数、默认字幕语言、默认格式策略。
  - `POST /api/cookies`、`DELETE /api/cookies`：上传/清除 cookies 文件；不在日志或 UI 中回显内容。
- 下载策略：
  - 默认格式为最佳视频+最佳音频并合并为 mp4/mkv；用户可选 best、2160p、1440p、1080p、720p、480p 或具体 format_id。
  - 字幕支持人工字幕、自动字幕、语言多选、格式优先级 `srt/vtt/best`。
  - playlist 支持全选、按序号范围选择、跳过已下载、失败继续、单项失败不阻断整批。
  - 需要 ffmpeg/ffprobe；启动时检测缺失并在 UI 显示可执行提示。
- 前端体验：
  - 首页即下载控制台，不做营销页。
  - URL 输入区支持单链接/playlist；分析后显示视频或 playlist 表格。
  - 右侧/顶部提供下载模式、分辨率、字幕语言、输出目录、cookies 状态、并发数。
  - 任务中心显示队列、单项进度、总进度、失败原因、取消按钮、打开下载目录提示。
  - 额外 brainstorm 功能：下载历史、重复链接提示、缩略图和 metadata 保存开关、速度限制、重试次数、完成后声音/系统通知开关。

## Implementation Plan
- 初始化后端：
  - 创建 `backend/pyproject.toml`，依赖 `fastapi`、`uvicorn`、`yt-dlp`、`pydantic-settings`、`sqlmodel`、`pytest`、`httpx`。
  - 创建 `backend/app/main.py`、`config.py`、`db.py`、`schemas.py`、`models.py`。
  - 配置默认数据目录为本地 `data/`，下载目录默认为 `downloads/`。
- 实现 yt-dlp 服务层：
  - `extract_metadata(url, cookies_path=None)` 使用 `yt_dlp.YoutubeDL(...).extract_info(download=False)`。
  - 将 yt-dlp 原始 formats/subtitles 转成前端友好的 DTO。
  - `download_job(job_id, options)` 通过 progress hook 写入 SQLite 并广播 SSE。
  - 下载选项统一从 API 请求转换为 yt-dlp opts，避免前端直接传任意 yt-dlp 参数。
- 实现任务系统：
  - SQLite 表：`settings`、`jobs`、`job_items`、`events`。
  - 后台 worker 使用 `asyncio.Queue` + 线程池运行阻塞下载；默认并发 2。
  - 任务状态固定为 `queued`、`running`、`succeeded`、`failed`、`cancelled`。
- 实现前端：
  - Vite React TypeScript 项目。
  - 页面组件：`UrlAnalyzer`、`DownloadOptions`、`PlaylistTable`、`JobQueue`、`SettingsPanel`、`CookieManager`。
  - 使用原生 CSS，确保桌面和移动端可用；任务表格在窄屏改为紧凑列表。
- 生成 `.gitignore`：
```gitignore
__pycache__/
*.py[cod]
.venv/
.env
.env.*
!.env.example
.pytest_cache/
.ruff_cache/

node_modules/
dist/
.vite/
coverage/

downloads/
data/
*.sqlite
*.sqlite3
*.db
cookies*.txt
*.part
*.ytdl
*.log

.DS_Store
Thumbs.db
.vscode/
.idea/
```

## Test Plan
- 后端单元测试：
  - analyze 单视频返回格式、字幕和基础 metadata。
  - analyze playlist 返回条目列表，并能处理空 playlist/无权限错误。
  - 请求选项能正确映射到 yt-dlp opts：分辨率、仅字幕、字幕语言、cookies。
  - 任务状态流转：queued -> running -> succeeded/failed/cancelled。
- 前端测试：
  - 输入 URL 后显示分析结果。
  - playlist 可多选条目并提交批量任务。
  - 字幕模式、分辨率选择、cookies 状态能正确反映到请求体。
- 集成/验收：
  - 使用一个公开视频下载 720p 视频。
  - 使用一个公开视频单独下载英文/中文字幕。
  - 使用一个小 playlist 批量下载，确认失败项不阻断其他项。
  - 在缺少 ffmpeg 时显示明确错误，不让任务静默失败。
  - `.gitignore` 确认下载文件、cookies、SQLite、node_modules 不进入 git。

## Assumptions
- 首版只服务本机单用户，不支持公网部署、多用户权限或远程文件管理。
- cookies 仅支持手动上传文件，不自动读取浏览器。
- 下载器用于用户有权下载的内容；不实现 DRM 绕过。
- 计划文件在执行阶段写入 `ai/plan.md`。

---

## 2026-05-16 09:34:33 +08:00 - 现有功能审计与元信息显示增强计划

### Summary
对现有 YouTube 下载器做一次不新增大功能的审计和测试加固，重点检查当前后端下载/解析、任务中心、字幕、cookies、设置和前端交互是否稳定。已确认当前基线：`python -m pytest backend\tests -q` 通过 12 项，`npm test` 通过 3 项，`npm run build` 通过。实现时先将本计划按本标题追加到 `ai/plan.md`，以后所有计划也按“时间戳 + 计划名”追加。

### Key Changes
- 审计与回归测试：
  - 保留分层测试策略：自动测试使用 mock/单元/前端测试；真实 YouTube 下载作为可选手动验收步骤记录，不纳入默认自动测试。
  - 增补覆盖现有核心路径：解析格式元信息、字幕语言、playlist 选择、任务暂停/重启/删除、实时进度、cookies 状态、缺少 ffmpeg 时的格式降级。
  - 若审计发现明确 bug，先写失败测试复现，再做最小修复；不引入新的大功能或新架构。
- 元信息显示增强：
  - 后端优先复用现有 `FormatOption.filesize` 字段；只在发现映射遗漏时修复 `_map_formats`。
  - 前端在“具体格式”下拉中显示更完整格式文案：`format_id · 分辨率 · fps · ext · 文件大小`。
  - 文件大小用前端 helper 格式化为 `B/KB/MB/GB`；缺失时显示“大小未知”。
  - 在分析结果摘要或下载选项附近显示可选格式数量，并在已选具体格式时显示当前格式的分辨率与大小。
- 文档与计划维护：
  - 将本计划追加写入 `ai/plan.md`，标题固定为本计划标题。
  - 因本次会修改功能显示与测试说明，同步更新 `README.md`，补充“格式大小显示”和审计/测试命令说明。
  - 不修改 `.gitignore`、不新增大型依赖、不增加账号/远程文件管理等新功能。

### Test Plan
- 后端：
  - `test_extract_metadata_maps_playlist_entries_formats_and_subtitles` 增加断言：`filesize` 与 `filesize_approx` 都正确进入 `FormatOption.filesize`。
  - 若发现格式 label 生成缺陷，增加对应单元测试后修复。
  - 跑全量：`python -m pytest backend\tests -q`。
- 前端：
  - 更新 `App.test.tsx`（现为 `frontend/src/download-request/analyzer.test.tsx`）的 analyze fixture，断言格式下拉显示 `720p` 和格式大小。
  - 增加/更新测试：选中具体格式后，页面显示当前格式分辨率和大小。
  - 跑全量：`npm test` 和 `npm run build`。
- 手动验收建议：
  - 解析一个公开视频，确认格式下拉同时显示分辨率和大小。
  - 解析一个小 playlist，确认选择条目、任务中心控制、进度显示仍正常。
  - 可选真实下载：720p 视频下载一次，英文或中文字幕单独下载一次。

### Assumptions
- 测试范围采用用户选择的“分层测试”：默认自动测试不真实下载 YouTube 内容。
- 本次增强只围绕已有功能质量、测试覆盖和元信息展示，不新增下载策略、账号系统、远程管理或新页面。
- 如果 `yt-dlp` 返回某格式没有大小，前端显示“大小未知”，不尝试额外发请求估算大小。
- 后续每次制定计划，都按“时间戳 + 计划名”追加到 `ai/plan.md`，不覆盖历史计划。

---

## 2026-05-16 +08:00 - 下载选项融合、并发默认值与合集任务展示增强计划

### Summary
在当前干净分支 `feature/audit-format-metadata` 上实现四组增量改动，并按用户要求每完成一组就 `git commit` + `git push`。执行时先将本计划按“时间戳 + 计划名”追加到 `ai/plan.md`，所有功能变更同步更新 `README.md`。

### Key Changes
- 下载清晰度融合：将下载选项里的“分辨率”和“具体格式”合并为一个“清晰度 / 格式”下拉；默认清晰度改为 `1080p`，分析后若当前视频没有 1080p，则自动选择该视频可用的最高分辨率。
- 并发数默认值：后端默认并发数改为 `os.cpu_count()` 返回的逻辑 CPU core 数量，最低为 1，不设置上限；已保存到 SQLite 的用户自定义并发数继续优先。
- 任务中心合集展开/折叠：仅对 `total_items > 1` 的合集任务显示展开/折叠按钮；运行中或失败的合集自动展开，用户手动选择后保留。
- 合集子视频信息展示：展开合集后，每个子视频显示标题、状态、百分比、已下载/总大小、已用时、ETA、速度、失败原因和独立进度条。

### Test Plan
- 每个 task 提交前至少运行对应 targeted tests。
- 每次 push 前运行：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。
- 手动验收建议：解析不同分辨率公开视频，确认清晰度默认和降级；创建 playlist 下载任务，确认展开/折叠和子视频进度、大小、速度、已用时、剩余时间显示。

### Assumptions
- 清晰度与具体格式合并为单一下拉。
- 并发数默认等于完整 CPU core 数量，不设上限。
- 合集任务运行中或失败时自动展开。
- 现有 API 的 `DownloadOptions.resolution` + `format_id` wire shape 保持兼容。

---

## 2026-05-16 +08:00 - Main 分支开发、Playlist 输出目录与任务删除增强计划

### Summary
后续开发切换到 `main` 分支进行。实现 playlist 自动下载到同名子文件夹、单视频任务中心去重显示、删除任务时可选择同时删除已下载文件。执行时先将本计划追加到 `ai/plan.md`，并同步更新 `README.md`。每个功能完成后验证、commit、push。

### Key Changes
- 分支策略：先确认工作区干净，切换到 `main`，拉取 `origin/main`，将当前 `feature/audit-format-metadata` 已完成并 push 的功能快进合入 `main`，之后所有本次改动都在 `main` 上提交并推送。
- Playlist 输出目录：设置里的下载目录继续表示基础根目录；playlist 任务自动下载到 `<下载根目录>/<playlist名称>/`；单视频仍保存到下载根目录。
- 任务中心单视频去重显示：`total_items === 1` 的单视频任务不再渲染重复的 `items[0]` 子项详情；playlist 才显示可展开的子视频列表。
- 删除任务时同时删除文件：删除任务支持 `delete_files=false` 选项；开启后删除该任务已下载文件及空的 playlist 子文件夹，默认只删除任务记录。

### Test Plan
- 每个 task 先跑 targeted tests，再跑完整验证。
- 完整验证命令：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。
- 手动验收：创建 playlist 任务，确认下载到 `<下载目录>/<playlist名称>/`；创建单视频任务，确认任务中心不重复显示同一个视频；分别验证删除任务时文件保留和文件删除两种路径。

### Assumptions
- 采用用户确认的方案 1：设置里的下载目录保持为基础根目录，playlist 自动使用其下同名子文件夹。
- 后续默认在 `main` 分支开发，除非用户明确指定其他分支。
- 不新增远程文件管理功能；删除文件仅限本机已下载产物。
- 对已有历史任务，如果没有任务级下载目录，则按当前基础下载目录兼容处理。
## 2026-05-16 23:56:56 +08:00 - UI 简化与合集子视频重启计划

### Summary
本次专注现有 UI 的减法和一个小型任务中心增强：移除不必要的面板说明文字，移除设置中的“默认清晰度”入口，把任务数量移动到任务中心标题同行右侧，并为 playlist 子视频增加单项重启。继续在 `main` 分支开发；每完成一组改动就测试、提交并 `git push`。README 随功能变化同步更新。

### Key Changes
- UI 简化：
  - 移除“解析链接”“下载选项”“设置”标题下方的补充说明文字。
  - 保留面板标题和图标，不改变核心操作入口。
- 设置面板简化：
  - 移除“默认清晰度”设置控件，避免和单次下载的“清晰度 / 格式”选择重复。
  - 设置面板聚焦下载根目录和并发数；清晰度仍由下载选项中的“清晰度 / 格式”决定，初始默认值继续由应用内默认 `1080p` 提供。
  - 后端设置 API 暂保留 `default_resolution` 字段以维持兼容，不在 UI 中暴露。
- 任务中心标题优化：
  - 将“x 个任务 / 暂无任务”移动到“任务中心”同行最右侧。
- Playlist 子视频重启：
  - 为 playlist 展开后的每个子视频添加独立重启按钮。
  - 后端新增受控 API，只允许重置指定 `job_item`，并将所属任务重新排队；不新增大架构。
  - 子视频重启会清空该子项进度、大小、速度、ETA、错误和输出路径，并刷新所属任务统计。

### Test Plan
- 前端：
  - 断言三个面板标题下的说明文字不再渲染。
  - 断言设置面板不再显示“默认清晰度”，保存设置时不提交 `default_resolution`。
  - 断言任务数量与任务中心标题位于同一标题区域。
  - 断言 playlist 子视频显示重启按钮，点击后调用子项重启 API 并更新任务。
- 后端：
  - 增加 playlist 子项重启 API 测试：重置单个 `JobItem`、刷新任务状态并返回更新后的 `JobRead`。
  - 继续跑全量：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。

### Assumptions
- “默认清晰度”只从前端设置 UI 移除，后端字段暂保留以避免破坏现有配置和测试。
- 子视频重启只针对 playlist 子项显示；单视频继续使用任务级重启。
- 删除/暂停/批量操作行为保持不变。
## 2026-05-17 10:03:11 +08:00 - 设置 UI 即时保存与下载目录选择计划

### Summary
本次只优化设置面板：移除看不出作用的“保存设置”按钮，改为设置项即时保存；将并发说明并入标签；新增本机文件夹选择对话框用于选择下载根目录。playlist 下载目录逻辑保持不变：若下载根目录为 `dir`，playlist 名称为 `list`，实际保存到 `dir/list/`。

### Key Changes
- 保存设置按钮：
  - 移除“保存设置”按钮。
  - 并发数输入仍保留，用户修改后在控件失焦时保存到 `/api/settings`。
  - UI 保留轻量保存状态提示，避免用户不知道修改是否生效。
- 并发标签：
  - 将下方说明“默认跟随 CPU core 数量，可按需覆盖。”移到标签中，改为“并发 (默认跟随 CPU Core 数量，可按需调整。)”。
- 下载目录选择：
  - 新增后端受控 API 打开本机目录选择对话框，不新增大型依赖，优先使用 Python 标准库 `tkinter.filedialog.askdirectory`。
  - 前端“下载目录”显示当前路径，并提供“选择文件夹”按钮；选择后立即保存设置并刷新 UI。
  - 保持默认下载目录和 playlist 自动同名子文件夹逻辑不变。

### Test Plan
- 前端：
  - 断言设置面板不再显示“保存设置”按钮。
  - 断言并发标签文案为“并发 (默认跟随 CPU Core 数量，可按需调整。)”。
  - 断言修改并发并失焦会调用 `PUT /api/settings`。
  - 断言点击“选择文件夹”会调用目录选择 API 并更新路径显示。
- 后端：
  - 测试目录选择 API 使用注入的选择器返回路径，并持久化为下载根目录。
  - 测试用户选择下载根目录后，playlist 任务仍创建 `<dir>/<playlist 名称>/`。
- 全量验证：
  - `python -m pytest backend\tests -q`
  - `npm test`
  - `npm run build`
  - `git diff --check`

### Assumptions
- 浏览器无法可靠读取任意本机文件夹绝对路径，因此目录选择由本机后端弹出系统对话框完成。
- 若运行环境没有 GUI 或用户取消选择，后端返回当前设置或明确错误，不改变下载目录。
- 本次不改变 cookies、任务中心、playlist 子目录生成规则或真实下载策略。

---

# 2026-05-17 +08:00 - 任务时间/实际分辨率展示与 ffprobe 警告移除计划

## Summary
- 实现前先将本计划按标题追加到 `ai/plan.md`，所有功能变更同步更新 `README.md`。
- “任务中心”每个任务显示开始时间、结束时间、实际下载分辨率；playlist 在合集行显示统一结果：相同则显示 `1920x1080`，不同则显示 `混合分辨率`。
- `ffprobe` 不是当前下载/合并的必需依赖；顶栏移除 `ffprobe` 红色警告，只保留 `ffmpeg` 与 cookies 状态。

## Key Changes
- 后端新增实际分辨率记录：给 `job_items` 增加 nullable `actual_width`、`actual_height`，通过现有轻量迁移补列，并在 `JobItemRead` 暴露。
- 后端新增 `JobRead.actual_resolution` 聚合字段：单视频使用唯一子项分辨率；playlist 若已知分辨率一致显示具体尺寸，若不一致显示 `混合分辨率`，未知时返回 `null`。
- 下载完成时从 yt-dlp progress payload 的 `info_dict/requested_formats` 提取实际宽高；若缺失，则用当前可用的 `ffmpeg` 执行文件探测兜底。重启任务或重启 playlist 子视频时清空旧分辨率。
- 前端 `JobQueue` 在任务指标区显示：开始时间、结束时间、实际分辨率；时间使用稳定格式 `YYYY-MM-DD HH:mm:ss`，空值显示 `--`，未知分辨率显示 `检测中`。
- 前端顶栏移除 `ffprobe` 状态 pill；README 说明 `ffmpeg` 是高分辨率合并所需，`ffprobe` 仅为可选诊断能力，不再作为警告项。

## Test Plan
- 先写失败测试再实现：
  - 后端 API 测试：任务响应包含 `started_at`、`finished_at`、`actual_resolution`，子项包含 `actual_width/actual_height`。
  - 后端 playlist 测试：多个子视频分辨率相同返回具体尺寸，不同返回 `混合分辨率`。
  - 后端 helper 测试：能从 yt-dlp `requested_formats` 提取 video-only 的实际宽高。
  - 前端测试：任务中心展示开始时间、结束时间、实际分辨率；playlist 展示 `混合分辨率`。
  - 前端测试：当 `settings.ffmpeg.ffprobe=false` 时不再渲染 `ffprobe` 红色状态。
- 每个编号任务完成后运行对应测试并提交推送：
  - `git commit -m "feat: show job timing and actual resolution"`，`git push origin main`
  - `git commit -m "chore: remove optional ffprobe warning"`，`git push origin main`
- 最终全量验证：
  - `python -m pytest backend\tests -q`
  - `npm test`
  - `npm run build`
  - `git diff --check`

## Assumptions
- `ffprobe` 不作为必需依赖；当前应用用 `ffmpeg` 已能满足下载合并与必要探测。
- playlist 合集行只需要一个统一分辨率显示；子视频展开区保留现有进度/大小信息，不额外强制显示每个子视频分辨率。
- 已有历史任务若没有分辨率记录，界面显示 `检测中` 或 `--`，不做批量回填。

---

# 2026-05-17 +08:00 - 并发/限速审计与解析区 UI 整合计划

## Summary
- 实现前先将本计划追加到 `ai/plan.md`，功能变更同步更新 `README.md`。
- 修复并发数只在 worker 启动时读取、设置修改后不生效的问题。
- 将 Cookies 上传/清除整合进“解析链接”面板，移除独立 Cookies 卡片。
- 在解析结果标题区显示当前所选清晰度/格式对应的视频大小。
- 将限速默认值改为 `2048 KB/s`；清空限速输入仍表示不限速。

## Key Changes
- 并发数：
  - 根因：`JobManager.start()` 只按启动时 `default_concurrency` 创建 worker，`PUT /api/settings` 更新后不会调整已运行 worker。
  - 后端新增可动态调整并发的 manager 方法；`PUT /api/settings` 保存新并发后立即应用，无需重启服务。
  - 启动时先加载 SQLite 中已保存的设置，再启动任务 manager，确保历史并发设置也生效。

- Cookies UI：
  - 删除右侧独立 `CookieManager` 卡片。
  - 在“解析链接”面板内加入紧凑 cookies 状态行：状态文本、上传 `cookies.txt`、清除按钮。
  - 保持现有 `/api/cookies` 上传/清除接口不变；上传后刷新 settings，解析请求继续默认使用 cookies。

- 清晰度大小显示：
  - `AnalysisPanel` 接收当前下载选项。
  - 标题区在视频标题旁显示当前选择对应大小：具体 `format_id` 使用该格式大小；清晰度策略使用同高度格式中最大可用 `filesize/filesize_approx`；缺失时显示“大小未知”。
  - 选择不同清晰度或具体格式时，标题区大小实时更新；`best` 显示“最佳可用 · 大小未知”。

- 限速：
  - `DownloadOptions.speed_limit_kbps` 后端默认改为 `2048`，前端初始值也改为 `2048`。
  - 后端保持现有行为：`None/null` 不写入 yt-dlp `ratelimit`，表示不限速；`2048` 写入 `2048 * 1024` bytes/s。
  - UI 文案明确“清空表示不限速”，避免把网络或 YouTube 侧速度波动误认为本地限速。

## Test Plan
- 后端：
  - 新增 `JobManager` 并发测试：并发为 1 时两个阻塞任务只启动一个；更新为 2 后第二个无需重启服务即可启动。
  - 新增设置加载测试：SQLite 中已有 `default_concurrency` 时，应用启动使用保存值。
  - 扩展限速测试：默认 `DownloadOptions()` 生成 `ratelimit=2048*1024`；显式 `speed_limit_kbps=None` 时不包含 `ratelimit`。
  - 运行 `python -m pytest backend\tests -q`。

- 前端：
  - 更新测试断言：Cookies 控件出现在“解析链接”面板内，独立 Cookies 卡片不再渲染；上传/清除仍调用原 API 并刷新 settings。
  - 新增测试：切换 `1080p`、`720p`、具体 `format_id` 后，标题区显示对应大小或“大小未知”。
  - 新增测试：限速输入默认显示 `2048`，清空后提交任务体为 `speed_limit_kbps: null`。
  - 运行 `npm test` 和 `npm run build`。

- 提交与推送：
  - 完成并发修复后验证、提交 `fix: apply concurrency changes immediately`，`git push origin main`。
  - 完成 Cookies UI 整合后验证、提交 `feat: integrate cookies into analyzer`，`git push origin main`。
  - 完成标题区大小显示后验证、提交 `feat: show selected quality filesize`，`git push origin main`。
  - 完成限速默认值后验证、提交 `fix: default speed limit to 2048 kbps`，`git push origin main`。
  - 最终运行 `git diff --check` 并确认工作区干净。

## Assumptions
- “不填写限速”定义为不限速；默认值改为预填 `2048 KB/s`，用户清空才发送 `null`。
- 分辨率大小只使用 analyze 已返回的格式大小，不额外请求 YouTube 估算。
- Cookies 只做 UI 整合，不改变 cookies 文件存储路径、接口或安全策略。

# 2026-05-17 16:46:26 +08:00 - 浏览器 YouTube Cookies 自动导入计划

> **2026-11-04 更新：该能力已整体移除**（应用不再从浏览器读取 cookies），
> 见 [ai/bug-fix/038](../bug-fix/038-browser-cookie-import-is-gone.md)。以下为当时的计划原文，保留作历史。

## Summary
针对 YouTube 返回 `Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies` 的场景，补齐自动通过 yt-dlp 从本机浏览器导入 cookies 的能力。功能入口放在“解析链接”面板，因为 cookies 直接影响单视频和 playlist 的解析与后续下载；playlist 解析失败时也走同一套自动导入与重试逻辑。实现后同步更新 `README.md`，验证通过后提交并 `git push origin main`。

## Key Changes
- 后端新增浏览器 cookies 导入能力：
  - 使用 yt-dlp 的 `extract_cookies_from_browser()` 从 Edge/Chrome/Firefox/Brave/Chromium 等浏览器读取 cookies。
  - 自动导入只保存 YouTube/Google 相关域名 cookies 到现有 `data/cookies.txt`，继续沿用当前 `cookiefile` 下载链路。
  - `POST /api/cookies/from-browser` 支持 `browser=auto` 或指定浏览器；返回来源浏览器和导入数量，不回显 cookie 内容。
  - `/api/analyze` 和创建任务前的 playlist 分析遇到 bot 登录错误时，如果当前 cookies 不可用，会自动尝试浏览器导入并重试一次。
- 前端 UI：
  - 在“解析链接”面板的 cookies 状态行增加紧凑的“从浏览器导入”按钮和浏览器选择框。
  - 保留手动上传/清除 `cookies.txt`，导入后刷新 settings，单视频和 playlist 都复用同一个 cookies 状态。
- 文档与测试：
  - 后端测试覆盖浏览器导入端点、自动重试和不暴露 cookie 内容。
  - 前端测试覆盖解析区导入按钮、API 调用和导入后状态刷新。
  - README 补充自动导入 cookies 的用途、风险和仍可手动上传的替代方式。

## Test Plan
- 后端：
  - `YtDlpService.import_browser_cookies("auto", target)` 能尝试候选浏览器、保存过滤后的 cookies 文件，并返回导入来源。
  - `POST /api/cookies/from-browser` 返回 `enabled=true`、`source=browser`，且不包含 cookie value。
  - `/api/analyze` 在无 cookies 文件且收到 bot 登录错误时自动导入并重试；playlist URL 同样适用。
  - 全量运行 `python -m pytest backend\tests -q`。
- 前端：
  - “解析链接”面板展示手动上传、清除、浏览器选择和“从浏览器导入”。
  - 点击导入调用 `/api/cookies/from-browser` 并刷新 settings。
  - 全量运行 `npm test` 和 `npm run build`。
- 最终：
  - `git diff --check`
  - `git commit -m "feat: import youtube cookies from browser"`
  - `git push origin main`

## Assumptions
- 自动导入只针对本机单用户环境；不会读取远程浏览器或绕过 DRM。
- 若浏览器未安装、未登录 YouTube、cookie 数据库被浏览器锁定或系统密钥链不可用，导入会失败并在 UI 中显示错误。
- 手动上传的 `cookies.txt` 与浏览器导入共用同一个本地 `data/cookies.txt`，清除 cookies 会删除该文件。

# 2026-05-18 +08:00 - 不可用分辨率提示与降级重启计划

## Summary
修复 `Requested format is not available` 场景：当用户明确选择 `1440p/1080p/...` 但当前视频不支持该高度时，不静默降级，也不只显示 yt-dlp 原始错误；任务中心要显示“当前没有 xx 分辨率，低于该分辨率的最高可用分辨率是 yy”，并提供“以 yy 重启”按钮。单视频在任务卡片显示提示，playlist 在展开后的失败子视频行显示提示。完成后追加计划到 `ai/plan.md`、更新 `README.md`、提交并 `git push origin main`。

## Key Changes
- 后端识别 yt-dlp 的 `Requested format is not available` 错误，仅针对明确数字清晰度如 `1080p` 生效；具体 `format_id` 不做猜测，继续显示原错误。
- 下载失败后用当前 cookies 对失败视频做一次 metadata 查询，计算低于请求高度的最高可用高度；例如请求 `1080p`，可用 `720p/360p`，则建议 `720p`。
- 给 `job_items` 增加轻量迁移字段：`options_json`、`requested_resolution`、`fallback_resolution`；API 增加 `resolution_fallback` 结构，单视频 job 聚合子项提示，playlist 子项单独返回提示。
- 重启接口保持兼容：`POST /api/jobs/{id}/restart` 和 `POST /api/jobs/{id}/items/{item_id}/restart` 可选请求体 `{ "resolution": "720p" }`；无请求体时保持原重启逻辑。
- playlist 子视频使用 `job_items.options_json` 保存单项重启覆盖配置，避免“以 720p 重启某一集”影响同一 playlist 的其他视频。
- 前端任务中心显示明确提示和按钮：单视频显示“以 720p 重启任务”；playlist 子项显示“以 720p 重启”。点击后调用对应 restart API 并传入 fallback resolution。

## Test Plan
- 后端 TDD：
  - `YtDlpService` helper 能从 `1080p` 请求和可用 formats 中计算 `720p` fallback。
  - job 下载遇到 `Requested format is not available` 后，失败 item 返回 `requested_resolution=1080p`、`fallback_resolution=720p` 和友好错误文案。
  - 单视频 `restart` 传 `{resolution:"720p"}` 会更新 job options 并重新入队。
  - playlist 单项 `restart` 传 `{resolution:"720p"}` 只更新该 item 的 `options_json`，不改整个 playlist job 的默认 options。
- 前端 TDD：
  - 单视频 failed job 显示无 `1080p`、建议 `720p`，点击“以 720p 重启任务”发送 restart body。
  - playlist failed item 显示同样提示，点击“以 720p 重启”发送 item restart body。
- 全量验证：
  - `python -m pytest backend\tests -q`
  - `npm test`
  - `npm run build`
  - `git diff --check`

## Assumptions
- 保持“不静默降级”的既有原则：只有用户点击 fallback 重启按钮后才会用较低分辨率重新下载。
- fallback 只针对选择了数字清晰度策略的任务；如果用户选择的是具体 `format_id`，不自动推断替代格式。
- 如果 metadata 查询也失败，或没有任何低于请求高度的格式，则任务中心显示原始失败原因，不提供 fallback 重启按钮。

# 2026-05-18 +08:00 - Edge Cookies 锁库导入修复计划

> **2026-11-04 更新：该能力已整体移除**（应用不再从浏览器读取 cookies），
> 见 [ai/bug-fix/038](../bug-fix/038-browser-cookie-import-is-gone.md)。以下为当时的计划原文，保留作历史。

## Summary
根因已定位：当前后端直接调用 `yt_dlp.cookies.extract_cookies_from_browser("edge")`，但 Windows 上 Edge 正在运行时会锁定 `Default\Network\Cookies` 数据库，yt-dlp 会抛出 `Could not copy Chrome cookie database`，对应官方问题 yt-dlp/yt-dlp#7271：https://github.com/yt-dlp/yt-dlp/issues/7271。
修复目标：保留自动导入 cookies，但当 Edge 锁库时给出明确、可操作的 UI；用户确认后应用关闭 Edge、重新导入 cookies，并自动重试 playlist 解析。完成后追加本计划到 `ai/plan.md`、更新 `README.md`、验证 Edge 成功、提交并 `git push origin main`。

## Key Changes
- 后端 cookies 导入改为可诊断流程：
  - `POST /api/cookies/from-browser` 请求体新增 `close_browser_if_locked: boolean = false`。
  - 当 Edge 抛出 `Could not copy Chrome cookie database` 时，返回结构化错误：`code=browser_locked`、`browser=edge`、友好中文提示。
  - 当 `close_browser_if_locked=true` 且 browser 为 `edge` 或 `auto` 时，后端先关闭 `msedge.exe` 进程，等待 Cookies 数据库解锁，再重试 yt-dlp Edge cookies 导入。
  - `auto` 模式下如果 Edge 锁库且其他浏览器没有 YouTube/Google cookies，优先返回 Edge 锁库错误，不再展示一长串无关浏览器失败信息。
  - 不关闭 Chrome/Firefox/Brave 等其他浏览器；本次只实现并验证 Edge。
- 前端解析区交互：
  - “从浏览器导入”首次仍先安全尝试导入，不主动关闭浏览器。
  - 若收到 `browser_locked`，在“解析链接”面板显示紧凑提示：Edge 正在运行，cookies 数据库被锁定。
  - 提供按钮“关闭 Edge 并导入”，点击前用确认弹窗说明会关闭所有 Edge 窗口；确认后调用 `{ browser: "edge", close_browser_if_locked: true }`。
  - 如果该错误来自解析 playlist 过程，导入成功后自动重试刚才的 playlist 解析。
- 错误与状态表达：
  - API 客户端支持读取结构化错误，不再把对象错误显示成 `[object Object]`。
  - Cookie 导入成功后继续只返回 `enabled/source/browser/imported_count/filename`，不回显任何 cookie 内容。
  - 下载任务若因 cookies 缺失或失效失败，错误提示改为引导用户到“解析链接”区导入 Edge cookies 后重启任务。
- 文档与计划：
  - 追加本计划到 `ai/plan.md`。
  - `README.md` 更新 Edge 导入说明：Edge 正在运行会锁库；应用会在用户确认后关闭 Edge 再导入；手动上传 `cookies.txt` 仍是备用方案。

## Test Plan
- 后端单元/API 测试：
  - mock `extract_cookies_from_browser("edge")` 抛出 `Could not copy Chrome cookie database`，断言 `/api/cookies/from-browser` 返回 `400/409` 结构化 `browser_locked` 错误。
  - 请求 `{ browser: "edge", close_browser_if_locked: true }` 时，断言调用 Edge 关闭 helper，并在第二次导入成功后写入 `data/cookies.txt`。
  - `browser=auto` 时，Edge 锁库且其他浏览器无 cookies，断言最终错误仍是 `browser_locked`，而不是混杂浏览器错误串。
  - `/api/analyze` 遇到 bot challenge 且 Edge 锁库时返回结构化 cookies 错误；导入成功后再次 analyze 可成功。
  - 确认响应与日志不包含 cookie value。
- 前端测试：
  - 点击“从浏览器导入”收到 `browser_locked` 后，解析区显示 Edge 锁库提示和“关闭 Edge 并导入”按钮。
  - 点击确认按钮后，请求体包含 `{ browser: "edge", close_browser_if_locked: true }`，成功后 cookies 状态变为已启用。
  - playlist 解析因锁库失败后，确认关闭 Edge 并导入成功，会自动重试原 playlist URL。
  - 结构化 API 错误在全局 alert 中显示可读中文消息。
- 全量验证：
  - `python -m pytest backend\tests -q`
  - `npm test`
  - `npm run build`
  - `git diff --check`
- Edge 真实验收：
  - 确认 Edge 已登录 YouTube。
  - 保持 Edge 打开，点击“从浏览器导入”，应出现锁库提示。
  - 点击“关闭 Edge 并导入”，应用关闭 Edge，导入成功，`cookies_enabled=true` 且 `imported_count > 0`。
  - 使用 playlist URL `https://youtube.com/playlist?list=PLHTh1InhhwT47Xpx7Cn-bPw9Qygjr98rs&si=jZPDPZwnzrtZarXj` 解析成功并显示 playlist 条目。
  - 若真实验收失败，不提交、不推送。

## Assumptions
- 已选择“确认后关闭 Edge”方案；应用不得在用户确认前主动关闭浏览器。
- Edge cookie profile 使用 yt-dlp 默认发现逻辑；本次不新增 profile 选择 UI。
- 关闭 Edge 仅在 Windows 上实现；其他系统遇到锁库时显示手动关闭后重试提示。
- 手动上传 cookies 功能保持不变。
- 修复完成后提交信息使用 `fix: handle locked edge cookies import` 并推送 `origin main`。

---

# 2026-05-18 +08:00 - Requested Format 不可用自动降级修复计划

## Summary
根因是：playlist 解析采用 flat metadata 时，前端无法可靠知道每个子视频是否支持所选分辨率；后端下载时又使用“精确高度”格式选择器，所以某个子视频缺少 `1080p/1440p` 时会触发 yt-dlp 的 `Requested format is not available`。本次按用户选择改为：下载前预检并自动降级到低于所选分辨率的最高可用分辨率，同时在任务中心明确提示“已从 xx 自动降级到 yy”。

## Key Changes
- 后端增加每个任务项的分辨率预检：仅针对数字清晰度策略；若所选高度不可用但存在更低可用高度，自动用最高的低一级分辨率下载，并记录 `requested_resolution`、`fallback_resolution` 与友好提示。
- playlist 每个子视频独立预检和降级；合集总行显示部分视频已自动降级，展开后显示每个子视频的具体降级信息。
- 具体 `format_id` 不做自动降级；metadata/cookies/network 失败时不伪装成分辨率问题。
- 更新 `README.md`，说明数字清晰度会自动降级并提示，具体格式不会自动替换。

## Test Plan
- 后端：覆盖 playlist 子视频请求 `1080p` 但仅有 `720p/360p` 时自动改用 `720p`、记录降级字段、无更低分辨率时给友好失败、具体 `format_id` 不降级。
- 前端：覆盖单视频和 playlist 子视频自动降级提示；失败无 fallback 时不显示重启/降级按钮。
- 全量验证：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。

## Assumptions
- 自动降级只适用于数字清晰度策略，不适用于具体 `format_id`。
- 自动降级必须可见，不能静默发生。
- playlist 允许不同子视频降级到不同分辨率。

---

# 2026-05-24 +08:00 - 下载任务与相关下载文件删除功能计划

## Summary
增加显式删除入口：每个任务/视频都有“仅删除任务”和“删除任务并删除已下载文件”两个操作。`delete_files=true` 删除该任务已知输出视频及字幕、metadata、缩略图、description、info.json 等相关 sidecar。删除运行中的任务/视频允许立即执行；删除 playlist 最后一个子视频时，同时删除父级 playlist 任务。每个阶段同步更新文档、提交并 `git push origin main`。

## Key Changes
- 后端新增 `POST /api/jobs/{job_id}/items/delete`，请求体为 `{ "item_ids": string[], "delete_files": boolean }`，响应体包含 `deleted_item_ids`、`job_deleted` 和剩余 `job`。
- `JobManager` 增加子视频删除能力，支持删除一个或多个 `JobItem`、刷新父任务聚合状态、删除最后一个子项时删除父任务，并用 `_deleted_items` 取消运行中的子视频。
- 现有 job 删除和批量 job 删除保留，`delete_files=true` 继续删除输出文件及 sidecar，并限制在下载根目录或 job 目录下。
- 前端移除任务中心全局“删除任务时同时删除已下载视频”复选框；任务、playlist 子视频和批量工具栏均提供“仅删除任务”和“删除任务并删除已下载文件”两个显式按钮，删除文件路径需要确认弹窗。
- 同步更新 `docs/` 中 API、用户手册、需求、技术、实现、测试、维护文档和相关 PlantUML 图。

## Test Plan
- 后端：覆盖默认删除保留文件、`delete_files=true` 删除视频和 sidecar、playlist 子视频删除后刷新父任务、删除全部子视频时父任务消失、运行中子视频删除取消下载、无匹配 item 返回 404。
- 前端：覆盖全局复选框不存在、单任务双删除按钮、确认框取消不发请求、playlist 子视频单个/多选删除、新批量双删除按钮。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

## Assumptions
- `ai/brainstorming.md` 是用户未跟踪文件，本次不触碰。
- 删除运行中内容允许立即执行；文件删除基于数据库已知 `output_path`，未记录最终输出路径时只删除任务记录。

---

# 2026-05-24 +08:00 - 任务中心与下载选项 UI 微调计划

## Summary
按 `frontend-design` 要求做克制、清晰的工具型界面优化，不改下载流程或 API。改动范围包括任务中心间距、playlist 展开区层级背景、删除文件按钮图标、限速/并发/cookies 文案，以及默认并发配置。保留未跟踪的 `ai/brainstorming.md` 不动；每个小步验证、提交并 `git push origin main`。

## Key Changes
- 任务中心标题行增加固定留白，避免标题图标被任务列表视觉覆盖。
- Playlist 展开后的子视频区域使用浅色背景、细边框和内边距，单个视频条目继续白底显示。
- “仅删除任务/视频任务”保留 `Trash2`，所有“删除任务并删除已下载文件”入口统一改用 `FileX2` 图标。
- 限速提示改为 `限速 KB/s（清空：不限速）`，避免标签换行；并发提示改为 `并发（若追求稳定，可设为 1）`，系统默认并发改为 5。
- Cookies 文件按钮改为两行“选择 / cookies”；浏览器来源下拉移除可见 label，`自动检测` 选项改为 `自动检测浏览器`。

## Test Plan
- 前端：覆盖 cookies 控件、限速标签、并发标签、任务中心删除入口和现有任务队列交互。
- 后端：覆盖默认并发从环境变量读取，未设置时默认 5，显式环境变量仍优先。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

## Assumptions
- “删除浏览器 cookies 来源文本控件”理解为移除可见 label 文本，不移除浏览器来源下拉框。
- 默认并发改为 5 只影响新默认值；用户已保存的并发设置不迁移、不覆盖。

---

# 2026-05-25 +08:00 - 任务中心播放、打开位置、复制与跳转功能计划

## Summary
为任务中心增加本地播放、打开文件夹、复制链接、跳转 YouTube 页面能力。UI 采用克制的工具型图标按钮；本地播放和打开文件夹通过后端受控 endpoint 调用系统打开器，复制与跳转由前端使用已有 URL 字段完成。`ai/tasks.md` 随每步勾选，`ai/brainstorming.md` 不触碰。

## Key Changes
- 新增受控本机打开能力：后端只根据数据库中的 `JobItem.output_path` 或 `Job.download_dir` 打开文件/目录，不接受任意前端路径。
- 单视频任务和 playlist 子视频支持播放已下载视频；无输出路径或文件不存在时返回明确错误，前端按钮禁用。
- 单视频任务、playlist 任务和 playlist 子视频支持打开所在文件夹。
- 单视频与合集支持复制源链接，并支持跳转到 YouTube 页面。

## Implementation Status
- [x] Step 1：单视频任务和 playlist 子视频播放入口已实现并推送。
- [x] Step 2：单视频任务和 playlist 子视频打开所在文件夹入口已实现并推送。
- [x] Step 3：playlist 任务行打开合集文件夹入口已实现并推送。
- [x] Step 4：单视频、playlist 和子视频复制源链接入口已实现并推送。
- [x] Step 5：单视频、playlist 和子视频跳转 YouTube 页面入口已实现并推送。

## Test Plan
- 后端：覆盖播放视频、打开视频目录、打开 playlist 目录、缺少路径/文件不存在/任务不存在的错误语义。
- 前端：覆盖播放、打开目录、复制链接反馈、跳转 YouTube 页面和按钮禁用状态。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

## Assumptions
- 本应用为本机单用户控制台，后端在用户本机调用系统播放器/文件管理器符合预期。
- “播放视频”只打开主视频文件，不自动打开字幕、metadata、缩略图或 description。

---

# 2026-05-26 +08:00 - Windows 打开文件夹前置修复计划

## Summary
当前“打开视频所在文件夹”和“打开合集文件夹”在 Windows 下可成功打开目录，但 `os.startfile()` 容易复用已有 Explorer 窗口，导致窗口只在任务栏闪烁而不明显弹出。修复目标是在不改变 API 和前端交互的前提下，让目录打开行为更符合用户预期。

## Key Changes
- Windows 下打开目录改为显式调用 `explorer.exe /n, <folder>` 新开 Explorer 窗口，减少只闪烁不前置的情况。
- Windows 下播放视频文件仍使用 `os.startfile()`，保持系统默认播放器关联不变。
- macOS/Linux 保持 `open` / `xdg-open` 行为不变。
- 同步更新 API、用户手册、实现说明和手动验收文档。

## Test Plan
- 新增后端单测覆盖 Windows 目录使用新 Explorer 窗口、Windows 文件仍使用默认文件关联。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

---

# 2026-05-26 +08:00 - 文档启动端口与命令去重计划

## Summary
README 快速启动当前混用了单端口托管和 Vite 开发模式，导致用户只启动后端或使用已构建前端时访问 `5173` 失败，而 `8000` 可正常打开。修复目标是把普通使用、开发模式和测试验证的命令职责拆清楚，并通过本地链接复用，避免 README、用户手册、开发文档和测试文档重复维护同一组命令。

## Key Changes
- README 改为普通用户单端口快速启动：先 `npm run build`，再启动 FastAPI，打开 `http://127.0.0.1:8000`。
- 用户手册不再复制安装命令，只说明启动入口、预期首页效果，并引用 README 和开发文档。
- 开发文档保留开发者安装说明和 Vite 热更新模式，明确 `5173` 只适用于已启动 Vite dev server。
- 测试和维护文档集中链接到测试文档，避免多处复制同一套验证命令。
- 新增首页截图 `docs/assets/screenshots/home.png`，用于说明打开后的预期页面。

## Test Plan
- 通过临时后端托管构建后的前端，确认首页可在后端端口访问并生成截图。
- 运行 `git diff --check`、前端构建和必要的测试验证后提交推送。

---

# 2026-05-29 +08:00 - 播放和打开文件夹误报文件不存在修复计划

## Summary
播放视频和打开视频所在文件夹时，真实视频文件存在但接口返回 `409 视频文件不存在`。根因是 yt-dlp 分离音视频下载会在 progress hook 中报告 `*.f137.mp4`、`*.f140.m4a` 等中间流文件，当前系统把中间文件名持久化到 `JobItem.output_path`；合并完成后真实文件是 `*.mp4`，中间文件可能已删除。本次同时优化前端错误展示，避免本地操作错误只出现在页面顶部。

## Key Changes
- 新增输出路径解析 helper：识别 `*.f<format_id>.<ext>` 中间文件名并解析到合并后的最终文件。
- 下载成功时优先持久化已存在的最终文件路径；历史任务若仍保存中间文件名，播放/打开文件夹接口也会在打开前自动解析。
- 删除文件时也纳入最终文件候选，避免 `delete_files=true` 漏删真实合并产物。
- 前端任务中心对播放/打开文件夹失败采用就地错误提示，显示在对应任务行或 playlist 子视频行附近。
- 同步更新 API、实现、用户手册和测试文档。

## Test Plan
- 后端覆盖中间文件名解析最终文件、相对中间文件名按 job 下载目录解析、分离流下载完成后写入最终路径。
- 前端覆盖单视频任务和 playlist 子视频本地文件操作失败时的就地错误提示。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

---

# 2026-05-29 +08:00 - 视频大小展示与本地播放器选择修复计划

## Summary
任务中心需要在下载前、下载中和下载完成后都显示视频大小；本地播放和打开文件夹不能只在任务栏闪烁，也不能盲目打开无法解码当前视频的默认播放器。本次在不改变 HTTP 请求体的前提下，补齐预检测大小、前台打开和可解码播放器选择。

## Key Changes
- 下载前预检测时从 yt-dlp 选中的格式读取 `filesize/filesize_approx`，写入 `JobItem.total_bytes`；下载中和完成后继续由 progress payload 校准并保留。
- 任务中心任务行显示聚合视频大小，playlist 子视频行显示大小和已下载量。
- Windows 打开文件夹继续使用新 Explorer 窗口，并尽量置前；播放视频优先选择 VLC、mpv、PotPlayer、MPC、IINA 等可确认可解码播放器，常见 MP4 可回退到 Windows Media Player。
- 找不到合适播放器时返回 `409`，错误显示在对应视频 UI 区域，包含当前格式和建议安装的播放器。
- 同步更新用户手册、API、技术、实现、测试、需求和维护文档。

## Test Plan
- 后端覆盖预检测大小写入、requested formats 大小求和、Windows 目录置前调用、播放器选择和无合适播放器错误。
- 前端覆盖任务中心大小展示和本地文件错误就地展示。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

---

# 2026-05-29 +08:00 - 默认清晰度字幕与任务文件关联修复计划

## Summary
将下载选项默认清晰度改为 `1440p`，默认字幕来源改为“两者都要”，并在下载选项面板显示字幕来源、格式和无字幕状态。同时修复任务中心过度依赖 `JobItem.output_path` 的问题，让旧任务、下载中任务和部分下载文件能按 YouTube id 与下载目录中的文件重新关联。

## Key Changes
- 后端 `DownloadOptions` 和应用默认清晰度改为 `1440p`，字幕来源默认 `both`。
- 前端默认清晰度保持 `1440p`，不因解析结果缺少该高度而提前改成其他清晰度；由后端降级策略解释原因。
- 下载选项面板显示字幕来源与格式；若缺少人工或自动字幕，提交前 fallback 到另一种可用来源；若两者都没有，显示无字幕。
- 后端读模型和本地打开接口在 `output_path` 缺失时按文件名中的 YouTube id 发现最终视频；打开文件夹可回退到任务下载目录。
- 删除文件时将按 YouTube id 发现的视频、字幕、metadata 和 `.part` 文件纳入删除候选，仍限制在下载根目录或任务目录内。
- 同步更新用户手册、API、技术、实现、测试、需求和开发文档。

## Test Plan
- 后端覆盖默认值、字幕选项、缺失 `output_path` 时发现已下载视频、播放发现的视频、打开任务目录、删除发现的最终/字幕/部分文件。
- 前端覆盖默认 `1440p`、字幕信息展示、无字幕展示、字幕来源 fallback，以及无最终输出路径时仍可打开任务文件夹。
- 全量验证：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

---

# 2026-05-30 +08:00 - 并发、限速、重试次数即时生效计划

## Summary
并发度已有运行时 worker 调整能力；限速与重试次数此前只作为 `DownloadOptions` 在任务创建或 yt-dlp 实例启动时生效。本次把限速和重试次数升级为全局运行时下载设置，保存后影响新任务、队列任务、暂停任务和正在下载的当前视频项。

## Key Changes
- `SettingsRead` 新增 `default_speed_limit_kbps` 和 `default_retries`，`SettingsUpdate` 支持保存这两个字段；空限速继续表示不限速。
- `PUT /api/settings` 更新限速或重试次数后，`JobManager` 会同步更新 queued/running/paused 任务的 `options_json`，并保留 playlist 子视频已有的分辨率覆盖。
- 对正在 running 的 `JobItem`，后端通过现有取消检查让当前 yt-dlp 实例退出，将该子项重新置为 queued 并重新入队；由于 yt-dlp 保持 `continuedl=True`，重启后按断点续传方式应用新的限速和重试次数。
- 前端“下载选项”中的限速和重试次数从 settings 初始化，用户修改后立即调用 `/api/settings` 保存，并用于后续创建任务。

## Test Plan
- 后端覆盖 settings 默认值、保存与清空限速、运行时任务 options 更新、playlist item 分辨率覆盖保留，以及 running item 收到设置变更后重新入队并使用新参数。
- 前端覆盖限速与重试次数从 settings 初始化、修改后调用 `PUT /api/settings`、创建任务时提交最新值。
- 验证命令：`python -m compileall backend\app`、`python -m pytest backend\tests -q`、`cd frontend && npm test`、`cd frontend && npm run build`、`git diff --check`。

## Assumptions
- “即刻生效”实现为保存后立即取消并重新入队当前正在下载的视频项；若 yt-dlp 正阻塞在底层网络读操作中，会在下一次 progress、重试或 socket timeout 边界退出。

---

# 2026-05-30 +08:00 - 限速与重试保存反馈计划

## Summary
在限速和重试次数升级为运行时设置后，继续补齐与并发设置一致的保存状态反馈，避免用户修改后不知道设置是否已写入后端。

## Key Changes
- “下载选项”中限速和重试次数修改后显示 `保存中...`、`已保存` 或 `保存失败`。
- 快速连续输入时使用请求序号保护，只让最后一次保存响应更新下载选项和反馈状态，避免旧响应覆盖新输入。
- 反馈状态复用现有设置保存样式，不改变下载任务 API、创建任务请求体或下载行为。

## Test Plan
- 前端覆盖保存中、保存成功和保存失败三种反馈状态。
- 保留限速/重试即时调用 `/api/settings`、创建任务提交最新值的测试。
- 完整验证：后端编译与测试、前端测试与构建、`git diff --check`。

---

# 2026-05-30 +08:00 - 4+1 架构视图补全文档计划

## Summary
检查 `docs/` 后确认已有系统上下文、组件、时序、生命周期和数据模型图，但没有按 4+1 架构视图明确组织逻辑、开发、进程、物理和场景视图。本次补齐专门文档与对应 UML 图，并把后续维护规则写入维护文档。

## Key Changes
- 新增 [4+1 架构视图](../../docs/4-plus-1-view.md)，覆盖逻辑视图、开发视图、进程视图、物理视图和场景视图。
- 新增 `four-plus-one-*.puml` 和对应 SVG 图，作为后续架构审查入口。
- 更新 README、文档中心、架构设计和维护文档，要求后续涉及架构、模块边界、运行时流程、部署假设或关键用户场景的变更同步检查 4+1 视图。

## Test Plan
- 渲染新增 PlantUML 为 SVG，确认 Markdown 引用存在。
- 运行 `git diff --check`。

---

# 2026-05-31 +08:00 - 文档写作与生成环境工程化交付计划

## Summary
将文档写作、PlantUML 渲染和一致性检查作为项目工程系统的一部分交付。开发人员不再手工寻找 PlantUML CLI 或临时 jar，而是使用仓库内统一工具入口初始化本地缓存、渲染图和检查文档。

## Key Changes
- 新增 `scripts/docs.py`，提供 `bootstrap`、`render` 和 `check` 三个命令。
- PlantUML 固定使用 Maven Central 的 `plantuml-mit-1.2026.5.jar`，首次执行自动下载到 `.tools/docs/` 并校验 SHA-256；`.tools/` 加入 Git 忽略。
- 新增 `.editorconfig`，统一 Markdown 和 PlantUML 的 UTF-8、LF、末尾换行和行尾空格规则。
- `check` 校验 README 和 `docs/**/*.md` 的本地链接、`.puml` 与 SVG 成对存在，以及 SVG 是否由当前 UML 源生成。
- 新增 [文档写作与生成环境](../../docs/documentation-workflow.md)，同步更新 README、开发、架构、4+1 物理视图、测试和维护文档。

## Test Plan
- `python scripts\docs.py bootstrap`
- `python scripts\docs.py render`
- `python scripts\docs.py check`
- `python -m compileall backend\app scripts`
- 后端、前端完整测试与构建，`git diff --check`。

---

# Download Performance and Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speed up YouTube downloads and reduce SQLite lock contention without weakening the existing anti-403 / anti-bot defaults (single fragment connection, conservative aria2c, cookie/profile fallback).

**Architecture:** Keep yt-dlp as the download engine. Remove redundant YouTube extracts and intra-item `sleep_interval` stalls on the happy path; throttle progress SQLite/SSE writes so five parallel videos do not commit on every yt-dlp callback; enable SQLite WAL so those remaining writes do not serialize the workers.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, SQLite, yt-dlp, pytest.

## Global Constraints

- Stay on `main`; one logical change per commit; `git push origin main` after each task.
- Do not enable `concurrent_fragment_downloads > 1`, multi-connection aria2c, or raise default worker concurrency.
- Keep `continuedl=True`, `http_chunk_size=16MiB`, `fragment_retries=20`, and the 403 profile chain.
- `sleep_interval_requests=1.0` stays on extract and download player-API requests (bot pacing).
- Resolution fallback reasons (`requested_resolution_missing`, `source_below_720_only`, `requested_resolution_unselectable`) must keep current user-visible meaning.
- TDD: failing test first; docs in the same task as the behavior change.
- Do not kill IncrediBuild; local uvicorn may use port 8001 if 8000 is taken.

## File Structure

- `backend/app/infra/db.py` — SQLite engine; enable WAL + busy timeout on connect.
- `backend/app/download/progress_persist.py` — decide whether a progress snapshot should hit SQLite/SSE.
- `backend/app/download/job_manager.py` — use the persist gate; skip extra `extract_metadata` when `prepare_download` already succeeds; merge missing-resolution fallback into `_prepare_download`.
- `backend/app/download/ytdlp_service.py` — stop applying playlist `sleep_interval` to single-URL downloads (`noplaylist=True`).
- `backend/tests/test_db.py` — WAL / busy_timeout.
- `backend/tests/test_progress_persist.py` — persist gate unit tests.
- `backend/tests/test_ytdlp_service.py` — sleep option assertions.
- `backend/tests/test_api.py` + `backend/tests/fakes.py` — happy-path extract count and fallback fakes.
- Docs: `docs/technical.md`, `docs/implementation.md`, `docs/development.md`, `docs/testing.md`, `docs/user-manual.md`, `docs/safety-review.md`, `docs/4-plus-1-view.md`, `docs/diagrams/four-plus-one-process-view.puml`, `docs/diagrams/four-plus-one-development-view.puml`, `docs/diagrams/single-video-sequence.puml`.

## Out of scope

- Raising `YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS` (YouTube 403 risk).
- Default-on aria2c or `-x` > 1.
- Caching yt-dlp `info_dict` from `prepare_download` into `download()` (yt-dlp still extracts on download; invasive).
- Changing default `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS` (already 5, item-level).

## Current bottlenecks (why these tasks)

1. `_run_item` always calls `_options_for_available_resolution` → `extract_metadata`, then `prepare_download` → another `extract_info`. Happy-path videos pay two YouTube extracts before the first media byte, plus the job-create extract.
2. `sleep_interval=2` / `max_sleep_interval=5` apply even with `noplaylist=True`, so video+audio+subtitle files of one item sleep 2–5s between each file.
3. Every yt-dlp progress callback opens a SQLModel session, commits, and publishes SSE. Five workers × dense hooks contend on SQLite DELETE journal mode.
4. Safety review already flagged WAL as the fix for concurrent progress writes.

---

### Task 1: SQLite WAL and busy timeout

**Files:**
- Modify: `backend/app/infra/db.py`
- Create: `backend/tests/test_db.py`
- Modify: `docs/implementation.md`, `docs/development.md`, `docs/safety-review.md`, `docs/4-plus-1-view.md`, `docs/diagrams/four-plus-one-process-view.puml`

**Interfaces:**
- Consumes: `create_app_engine(settings: AppSettings) -> Engine`
- Produces: every SQLite connection runs `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA synchronous=NORMAL`

- [x] **Step 1: Write the failing test**

Create `backend/tests/test_db.py`:

```python
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from app.config import AppSettings
from app.db import create_app_engine, init_db


def test_sqlite_engine_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )
    engine = create_app_engine(settings)
    init_db(engine)

    with Session(engine) as session:
        journal_mode = session.exec(text("PRAGMA journal_mode")).one()
        busy_timeout = session.exec(text("PRAGMA busy_timeout")).one()
        synchronous = session.exec(text("PRAGMA synchronous")).one()

    assert str(journal_mode[0]).lower() == "wal"
    assert int(busy_timeout[0]) == 5000
    assert int(synchronous[0]) == 1
```

SQLite `synchronous=NORMAL` is integer `1`. `journal_mode` returns `wal`.

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend\tests\test_db.py::test_sqlite_engine_enables_wal_and_busy_timeout -v`

Expected: FAIL because `journal_mode` is `delete` (or not `wal`).

- [x] **Step 3: Write minimal implementation**

In `backend/app/infra/db.py` add SQLAlchemy connect hook:

```python
from collections.abc import Generator

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import AppSettings


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_app_engine(settings: AppSettings) -> Engine:
    settings.ensure_directories()
    sqlite_url = f"sqlite:///{settings.database_path.as_posix()}"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine
```

Keep `init_db` and `session_dependency` unchanged.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend\tests\test_db.py::test_sqlite_engine_enables_wal_and_busy_timeout -v`

Expected: PASS

- [x] **Step 5: Sync docs**

In `docs/implementation.md` persistence section, state that `create_app_engine` enables WAL, `busy_timeout=5000`, and `synchronous=NORMAL` so concurrent progress writes wait instead of raising `database is locked`.

In `docs/development.md` SQLite / env section, note WAL sidecar files `app.sqlite3-wal` and `app.sqlite3-shm` live next to `YTDL_DATABASE_PATH` and must stay gitignored.

In `docs/safety-review.md` item 5, mark WAL as implemented via `create_app_engine` connect pragma.

In `docs/diagrams/four-plus-one-process-view.puml`, change persist step to mention WAL SQLite.

In `docs/4-plus-1-view.md` process view paragraph, mention WAL.

- [x] **Step 6: Commit and push**

```powershell
python -m pytest backend\tests\test_db.py -q
git add backend/app/infra/db.py backend/tests/test_db.py docs/implementation.md docs/development.md docs/safety-review.md docs/4-plus-1-view.md docs/diagrams/four-plus-one-process-view.puml
git commit -m "feat: enable SQLite WAL for concurrent download writes"
git push origin main
```

---

### Task 2: Throttle progress SQLite and SSE writes

**Files:**
- Create: `backend/app/download/progress_persist.py`
- Create: `backend/tests/test_progress_persist.py`
- Modify: `backend/app/download/job_manager.py` (`_run_item` progress_hook)
- Modify: `docs/implementation.md`, `docs/testing.md`, `docs/diagrams/four-plus-one-development-view.puml`, `docs/diagrams/single-video-sequence.puml`

**Interfaces:**
- Consumes: `DownloadProgressAggregator.update` still runs on every payload; `TransferStats.record` still runs on every byte update.
- Produces: `ProgressPersistGate.allow(status: str | None, progress: float, now: float) -> bool`

Constants:

```python
PROGRESS_PERSIST_MIN_INTERVAL_SECONDS = 0.25
PROGRESS_PERSIST_MIN_DELTA = 0.5
```

Rules:

- Always allow the first snapshot.
- Always allow `status in {"finished", "error"}`.
- Allow when `now - last_allowed >= 0.25`.
- Allow when `abs(progress - last_progress) >= 0.5`.
- Otherwise skip SQLite commit and SSE. Do not skip aggregator or transfer stats.

- [x] **Step 1: Write the failing tests**

Create `backend/tests/test_progress_persist.py`:

```python
from app.progress_persist import ProgressPersistGate


def test_progress_persist_gate_allows_first_and_finished() -> None:
    gate = ProgressPersistGate(min_interval_seconds=1.0, min_progress_delta=50.0)
    assert gate.allow(status="downloading", progress=1.0, now=0.0) is True
    assert gate.allow(status="downloading", progress=1.1, now=0.01) is False
    assert gate.allow(status="finished", progress=1.1, now=0.02) is True


def test_progress_persist_gate_allows_interval_or_progress_jump() -> None:
    gate = ProgressPersistGate(min_interval_seconds=0.25, min_progress_delta=0.5)
    assert gate.allow(status="downloading", progress=10.0, now=0.0) is True
    assert gate.allow(status="downloading", progress=10.2, now=0.05) is False
    assert gate.allow(status="downloading", progress=10.8, now=0.06) is True
    assert gate.allow(status="downloading", progress=10.9, now=0.10) is False
    assert gate.allow(status="downloading", progress=11.0, now=0.40) is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend\tests\test_progress_persist.py -v`

Expected: FAIL with `ModuleNotFoundError: app.progress_persist`

- [x] **Step 3: Implement the gate**

Create `backend/app/download/progress_persist.py`:

```python
PROGRESS_PERSIST_MIN_INTERVAL_SECONDS = 0.25
PROGRESS_PERSIST_MIN_DELTA = 0.5


class ProgressPersistGate:
    def __init__(
        self,
        min_interval_seconds: float = PROGRESS_PERSIST_MIN_INTERVAL_SECONDS,
        min_progress_delta: float = PROGRESS_PERSIST_MIN_DELTA,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.min_progress_delta = min_progress_delta
        self._last_allowed_at: float | None = None
        self._last_progress: float | None = None

    def allow(self, *, status: str | None, progress: float, now: float) -> bool:
        if status in {"finished", "error"} or self._last_allowed_at is None:
            self._remember(progress, now)
            return True
        elapsed = now - self._last_allowed_at
        delta = abs(progress - (self._last_progress or 0.0))
        if elapsed >= self.min_interval_seconds or delta >= self.min_progress_delta:
            self._remember(progress, now)
            return True
        return False

    def _remember(self, progress: float, now: float) -> None:
        self._last_progress = progress
        self._last_allowed_at = now
```

- [x] **Step 4: Wire the gate into `job_manager.py`**

Add imports:

```python
import time

from .progress_persist import ProgressPersistGate
```

Inside `_run_item`, next to `progress_aggregator = DownloadProgressAggregator()`:

```python
persist_gate = ProgressPersistGate()
```

At the start of `progress_hook`, after computing `progress = progress_aggregator.update(payload)` and recording transfer stats, return early when the gate rejects:

```python
def progress_hook(payload: dict[str, Any]) -> None:
    if job.id in self._deleted:
        return
    status = payload.get("status")
    progress = progress_aggregator.update(payload)
    if progress.downloaded_bytes is not None:
        transfer_stats.record(progress.downloaded_bytes)
    if not persist_gate.allow(status=status, progress=progress.progress, now=time.monotonic()):
        return
    with Session(self.engine) as hook_session:
        # existing persist + SSE body unchanged
```

Move `status = payload.get("status")` before the session (it is currently inside the session). Keep the rest of the hook body.

- [x] **Step 5: Run tests to verify they pass**

Run:

```powershell
python -m pytest backend\tests\test_progress_persist.py backend\tests\test_download_progress.py backend\tests\test_api.py -q
```

Expected: PASS. Intermediate UI polls in `test_api.py` still work because the first downloading snapshot and every `finished` payload persist.

- [x] **Step 6: Sync docs**

`docs/implementation.md` progress section: aggregator still updates every payload; SQLite/SSE persist at most about 4 Hz unless progress jumps ≥ 0.5% or the payload is `finished`/`error`.

`docs/testing.md` table: add `test_progress_persist.py` and `test_db.py`.

`docs/diagrams/four-plus-one-development-view.puml`: add `[progress_persist.py]` and `Manager --> PersistGate`.

`docs/diagrams/single-video-sequence.puml`: `Manager -> DB : throttled progress persist`.

- [x] **Step 7: Commit and push**

```powershell
git add backend/app/download/progress_persist.py backend/app/download/job_manager.py backend/tests/test_progress_persist.py docs/implementation.md docs/testing.md docs/diagrams/four-plus-one-development-view.puml docs/diagrams/single-video-sequence.puml
git commit -m "perf: throttle download progress database writes"
git push origin main
```

---

### Task 3: Skip redundant extract_metadata on the happy path

**Files:**
- Modify: `backend/app/download/job_manager.py` (`_run_item`, `_prepare_download`; delete `_options_for_available_resolution`)
- Modify: `backend/tests/fakes.py`
- Modify: `backend/tests/test_api.py`
- Modify: `docs/implementation.md`, `docs/technical.md`, `docs/diagrams/playlist-sequence.puml`

**Interfaces:**
- Consumes: `YtDlpService.prepare_download(...)` and `extract_metadata(...)` (unchanged signatures)
- Produces: `_prepare_download` is the only download-time resolution gate. Happy path calls `prepare_download` only. Missing/unselectable paths call `extract_metadata` once, then `prepare_download` with fallback options.

- [x] **Step 1: Write the failing test and update fallback fakes so prepare reflects selectable formats**

In `backend/tests/fakes.py`, add `item_extract_urls` counting to `FakeYtDlpService` subclasses used for fallback, and add a happy-path counter service.

Add to `SingleAutoFallbackYtDlpService` and `AutoFallbackYtDlpService`:

```python
def prepare_download(self, url, options, cookies_path=None):
    if options.resolution in {"1080p", "1440p", "2160p"}:
        if getattr(self, "_url_missing_1080", lambda u: False)(url):
            return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)
        if "two" in url:
            return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)
        if url.endswith("unsupported") or "low-source" in url:
            return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)
    if options.resolution == "720p":
        return SimpleNamespace(is_selectable=True, width=1280, height=720, actual_format="mp4 · avc1 + mp4a")
    if options.resolution == "360p":
        return SimpleNamespace(is_selectable=True, width=640, height=360, actual_format="mp4 · avc1 + mp4a")
    return SimpleNamespace(is_selectable=True, width=None, height=None, actual_format=None)
```

Use explicit per-class methods instead of one shared helper:

`SingleAutoFallbackYtDlpService.prepare_download`:

```python
def prepare_download(self, url, options, cookies_path=None):
    if options.resolution == "720p":
        return SimpleNamespace(is_selectable=True, width=1280, height=720, actual_format="mp4 · avc1 + mp4a")
    return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)
```

`AutoFallbackYtDlpService.prepare_download`:

```python
def prepare_download(self, url, options, cookies_path=None):
    if "two" in url and options.resolution == "1080p":
        return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)
    height = 720 if options.resolution == "720p" else 1080
    return SimpleNamespace(
        is_selectable=True,
        width=int(height * 16 / 9),
        height=height,
        actual_format="mp4 · avc1 + mp4a",
    )
```

Add `HappyPathExtractCountingYtDlpService(FakeYtDlpService)`:

```python
class HappyPathExtractCountingYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.extract_urls: list[str] = []
        self.prepare_urls: list[str] = []

    def extract_metadata(self, url, cookies_path=None):
        self.extract_urls.append(url)
        return AnalyzeResponse(
            url=url,
            title="Counted",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def prepare_download(self, url, options, cookies_path=None):
        self.prepare_urls.append(url)
        return SimpleNamespace(
            is_selectable=True,
            width=1920,
            height=1080,
            actual_format="mp4 · avc1 + mp4a",
            filesize=1_000_000,
        )
```

In `backend/tests/test_api.py` import the new fake and add:

```python
def test_happy_path_download_does_not_extract_metadata_again(tmp_path: Path) -> None:
    service = HappyPathExtractCountingYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/counted",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        wait_for_job_status(client, response.json()["id"], "succeeded")

    assert service.extract_urls == ["https://youtu.be/counted"]
    assert service.prepare_urls == ["https://youtu.be/counted"]
```

Today this fails because `_options_for_available_resolution` appends a second extract.

- [x] **Step 2: Run the new test to verify it fails**

Run: `python -m pytest backend\tests\test_api.py::test_happy_path_download_does_not_extract_metadata_again -v`

Expected: FAIL `assert ['https://youtu.be/counted', 'https://youtu.be/counted'] == ['https://youtu.be/counted']`

- [x] **Step 3: Merge resolution fallback into `_prepare_download` and stop calling `_options_for_available_resolution`**

In `_run_item`, delete:

```python
options = self._options_for_available_resolution(session, item, options)
```

Keep:

```python
options = self._prepare_download(session, item, options)
```

Replace `_prepare_download` with:

```python
def _prepare_download(self, session: Session, item: JobItem, options: DownloadOptions) -> DownloadOptions:
    preparation = self.service.prepare_download(item.source_url, options, cookies_path=self._cookies_path())
    if preparation.is_selectable:
        self._apply_download_preparation(session, item, preparation)
        return options
    if options.format_id or YtDlpService._resolution_height(options.resolution) is None:
        return options

    try:
        analysis = self.service.extract_metadata(item.source_url, cookies_path=self._cookies_path())
    except Exception:
        analysis = None

    requested_height = YtDlpService._resolution_height(options.resolution)
    available_heights = {
        int(format.height)
        for format in (analysis.formats if analysis is not None else [])
        if format.height is not None
    }
    height_missing = analysis is None or requested_height not in available_heights
    allow_below_min = height_missing
    fallback = None
    if analysis is not None:
        fallback = YtDlpService.suggest_lower_resolution(
            options.resolution,
            analysis.formats,
            allow_below_min_if_source_below_min=allow_below_min,
        )
    if not fallback:
        if height_missing:
            raise RuntimeError(self._no_supported_fallback_message(options.resolution))
        raise RuntimeError(self._unselectable_resolution_message(options.resolution))

    if height_missing:
        reason = (
            SOURCE_BELOW_720_ONLY
            if analysis is not None and not YtDlpService.has_resolution_at_or_above(analysis.formats)
            else REQUESTED_RESOLUTION_MISSING
        )
    else:
        reason = REQUESTED_RESOLUTION_UNSELECTABLE

    fallback_options = self._options_with_resolution(options, fallback)
    fallback_preparation = self.service.prepare_download(
        item.source_url,
        fallback_options,
        cookies_path=self._cookies_path(),
    )
    if not fallback_preparation.is_selectable:
        raise RuntimeError(self._unselectable_resolution_message(options.resolution))

    self._set_resolution_fallback(item, options.resolution, fallback, reason)
    item.error = None
    item.updated_at = utc_now()
    session.add(item)
    session.commit()
    self._apply_download_preparation(session, item, fallback_preparation)
    return fallback_options
```

Delete `_options_for_available_resolution` entirely. Keep `_fallback_resolution_for_item` for media-stream / post-error annotation.

If `analysis is None` (extract failed) and prepare already failed, raise the unselectable/no-fallback message rather than downloading a guessed height.

- [x] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest backend\tests\test_api.py -q
```

Expected: PASS, including existing fallback tests (`requested_resolution_missing`, `source_below_720_only`, `requested_resolution_unselectable`).

- [x] **Step 5: Sync docs**

`docs/implementation.md` 清晰度与降级: remove `_options_for_available_resolution`. Document that download-time `extract_metadata` runs only after `prepare_download` reports not selectable.

`docs/technical.md` 下载前预检测: same order — `prepare_download` first; extract only for fallback classification.

`docs/diagrams/playlist-sequence.puml`: worker loop is `prepare_download` then `download`; extract only on fallback.

- [x] **Step 6: Commit and push**

```powershell
git add backend/app/download/job_manager.py backend/tests/fakes.py backend/tests/test_api.py docs/implementation.md docs/technical.md docs/diagrams/playlist-sequence.puml
git commit -m "perf: skip extra YouTube extract when format is selectable"
git push origin main
```

---

### Task 4: Do not sleep 2–5s between files of one video

**Files:**
- Modify: `backend/app/download/ytdlp_service.py` (`build_download_options`)
- Modify: `backend/tests/test_ytdlp_service.py`
- Modify: `docs/technical.md`, `docs/user-manual.md`

**Interfaces:**
- Consumes: `build_download_options(...)` existing signature
- Produces: download opts keep `sleep_interval_requests=1.0`; they do **not** set `sleep_interval` or `max_sleep_interval`. Extract metadata opts are unchanged.

Why: `JobManager` already spaces videos via item-level concurrency. yt-dlp `sleep_interval` still fires between video, audio, subtitle, and thumbnail files of a **single** `noplaylist` download, adding 2–5 seconds of idle time per sidecar.

- [x] **Step 1: Write the failing test**

Replace `test_download_options_apply_conservative_youtube_request_pacing` assertions in `backend/tests/test_ytdlp_service.py`:

```python
def test_download_options_pace_player_requests_but_not_intra_item_files(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["sleep_interval_requests"] == 1.0
    assert "sleep_interval" not in opts
    assert "max_sleep_interval" not in opts
```

Keep `test_extract_metadata_maps_playlist_entries_formats_and_subtitles` asserting `captured_opts["sleep_interval_requests"] == 1.0`.

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend\tests\test_ytdlp_service.py::test_download_options_pace_player_requests_but_not_intra_item_files -v`

Expected: FAIL because `sleep_interval` is `2.0`.

- [x] **Step 3: Remove playlist sleep from download options**

In `build_download_options`, delete:

```python
"sleep_interval": YTDLP_DOWNLOAD_SLEEP_SECONDS,
"max_sleep_interval": YTDLP_MAX_DOWNLOAD_SLEEP_SECONDS,
```

Keep `sleep_interval_requests`. Leave `YTDLP_DOWNLOAD_SLEEP_SECONDS` and `YTDLP_MAX_DOWNLOAD_SLEEP_SECONDS` unused only if nothing else references them — then delete the unused constants to avoid dead code.

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend\tests\test_ytdlp_service.py -q`

Expected: PASS

- [x] **Step 5: Sync docs**

`docs/technical.md` 稳定下载策略: document that worker concurrency is the video-level throttle; yt-dlp no longer sleeps 2–5s between files of one item; player-API `sleep_interval_requests` remains 1s.

`docs/user-manual.md` 下载选项 / 进度: note that a single video's audio merge should start without a multi-second pause after the video stream finishes; if YouTube starts returning 403 more often, lower concurrency to 1 rather than re-adding intra-item sleep.

- [x] **Step 6: Commit and push**

```powershell
git add backend/app/download/ytdlp_service.py backend/tests/test_ytdlp_service.py docs/technical.md docs/user-manual.md
git commit -m "perf: remove intra-item yt-dlp sleep between media files"
git push origin main
```

---

### Task 5: Full verification

- [ ] **Step 1: Run the full backend suite**

```powershell
python -m compileall backend\app
python -m pytest backend\tests -q
git diff --check
```

Expected: compile OK, all tests PASS, no whitespace errors.

- [ ] **Step 2: If any doc/UML sources changed in earlier tasks, leave SVG stale when Graphviz `dot` is missing**; do not block on `python scripts\docs.py render`.

- [ ] **Step 3: No extra commit unless verification found a gap.** If a test or doc gap appears, fix it in a dedicated commit and push.

---

## Self-review

1. Spec coverage: WAL → Task 1; progress lock contention → Task 2; duplicate extract → Task 3; intra-item sleep → Task 4; fragment/aria2c defaults explicitly out of scope.
2. Placeholder scan: no TBD/TODO; tests and implementation code are inlined.
3. Type consistency: `ProgressPersistGate.allow(status=, progress=, now=)` is the only persist API; `_prepare_download` remains the method later tasks and docs name.