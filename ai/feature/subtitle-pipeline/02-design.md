# 设计：字幕下载与 B站 CC 字幕上传

适用读者：要改这段逻辑的人。需求在 [01-requirements.md](01-requirements.md)，
落地细节在 [03-implementation.md](03-implementation.md)。

## 主语言怎么判

### 判据是平台给的，不是猜的

yt-dlp 会给**原始语音语言**那条 ASR 轨加 `-orig` 后缀。源码里的注释写得很明确：

> Add an `-orig` label to the original language so that it can be distinguished
> —— `yt_dlp/extractor/youtube/_video.py`

所以：

- 存在 `zh-Hans-orig` ⇒ 这个视频**说的是中文**；
- 存在 `en-orig` ⇒ **说的是英文**。

这是平台直接给出的信号，比按标题、频道或描述猜可靠得多 —— 后者在「中文频道发英文视频」这种
最常见的例外上必然判错。

### 强度排序

[`detect_primary_language`](../../../backend/app/download/subtitle_plan.py#L140) 按强度只排两级：

1. **ASR 的 `-orig` 轨**（平台标注的原声语言）。绝大多数带自动字幕的视频都命中这一条。
2. **人工字幕**：出现中文 ⇒ 判中文；否则出现英文 ⇒ 判英文。

第 2 条只在「完全没有自动字幕」时才轮到。中文优先于英文的理由是：人工挂了中文字幕的视频几乎只
可能面向中文观众。

判不出来时退回 **`en`**（[`FALLBACK_PRIMARY_LANGUAGE`](../../../backend/app/download/subtitle_plan.py#L42)），
不是「不下字幕」—— 本项目的既有默认就是英文，改成「不下」会让一批老任务悄悄丢掉字幕。这是
**有界默认**，不是随机猜。

两级都取到多族时按排序取第一个：**同一份输入永远同一个结论**。判定的可复现比「挑得更准」重要。

### 粤语不算中文

`yue`（粤语）在 yt-dlp 里是单列的一族，本项目也把它排除出中文族
（[`language_family`](../../../backend/app/download/subtitle_plan.py#L109) 用 `-` 前的第一段匹配 `en` / `zh`）。
混进来会让「中文视频」的判定漂到粤语内容上 —— 而粤语视频的 ASR 文本与普通话书面语差异大到
不能当同一份字幕用。

## 人工优先与降级为什么是免费的

yt-dlp 的 `process_subtitles` 就是这个顺序：先把 `subtitles`
（人工/官方）放进候选，再用 `automatic_captions` **补**没有被占掉的语言。
（依据：`YoutubeDL.py` 的 `process_subtitles`，manual 先进 `orderedSet_from_options`，
自动字幕只填 `lang in already_langs` 之外的。）

所以只要把 `subtitleslangs` 收敛成**一条**语言，优先级就是免费的。剩下两件事要在我们自己这边做：

1. **同一族里有多条轨时只挑一条**（[`pick_track`](../../../backend/app/download/subtitle_plan.py#L188)）。
   填多条会把 `en` + `en-orig` 都写下来，于是同一条字幕出现两份文件。偏好表：

   | 族 | 越靠前越优先 |
   | --- | --- |
   | `en` | `en` > `en-us` > `en-gb` > `en-ca` |
   | `zh` | `zh-hans` > `zh-cn` > `zh` > `zh-hant` > `zh-tw` > `zh-hk` |

   简体优先于繁体（受众面）；无后缀的基础码优先于地区变体；`-orig` 排在同名普通轨之后
   （两者内容相同，普通轨是平台对外那条）。

2. **「跳过」要能被表达，而且不能悄悄变成「下英文」**。见下一节。

## 为什么跳过要请求族名而不是给空列表

这一条是本册最容易写错、且**错了不报错**的地方。

- yt-dlp 用 `fullmatch` 匹配语言代码。真实字幕轨是 `zh-Hans`、`en-orig` 这种**带后缀**的，
  所以族名 `zh` / `en` **匹配不到任何轨** ⇒ 什么也不下，而且**不报错**。
  这正是需求要的「跳过下载，不中断流程」，不需要额外开关。
- 反过来，**空列表不是等价的**：`subtitleslangs` 为空时 yt-dlp 当「没给」，然后按它自己的默认
  去挑英文 —— 把「跳过」悄悄变成「下载英文」。
- **更不能退化成 `["all"]`**：那会一次拉 20+ 条轨，撞 HTTP 429，结果连**视频本体**一起失败。
  所以 `SubtitlePlan.languages` 被设计成**永远有界**（0 或 1 条），返回 `["all"]` 在类型上就不可能。

结论：`SubtitlePlan.languages` 返回 `[code]` 或 `[]`；没有可用轨时上层请求**族名**
（[`_options_with_subtitle_plan`](../../../backend/app/download/job_manager.py#L1019)），靠 `fullmatch` 落空实现跳过。

## 语言标识与平台白名单

需求举的是 `zh-CN` / `en-US`，实测**两个都被 B站 拒**（`code=79011 不合法的语言`）。
白名单只有：`zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` / `ko` / `yue` / `fr`。
判据见 [04-testing.md](04-testing.md#语言白名单的实测依据)。

因此 [`bilibili_language`](../../../backend/app/bilibili/bili_subtitle.py#L83) 的映射是：

| 输入 | 输出 | 理由 |
| --- | --- | --- |
| `en` / `en-US` / `en-orig` / `en-GB`… | `en` | 白名单里英文只有 `en` |
| `zh` / `zh-Hans` / `zh-CN` / `zh-Hans-orig`… | `zh` | `zh-Hans` **不在**白名单，必须压平 |
| `zh-Hant` / `zh-TW` / `zh-HK` | 原样 | 它们在白名单里，压成 `zh` 反而丢信息 |
| 其它 / 空 | `en` | 有界默认 |

**注意这个映射与下载侧的族名不是同一个东西**：族名是「我们内部怎么分」（两族 + 忽略其它），
`lan` 是「平台收什么」（白名单里的具体值）。两者共用一个 `language_family` 判族，但输出不同。

## 模块落点

两个新模块都落在 **L3 领域层**，理由与既有模块一致：**判定必须能离线单测**。

| 模块 | 层 | 职责 | 不负责 |
| --- | --- | --- | --- |
| [subtitle_plan.py](../../../backend/app/download/subtitle_plan.py) | L3 领域 | 主语言判定、轨偏好、下载计划（`SubtitlePlan`）、来源标签与摘要文案。**纯逻辑：不联网、不读库、不 import yt-dlp**。 | 不写库、不发请求、不知道 `JobItem` 的存在 |
| [bili_subtitle.py](../../../backend/app/bilibili/bili_subtitle.py) | L3 领域 | SRT/VTT → BCC 转换、语言标识映射、一次字幕上传（`SubtitleOutcome`）。 | 不参与稿件状态判定，不写库，不重试 |
| [bili_api.save_subtitle](../../../backend/app/bilibili/bili_api.py#L366) | L3 领域 | CC 字幕接口的端点与参数封装。 | 不知道 BCC 长什么样（那是 `bili_subtitle`） |

依赖方向仍然是 `L2 → L3`：

- `ytdlp_service`（L3）算计划、把结论挂在 `DownloadPreparation.subtitle` 上返回；
- `job_manager`（L2）**只做 IO 与时序**：把计划写进 `subtitle_languages`、下载成功后按磁盘证据落库；
- `bili_upload`（L3）在 `run_single` 最后调 `_attach_subtitle`；
- `bili_manager`（L2）把字幕文件从 `JobItem` 读出来塞进 `UploadTarget`，并把三档状态落库。

**判定没有一处上移到 L2**：`_prepare_download` 的 docstring 里明确写了「本方法只保留 IO 与时序」。

## 数据流

### 下载侧

```
prepare_download
  ├─ 取元数据（含 subtitles / automatic_captions 两份字典）
  ├─ _subtitle_plan(info, options) → SubtitlePlan（language / code / origin / reason）
  │     记一条日志：subtitle plan: language=… code=… origin=… reason=…
  └─ DownloadPreparation(subtitle=plan)
        │
job_manager._prepare_download
  └─ _options_with_subtitle_plan：仅当用户没显式指定语言时，把 subtitle_languages
     改成 [plan.code or plan.language]
        │
下载（yt-dlp 自己按 manual→auto 的顺序挑）
        │
job_manager._record_subtitle_result
  └─ find_subtitle_file(视频路径, 族) → 按**磁盘证据**落 subtitle_language / _source / _path
```

### 上传侧

```
bili_manager._load_request
  └─ _subtitle_of(session, row)：从 JobItem 读字幕路径（文件不在了就 (None, None)）
        → UploadTarget(subtitle_path=…, subtitle_language=…)
        │
bili_upload.run_single
  ├─ biliup 投稿 → 回读对账 → result.ok / aid / cid / bvid
  └─ _attach_subtitle（最后一步）
        ├─ 缺文件 / 未核实 / 缺 aid+cid+bvid → skipped（各带一句人话）
        └─ bili_subtitle.upload_subtitle → posted / failed
        │
bili_manager._store_result
  └─ 落 subtitle_state / subtitle_lan / subtitle_message（与稿件 status 分开）
```

**字幕路径只在 `JobItem` 上存一份**，不复制到 `BiliUpload`：上传时按文件**现在还在不在**重新判断，
复制一份等于让「记录里有路径」与「磁盘上有文件」变成两件互不相干的事。

## 状态与数据

### 下载侧（`JobItem` 三列）

| 列 | 取值 | 说明 |
| --- | --- | --- |
| `subtitle_language` | `en` / `zh` / `NULL` | **族名**，不是轨代码 |
| `subtitle_source` | `human` / `auto` / `none` / `NULL` | `NULL` = 这一轮还没有结论；`none` = 有结论（没有可用字幕） |
| `subtitle_path` | 路径 / `NULL` | 上传环节实际读的东西 |

`NULL` 与 `none` 分开是刻意的：混成一句会让用户在任务刚提交、还没跑预检时就看到「无字幕」。

### 上传侧（`BiliUpload` 三列）

| 列 | 取值 | 说明 |
| --- | --- | --- |
| `subtitle_state` | `posted` / `skipped` / `failed`，默认 `skipped` | **独立于** `BiliUpload.status` |
| `subtitle_lan` | 平台侧实际用的标识（`zh` / `en` / `zh-Hant`…） | 空串表示没传 |
| `subtitle_message` | 恒非空（成功也写） | 给用户看的那句 |

三档状态**刻意不参与徽标聚合**：徽标回答「这个任务传上去没有」，字幕是附加物。把它算进徽标，
「视频已发布 + 字幕失败」就会显示成一种**新的失败档**，而用户对此能做的唯一正确动作是「重传字幕」，
不是「重投视频」。

历史行的 `_ensure_columns` 默认值是 `skipped`：改动前从来不传字幕，写 `failed` 等于给一批旧记录
凭空编一个失败。

## 失败语义与重试

| 情况 | 结论 | 用户的下一步 |
| --- | --- | --- |
| 该视频没有可用中英字幕 | `skipped`（下载侧 `none`，上传侧 `skipped`） | 无。这就是预期结果 |
| 字幕文件被删了 | `skipped` | 重下一次该视频（或手动放一个同名 `.srt`） |
| 不是 `.srt` / `.vtt` | `skipped` | 换格式，或重下一次并选 SRT |
| 文件里没有可读的字幕 | `skipped` | 检查源字幕 |
| SRT/VTT 解析失败 | `failed` | 重试（见下）；仍失败就先看文件本身 |
| 平台接口报错（含非法 `lan`） | `failed` | 重试；文案带上平台原因 |

**重试只重传字幕**：已发布/审核中且 `subtitle_state=failed` 的稿件，
`retry_upload` 走 `_reverify_work(..., subtitle)` 而不是拒绝，回执写「已重新上传字幕」。
重投视频不可撤销，只重传字幕是幂等的 —— 这是唯一安全的补救动作。

**`upload_subtitle` 不抛异常**（任何失败都变成 `failed` 结论）：这一步挂在「视频已经上传成功」之后，
抛出去会让一条**已发布**的稿件被标成失败，而用户看到的错误还是「字幕没传上去」。两件事混在一条
结论里没法解释。

## 格式转换要点 BCC

`POST /x/v2/dm/subtitle/draft/save` 收的是 **BCC**：一个 `{"font_size":…, "body":[{…}]}` 的 JSON。

| 要点 | 说明 |
| --- | --- |
| `from` / `to` 是**秒**（浮点数） | 不是毫秒。按毫秒填会让整条字幕的时间轴放大 1000 倍 |
| `oid` 必须是 **`cid`** | 不是 `aid`。字幕挂在**分P**上 |
| 布尔要发小写字面量 | Python 的 `True` 会被 urlencode 成 `True`；必须显式写 `"true"` / `"false"` |
| `sign` / `submit` 是这两个字面量 | `sign=false`、`submit=true`（提交）/ `false`（只存草稿） |
| 读文件用 `utf-8-sig` | 带 BOM 的 SRT 不是罕见情况 |
| 解析要剥 `<c>` 内联标签 | ASR 字幕里大量存在，不剥会把标签显示给观众 |
| 只合并**相邻且内容完全相同**的字幕 | ASR 常把同一句话切成好几条；但「内容相同但不相邻」不能合并（那是副歌/重复台词） |

`parse_timestamp` 解析不出来返回 `None`（不猜 0）—— 时间错了整条字幕就废了。

## 已知限制

1. **主语言判定依赖平台提供 `-orig` 标记**。平台不给（例如只有人工字幕、无自动字幕）时退化为
   「人工字幕里的中/英」，再退化为 `en`。这是**有界默认**，不是精确判定。
2. **一个视频只下一族字幕**。同时需要中英两份的场景不支持（需求也不要求）。
3. **一次投稿只挂一条字幕轨**。`save` 一次一轨；挂两条要两次调用并各自记状态，本轮不做。
4. **字幕语言标识受平台白名单约束**，繁体之外的地区变体（如 `zh-Hans`）会被压成 `zh`。
   这是平台限制，不是转换 bug。
5. **字幕失败不进徽标**，因此任务行上看不到「字幕没传上去」—— 要看明细行。这是刻意的（见第七节）。
6. **SRT/VTT 之外的格式不支持**（例如 `.ass`、`.json3`）。下载侧不会产出它们，手动放进来会被跳过。

## 增量设计：打开字幕为什么不自己挑程序

> **后续变更（2026-10-21）**：这一节里的「主段 + 右段箭头」两段式**已撤回**。箭头弹的窗口
> （`OpenWith.exe`）从来没被置前过，点下去看起来毫无反应；现在只有一颗按钮，点它就是弹选择器，
> 接口缺省也从 `default` 改成 `chooser`。本次只保留「不自己挑程序」这一层设计。
> 见 [ui/006](../../ui/006-the-subtitle-arrow-looks-dead.md)。

需求给了两条路（「直接调默认程序」或「弹窗让用户选」）并额外要「记住上一次用的」。三条看起来都要写代码，
其实**只有第三件需要额外的代码，而它的最佳实现是不写**：

### 一、程序的选择权交给系统，因为这个权限本来就是它的

系统里有一份完整的处理器注册表（`.srt` / `.vtt` → 谁），还有一份「这个用户上次选的是谁」
（`HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\<ext>\UserChoice`）。
应用侧再列一份 notepad / VS Code / Sublime 的清单，只能是它的子集 —— 用户装了别的我们不知道，
三个平台还得各维护一份。所以这里只暴露 shell 的两种 verb：

| 界面动作 | 后端 | 谁决定程序 |
| --- | --- | --- |
| 主段（`title="打开字幕文件"`） | `os.startfile(path)` | 该后缀的关联程序 |
| 右段小箭头（`title="选择打开方式"`） | 系统「打开方式」窗口 | 用户当场挑，可让系统记住 |

### 二、「记住上一次」由系统记，应用不存第二份

在「打开方式」窗口里勾「始终使用此应用」= 改了该后缀的关联 ⇒ 下次点主段就是它。**这已经满足需求**，
而且比自建记忆更可靠：应用记的那份迟早会和系统的关联结论打架（用户在系统里改过、我们没收到通知），
而用户根本没有判据去决定信谁。代价要写清楚：**换程序要去系统窗口里换**，我们提供入口，但不代管这份状态。

### 三、「没有关联」不是失败，是「还没人认领这个后缀」

干净的 Windows 上点 `.srt`，`os.startfile` 抛 `OSError [WinError 1155]`。资源管理器遇到它是**弹选择器**，
所以这里也只对 1155 降级成弹窗；其它 `OSError` 照原样抛 —— 把「拒绝访问」也变成弹窗，就是把真问题藏起来。

### 四、控件形态：一个按钮做成两段

修饰键（Shift 点击、长按）是最省代码的做法，但**界面上看不见的入口等于没做**。所以做成一个视觉上连在
一起的组合控件：左边 `Captions`（CC 图标，一望即知是字幕）+ `title="打开字幕文件"`，右边一个 20px 的
小箭头 `title="选择打开方式"`。两段共用外框、圆角只留外侧；中间**恰好一条** 1px 分隔线 —— 那是主段自己的
右边框，右段 `border-left: 0` 去掉叠起来的那条（否则是 2px 的双线）。这条线留着是有意的：它把「一个动作 +
一个下拉」读成同一控件的两半，比一整块没有分界的板子更清楚。

（真机探针 `tmp_acceptance/ui_subtitle_button.py` 量到的是：`gap=0.0px`、内圆角 `0px`、
主段右框 `1px` / 右段左框 `0px`、两段与相邻文件夹按钮 `Δheight=0.00px`。这些在 jsdom 里一律量不到 ——
`getBoundingClientRect()` 全返回 0。）

### 五、按钮可点性只看后端算出的 `subtitle_path`

界面上不猜「这条有没有字幕」。读模型给的是**真的存在于磁盘**的路径（记录值优先，其次看视频旁边有没有
同名字幕 —— 补这一列之前的历史任务靠后者），`null` 就把按钮灰掉。理由与 `output_path` 一致：
可点的按钮点下去必然报错，比灰着的按钮更让人困惑。

### 六、`mode` 用枚举，不用布尔

`default` / `chooser` 两个取值，别的值 `422`。写成 `chooser: bool` 的话，将来再加一种打开方式（比如
「用指定的程序」）就要加第二个布尔，四个组合里有两个是没意义的。
