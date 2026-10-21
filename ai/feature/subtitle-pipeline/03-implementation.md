# 实现：字幕下载与 B站 CC 字幕上传

适用读者：要改代码的人。需求在 [01-requirements.md](01-requirements.md)，
设计取舍在 [02-design.md](02-design.md)，测试与验收在 [04-testing.md](04-testing.md)。

## 一、新增模块

### `backend/app/subtitle_plan.py`（L3，纯逻辑）

不联网、不读库、不 import yt-dlp —— 「主语言怎么判」「人工和 ASR 谁优先」这些最容易写歪的分支，
全部可以在离线测试里跑出来。

| 符号 | 行 | 说明 |
| --- | --- | --- |
| `ENGLISH` / `CHINESE` / `SUBTITLE_LANGUAGE_FAMILIES` | [L36](../../../backend/app/subtitle_plan.py#L36) | 值是**族名**，不是某条轨 |
| `FALLBACK_PRIMARY_LANGUAGE` | [L42](../../../backend/app/subtitle_plan.py#L42) | 判不出主语言时的兜底 = `en` |
| `ORIGINAL_TRACK_SUFFIX` | [L45](../../../backend/app/subtitle_plan.py#L45) | `-orig` |
| `SubtitleOrigin` | [L48](../../../backend/app/subtitle_plan.py#L48) | `human` / `auto` / `none` |
| `SubtitlePlan` | [L58](../../../backend/app/subtitle_plan.py#L58) | `language`（族）+ `code`（真实轨）+ `origin` + `reason` |
| `SubtitlePlan.languages` | [L74](../../../backend/app/subtitle_plan.py#L74) | 交给 yt-dlp 的列表，**永远 0 或 1 条** |
| `ORIGIN_LABELS` / `subtitle_summary` | [L85](../../../backend/app/subtitle_plan.py#L85) | 人话：`en · 人工` / `无字幕`；**没有结论时返回 `""`** |
| `language_family` | [L109](../../../backend/app/subtitle_plan.py#L109) | 唯一的族判定入口；`yue` 不算中文 |
| `is_supported_language` / `filter_supported_languages` | [L127](../../../backend/app/subtitle_plan.py#L127) | 保序过滤（顺序是偏好排序的依赖） |
| `detect_primary_language` | [L140](../../../backend/app/subtitle_plan.py#L140) | 两级判据：`-orig` 轨 → 人工字幕 |
| `_TRACK_PREFERENCE` / `_track_rank` / `pick_track` | [L171](../../../backend/app/subtitle_plan.py#L171) | 同族多条轨只挑一条 |
| `resolve_subtitle_plan` | [L200](../../../backend/app/subtitle_plan.py#L200) | 总入口；**每条分支都填 `reason`** |

### `backend/app/bili_subtitle.py`（L3，转换 + 一次上传）

| 符号 | 行 | 说明 |
| --- | --- | --- |
| `LAN_CHINESE` / `LAN_ENGLISH_BILI` / `DEFAULT_LAN` | [L43](../../../backend/app/bili_subtitle.py#L43) | 平台白名单里的值 |
| `_TRADITIONAL_CHINESE` | [L51](../../../backend/app/bili_subtitle.py#L51) | `zh-Hant` / `zh-TW` / `zh-HK` 原样保留 |
| `SUBTITLE_POSTED` / `SKIPPED` / `FAILED` | [L70](../../../backend/app/bili_subtitle.py#L70) | 三档字幕状态 |
| `SUBTITLE_SUFFIXES` | [L74](../../../backend/app/bili_subtitle.py#L74) | 只认 `.srt` / `.vtt` |
| `bilibili_language` | [L83](../../../backend/app/bili_subtitle.py#L83) | 轨代码 → 平台标识（见 [02 语言标识与平台白名单](02-design.md#语言标识与平台白名单)） |
| `parse_timestamp` | [L103](../../../backend/app/bili_subtitle.py#L103) | 解析不出来返回 `None`，**不猜 0** |
| `_clean_line` | [L123](../../../backend/app/bili_subtitle.py#L123) | 剥 `<c>` 内联标签 + 反转义实体 |
| `parse_subtitle_text` | [L132](../../../backend/app/bili_subtitle.py#L132) | SRT/VTT 共用解析器；跳过 WEBVTT/NOTE，容忍 `align:start position:0%` |
| `merge_repeated_cues` | [L164](../../../backend/app/bili_subtitle.py#L164) | **只合并相邻且完全相同**的字幕 |
| `to_bcc` | [L180](../../../backend/app/bili_subtitle.py#L180) | 输出 BCC；`from` / `to` 是**秒（浮点）** |
| `load_bcc` | [L200](../../../backend/app/bili_subtitle.py#L200) | `utf-8-sig` 读；读不出来就抛，由调用方决定 skip/fail |
| `SubtitleOutcome` | [L209](../../../backend/app/bili_subtitle.py#L209) | `state` / `lan` / `message` / `cue_count` |
| `upload_subtitle` | [L218](../../../backend/app/bili_subtitle.py#L218) | **任何失败都变成结论，不抛异常** |

## 二、改动的既有模块

### `backend/app/ytdlp_service.py`

| 位置 | 改动 |
| --- | --- |
| [DownloadPreparation](../../../backend/app/ytdlp_service.py#L175) | 新增 `subtitle: SubtitlePlan \| None = None` |
| [prepare_download](../../../backend/app/ytdlp_service.py#L395) | `subtitles_only` 分支也走 `_extract_info_for_subtitles`，返回带计划的 `DownloadPreparation`；**元数据失败在那里不致命**（记 warning，仍按可选返回） |
| [_extract_info_for_subtitles](../../../backend/app/ytdlp_service.py#L467) | 复用 `build_download_options` 并强制 `skip_download`，只为拿字幕字典 |
| [_subtitle_plan](../../../backend/app/ytdlp_service.py#L485) | 算计划并记一条 `subtitle plan: language=… code=… origin=… reason=…` |
| [_subtitle_languages](../../../backend/app/ytdlp_service.py#L1103) | **只保留中英**；空列表改为回退到有界默认（`default_subtitle_languages` 里过滤后的中英子集），不再等同 `["all"]` |
| [_map_subtitles](../../../backend/app/ytdlp_service.py#L1193) | 跳过非中英语种 —— 与下载侧共用同一份过滤，避免「界面能选、下了没有」 |

### `backend/app/output_paths.py`

| 位置 | 改动 |
| --- | --- |
| [SUBTITLE_SUFFIXES](../../../backend/app/output_paths.py#L28) | `(".srt", ".vtt")` |
| [subtitle_candidates](../../../backend/app/output_paths.py#L122) | 返回 `[(Path, 语言代码)]`。用 `iterdir()` **而不是 `glob`** —— 标题里的 `[` `]` 会被 glob 当成字符类 |
| [subtitle_language_from_path](../../../backend/app/output_paths.py#L149) | 取 stem 最后一段点号后的部分（`作品 [abc123].zh-Hans.srt` → `zh-Hans`） |
| [find_subtitle_file](../../../backend/app/output_paths.py#L162) | 按族过滤 + `pick_track` 挑一条；返回 `(Path, code) \| None` |

### `backend/app/job_manager.py`

| 位置 | 改动 |
| --- | --- |
| [_run_item](../../../backend/app/job_manager.py#L566) | 开跑前重置 `subtitle_language` / `subtitle_source` / `subtitle_path`（重跑不能留旧路径）；成功后调 `_record_subtitle_result` |
| [_prepare_download](../../../backend/app/job_manager.py#L932) | 返回 `tuple[DownloadOptions, SubtitlePlan \| None]`；用 `getattr(preparation, "subtitle", None)` 取计划（测试的 fake 是 `SimpleNamespace`） |
| [_options_with_subtitle_plan](../../../backend/app/job_manager.py#L1019) | **只在用户没显式指定语言时**改写；填 `[plan.code or plan.language]` |
| [_record_subtitle_result](../../../backend/app/job_manager.py#L1062) | **按磁盘证据落库**；`plan` 为 `None` 时仍然按磁盘发现认字幕，但 `subtitle_source` 留 `NULL`（没有来源结论 ≠ 结论是没有） |

### `backend/app/models.py` / `db.py`

| 表 | 新增列 | 默认 |
| --- | --- | --- |
| `jobitem` | `subtitle_language` / `subtitle_source` / `subtitle_path`（[L81](../../../backend/app/models.py#L81)） | `NULL` |
| `bili_upload` | `subtitle_state` / `subtitle_lan` / `subtitle_message`（[L167](../../../backend/app/models.py#L167)） | `skipped` / `""` / `""` |

历史行的 `subtitle_state` 补成 `skipped`：改动前从来不传字幕，写 `failed` 等于给旧记录凭空编一个失败。

### `backend/app/bili_api.py`

[SUBTITLE_SAVE_URL](../../../backend/app/bili_api.py#L36) = `https://api.bilibili.com/x/v2/dm/subtitle/draft/save`；
[save_subtitle](../../../backend/app/bili_api.py#L366) 要求 `bili_jct`，发
`aid` / `bvid` / `oid`(=cid) / `type=1` / `csrf` / `data` / `lan` / `sign="false"` / `submit`。
非 0 `code` 抛 `BiliApiError`。`submit=False` 只存草稿 —— 它是唯一能在**不动任何已发布稿件**的前提下
验证接口形态的方式。

### `backend/app/bili_upload.py`

| 位置 | 改动 |
| --- | --- |
| [UploadTarget](../../../backend/app/bili_upload.py#L91) | 新增 `subtitle_path` / `subtitle_language`（**必须成对给**） |
| [UploadResult](../../../backend/app/bili_upload.py#L136) | 新增 `subtitle_state`（默认 `skipped`）/ `subtitle_lan` / `subtitle_message` |
| [_attach_subtitle](../../../backend/app/bili_upload.py#L305) | 三个前置：有字幕路径、`result.ok`、拿到 `aid`+`cid`+`bvid`；缺任一项写一句 `skipped` 的理由 |
| [run_single](../../../backend/app/bili_upload.py#L346) | `_attach_subtitle` 是**最后一步**（必须有 aid/cid） |
| [reverify](../../../backend/app/bili_upload.py#L424) | 新增 `subtitle_path` / `subtitle_language` 参数，可只重挂字幕 |

### `backend/app/bili_manager.py`

| 位置 | 改动 |
| --- | --- |
| [_load_request](../../../backend/app/bili_manager.py#L1116) | 用 `_subtitle_of` 填 `UploadTarget` |
| [_subtitle_of](../../../backend/app/bili_manager.py#L1140) | 从 **`JobItem`** 读（不在 `BiliUpload` 上复制一份）；文件不在就 `(None, None)`；语言取文件名里的轨代码、兜底用族名 |
| [_store_result](../../../backend/app/bili_manager.py#L1212) | 落三列 |
| [retry_upload](../../../backend/app/bili_manager.py#L921) | 已发布/审核中且 `subtitle_state == failed` 时，走 `_reverify_work` **只重传字幕**，回执「已重新上传字幕」；`unverified` 分支同样带上字幕 |

### 契约层（三处必须同步）

| 文件 | 字段 |
| --- | --- |
| [schemas.py](../../../backend/app/schemas.py#L135) | `JobItemRead.subtitle_language` / `subtitle_source` |
| [schemas.py](../../../backend/app/schemas.py#L407) | `BiliPrefillItem.subtitle`（**服务端拼好的文案**，前端不自己拼） |
| [schemas.py](../../../backend/app/schemas.py#L524) | `BiliUploadRead.subtitle_state` / `subtitle_lan` / `subtitle_message` |
| [job_read_model.py](../../../backend/app/job_read_model.py#L87) | 投影 `JobItem` 那两列 |
| [bili_read_model.py](../../../backend/app/bili_read_model.py#L121) | 投影 `BiliUpload` 那三列（带 `or ""` / `or "skipped"` 兜底） |
| [types.ts](../../../frontend/src/types.ts#L110) | `JobItem.subtitle_language` / `subtitle_source`；`SubtitleOrigin` 与 `SubtitleSource` **是两个类型**（前者有 `none`） |
| [types.ts](../../../frontend/src/types.ts#L435) | `BiliUpload.subtitle_state`（字面量联合 `"posted" \| "skipped" \| "failed"`） |
| [docs/openapi.yaml](../../../docs/openapi.yaml) | 上述字段；`subtitle_lan` 一处注明平台白名单不是 `zh-CN` / `en-US` |

契约漂移由 `scripts/check_api_contract.py` 把住（只比字段名集合与路径×方法，不比类型）。

### 前端

[BiliUploadDialog.tsx](../../../frontend/src/components/BiliUploadDialog.tsx#L492)：预览表新增一列「字幕」，
渲染 `item.subtitle || "—"`。**文案由服务端给**，组件里的注释写明了「前端拼一遍就会出现两套说法」。

## 三、日志

| 时机 | 级别 | 样例 |
| --- | --- | --- |
| 算出下载计划 | INFO | `subtitle plan: language=zh code=zh-Hans origin=human reason=…` |
| 字幕文件已不存在 | INFO | `bili 字幕已不存在，跳过：item=… path=…` |
| 转换失败 | WARNING | `bili 字幕转换失败：file=… error=…` |
| 接口失败 | WARNING | `bili 字幕上传失败：bvid=… lan=… error=…` |
| 成功 | INFO | `bili 字幕上传完成：bvid=… lan=… 条数=…` |

字幕的日志与 `biliup 输出 | ` 那条通道**刻意分开**：排查时一眼能分清是投稿还是字幕。

## 四、限制与后续

1. 一次投稿只挂**一条**字幕轨（`save` 一次一轨）。要挂两条需两次调用并各自记状态。
2. 只认 `.srt` / `.vtt`。`.json3`、`.ass` 等不会被上传（也不是下载侧产物）。
3. 字幕的三档状态**不参与徽标聚合**，任务行上看不到「字幕没传上去」，要看明细行。
4. `subtitle_source` 只有在**跑过预检并留下计划**时才有值；`video_only` 模式与不需降级的早退路径
   会留 `NULL`（有字幕文件、但没有来源结论）—— 界面上显示为空白而不是「无字幕」。
5. 主语言判定依赖 `-orig` 标记；平台不给时退化为人工字幕的中/英，再退化为 `en`。

## 增量实现：打开字幕（2026-10-18 下午）

> **后续变更（2026-10-21）**：`.split-button` / 右段箭头那两行**已经不存在**（`styles.css` 里三条
> 只服务于拆分的规则一并删除）；`open-subtitle` 的 `mode` 缺省改为 `chooser`，前端调用不带该参数。
> 见 [ui/006](../../ui/006-the-subtitle-arrow-looks-dead.md)。下面的落点表是当时的记录。

设计理由在 [02-design.md](02-design.md#增量设计打开字幕为什么不自己挑程序)，这里只列改动落点。

### 后端（通用能力，落在已有的「本机打开」模块）

| 落点 | 位置 | 做什么 |
| --- | --- | --- |
| `system_open.ERROR_NO_ASSOCIATION` | [L18](../../../backend/app/system_open.py#L18) | `1155` 的命名常量：不是打开失败，是「还没人认领这个后缀」 |
| `open_path_with_default_app` | [L28](../../../backend/app/system_open.py#L28) | Windows 分支加 `try/except`：**只有** 1155 降级成弹选择器，其它 `OSError` 原样抛 |
| `open_path_with_chooser` | [L46](../../../backend/app/system_open.py#L46) | 新增。Windows 起系统的「打开方式」入口（shell 的 `OpenAs_RunDLL`）；其它平台退化成默认打开（没有等价选择器） |
| `existing_subtitle_file` | [L184](../../../backend/app/output_paths.py#L184) | 新增。字幕位置的**唯一计算处**：记录值优先（只验存在）→ 视频旁边的同名字幕。视频路径由调用方给，不自己发现一遍（读模型每次轮询都要问） |
| `subtitle_file` | [L56](../../../backend/app/job_artifacts.py#L56) | 新增。定位失败 → `409 字幕文件尚不可用。`（不是 404：条目在库里） |
| `SubtitleOpenMode` / `_subtitle_opener` | [L23](../../../backend/app/routers/job_files.py#L23) | `Literal["default", "chooser"]`；按 mode 选 opener，路由本身仍然只定状态码 |
| 两条路由 | [L46](../../../backend/app/routers/job_files.py#L46) / [L80](../../../backend/app/routers/job_files.py#L80) | 任务级与条目级各一条 `POST .../open-subtitle` |
| `ApiContext.open_with_chooser` | [L38](../../../backend/app/api_context.py#L38) | 新注入位。`create_app(chooser_opener=…)`；**注入了 `system_opener` 时默认沿用它**，否则一个单测就会在桌面上弹窗（[main.py L91](../../../backend/app/main.py#L91)） |
| `JobItemRead.subtitle_path` | [L140](../../../backend/app/schemas.py#L140) | 新增字段，前端据此决定按钮可点性 |
| `_project_subtitle_path` | [L110](../../../backend/app/job_read_model.py#L110) | 复用它上面刚算好的 `output_path` 当视频路径，**不再自己发现一遍**；只回「真的存在」的路径 |

### 前端

| 落点 | 位置 | 做什么 |
| --- | --- | --- |
| `SubtitleOpenButton` | [L17](../../../frontend/src/components/SubtitleOpenButton.tsx#L17) | 新增组合控件：主段 + 右段小箭头，两段都只发一个 `mode` |
| `JobQueue` | [L276](../../../frontend/src/components/JobQueue.tsx#L276) / [L453](../../../frontend/src/components/JobQueue.tsx#L453) | 单视频任务行与条目行，紧跟在文件夹按钮之后 |
| `api.openJobSubtitle` / `openJobItemSubtitle` | [L131](../../../frontend/src/api.ts#L131) / [L135](../../../frontend/src/api.ts#L135) | 总带 `?mode=…`，URL 稳定可断言 |
| `SubtitleOpenMode` | [L15](../../../frontend/src/types.ts#L15) | 与后端枚举同名同值，注释写明「不表示具体程序」 |
| `.split-button` | [L858](../../../frontend/src/styles.css#L858) | 两段共用一个外框：圆角只留外侧，中间留**一条** 1px 分隔线（主段右框；右段 `border-left:0` 防叠成 2px） |

### 契约

`JobItemRead` 加 `subtitle_path` ⇒ 三处同步：`schemas.py` ↔ [types.ts](../../../frontend/src/types.ts) ↔
[openapi.yaml](../../../docs/openapi.yaml)（两条新路径 + `mode` 参数）。`check_api_contract.py` 现在比对
**76 个条目**（上一轮 74）。

### 一处需要说明的取舍

`open_with_chooser = chooser_opener or system_opener or open_path_with_chooser`：注入了 `system_opener`
的调用方等于声明「本机打开这件事由我接管」，那么选择器也不该真去起外部进程 —— 否则写一个
`mode=chooser` 的接口测试，就会在你桌面上弹一个「打开方式」窗口。测试要区分两条路时显式传 `chooser_opener`。
