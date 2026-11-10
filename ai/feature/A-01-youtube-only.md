# A-01　非 YouTube 链接：明确拒绝，而不是给出莫名报错

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §A-01。2026-11-11 落地，一项一个提交。

## 结论：判定放在**发请求之前**，并且只保留一个维度 —— 站点

候选池的原话是「解析入口加站点判定，非 YouTube 域直接返回『本工具只支持 YouTube』」。做的时候把它拆成两层，因为**「主机名」和「yt-dlp 的判断」各自会骗人**：

| 层 | 时机 | 能回答什么 | 不能回答什么 |
| --- | --- | --- | --- |
| 主机名白名单 | 收到请求后立刻 | 这个链接**根本不用试** | 短链、跳转、代理可以让主机名与真实站点不一致 |
| yt-dlp 的 `extractor_key` | 拿到页面之后 | 平台自己说它是什么 | 要花一次网络请求；对「不该试」的站点已经太晚 |

所以两层都留：第一层负责「拒绝得体且不浪费请求」，第二层负责「主机名说谎时仍然判对」。候选池风险栏里那句「以 `extractor_key` 判定优先于 URL 正则」是第二层的依据，不是取消第一层的理由 —— 只有第二层时，B站 链接仍然要先发出一次请求、再由 yt-dlp 抛一句与本应用无关的错误。

## 为什么必须「一次请求都不发」

原来的行为不是「报错难懂」，而是**报错与本应用无关**：`POST /api/analyze` 把 B站 / 抖音 / 播客链接直接交给 yt-dlp，回来的可能是 `Unsupported URL`、`Requested format is not available`、甚至一次 403。这三句都指向 YouTube 的策略（profile 链、PO token、分辨率档位），而排障手册里查不到它们与「你贴错了站点」的关系。

拒绝文案因此有三段，缺一段都会把人引向错误方向：

> 本工具只支持 YouTube 链接（youtube.com / youtu.be）。这个链接的站点是 `bilibili.com`，没有向它发起请求 —— 换一个 YouTube 链接再试。

- 「只支持 YouTube」：说明这是**边界**，不是故障；
- 「站点是 …」：证明判定看的是这条链接，不是随机失败；
- 「没有向它发起请求」：排除网络 / 代理 / cookies 三条最常见的误判方向。

主机名会回显到界面上，所以回显前只保留 `[a-z0-9.-]`：用户输入里的尖括号、空格、引号不能原样进文案。

## 实现

- [`ytdlp_service.py`](../../backend/app/download/ytdlp_service.py)：
  - `YOUTUBE_HOST_SUFFIXES = ("youtube.com", "youtu.be", "youtube-nocookie.com")`；
  - `is_youtube_url()` 按**主机名的点**切分（`host == suffix or host.endswith("." + suffix)`），并先把协议限死在 http/https —— `ftp://youtube.com/x` 的主机名是对的，但它不是网页地址；
  - `unsupported_site_message()` 区分「站点不对」与「根本不是 http/https 地址」两种句式，两句话都带「只支持 YouTube」与「没有发请求」；
  - `ensure_youtube_url()` 是入口用的抛错版本；
  - `is_youtube_extractor()` 读 `extractor_key`（兼容 `extractor`）：`Youtube` / `YoutubeTab` / `youtube:tab` 都算；**缺这个键时返回 True**，不在服务层二次拒绝；
  - `extract_metadata()` 在 `extract_info` 之后、映射条目之前复核一次。
- [`api_support.py`](../../backend/app/api/api_support.py) 的 `extract_metadata_with_cookies()` 第一行就调 `ensure_youtube_url()`。放在这里而不是路由里，是因为 `POST /api/analyze`、`POST /api/jobs` 与克隆**三条路都经过它** —— 判定只写一次。
- 前端没有改动：400 的 `detail` 由既有的页面级错误回执条原样显示（[ui/009](../ui/009-page-level-errors-scroll-out-of-view.md)），用户滚到哪里都看得见。

## 覆盖的边界

| 输入 | 结果 | 依据 |
| --- | --- | --- |
| `https://www.bilibili.com/video/...` | 400 + 站点名，**0 次请求** | 主机名不在白名单 |
| `https://vimeo.com/...` / `https://example.com/...` | 同上 | 同上 |
| `https://evil-youtube.com/...` | 拒绝 | 后缀必须落在点上，不是字符串包含 |
| `https://youtube.com.evil.example/...` | 拒绝 | 同上（`endswith(".youtube.com")` 为假） |
| `ftp://youtube.com/...` / `file:///...` | 拒绝，用「不是 http/https」那句 | 协议白名单 |
| `youtu.be/abc`（没有协议）、`随便一段文字`、空串 | 拒绝，用「不是 http/https」那句 | `hostname` 为空 |
| host 里带 `<`、`>` 等字符 | 拒绝，且文案里不出现它们 | 回显前白名单过滤 |
| `youtu.be/*`、`m.` / `music.` / `www.youtube.com`、`/shorts/*`、`/live/*`、`playlist?list=`、`youtube-nocookie.com/embed/*` | 正常解析 | 11 条参数化用例 |
| 主机名是 YouTube、`extractor_key` 是 `BiliBili` | 400 | 第二层复核 |
| `extractor_key` 缺失（测试替身 / 老版本 yt-dlp） | 放行 | 不在服务层拒绝，避免把「没写字段」误判成「不是 YouTube」 |
| `POST /api/jobs` 用非 YouTube 链接 | 400，且不留下任务记录 | 与解析共用入口 |

## 验证

| 层 | 结论 |
| --- | --- |
| [`test_analyze_scope.py`](../../backend/tests/test_analyze_scope.py) | 35 条：11 条合法形态、11 条拒绝形态、文案含站点 / 「没有发请求」/ 不回显原始输入、解析与建任务都 400 **且替身的 `extract_urls == []`**、合法链接确实到达服务层、`extractor_key` 两层判定与缺键放行 |
| 后端全量 | **1428 passed**（57 个 `test_*.py`），包含原来的 1393 条 —— 没有一条既有用例被这次收窄误伤 |
| 变异验证 | 同时撤掉两层判定（入口的 `ensure_youtube_url` 与 `extract_metadata` 里的复核）：**3 failed / 32 passed** —— 两条入口用例（解析 / 建任务）与一条 `extractor_key` 用例转红；恢复后 35 passed。说明这些断言咬的就是判定本身，不是文案 |

## 遗留

- 白名单是**手写的三组后缀**。YouTube 若新增前端域名（历史上出现过 `gaming.youtube.com`），需要同步补一行；没有做成「从 yt-dlp 的 extractor 列表反查域名」——那会把判定绑到 yt-dlp 的内部结构上。
- 只有 `POST /api/analyze` / `POST /api/jobs` / 克隆这条链有入口判定。**重启任务**不重新校验 URL（它直接用条目里已经存在的 `source_url`），这是有意的：任务能存在，说明当初已经通过过判定。
- 文案是中文单语，与项目其余部分的处理一致；`detail` 里没有错误码，前端按文本原样展示。
