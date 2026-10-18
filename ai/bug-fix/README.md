# 修复记录（bug-fix）

本目录记录**每一处修复**：一个问题 = 一个文件 = 一个 commit。
目的是让后来的人（包括未来的我们）能回答三个问题：**当时坏在哪、为什么坏、凭什么说修好了**。

> **界面缺陷（能跑、不报错，但呈现错位或不一致）不记在这里，记在 [`ai/ui/`](../ui/README.md)。**
> 判据很简单：有报错、有失败用例、数据被破坏 → bug-fix；功能正常但样子不对 → `ai/ui/`。
> 那条目录同样有自己的「已知但未处理」清单。

## 记录约定

每个文件按固定小节写，宁可写「未验证」也不要写猜测：

| 小节 | 要求 |
|---|---|
| 问题 | 用户能观察到的现象，不是代码现象 |
| 原因 | 具体到文件/行/函数，附**实测**证据（命令与输出），不写「可能是」 |
| 修复方案 | 改了什么、为什么这么改、为什么不做别的 |
| 效果 | 修复前后的可对照结果（数字/日志） |
| 验证 | 新增/修改的测试用例表 + 回归结果 |
| 风险与回滚 | 副作用、回滚命令；有破坏性的必须标红说明 |
| 关联 | 指向相邻缺陷，说明它们是否是同一条失败链 |

`未覆盖 / 如实说明` 小节是**必填的诚实项**：本轮没验到的地方要写清楚，
不能用「已修复」掩盖「没验证」。

## 索引

| # | 标题 | 提交 | 一句话 |
|---|---|---|---|
| [001](001-bot-check-does-not-escalate-profile.md) | bot 校验错误不触发 anti403 profile 阶梯 | `545c846` | `Sign in to confirm you're not a bot.` 既非 403 也非连接重置，被当成「换 profile 也没用」，anti403 阶梯一次都没跑过 |
| [002](002-po-token-provider-has-no-browser-path.md) | PO token provider 拿不到浏览器路径 | `f3f7e50` | 插件要求 `browser_path` 存在，配置项默认 `None` → `wpc-1.1.2 (external, unavailable)` |
| [003](003-js-runtime-invisible-outside-path.md) | JS 运行时只查 PATH | `21ebd9d` | 服务进程 PATH 里没有 node → `JS Challenge Providers: node (unavailable)` → nsig 解不出来 |
| [004](004-empty-subtitle-languages-expand-to-all.md) | 字幕语言为空退化成 `["all"]` | `a579a93` | 空列表变成「拉全部字幕轨」→ HTTP 429 → **整个条目判失败，视频本体也下不到** |
| [005](005-edge-cdp-fallback-touches-the-live-profile.md) | Edge CDP 回退挂载真实 profile | `a482a9e` | 既必然失败（Chromium 拒绝默认数据目录），又曾**清空真实 cookie 库（53 → 0）** |
| [006](006-proxy-is-not-configurable.md) | 应用没有代理配置项 | `d779b52` | 环境变量静默顶掉 Windows 系统代理（实测：坏变量 → 502；修好 → HTTP 200），且界面上看不到也改不了 |
| [007](007-js-challenge-fails-only-when-cookies-are-on.md) | 带 cookies 反而失败：n challenge 被宿主环境打坏 | `5e556f9` | `NODE_OPTIONS=--require=` 让 yt-dlp 启动的 node 在权限模型下 `ERR_ACCESS_DENIED` 退出，报出「The page needs to be reloaded.」；而应用当时**根本不写日志**，连查都没法查 |
| [008](008-return-in-finally-swallows-the-real-error.md) | 收尾阶段出的错被静默吞掉 | `fd8b50e` | `finally` 里的 `return` 丢弃正在传播的异常，而 `_worker` 又没有兜底 → 删除/重启竞态下要么条目永久停在 `running`、要么队列静默少一个消费口，两者都不留一行日志 |
| [009](009-local-dev-port-is-occupied-and-unconfigurable.md) | 8000 被 IncrediBuild 长期占用，启动失败而报错指着「权限」 | `b4f7aba` | `Manager.exe` 以 `0.0.0.0:8000` **独占**监听 → 绑 `127.0.0.1:8000` 得到 `WinError 10013`（权限）而不是 10048（地址已用）；端口值散落多处、Vite 代理还硬编码，换端口会让 `/api` 静默打到 IncrediBuild 上 |
| [010](010-unselectable-probe-raise-skips-the-fallback.md) | 提示「已自动降级到 1080p」，却根本没有开始下载 | `5eb90e3` | yt-dlp 把「选不出格式」实现成**抛异常**，而降级分支挂在「返回 `is_selectable=False`」上 → 整段降级成了死代码；默认清晰度 1440p 遇上只有 1080p 的视频必然命中 |
| [011](011-biliup-refuses-a-token-less-cookie-file.md) | 扫码登录只拿到 cookies，biliup 拒绝开工：投稿必失败且查不出原因 | 本次 | 登录文件里 `token_info.access_token` 是空串，`login_by_cookies` 会**无条件先**拿它去 `oauth2/info` 校验，空串被 B站 判成 `-400 请求错误` → biliup 在发出任何投稿请求之前退出（实测 1.4 s）；同时 `on_log` 没有任何调用方，失败建议却让人去 app.log 搜原文 —— 照做只会扑空 |
| [012](012-preflight-contradicts-itself-and-the-server.md) | 投稿表单的本地预检有两处口径不对：列表还在读就要求用户去选合集、标签回显截断写死 12 个字 | 本次 | 两条 `...(cond ? […] : [])` 各自求值，「正在读取…」与「还没选是哪个合集」在列表未就绪时同时成立 → 表单自相矛盾；而回显截断写死 12（服务端是 `TAG_PREVIEW_MAX = 40`），12 < 单项上限 21，于是那个三元的不截断分支**从未执行过** —— 服务端注释早就点名了这个坑 |
| [013](013-season-section-id-confusion.md) | 「新建合集」之后的归集把合集 id 当成分区 id，平台每次都 `-404` 且重试不自愈 | 本次 | `season/add` 回的是**合集** id，而归集接口的 `sectionId` 要的是**分区** id（实测 `9343273` → `10455347`）。两者都是 `int`、又被同一个变量名装着，替身还一律回 `code:0` —— 本地全绿、真账号必失败；重试读回落库的 `season_id`，于是同一个错值再发一遍 |
| [014](014-a-dead-notify-switch.md) | 「完成后通知」是一个谁都没实现的开关 | `e74fddc` | 字段在 `schemas.py`、开关在 `DownloadOptionsPanel`，但前后端**没有任何一行**消费它（检索 `notify` 只命中定义处）。现按「全局选方式 + 逐任务决定要不要」两层接线：提醒在浏览器里做（Web Audio 合成提示音 + 右下角自动缩回的浮层），后端只存偏好并把逐任务开关从 `options_json` 抬进 `JobRead` |

## 本轮（2026-10-01）背景

起点是上一轮的性能修复与真实验收：验收时发现「部分条目整体失败，且它们根本没在下载视频」。
顺着这条线挖下去，发现 001~003 是**同一条失败链**上的三个环节——

```
提取 YouTube 页面
  ├─ 需要 JS 运行时解 nsig        ← 003（探测不到 node）→ 007（node 看得到却跑不起来）
  ├─ 需要过 bot 校验              ← 001（过不了时不会去试 anti403 profile）
  └─ 需要 PO token               ← 002（provider 因缺 browser_path 而 unavailable）
```

**任何一环断掉，「下载 YouTube 视频」就整体不可用。** 三个修复必须同时生效才有意义，
所以它们虽然各自独立提交，效果要合起来看。

004 与 005 是验收过程中暴露的存量缺陷，与上面三条链无直接关系，
但都会让「下载」这件事对用户失效（一个炸 429、一个破坏用户数据）。

006 是这条链的**前置条件**：上面三环再正确，请求到不了 YouTube 也没用。
它也纠正了上一轮分析里「直连超时」那个错误结论 —— 真因是环境变量把系统代理顶掉了，
而当时没有任何地方能看出「应用实际用了哪个代理」。

007 是这条链的**收尾**，也是三次「查不出来」的总结：它发现 003 之后 node 虽然能被看见、
却仍然**跑不起来**（宿主往 `NODE_OPTIONS` 里塞了 `--require`），而真正的报错被吃掉、
抛给用户的是一句 `The page needs to be reloaded.`。修它必须同时补上三件基础设施：
**日志落盘**（在此之前应用从不写日志，INFO 被静默丢弃）、**真跑一次的运行时探测**、
**把异常翻译成下一步的建议**。三者缺一，同类问题还会再花一轮才能查清。

## 本轮（2026-10-01 后续）背景：一次启动失败牵出的两处

这一轮的输入只有**一份启动日志**，而日志的头和尾各躺着一个彼此无关的问题：

```text
backend\app\job_manager.py:630: SyntaxWarning: 'return' in a 'finally' block        ← 008（日志开头）
backend\app\job_manager.py:647: SyntaxWarning: 'return' in a 'finally' block        ← 008
...
ERROR uvicorn.error | [Errno 13] error while attempting to bind on address
('127.0.0.1', 8000): [winerror 10013] 以一种访问权限不允许的方式做了一个...尝试。   ← 009（日志结尾）
```

- [008](008-return-in-finally-swallows-the-real-error.md) 是**症状**：两条 `SyntaxWarning`。
  真问题在语义层 —— `finally` 里的 `return` 会吞掉正在传播的异常，而 `_worker` 又没有兜底，
  于是「条目卡在 running」和「队列静默少一个并发口」是同一件事的两种结局。
- [009](009-local-dev-port-is-occupied-and-unconfigurable.md) 是**服务根本没起来**。
  真因与「权限」无关：8000 被 IncrediBuild 的 Coordinator 长期独占，Windows 因此返回 10013；
  而这条报错之所以把人引偏，正是因为此前没有任何地方能看出「谁占了它」。

两者没有因果关系，各自独立提交、可独立回滚。共同点是同一个毛病：
**报错文本指向的地方，和真正坏掉的地方不是同一处** —— 与 001~007 那一轮的主题一脉相承。

## 本轮（2026-10-01 晚）背景：一份界面提示牵出的一处

输入是用户在 Edge 里下载 `https://youtu.be/hTdSU7q5WCo` 时看到的两句话：

```text
failed · 0/1 完成 · 当前没有 1440p 的视频，低于选定分辨率的最高可用分辨率是 1080p。
检测到 1440p 清晰度，但该清晰度当前没有可下载的视频/音频组合，已自动降级到 1080p。
```

[010](010-unselectable-probe-raise-skips-the-fallback.md) 的特殊之处在于 ——
**它是一处「看起来已经处理了」的缺陷**。降级逻辑写得相当完整：解析元数据分类原因、
算降级候选、用降级后的清晰度**再探一次**、写回状态与文案。
但它的入口条件挂在「探针**返回** `is_selectable=False`」上，而真实故障里探针是**抛异常**，
于是这一整段从未执行过。

与 [008](008-return-in-finally-swallows-the-real-error.md) 属于同一主题：
**分支本身写对了，触发条件写错了。** 008 在收尾阶段（`finally` 里的 `return` 让收尾走不到），
010 在下载前（降级挂在错误的信号上）。两者的症状也同形 ——
代码「什么都没做」，而日志与界面都在说「已经做过了」。

## 本轮（2026-10-17）背景：一次投稿牵出的一处

输入不是报障，而是**给宣传视频投稿**这件本来很小的事。手动打平台接口时，新建合集后的归集
返回 `-404 啥都木有`，换成分区 id 立刻成功 —— 于是回头读代码，发现两条归集路径都把
**合集 id** 当成了 **分区 id**。

[013](013-season-section-id-confusion.md) 的特殊之处是**它哪里都「写对了」**：端点路径、
参数名（`sectionId`，camelCase）、csrf 走 query —— 每一处都对着社区实测记录核过，
连替身都把参数形态钉成了用例。错的只有**值的来源**：`season/add` 回的是合集 id。
这类缺陷没有死代码可查、没有类型可依（两个值都是 `int`），只能靠「平台上这个字段是什么意思」
去核；而它的表现又恰好是一条最容易被当成偶发故障的错误码。

与 [011](011-biliup-refuses-a-token-less-cookie-file.md) 同一类：**本地用例全绿，真账号必失败。**

## 已知但**未处理**的问题（下一轮候选）

1. ~~**应用没有代理配置项。**~~ → 已由 [006](006-proxy-is-not-configurable.md) 处理。
   遗留：PAC/自动配置脚本不支持；「来源=环境变量」时 aria2c 仍拿不到代理。
2. **`_close_browser_for_cookie_import()` 用 `taskkill /IM msedge.exe /F /T`**，
   会杀掉用户全部 Edge 进程（含未保存标签页）。虽属用户显式同意，破坏面仍过大。见 005 遗留风险。
3. **视频级端到端仍未验收通过。** 001~003 把 PO token provider 从「不可用」推进到「可用」，
   006 让请求能真正出网，007 让 JS 求解真的跑得起来；但本轮只验到 **元数据提取成功**
   （带/不带 cookies 都能解析、`LOGGED_IN` 正确），「媒体流下得下来 + 合得起来」仍需在干净的
   代理环境下单独验收。
4. 同一 item 内多条字幕轨仍是串行请求，若 YouTube 收紧限流可能还需请求间隔控制（见 004 关联）。
5. **环境净化只覆盖 `NODE_OPTIONS`**（007 遗留）。其它同样能打坏子进程的宿主注入没有处理 ——
   只处理实测造成故障的那一个，不凭想象扩大范围。
6. ~~**`docs/development.md` 的环境变量表缺两个字段**：`YTDL_JS_RUNTIME_PATH` 与 `YTDL_PROXY`~~
   → 已处理（2026-10-02 文档同步轮）：两行已补进表内，`YTDL_JS_RUNTIME` 这个名字的错误写法
   也已在 `docs/technical.md` 点明「设了不生效」。
   **遗留**：是否反过来把 `AppSettings.js_runtime_path` 改名成 `js_runtime` 属于产品决定，没有动。
7. **端口预检只在 `python -m app` 这条入口生效**（009 遗留）。继续敲
   `python -m uvicorn app.main:app --port 8000` 仍然是原始报错；`docs/openapi.yaml` 的 server URL
   也仍是硬编码 `8000`，没有跟随 `YTDL_API_PORT`。
   → **后半句已处理**：`docs/openapi.yaml` 现在把该 server 明确标为「默认端口」，
   并在 `info.description` 里写明端口来自 `YTDL_API_PORT`、以启动日志打印的地址为准。
   OpenAPI 的 `servers[].url` 本身必须是具体 URL，无法表达变量，所以只能这样降级表达。
   前半句（裸 uvicorn 无预检）保持未处理。
8. **008 的触发路径没有在真实界面里出现过**：删除竞态 + 收尾出错这个组合只在测试里构造过，
   没有真的在浏览器里删掉一个正在下载的条目来观察。
9. **`port_available()` 存在「探测通过、随后被抢走」的竞态**（009 遗留），没有加锁也没有重试 ——
   预检只负责把话说清楚，不构成占用保证。
10. **`requested_resolution_unselectable` 的文案仍然名实不符**（010 遗留）。该原因在**下载阶段**
   被标注时，文案会写「已自动降级到 X」，但设计上那里只做标注、**不**自动重下
   （见 [architecture.md](../../docs/architecture.md)），用户必须自己点重启。
   010 修掉了「预检抛异常被误判成该原因」这条错路，但文案本身没动 ——
   改它要同步前端 fixture 与 `docs/openapi.yaml` 的示例值，属另一件事。
11. **预检仍会向 stderr 打一行 `ERROR: ... Requested format is not available.`**（010 遗留）。
   实测确认 yt-dlp 在 `trouble()` 里先 `to_stderr()` 再抛异常，`quiet=True` 拦不住。
   对用户无害（任务其实成功），但看起来像报错。修法是在探针上挂一个静默 logger，未做。
12. **`is_requested_format_unavailable_error` 是纯字符串匹配**（010 遗留）。yt-dlp 一旦改这句英文，
   两处归一同时失效，缺陷会以「降级不执行」的形式回归。有意保持窄匹配（宁可失效也不吞真故障），
   但没有版本探测或第二个关键词兜底。
13. **删任务不连带删 B站 上传记录**（bilibili 分支移植发现）。`JobManager.delete()` /
   `delete_items()` 只删 `JobEvent` / `JobItem` / `Job`，`bili_upload` / `bili_season` 会留下；
   而 `read_all_job_uploads()` 的 job 集合正是从这两张表取并集，于是
   `GET /api/bilibili/uploads/summary` 会一直为已不存在的任务回记录，行数也无限累积
   （界面上看不见：前端按 `/api/jobs` 渲染徽标，所以这是个静默的存储面损伤）。
   修法不止一行删表 —— 投稿跑在 `BiliManager` 的另一个 worker 上，删任务时可能正有一条在途，
   删完再写回等于白删；要先给 `BiliManager` 加**按 job 取消在途上传**的能力
   （现在还只有整体 `stop()`）。详见
   [03-implementation.md §5.5](../feature/bilibili-upload/03-implementation.md#55-删任务不连带删上传记录)。
14. **令牌的自动续期（`renew_tokens`）没有实测过**（011 遗留）。`token_info` 的有效期是 180 天，
   到期后 biliup 会拿 `refresh_token` 自己去续；我们只保证了 `platform="BiliTV"`（它靠这个挑 appkey）
   与 `refresh_token` 已写入，**没有构造过「令牌将过期」的状态来跑那条路**。
   真要验收得手工把 `expires_in` 改小或等 180 天。风险是「某天开始投稿又变成 -400」——
   届时界面上的凭据体检（011 新增的那一项）会把它显出来，不会再静默。
15. **B站 的三条只读请求没有超时与 `AbortController`**（012 遗留）。只有 `listBiliSeasons`
   因为踩过「DNS 挂起不受 socket timeout 覆盖」而补了 12 s 兜底 + 请求去重，
   而 `getBiliPrefill` / `getBiliUploadsSummary` / `getBiliStatus` 仍是裸 `fetch`。
   DNS 挂起时它们同样会给出一个**没有终点**的 loading，而 012（一）修的那句
   「正在读取账号里的合集…」正是以「列表最终会回来」为前提的 —— 前提不成立时，
   用户看到的就是一句永远不消失的说明。修法是给这三条也套同一套兜底（或抽一个共用的
   `fetchWithTimeout`），未做。**它若立项应取 014**（012 里写的「013 候选」没有占编号）。
16. **归集时「这批稿件已经在合集中」被当成失败**（013 遗留）。实测重复添加同一条稿件，
  平台返回 `code=20080 message=当前稿件已存在在合集中`，而 `add_episodes` 把任何非零 code
   都当失败 → 一次**实际已成功但响应丢失**的归集，重试会落成「视频没能加进去」。
   没顺手判成功，是因为**混合批的语义没验证**：一批里既有已存在的、又有新的时，
   平台是整批拒绝还是逐条处理？前者会让「判成功」变成谎报。要修得先在真账号上验这一条。
17. **`/api/bilibili/seasons` 只读第一页**（013 遗留）。`list_seasons` 固定 `pn=1, ps=30`，
   账号下合集多于 30 个时第 31 个之后的**在界面上选不到**（本机账号 27 个，还没撞上，
   代码面是确定的）。`find_season_section` 已加翻页，这条没动。
18. **提示音没有在真实浏览器里听过**（014 遗留）。合成参数（C6→G6、0.5 s、峰值 0.16）
   与「浏览器是否放行音频」都只在替身层面验过（起振 2 次 / `blocked` 不抛）；
   真机上「提交任务 → 十几分钟后切回来 → 到底响不响」没有实测。失败路径是静默的。
19. **提醒只覆盖 `succeeded` / `failed`**（014 遗留）。`paused` 不是终态所以不提醒，
   但用户手动暂停后忘了就没有任何后续提示。这是有意的选择，不是遗漏 ——
   记在这里是因为「有意」和「忘了」在半年后看不出来区别。
20. **浮层没有做过浏览器级测量**（014 遗留）。右下角 20px 间距、360px 上限、`z-index: 60`
   是按 `.modal-backdrop`（50）推的，没量过它会不会盖住任务中心右下角的按钮
   （`ai/ui/` 那套 CDP 探针本轮没跑）。
