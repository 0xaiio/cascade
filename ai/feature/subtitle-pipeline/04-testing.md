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
| [bili-upload.test.tsx](../../../frontend/src/bili-upload.test.tsx) | 50 → **51**（+1） | 投稿预览表**在提交前**显示每条会带什么字幕（`en · 人工` 与 `无字幕` 都要渲染出来） |

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

第 7 步的判据：**投稿记录里的 `aid` / `bvid` 在重试前后是同一个**。变了就说明重投了视频，那是错的那一边。

## 回归点

改动下列位置时，本功能的测试必须重跑：

- `subtitle_plan.py` 的任一条判定 → `test_subtitle_plan` + `test_subtitle_recording` + `test_ytdlp_service`
- `output_paths.py` 的发现逻辑 → `test_output_paths`（**特别注意 `iterdir` 不能被改回 `glob`**）
- `bili_upload.run_single` 的步骤顺序 → `test_bili_upload`（`_attach_subtitle` 必须在拿到 aid/cid 之后）
- `bili_manager.retry_upload` 的三分支 → `test_bili_manager`
- 契约层任一处字段增删 → `scripts/check_api_contract.py`
