# 测试与验收：字幕下载与 B站 CC 字幕上传

适用读者：要验证这段功能、或要判断「某个改动会不会悄悄破坏它」的人。
需求在 [01-requirements.md](01-requirements.md)，实现在 [03-implementation.md](03-implementation.md)。

## 自动测试（全离线，不需要真实账号/网络）

命令与项目其它部分一致，见 [docs/testing.md](../../../docs/testing.md#自动测试命令)。本节只列**本功能**
相关的文件与用例数（实测收集数，2026-10-18）：

| 文件 | 用例数 | 本功能覆盖什么 |
| --- | --- | --- |
| [test_subtitle_plan.py](../../../backend/tests/test_subtitle_plan.py) | **45**（新增） | 族判定（`zh-Hans`/`en-US`/`zh-Hans-orig` 归族、`yue` 不算中文、其它语种返回 `None`）；`-orig` 原声轨优先于人工字幕；**原声轨与人工字幕冲突时以原声轨为准**；同族多条轨只挑一条（简体优先于繁体、基础码优先于地区变体、`-orig` 排后）；人工优先 → 降级 ASR → `none`；`allow_human` / `allow_auto` 两个开关各自的效果；**每条分支的 `reason` 都非空**；`subtitle_summary` 区分「`none`」与「没有结论（`""`）」 |
| [test_bili_subtitle.py](../../../backend/tests/test_bili_subtitle.py) | **38**（新增） | 时间戳 → 秒（`00:01:02,500` → 62.5，解析不出来返回 `None` 而非 0）；SRT 与 VTT 共用解析（含 WEBVTT 头、NOTE 块、`align:start position:0%`）；剥 `<c>` 内联标签与反转义；**只合并相邻且完全相同**的字幕（不相邻的同文不合并）；BCC 形状与 `from`/`to` 是秒；`utf-8-sig` 读带 BOM 文件；**语言白名单参数化**（`zh-CN`/`en-US`/`zh-Hans`/`en-GB` 必须映射出白名单内的值）；端点与表单断言（`oid == cid`、`sign == "false"`、`submit == "true"`、缺 `bili_jct` 报错）；**失败一律落成结论、绝不抛异常**（文件不存在 / 扩展名不对 / 空字幕 / 转换失败 / 接口失败） |
| [test_subtitle_recording.py](../../../backend/tests/test_subtitle_recording.py) | **6**（新增） | 落库**按磁盘证据**而不是计划；自动模式只请求计划里那一条轨；没有可用轨时请求族名（不是空列表，也不是 `["all"]`）；字幕文件缺失时落成 `none`；非中英语种不被记录；**重跑会清掉上一轮的旧结论** |
| [test_output_paths.py](../../../backend/tests/test_output_paths.py) | 18 → **26**（+8） | 字幕定位：标题里带 `[` `]` 时仍能找到（**不能用 `glob`**，它把方括号当字符类）；不会误吃到 `作品 [abc123]2.en.srt` 这类同前缀文件；按族偏好挑一条；不支持的语种被过滤；语言取 stem 最后一段点号后 |
| [test_bili_upload.py](../../../backend/tests/test_bili_upload.py) | 69 → **76**（+7） | 投稿成功时挂字幕；`zh-Hans` → `zh`；**字幕失败不把稿件状态翻成失败**；没有字幕 / 未核实稿件 / 文件已不在 → `skipped`（各带一句人话）；`reverify` 可只重挂字幕 |
| [test_ytdlp_service.py](../../../backend/tests/test_ytdlp_service.py) | 74 → **81**（+7） | 显式字幕语言优先于默认；**非中英语言被忽略**、混合列表只留下中英；计划随 `DownloadPreparation` 回传（语言/轨码/来源/理由）；`subtitles_only` 路径也算出计划、且元数据失败在那里不致命 |
| [bili-upload.test.tsx](../../../frontend/src/bilibili/bili-upload.test.tsx) | 50 → **51**（+1） | 投稿预览表**在提交前**显示每条会带什么字幕（`en · 人工` 与 `无字幕` 都要渲染出来） |

合计后端 **+111**（928 → 1039），前端 **+1**（195 → 196）。改完必须自己数一遍，不要只信 `-q` 的汇总行
—— 历史上把「收集不全的那一次」当成基线，写出过 267 / 286 / 289 三个数，而真实基线一直是当时的 913。

## 没被自动测试覆盖的部分

| 缺口 | 为什么 | 怎么补 |
| --- | --- | --- |
| 平台真的接受我们发的 BCC | 需要真实账号与已发布稿件 | 用 `submit=false` 的草稿探针（下一节） |
| 播放器里字幕**真的可关** | 只能人工看 | 手工验收第 4 步 |
| yt-dlp 实际写出的文件名形状 | 依赖版本 | 手工验收第 1 步看磁盘产物 |
| 主语言判定在真实视频上的准确率 | 依赖平台给不给 `-orig` | 抽 5~10 个真实视频对照（手工验收第 5 步） |

## 语言白名单的实测依据

`zh-CN` / `en-US` 被拒这条结论不是推理出来的，是**逐个枚举出来的**。做法是用真实账号对
`POST /x/v2/dm/subtitle/draft/save` 发 **`submit=false`**（只存草稿、不发布）的请求，逐个试候选值：

- 探针脚本落在 `tmp_acceptance/`（gitignore，不入库）：
  `probe_bili_lan.py`、`probe_bili_lan_values.py`、`probe_bili_subtitle.py`、`probe_bili_subtitle_write.py`。
- 判据是响应里的 `code`：`0` = 接受，`79011 不合法的语言` = 拒绝。

结果：

| 接受 | 拒绝 |
| --- | --- |
| `zh` `en` `zh-Hant` `zh-TW` `zh-HK` `ja` `ko` `yue` `fr` | `zh-CN` `en-US` `zh-Hans` `en-GB` |

单测里的参数化用例把这张表钉住了：**任何一个值被改成非法值，测试当场红**。

`submit=false` 值得单独记一笔：它是唯一能在**不动任何已发布稿件**的前提下验证接口形态的方式。
真发一次投稿去试语言值，试错成本是一堆不可撤销的稿件。

## 手工验收

前置：已登录 B站（设置面板的「B站 上传」区显示已登录）、有一个**已下载完成**的视频任务。

| # | 步骤 | 期望 |
| --- | --- | --- |
| 1 | 下一个**英文**视频（最好只有 ASR 英文、没有人工英文字幕） | 下载成功；任务明细/接口里 `subtitle_language=en`、`subtitle_source=auto`；视频旁边出现 `.en*.srt` 一类的文件 |
| 2 | 下一个**中文**视频（有人工 `zh-Hans`） | `subtitle_language=zh`、`subtitle_source=human`；**只有一份**中文字幕文件（不会同时落下 `zh-Hans` 与 `zh-Hans-orig` 两份） |
| 3 | 下一个**只有日文字幕**的视频 | 视频下载**照常成功**，字幕结论是「没有可用字幕」，**不报错、不中断** |
| 4 | 把 1 或 2 的产物投稿到 B站 | 稿件状态「已上传」；明细行字幕一句显示「CC 字幕已上传（N 条，en/zh），观众可在播放器里开关」；**打开 B站 播放器确认字幕开关存在、且默认可关** |
| 5 | 把 3 的产物投稿 | 稿件正常发布；字幕一栏「已跳过」；**稿件状态不受影响** |
| 6 | 在投稿表单的预览表里看「字幕」列 | 每条显示 `en · 人工` / `zh · 自动生成` / `无字幕` 之一 |
| 7 | 制造一次字幕失败（把字幕文件改名后重传） | 稿件状态**不变**（仍是「已上传」），明细行显示「已重新上传字幕」或失败原因；点重试**不会**再投一次视频 |
| 8 | 抽 5~10 个真实视频对照主语言判定 | 判错的那个要看 `reason` 日志（`subtitle plan: …`）确认是走了哪一级判据 |
| 9 | 投稿一条**带字幕**的视频，等稿件在 B 站 可正常访问后，看投稿明细的字幕行并点「重传字幕」 | 字幕行从「平台还没给出分P编号」变成「CC 字幕已上传（N 条，…）」；`aid` / `bvid` 前后不变（没有重投）。见 [016](../../bug-fix/016-subtitle-lost-while-the-video-is-in-review.md) |

第 7 步的判据：**投稿记录里的 `aid` / `bvid` 在重试前后是同一个**。变了就说明重投了视频，那是错的那一边。

## 回归点

改动下列位置时，本功能的测试必须重跑：

- `subtitle_plan.py` 的任一条判定 → `test_subtitle_plan` + `test_subtitle_recording` + `test_ytdlp_service`
- `output_paths.py` 的发现逻辑 → `test_output_paths`（**特别注意 `iterdir` 不能被改回 `glob`**）
- `bili_upload.run_single` 的步骤顺序 → `test_bili_upload`（`_attach_subtitle` 必须在拿到 aid/cid 之后）
- `bili_manager.retry_upload` 的三分支 → `test_bili_manager`
- 契约层任一处字段增删 → `scripts/check_api_contract.py`

## 增量测试：打开字幕（2026-10-18 下午）

> **后续变更（2026-10-21）**：下表的用例数已变——「右段打到 `?mode=chooser`」那条改成断言
> **没有那颗箭头**、且点击只发一条**不带 `mode`** 的请求；`test_api.py` 里「`mode=chooser` 与默认
> 走不同入口」改成「**缺省**走选择器、`?mode=default` 走文件关联」。见
> [ui/006](../../ui/006-the-subtitle-arrow-looks-dead.md)。

需求见 [01](01-requirements.md#增量打开字幕文件2026-10-18-下午)，设计见 [02](02-design.md#增量设计打开字幕为什么不自己挑程序)。

| 文件 | 用例数 | 增量覆盖什么 |
| --- | --- | --- |
| [test_system_open.py](../../../backend/tests/test_system_open.py) | 4 → **8**（+4） | **没有关联（`1155`）时改弹系统选择器**；非 1155 的 `OSError` 照原样抛（不许把「拒绝访问」变成弹窗）；`chooser` 真的起系统的「打开方式」入口；非 Windows 退化成默认打开 |
| [test_output_paths.py](../../../backend/tests/test_output_paths.py) | 26 → **30**（+4） | `existing_subtitle_file` 四条：记录值优先、记录为空时退回视频旁边的同名字幕（历史任务）、记录指向已删文件时返回 `None`、视频没定位到时不扫目录 |
| [test_api.py](../../../backend/tests/test_api.py) | 84 → **92**（+8） | 单视频与条目级各自打开**自己**那份字幕；`mode=chooser` 与默认走**不同**入口（两条路都真的不同才叫实现了选程序）；没有字幕 `409` 且什么都没打开；`mode` 取别的值 `422`；读模型给出 `subtitle_path`；**旧记录（该列为空）能靠视频旁边的同名字幕找到并打开**；文件没了回 `null` |
| [task-center-files.test.tsx](../../../frontend/src/task-center/task-center-files.test.tsx) | 9 → **13**（+4） | 单视频与子视频各自打到自己的接口、带 `?mode=default`；右段打到 `?mode=chooser`；`subtitle_path` 为空时**主段与右段都禁用**；失败文案落在对应那一行 |

合计后端 **1050 → 1066**（+16），前端 **196 → 200**（+4）。逐文件相加恰等于全集
（2026-10-18 实测；上一轮记录的 1039 是当前基线的前一个数，中间合入了「进程重启恢复」那 11 条）。

### 没被自动测试覆盖的部分

| 缺口 | 为什么 | 怎么补 |
| --- | --- | --- |
| 点下去后程序**真的**起来了 | 单测里 opener 是替身，起不了真进程 | 手工验收第 12a 步 |
| 「记住上一次用的程序」真的生效 | 这是**系统**的行为（改的是 `FileExts\<ext>\UserChoice`），应用侧没有可断言的变量 | 手工验收第 12a 步后半段：勾了「始终使用」之后，再点主段要用同一个程序 |
| 系统的「打开方式」窗口长什么样 | 是系统 UI，只能看 | 同上 |

### 手工验收（第 12a 步，摘录）

| # | 步骤 | 期望 |
| --- | --- | --- |
| 12a-1 | 对一条**下到字幕**的视频点主段 | 一个程序真的打开了那份 `.srt`/`.vtt` |
| 12a-2 | 点右段小箭头 | 弹出系统「打开方式」窗口 |
| 12a-3 | 在窗口里挑另一个程序并勾「始终使用此应用」，再点主段 | **直接用同一个程序**打开 —— 这是「记住」唯一可验的判据 |
| 12a-4 | 对一条**没下到字幕**的视频看按钮 | 灰着，悬停提示「字幕文件尚不可用」 |
| 12a-5 | 手动删掉字幕文件后刷新页面 | 按钮变灰（读模型不回声已不存在的路径） |

## 增量测试：审核期的字幕（2026-10-21，缺陷 [016](../../bug-fix/016-subtitle-lost-while-the-video-is-in-review.md)）

验收这一段时对着**真实库**复核，发现一条真实丢字幕的路径：稿件在审核/转码中，
`x/web-interface/view` 取不到分P编号 ⇒ `cid` 为空 ⇒ 字幕挂不上，却被记成 `skipped`；
而重试只对 `failed` 开放补传 ⇒ 那条字幕永久丢失（本机库实测 2 条）。

| 文件 | 用例数 | 增量覆盖什么 |
| --- | --- | --- |
| [test_bili_upload.py](../../../backend/tests/test_bili_upload.py) | 76 → **77**（+1） | 审核中（`state=-30` + 详情接口为空）投稿：稿件 `reviewing`、`cid is None` ⇒ 字幕必须记 **`failed`** 并给出「重传字幕」的指引；一次字幕请求都没发 |
| [test_bili_manager.py](../../../backend/tests/test_bili_manager.py) | 82 → **84**（+2） | ①完整真实序列：审核中投稿 → 字幕 `failed` → 重试仍取不到 `cid` ⇒ `action=reverify` 且回执说「仍然没传上去」→ 过审后能取到 `cid` ⇒ 补传成功 `posted`；**全程投稿命令只跑 1 次** ②历史行（`published` + `skipped`）也能补传 —— 放宽判据只为救已有数据 |

前端合计 **200 → 208**（+8，新增 1 个文件）：

| 文件 | 用例数 | 增量覆盖什么 |
| --- | --- | --- |
| [bili-subtitle-line.test.tsx](../../../frontend/src/bilibili/bili-subtitle-line.test.tsx) | **5**（新文件） | `posted` 显示一句结论且**不给**重传按钮；`failed` 带警告并给「重传字幕」、点了打到**这一条**；`skipped` 不画这一行；稿件本身没 `video_url`/`error` 时字幕失败**仍单独撑起一行**；「已上传 + 字幕失败」是两行，徽标文案不被带偏 |
| [biliStatus.test.ts](../../../frontend/src/bilibili/biliStatus.test.ts) | 15 → **17**（+2） | 字幕两档判据：`isBadSubtitle` 只有 `failed`，`isNotableSubtitle` 是 `posted` + `failed`；`isNotableUpload` 把字幕失败也算「有话说」 |

**顺带补上的一处缺口**：这块渲染代码（`BiliJobStatus.tsx` / `biliStatus.ts` / `types.ts` 的
`BiliSubtitleState`）此前一直**没进版本库** —— HEAD 里既没有「CC 字幕」那一行的渲染，
也没有任何用例。后果是后端三档状态齐全、用户却**看不到字幕结论、也没有补传入口**，
与本轮修的补传路径直接矛盾。本轮把它连同用例一起提交。

后端合计 **1066 → 1069**（+3），前端 **200 → 208**（+8）。
六块沙箱分块命令里的 `c` 块 213 → 216，其余不变。

变异测试：把 `_attach_subtitle` 的第三条分支还原成修复前（只设 `subtitle_message`、不动
`subtitle_state`），两条新用例都红在 `assert 'skipped' == 'failed'`；恢复后 10 条字幕用例全绿。

### 没被自动测试覆盖的部分（本增量）

| 缺口 | 为什么 | 怎么补 |
| --- | --- | --- |
| 「过审之后 `view` 接口真的会给出 `cid`」 | 要等平台的审核周期，且会在真实账号留稿件 | 手工验收第 9 步：投稿带字幕的视频，等稿件可访问后点「重传字幕」 |
| 历史那两条丢字幕的稿件已实际补回 | 需要真实账号与已过审稿件 | 同上；判据是投稿记录里 `subtitle_state` 从 `skipped` 变 `posted`、且 `aid`/`bvid` 不变 |
