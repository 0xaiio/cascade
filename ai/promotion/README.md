# cascade 推广物料

本目录存放 cascade（YouTube Downloader）的对外推广物料。**展示的界面与命令全部来自真实运行**，
不是示意图；逐镜旁白与字幕脚本见 [promo-script.md](promo-script.md)。

| 文件 | 说明 |
| --- | --- |
| [promo-script.md](promo-script.md) | 宣传文稿：要点覆盖、成片规格、分镜表、逐镜旁白/字幕、可复现的演示操作清单 |
| [cascade-promo.mp4](cascade-promo.mp4) | 成片，1920×1080 / 30 fps / H.264 + AAC |
| [cascade-promo.srt](cascade-promo.srt) | 外挂字幕（与片内硬字幕同源） |

## 发布记录

成片已投稿到 B站，并归入合集 `0xaiio`。

| 项 | 值 |
| --- | --- |
| 稿件 | [BV1JQJY6LExf](https://www.bilibili.com/video/BV1JQJY6LExf)（aid `117453598230428`，cid `42806217520`） |
| 标题 | cascade：本机 YouTube 下载控制台｜解析·下载·任务中心·B站投稿完整演示 |
| 分区 | `231` 科技 · 计算机技术 |
| 标签 | `youtube`、`yt-dlp`、`bilibili` |
| 类型 | 自制（`copyright=1`） |
| 封面 | 抽自成片第 6 秒的片头卡 |
| 合集 | `0xaiio`（season_id `9343273`，「正片」分区 section_id `10455347`，`epCount=1`） |
| 置顶评论 | rpid `320952847680`（2026-10-17）：项目仓库链接 + SSH/HTTPS 克隆命令 + 一键启动命令 |
| 投稿时间 | 2026-10-17 |

置顶评论走的是项目自己的 `bili_api.send_and_top_comment()`（先查自己有没有发过同一条，
有就只补置顶，所以重跑是幂等的）。**首次置顶返回过一次 `-404 啥都木有`**，12 秒后重跑同一条
就成功了 —— 新评论还没被索引进置顶接口时，这条接口报的也是这个既不像网络问题也不像权限问题的码。

投稿与归集**没有走应用界面**，而是复用项目自身的模块（`bili_bin` 调 `biliup`、`bili_auth` 读登录文件、
`bili_api` 打合集接口）——因为应用的投稿入口是**任务行级别**的，只受理它自己下载完成的任务，
无法受理一个仓库里的现成视频文件。

**本片之后的功能更新**：成片基于提交 `0dd4b5d` 制作；该提交之后 `main` 上的新功能与修复
（字幕流水线、任务完成提醒、进程重启恢复、时长显示、B站 上传体验大修）逐条记录在
[更新日志](../docs/changelog/CHANGELOG.md)，附镜头建议，是制作下一支视频的素材清单；
分支对比见 [BRANCH-DIFF](../docs/changelog/BRANCH-DIFF.md)（结论：下一支视频应以 `main` HEAD 为基准）。

### 归集接口需要的是「分区 id」，不是「合集 id」

实测记录（2026-10-17）：

- 建合集 `POST /x2/creative/web/season/add` 返回的是**合集 id**（`9343273`）；
- 归集 `POST /x2/creative/web/season/section/episodes/add` 的 `sectionId` 要的是**分区 id**
  （该合集默认分区「正片」的 `10455347`）；
- 传合集 id 时接口返回 `code=-404 message=啥都木有`；换成分区 id 立刻成功（`epCount` 从 0 变 1）。

也就是说：**从界面新建合集再归集这条路径在平台侧会失败**，报的是 `-404 啥都木有`——
一句既不像网络问题、也不像权限问题的提示。**已修复并验证**，见
[`ai/bug-fix/013`](../bug-fix/013-season-section-id-confusion.md)。

## 成片参数（实测）

| 项 | 值 |
| --- | --- |
| 时长 | 362.6 秒（6 分 03 秒） |
| 画面 | 1920×1080，30 fps，H.264（CRF 21） |
| 音频 | AAC 192 kbps / 48 kHz 立体声，响度归一至 −15 LUFS，真峰值 −1.3 dBTP |
| 字幕 | 81 条，硬字幕烧录；句子边界取自 TTS 的词级时间戳 |
| 体积 | 约 40.7 MB |

用 `ffprobe` 复核：

```bash
ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=codec_name,width,height,r_frame_rate,channels \
  -of default=nw=1 ai/promotion/cascade-promo.mp4
```

## 逐镜时长（旁白音频实测值）

| 镜 | 场景 | 时长 | 镜 | 场景 | 时长 |
| --- | --- | --- | --- | --- | --- |
| S0 | 片头 | 12.4s | S7 | 下载选项 | 41.1s |
| S1 | 项目定位 | 17.2s | S8 | 加入队列与任务中心 | 35.0s |
| S2 | 目标用户与边界 | 23.3s | S9 | 设置与运行环境 | 37.5s |
| S3 | 克隆仓库 | 13.3s | S10 | 代理与 cookies | 37.4s |
| S4 | 一键启动 | 24.7s | S11 | 辅助说明 | 17.6s |
| S5 | 打开首页 | 22.6s | S12 | B站 投稿 | 50.2s |
| S6 | 解析链接 | 19.4s | S13 | 片尾 | 11.0s |

合计 362.5 秒；各镜时长由旁白音频长度决定，画面被裁/补到同一长度，因此字幕、解说、画面三者严格对齐。

## 制作方式

1. **旁白**：`edge-tts`（`zh-CN-YunyangNeural`，男声，语速 −4%）逐镜合成，同时抓取 `WordBoundary`
   事件；字幕行按词级时间戳回填，不做人工估时。
2. **画面**：
   - 界面场景：本机真实运行 cascade，用 CDP 驱动一个无头 Edge（1440×810 @2x）真实点击/输入/滚动，
     同时抓截屏流，按帧时间戳编码成片段。
   - 终端场景：真实执行 `git clone` 与 `init\start.ps1`，把命令与输出逐行重放到浏览器里渲染的终端界面。
   - 片头片尾：HTML 渲染的标题卡。
3. **合成**：ffmpeg 归一化各镜 → 拼接 → 烧录 ASS 字幕 → 混入旁白与低电平音床 → 响度归一。

## 保真度说明（重要）

为了透明，以下处理必须写明；它们都不改变「界面上看到的内容是真的」这一点。

1. **界面全部真实**。S5–S12 的画面来自真实运行的应用：真实解析、真实创建下载任务、真实进度与速度、
   真实的任务库、真实的 B站 环境体检（本机已装 `biliup` 且已登录）。
2. **S6「解析」的响应到达时刻被固定**。本机 `yt-dlp` 解析一个视频的耗时实测在 5～29 秒之间波动，
   直接录无法保证「结果出现」恰好落在对应旁白处。因此录制时用 CDP 的 `Fetch` 域拦截
   `/api/analyze`，只接管**响应到达的时刻**（固定约 6 秒）；**响应体取自同一后端真实解析结果的原始
   JSON**，点击与渲染都是真实的。
3. **终端场景是真实输出的重放**。S3/S4 的命令与输出逐行取自真实执行的 transcript
   （见下方「录屏为何不走控制台窗口」）。为了让画面稳定，重放发生在浏览器渲染的终端里，
   而不是直接录控制台窗口。
4. **跳剪而非加速伪造**。本机到 GitHub 的一次完整克隆实测 288 秒（SSH）、启动脚本实测约 20 秒；
   成片用跳剪呈现「开始 → 完成」，中间等待被剪掉。
5. **克隆使用 SSH**：`git@github.com:0xaiio/cascade.git`。片头/片尾卡显示的是网页地址
   `github.com/0xaiio/cascade`（供观看者直接访问），两者用途不同。
6. **启动脚本录制加了 `-NoProfile`**：本机 PowerShell 配置文件里有一条与项目无关的 `opam`
   报错，加该开关只为不把这条噪声录进成片；命令语义不变。
7. **终端提示符做了简写**：显示为 `D:\projects`，实际工作目录是
   `tmp_acceptance/promo/demo_ws/`。
8. **没有向 B站 提交任何内容**：S12 只打开投稿表单，没有点击「开始上传」，也没有产生任何真实投稿。
9. **演示素材**：下载/投稿演示使用公开视频 *C++ Weekly - Ep 25 C++17's Aggregate Initializations*，
   仅用于展示解析、下载与投稿界面。

### 录屏为何不走控制台窗口

原计划直接把控制台窗口录下来。实测本机控制台宿主存在两处不可控行为：

1. `SetCurrentConsoleFontEx` 会触发宿主**异步**重排窗口，量到的客户区尺寸与真正绘制的区域不一致；
2. `SetConsoleScreenBufferSize` / `SetConsoleWindowInfo` 在请求超出屏幕可容纳格数时返回失败，
   但随后的 `MoveWindow` 仍会把窗口拉大 —— 结果是窗口很大、控制台只画左上角，其余露出桌面
   （实测请求 172 列、系统上限 170 列时必现）。

两条都表现为「录出来的画面里混进桌面」。因此终端场景改为：真实执行命令 → 记录 transcript →
在浏览器里按同一时序重放。命令与输出文本没有改动。

## 复现步骤

工具链（Windows）：

```text
1) edge-tts：pip install edge-tts
2) 浏览器：Microsoft Edge（CDP 无头驱动）
3) ffmpeg / ffprobe：需含 libass、libx264
```

流程（中间产物都落在 `tmp_acceptance/promo/`，该目录不进入 Git）：

```text
python make_narration.py        # 合成旁白 + 词级字幕时间轴 -> out/narration/
python record_browser.py        # 录界面场景 -> out/frames/<ID>/
python record_terminal_web.py   # 录终端场景 -> out/frames/TERM_<ID>/
python build_video.py           # 归一化 + 拼接 + 字幕 + 混音 + 响度归一 -> ../ai/promotion/
```

`record_browser.py` 需要一个已在运行的后端（默认 `http://127.0.0.1:8010`），可用
`python -m app --port 8010` 在 `backend/` 下启动。

## 待处理

**（已关闭）新建合集后的归集把「合集 id」当成了「分区 id」** —— 2026-10-17 发现，
同轮修复并验证，记录在 [`ai/bug-fix/013`](../bug-fix/013-season-section-id-confusion.md)。

- 位置（修复前）：`bili_upload.group_into_season()` 先 `create_season()` 拿到合集 id，随后
  `add_episodes(season_id=<合集 id>)`；而 `bili_api.add_episodes()` 把它写进请求体的
  `sectionId`。接口路径是 `.../season/section/episodes/add`，这个字段要的是**分区 id**。
- 现象：`code=-404 message=啥都木有`。这条记录会落成「归集失败」并给出「重试归集」，
  但重试仍会用同一个合集 id，因此**不会自愈**。
- 修法（已实施）：新增 `bili_api.find_season_section()`，归集前回读合集列表把合集 id 换成
  默认分区（`type == 1`）的 id；换不到就落一条说得清的结论、**不发归集请求**。
  两条路（新建 / 加入已有）都要换号，所以重试会重新换、能自愈。
- 复现证据（2026-10-17 实测）：合集 id `9343273` → `-404`；分区 id `10455347` → 成功，`epCount` 0→1。
- 遗留（不在本条范围）：重复添加返回 `20080 当前稿件已存在在合集中` 仍判失败；
  多分区合集进哪个分区是猜的（按 `order` 取第一个）。两条都记进了
  [bug-fix README 的「已知但未处理」](../bug-fix/README.md#已知但未处理的问题下一轮候选) 第 16 条。

## 维护

改动界面或调整旁白后，需要**重跑受影响的镜**再重新合成；只改旁白的镜头无需重录画面。
`promo-script.md` 与成片必须同步：分镜表的时长、旁白文本、字幕都是同一份数据的三种呈现。
