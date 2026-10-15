# 设计：视频自动上传 bilibili

适用读者：要判断「这段逻辑该放哪一层」「改动会不会破坏运行时假设」的开发者。

本页假定你已读过 [01-requirements.md](01-requirements.md) 的「技术事实」一节 —— 那里的 F2/F4/F5/F9/F10
是下面全部取舍的前提。

## 1. 技术选型

### 1.1 提交工具：沿用 `biliup-rs 0.2.4`（不自研上传协议）

| 方案 | 结论 | 依据 |
| --- | --- | --- |
| **`biliup-rs 0.2.4`（选定）** | 本机已装、arXiv-daily 已实测成功、命令行契约稳定（F2/F3/F6）。 | 不引入新依赖，风险最低。 |
| 继任版 `biliup/biliup`（有原生 `season add`） | **不采用。** 无 winget 包（F5），它是一个含服务端与 Web 前端的大型产品，替换会改变部署与运维面；而我们要的「归集」只是 3 个 HTTP 调用，自研成本远低于换工具链。 | F5 |
| 自研上传协议（直接打 B站 分片上传接口） | **不采用。** 分片上传 + 提交的协议面远大于归集，且 B站 侧频繁变更；重复实现 `biliup` 已解决的工程问题没有收益。 | F1、F3 |

> 判断（非事实）：`biliup-rs` 已归档（上游迁移到 `biliup/biliup`），存在长期失效风险。
> 风险已登记（requirements R3），并且**所有对 `biliup` 的依赖都收敛在 `bili_bin.py` 一个模块**，
> 将来换工具只需改这一处与它的单测。

### 1.2 合集归集：自研 3 个 member API 调用

`biliup-rs 0.2.4` 不支持 `season_id`（F4），所以合集只能由本功能自己归集：

| 步骤 | 端点 | 说明 |
| --- | --- | --- |
| 创建合集 | `POST /x2/creative/web/season/add` | form-urlencoded：`title/desc/cover/season_price=0/csrf`；返回 `data` = season_id。**按 F9 会先进入人工审核。** |
| 添加稿件 | `POST /x2/creative/web/season/section/episodes/add?csrf=…` | JSON：`{"sectionId": <int>, "episodes": [{"aid","cid","title","charging_pay":0}]}`。参数名是 `episodes`/`sectionId`（F10）。 |
| 列出合集（体检/复核用） | `GET /x2/creative/web/seasons?pn=1&ps=30` | 只读，用于把 season_id 反查成人可读的合集标题。 |

**为什么不做「多 P 单稿件」**：多 P 在 Web 端受账号权重限制（F14），且全部条目共享一组元数据，
与「为每个视频生成元数据」的需求直接冲突。逐个投稿为独立稿件既绕开权重限制，又让每条视频有独立标题与封面。

### 1.3 登录：自建扫码轮询，不用 `biliup login`

`biliup login` 要求交互终端，沙箱与无人值守场景不可用（requirements R6）。
改为直连 `passport.bilibili.com` 的二维码生成/轮询接口，把 `biliup` 认识的 `LoginInfo` 结构写盘；
这条链路 arXiv 项目已实测跑通（`tools/bili_qr_login.py`）。

## 2. 总体结构与数据流

```
前端（任务中心）                         后端
────────────────────────────────────────────────────────────────
[B站 图标 + 上传]  ──POST /api/bilibili/uploads──────────┐
[合集模板弹窗]     ──POST /api/bilibili/collections──────┤
                                                         ▼
                                              BiliManager（L2 编排）
                                              队列 → worker（工作线程）
                                                         │
                                    ┌────────────────────┼────────────────────┐
                                    ▼                    ▼                    ▼
                            bili_meta（元数据）   bili_bin（biliup 进程）  bili_api（HTTP）
                                    │                    │                    │
                                    └────────► bili_upload（L3 单次上传逻辑）◄┘
                                                         │
                                              写 BiliUpload / BiliSeason 表
                                                         │
                                              EventBroker.publish ──► SSE ──► 前端重拉
```

**一次合集上传的完整时序**（`bili_upload.run_collection`）：

```
for 每条视频（串行，条间 sleep gap_seconds）:
    meta  = bili_meta.render(template, item)          # 生成该视频的标题/简介/标签/分区
    cover = bili_bin? 否：bili_cover 抽帧（可选）
    rc, log = bili_bin.upload(video, meta, cover)     # 调 biliup
    若 rc != 0 → 该条 failed（分类：601 频控 / 未登录 / 其他），继续下一条
    arc = bili_api.find_archive(title)                # 对账（可能有延迟 → 退避重试 N 次）
    若对账不到 → 该条 unverified（不谎报成功），继续下一条
    写 BiliUpload(status=published|reviewing|unverified, aid, bvid, cid)

若至少有 1 条拿到 aid:
    season_id = bili_api.create_season(title, desc, cover_url)     # 需稿件封面 URL
    bili_api.add_episodes(season_id, [(aid, cid, title), ...])     # 只加已就绪的
    写 BiliSeason(status=grouped | pending | failed)
```

**这里有一条必须说清的语义**：平台对合集有**人工审核**（F9），所以合集能到达的终态是
「已提交审核」，不是「已通过」。界面文案与文档一律照此表述。

## 3. 模块分层落位

新增模块按下表落位，并**必须**同步登记进 [docs/design.md](../../../docs/design.md) 的模块职责矩阵
（`scripts/check_layers.py` 会把「未登记」判为失败）。

| 模块 | 层 | 职责 | 不负责 |
| --- | --- | --- | --- |
| `bili_bin.py` | L3 领域 | `biliup` 可执行探测（含 winget 0 字节空壳识别）、命令组装、子进程执行、输出解析、错误分类（601 频控等）。 | 不写库、不发 B站 HTTP、不生成元数据。 |
| `bili_api.py` | L3 领域 | B站 会员态 HTTP 客户端：稿件对账、上传封面、创建合集、添加稿件到合集、列出合集、发/置顶评论。全部经**可注入 transport**。 | 不调 `biliup`、不读库、不决定「该不该归集」。 |
| `bili_auth.py` | L3 领域 | 扫码登录（生成二维码 / 轮询状态）、cookie 文件读写与三种格式兼容、凭据摘要（**只回传存在性与掩码**）。 | 不发 B站 业务请求、不写库。 |
| `bili_meta.py` | L3 领域 | 投稿元数据模板（占位符渲染）与平台硬限校验/截断、分区（tid）词表。 | 不计长度之外的事，不读库、不发请求。 |
| `bili_upload.py` | L3 领域 | **单次上传的领域逻辑**：编排 `bili_bin` + `bili_api` + `bili_meta` + `bili_auth`，返回结构化结果（含状态与下一步文案）。纯函数式，**不写库、不发事件**。 | 不做队列、不做状态持久化。 |
| `bili_manager.py` | L2 编排 | 上传队列与 worker、串行与间隔、状态写库与收敛、进度事件发布、失败分类落账、重启后状态可读。 | 不直接调 `biliup`（经 `bili_upload`），不解析 B站 内部结构。 |
| `routers/bilibili.py` | L0 入口 | 声明 B站 相关 HTTP 路由：取参、调辅助、定状态码。 | 不做状态转换，不写领域规则。 |
| `bili_policy.py` | L3 领域 | B站 上传的**文案与分类常量**：状态码→人话、错误类别→可执行建议。 | 不做判定（判定在 `bili_upload` / `bili_manager`）。 |
| `bili_http.py` | **L4 基础设施** | B站 请求的 HTTP 接缝：标准库 `urllib` 实现、强制直连、4xx/5xx 也带回正文与 `Set-Cookie`。 | 不知道任何端点的语义（端点在 `bili_api` / `bili_auth`）。 |
| `bili_read_model.py` | L2 编排 | 把两张表投影成 API 读模型；**「多条记录 → 一档徽标」的聚合规则只此一处**。 | 不写库、不改状态。 |

分层位置与依赖方向：`routers/bilibili` → `bili_manager`（L2）→ `bili_upload`（L3）→
`bili_bin` / `bili_api` / `bili_auth` / `bili_meta` / `bili_policy`（L3）→ `bili_http`（L4）。
横向依赖有两组：`bili_upload` → 其余四个 L3 模块（复用而非成环），以及
`bili_read_model` → `job_read_model`（复用 `elapsed_seconds`），与既有
`job_manager` → `ytdlp_formats` 的先例同构。两组都逐条登记在
[docs/design.md](../../../docs/design.md) 的同层横向依赖清单里。

## 4. 数据模型

两张新表，随 `SQLModel.metadata.create_all` 建出（新表不需要 `_ensure_columns` 补列）。

### `bili_upload`（稿件级：一次投稿 = 一行）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str PK | 记录 id。 |
| `job_id` / `item_id` | str / str? | 关联的下载任务与条目（单视频任务的 `item_id` 是其唯一条目）。 |
| `title` / `desc` / `tags` / `tid` | str | **实际提交**的元数据快照（用于对账与复现）。 |
| `video_path` / `cover_path` / `cover_url` | str | 视频路径、本地封面、封面在 B站 的 URL。 |
| `status` | str index | 见 §5。 |
| `progress` | float | 0~1，`biliup` 输出解析出的粗粒度进度。 |
| `bvid` / `aid` / `cid` | str?/int?/int? | 对账读回的平台标识。 |
| `verified_state` / `verified_note` | str? | 平台 `state` 原值 + 人话解释。 |
| `error` / `advice` | str? | 可读原因 + **下一步**（验收口径要求）。 |
| `log_tail` | str? | `biliup` 输出尾部（经 `log_safety` 清洗）。 |
| `attempt` | int | 第几次尝试（重试计数）。 |
| `created_at` / `updated_at` / `started_at` / `finished_at` | datetime | 时间线。 |

### `bili_season`（合集级：一次归集 = 一行）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str PK | 记录 id。 |
| `job_id` | str index | 被归集的那个下载任务。 |
| `title` / `desc` / `cover_path` / `cover_url` | str | 合集元数据。 |
| `season_id` | int? | 平台返回的合集 id。 |
| `status` | str | `pending` / `grouping` / `grouped` / `failed`。 |
| `upload_ids_json` | str | 参与归集的稿件记录 id（**保序**，用于重试与展示）。 |
| `error` / `advice` / `log_tail` | str? | 同稿件表。 |
| `created_at` / `updated_at` / `started_at` / `finished_at` | datetime | 时间线。 |

**为什么合集状态单独一张表**：稿件的成败与合集的成败是**两个独立生命周期** —— 稿件可以全部成功而归集失败
（requirements FR-B9）。塞进一行会让「合集图标状态」与「单条视频状态」互相污染。

## 5. 状态机

### 5.1 稿件（`bili_upload.status`）

```
queued ──► uploading ──► submitted ──┬─► published   对账：已发布/开放浏览
   │            │                     ├─► reviewing   对账：审核中/仅自己可见等非终态
   │            │                     ├─► rejected    对账：审核不通过
   │            │                     └─► unverified  对账查不到（**不谎报成功**）
   └────────────┴─────────────────────► failed       可分类且带 advice
```

`unverified` 是刻意引入的一档：`biliup` 返回 0 只说明「命令跑完了」（F7），
对账查不到时必须停在「待核对」，而不是折进 `published`。

### 5.2 合集（`bili_season.status`）

```
pending ──► grouping ──┬─► grouped   已提交归集（含平台审核中）
   ▲                   └─► failed    可重试
   └─────────────────────────── 重试
```

### 5.3 界面状态（读模型派生，单一来源在后端）

界面只需要 5 档，由后端读模型算出**状态 + 人话文案**（沿用 `fallback_policy` 的先例，文案留在领域层）：

| 界面状态 | 派生规则 | 文案 |
| --- | --- | --- |
| （无） | 没有任何记录 | —— |
| `uploading` | 存在 `queued/uploading/submitted/grouping` | 上传中 |
| `uploaded` | 单视频：`published/reviewing`；合集：`grouped` | 已上传 |
| `unverified` | 全部 `unverified` | 待核对 |
| `partial` | 有成功也有失败 | 部分失败 |
| `failed` | 全部失败，或合集 `failed` | 失败 |

## 6. HTTP API

单一来源仍是运行时 `app.openapi()`；`frontend/src/types.ts` 与 `docs/openapi.yaml` 是手写副本，
由 `scripts/check_api_contract.py` 校验无漂移。新增接口必须同时更新三处。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/bilibili/status` | 环境体检：`biliup` / `ffmpeg` / cookie / 登录态 + 缺项清单（人话）。 |
| POST | `/api/bilibili/login/qrcode` | 生成登录二维码，返回 `qrcode_key` 与 **SVG** 二维码（内联文本，不是 base64 位图：`qrcode` 的 SVG 工厂不牵入 Pillow，见 §12）。 |
| GET | `/api/bilibili/login/poll` | 轮询扫码状态（waiting/scanned/confirmed/expired）。 |
| POST | `/api/bilibili/cookies` | 导入已有 cookies.json（multipart）。 |
| DELETE | `/api/bilibili/cookies` | 清除登录态。 |
| GET | `/api/bilibili/prefill` | 预填元数据：单视频给一组值；合集给「模板 + 逐条预览」。 |
| POST | `/api/bilibili/uploads` | 启动单视频上传。 |
| POST | `/api/bilibili/collections` | 启动合集上传（模板驱动，逐个投稿后自动归集）。 |
| GET | `/api/bilibili/uploads` | 按任务读回上传状态（任务级 + 条目级）。 |
| POST | `/api/bilibili/uploads/{upload_id}/retry` | 重试单条稿件（幂等：已成功的条目不重复投）。 |
| POST | `/api/bilibili/collections/{season_record_id}/group` | 重试合集归集。 |

失败一律返回可读 `detail`；未登录、`biliup` 缺失等**前置条件不满足**返回 409，参数不合法返回 400
（与既有 `cookie_import_status_code` 的处置口径一致：状态冲突 409、请求本身不行 400）。

## 7. 元数据模板

合集模板有五个字段，其中标题与简介支持占位符：

| 占位符 | 展开为 |
| --- | --- |
| `{index}` | 该视频在合集里的序号（从 1 开始，两位补零：`01`） |
| `{title}` | 该视频的标题（已去扩展名） |
| `{count}` | 合集总条数 |
| `{job}` | 下载任务标题（即合集名） |
| `{source}` | 该视频自己的原始链接 |

渲染后按平台硬限处理（F13）：标题/简介**超限直接报错而不是静默截断** —— 静默截断会让用户以为发出去了
完整内容（与 ai/ui 001/003 的「意图不等于实现」是同一类盲区）；界面在提交前就给出「第 N 条超出 X 字」的定位。

单视频不套模板：直接给「标题 = 视频标题」「简介 = 任务标题 + 源链接」、默认标签、默认分区，
全部可编辑，另加**版权与来源**两项。

分区（`tid`）用**小词表**而非全量分区表：映射「知识 / 科技 / 生活 / 影视 / 音乐 / 游戏 / 其他」等常见分区到 `tid`，
**默认 `122`（知识 · 野生技术协会）**。词表放 `bili_meta.py`，未知分区回退默认值并如实标注。

> 这里特意写明一条**取数纪律**：`122` 这个数字来自 bilibili-API-collect 的现行分区表，
> 而不是从 arXiv 项目的注释里抄的 —— 那份注释把「野生技术协会」写成了 `171`，
> 而现行表里 `171` 是游戏区的「电子竞技」（见 [01-requirements.md F12](01-requirements.md)）。
> 分区表是会变的，凡引用具体数字都要能指回一张当期表。

### 7.1 版权（自制 / 转载）与来源

这两项是后来补上的（原先 `bili_bin` 把 `--copyright` 写死成 `1`），有三条设计判据：

1. **默认转载。** 这个工作流搬的是从别的平台下载下来的视频，绝大多数稿件都不是自己拍的。
   默认「自制」会把每一单都填成假信息，而 B站 对「把转载标成自制」是有处置的。
   反向兜底也一致：`normalize_copyright` 读不出合法值时回退**转载**而非自制 ——
   标成自制是替用户声明了一件他没说过的事，标成转载最多让他改一下。

2. **合集模板上刻意没有 `source` 字段。** 合集里每条视频的出处都不一样，
   写一个共用的来源在语义上就是错的。逐条渲染时来源取该条自己的 `source_url`。
   界面上相应地**只在单视频场景显示来源输入框**（`BiliUploadDialog`），
   模板的 `_metadata_model` 投影也刻意留空而不是回填第一条 ——
   回填一个看着像「所有条目都用它」的值，正是这条设计要避免的误导。

3. **「界面上有值」与「命令里带这个参数」必须收在一处。** `effective_source` 是唯一判据
   （`bili_upload.render` / `bili_meta.default_single` / `bili_bin.build_upload_argv` 三处调用）：
   只有转载稿才有来源，自制稿即便带着值也不提交。不这么收口的话，
   落库的快照与实发参数迟早不一致，而「自制稿带了来源」可能被平台判成误标。

**校验拦在提交之前**：转载稿缺来源会被 B站 退回来（`21004 Missing source for reprint`，见 F16），
所以 `bili_meta.validate` 与界面各写一份同判据的检查 —— 界面那份是为了让用户在输入框旁边就看到，
服务端那份是权威。写两遍而不是只留一处，是因为界面那份能在**点提交之前**给出定位。


## 8. 可测试性：注入接缝

沿用既有 `create_app(ytdlp_service=…)` 的先例：

- `bili_bin` 的进程执行是一个可替换的 `runner(argv, timeout, on_line) -> CommandResult` 回调
  （返回的是带 `returncode`/`stdout`/`stderr` 的 dataclass，而不是裸三元组 —— 调用点要读的属性有三个，
  裸元组会让「第几个是 stderr」变成每处都要重新确认的事）。
- `bili_api` 的 HTTP 走一个可注入 `transport(method, url, params=…, headers=…, form=…, json_body=…, timeout=…) -> HttpResult`
  协议，默认实现用**标准库 `urllib`**（不新增依赖，requirements 兼容性一条）。
- `bili_auth` 的扫码轮询同样经 transport。
- `bili_upload` 还额外接收可替换的 `sleeper`（对账退避与合集条间隔都用它）——
  否则「条间必须等 20 秒」这条会被真测成 20×N 秒的真实等待。
- `create_app(..., bili_manager=None)`：为 `None` 时按真实依赖装配 `BiliManager`；测试传 fake。

于是**全部自动测试离线**：不启动 `biliup`、不联网、不需要账号（requirements R5 说明真实投稿只能人工验收）。

## 9. 错误分类与文案

分类集中在 `bili_policy.py`（判定在 `bili_upload`），`bili_manager` 只落账：

| 类别 | 判定依据 | 文案与下一步 |
| --- | --- | --- |
| 未登录 / 登录态过期 | `biliup` 输出含登录关键词，或 cookie 缺 `SESSDATA`/`bili_jct`；对账返回 `code != 0` | 「B站 登录已失效」+「去设置里重新扫码登录」 |
| 账号频控 601 | 输出含 `601` | 「B站 说这次上传太快了。这是账号侧的临时限流，等几分钟原样重投即可，**不要改参数硬刷**。」（F8） |
| `biliup` 缺失 | 探测不到可用二进制 | 「没找到投稿工具」+ 具体的 `winget install --id ForgQi.biliup-rs --exact` 命令 |
| 视频文件不存在 | `video_path` 不存在 / 无 `output_path` | 「视频还没下载好」+「先让下载任务跑完」 |
| 对账查不到 | `biliup` 返回 0 但 member 列表里没有 | 「已提交，但还没在 B站 看到它」+「去创作中心核对，或稍后重试」 |
| 归集失败 | `season/add` 或 `episodes/add` 非 0 | 「视频都传上去了，合集没建成」+「可重试归集」 |
| 其他 | 兜底 | 透传清洗后的原文，不猜测原因 |

**文案约束**（沿用仓库既有约定）：文案里不出现会被上游误分类的裸词（`timed out` / `reset` / `403`）。

## 10. 界面设计

沿用现有语言：卡片式 panel、圆角 8px、`#dce2ee` 描边、`#1f6feb` 主色、`lucide-react` 图标。
B站 品牌色 `#FB7299` 只用于 B站 图标本身，按钮其余样式与既有 `.icon-button` / `.ghost-button` 一致。

| 位置 | 元素 |
| --- | --- |
| 任务行（单视频） | B站 图标按钮，文案「上传」/「已上传」/「上传中」；禁用态给出 title 提示。 |
| 任务行（合集） | 同上，但走合集模板流程。 |
| 条目行 | B站 图标按钮，单条上传。 |
| 弹窗 | 单视频表单（4 个可编辑字段 + 封面开关）/ 合集模板表单（模板 + 逐条预览表）。 |
| 设置面板 | B站 登录区：环境体检结论、扫码按钮（弹二维码）、导入 cookie 文件。 |

**为什么要用模态弹窗**（与既有 `HelpPopover` 的「非模态」原则相反）：说明浮层是**可选阅读**，
而上传表单是**必须先填完才能确认**的动作，需要焦点管理。两者性质不同，所以不套用同一条约束；
但焦点管理要写对：打开时把焦点移到第一个输入、`Esc` 关闭、关闭后焦点还给触发按钮。

## 11. 已知限制与设计债

1. **不承诺「已发布」**：稿件受平台转码与审核影响，合集更是**人工审核**（F9）。本功能只承诺「已提交」+ 对账结论。
2. **对账有窗口期**：平台侧稿件列表有延迟，投稿后可能短时间内查不到，此时停在 `unverified`。重试即重新对账。
3. **依赖非公开接口**：`season/*`、`reply/*`、`cover/up` 都不是官方开放接口，B站 变更即失效（R3）。
4. **`biliup-rs` 已归档**：上游迁移（F5），长期存在失效风险；依赖已收敛在 `bili_bin.py`。
5. **无「上传中途取消」**：见 requirements 的非目标表。
6. **单进程单队列**：上传与下载共用同一个 `EventBroker` 但各自的队列，互不阻塞；上传串行是**刻意的**（抗频控），
   不提供并发度配置 —— 提高并发会直接把 601 频控的概率拉起来（F8）。

## 12. 为什么为二维码破例加了一个依赖

`bili_auth` 需要把登录 URL 变成可扫的二维码。三条路都试过：

| 方案 | 否决理由 |
| --- | --- |
| 在仓库里手写 QR 编码器 | QR 的 Reed–Solomon 纠错与掩码选择是**看起来对、错了也看不出来**的那类代码。**离线无法验证「扫得出来」**，等于交付一个自己都没验过的东西 —— 违反「先取证再交付」。 |
| 只把登录 URL 返给前端、让用户手输 | 该 URL 是为 App 扫码设计的，在手机浏览器里打开无法完成登录；手输 200+ 字符不现实。 |
| **加 `qrcode>=8.0`（选定）** | 纯 Python、无编译产物；只用 `qrcode.image.svg.SvgPathImage`，**因此不会牵入 Pillow**（`pip install qrcode` 的默认工厂才需要 PIL）。这是本次唯一的运行时依赖新增。 |

代价与兜底：`bili_auth` 对 `qrcode` 的 import 做了容错 —— 装不上时扫码接口返回明确的
「缺少二维码组件，请改用导入 cookies.json」，而不是让整个设置面板炸掉。
