# 038 · 浏览器 cookies 导入：一个必然失败的入口，外加一条会杀掉所有 Edge 窗口的失败分支

## 问题

界面上曾经有一个「从浏览器导入」的下拉框 + 按钮。它在本机结构上**必然失败**，而且
失败路径带数据破坏风险：

1. 本机 Edge 的 YouTube cookies 是 **v20 app-bound 加密**，密钥绑定正在运行的 Edge 进程本身，
   离线程序解不开（实测一律 `Failed to decrypt with DPAPI`，见 [ai/perf/PLAN.md §9.2-B](../perf/PLAN.md)）。
2. 失败后会走「关掉占用数据库的浏览器再试」的分支：`taskkill /IM msedge.exe /F /T`，
   关掉的是用户**所有** Edge 窗口（含未保存的标签页）。
3. 更早的 CDP 回退还会挂载**真实** Edge 配置，把用户真实 cookie 库清空过（53 → 0，
   见 [005](005-edge-cdp-fallback-touches-the-live-profile.md)）。

除此之外还有两条**用户看不见**的自动路径，共用同一套实现：

- `POST /api/analyze` 遇到「需要登录 / bot 校验」时，自动导入浏览器 cookies 后**重试一次**；
- 任务下载遇到同类错误时，任务管理器再自动导入一次并重试当前子视频。

也就是说：一个必然失败的按钮，加上两处会在用户不知情时去碰浏览器的后台逻辑。

## 根因

不是实现写错了，而是**这个能力在本机结构上不成立**。三个来源（UI 入口、解析重试、下载重试）
都指向同一个 `BrowserCookieImporter`，而它的成功条件——「离线解开正在运行的 Edge 的 cookie」——
在这台机器上永远不满足。剩下的只有失败分支，而失败分支里最「有效」的一步是杀进程。

## 决定：移除入口，只留导出

这是候选池 D-04 里「需要拍板」的一条。两个方向：

| 方案 | 结果 |
| --- | --- |
| ① **移除入口，只留导出**（本轮采用） | 界面上不再有一个必然失败的按钮；`taskkill` 与 CDP 的破坏面**整体消失** |
| ② 保留入口，点击前标注「本机不支持」 | 按钮还在，失败分支还在 —— 标注只能降低误点率，去不掉那条会杀进程的路 |

选 ①。判据不是「少一个按钮更简洁」，而是：**②没有消除任何风险，只是把风险解释了一遍**。

## 改了什么

删掉的（整条链路）：

| 位置 | 内容 |
| --- | --- |
| `backend/app/host/browser_cookies.py` | 整个文件（`BrowserCookieImporter`、`taskkill`、CDP 回退、域名过滤） |
| `POST /api/cookies/from-browser` | 路由本身（现在返回 404） |
| `contracts/schemas.py` | `BrowserCookieImportRequest`；`CookieStatus.browser` / `imported_count`；`CookieSource` 的 `browser` 取值 |
| `YtDlpService.import_browser_cookies` | 转发入口 |
| `api_support.extract_metadata_with_cookies` | 「失败 → 自动导入 → 重试一次」整段 |
| `job_manager._download_with_cookie_refresh` | 同上；改名为 `_download_with_cookie_guidance`，只尝试一次 |
| `frontend/src/download-request/UrlAnalyzer.tsx` | 浏览器下拉框、「从浏览器导入」按钮、「关闭 Edge 并导入」提示 |
| `frontend/src/cookies/cookieLock.ts` | `browser_locked` → 界面状态的映射（入口没了，没有东西可映射） |
| `frontend/src/shared/api.ts` | `importBrowserCookies()` |
| `frontend/src/styles.css` | `.browser-cookie-import` / `.cookie-lock-note` / `.compact-select*` |

留下的：

- `POST /api/cookies` 上传、`DELETE /api/cookies` 清除、`POST /api/cookies/verify` 体检；
- 「获取方式」说明浮层（浏览器扩展 / `scripts/export_cookies_via_cdp.py`）；
- 导出脚本本身 —— 它是**独立的命令行工具**，不是应用内入口，且使用独立 profile、不碰真实配置。

失败时说什么：两处共用 [cookie_required_message](../backend/app/download/error_advice.py#L75)：

```
YouTube 要求登录或通过 bot 校验。本应用只读取你给的 cookies.txt，不会去读浏览器里的 cookies。
下一步：用浏览器扩展（如 Get cookies.txt LOCALLY）在 youtube.com 页面导出 Netscape 格式的
cookies.txt，再到「解析链接」面板点「选择 cookies」上传；也可以在仓库根运行
python scripts/export_cookies_via_cdp.py 导出到 data/cookies.txt。
原始 yt-dlp 错误：…
```

**为什么必须共用一句**：解析失败、下载失败、诊断建议三处说的是同一件事，分开写必然漂移；
而只要其中一处还写着「点从浏览器导入」，用户就会去找一个已经不存在的按钮。

## 验证

后端新增 `backend/tests/test_cookie_import_removed.py`（8 条）：

| 用例 | 钉住什么 |
| --- | --- |
| `test_the_browser_import_route_is_gone` | `POST /api/cookies/from-browser` → **404**（不是「留着但永远 400」） |
| `test_uploading_and_clearing_a_cookie_file_still_works` | 上传/清除的响应**恰好**是 `{enabled, filename, source}`，没有 `browser` / `imported_count` |
| `test_cookie_status_has_no_browser_only_fields` | 契约里不再有「永远为空」的字段，`CookieSource` 不含 `browser` |
| `test_analyze_tells_you_how_to_export_instead_of_reading_the_browser` | 400、消息带导出步骤、**不提**已删按钮、只尝试一次、不产出 cookies 文件 |
| `test_analyze_with_a_stale_cookie_file_still_attempts_exactly_once` | 边界：文件在但过期，仍只试一次（旧实现会「自动刷新」后再试） |
| `test_two_concurrent_analyses_both_get_the_same_guidance` | 并发：两个请求都只试一次、都拿到同一句下一步、不写任何文件 |
| `test_a_failed_download_says_how_to_export_and_does_not_retry` | 下载侧同理：`cookie_snapshots` 只有一条，错误里不含「关闭 Edge」 |
| `test_the_destructive_browser_shutdown_is_gone_from_the_source` | 源码级：没有以独立字面量出现的 `taskkill`、没有 `browser_cookies` 的 import、模块文件不存在 |

前端（`npm test`，27 文件 / 342 条）：

- `cookies.test.tsx` 新增「没有任何浏览器导入入口」（按钮 / 下拉框 / 锁库提示都不在），
  以及「需要登录时显示导出路径」（回执条带下一步、只发一次 `/api/analyze`、不出现旧按钮名）；
- `error-banner.test.tsx` 原来那条「cookies 失败走同一条红条」改走新的 400 路径。

A/B：把实现文件（含 `browser_cookies.py`、`fakes.py`）还原到修复前，新用例 **8 条全红**：

| 用例 | 旧代码上的表现 |
| --- | --- |
| 路由已消失 | 200（接口还在，还真的写了 cookies.txt） |
| 上传/清除响应 | 多出 `browser` / `imported_count` 两个键 |
| 契约字段 | `CookieStatus` 里仍有那两个字段，`CookieSource` 仍含 `browser` |
| 解析只试一次 | 200（自动导入成功后又解析了一次） |
| 过期 cookies 边界 | 200，`extract_calls` 有两条 |
| 并发 | 两条都 200，且都各自导入了一次 |
| 下载只试一次 | `cookie_snapshots` 有两条（自动刷新后重试） |
| 源码扫描 | `browser_cookies.py` 还在，`taskkill` 字面量还在 |

恢复后 8 条全绿。这也说明这批用例钉的是**行为**，不是「文件被删了」这件事本身。

## 教训

1. **「按钮存在但必然失败」是名实不符的极端形态**；当失败路径还带破坏性时，「标注不可用」
   不是修复 —— 它只是把风险解释了一遍。
2. **自动重试是最容易藏住「去碰外部状态」的地方**：三处入口里有两处用户看不见，
   而它们共用同一个必然失败的实现。删入口时要顺着实现找**所有**调用方，不能只删按钮。
3. **契约里「永远为空」的字段要一起删**。留着 `browser` / `imported_count` 会让下一个读代码的人
   以为还有一条从浏览器来的路 —— 这比多改两处类型更贵。
4. 源码级回归用例要**收窄判据**：注释和 docstring 里提到 `taskkill` 是允许的（它们解释为什么删），
   `msedge.exe` 也仍合法地出现在「找 Edge 可执行文件给 PO token provider」的表里 ——
   所以扫的是**独立的字符串字面量**与 import，而不是裸词。

## 遗留

- 用户必须自己导出 `cookies.txt`（浏览器扩展或脚本）。对不熟悉扩展的人多了一步；
  应用能做的只有在失败消息里把这一步写清楚。
- `scripts/export_cookies_via_cdp.py` 需要本机 Python 与**有头**浏览器窗口，本轮没有在干净机器上
  重新验收（最近一次实测见 [ai/perf/PLAN.md §9.2-B](../perf/PLAN.md)）。
- `CookieStatus` 删了两个字段，是对外契约的变化。本仓库只有自带前端消费它，
  `check_api_contract.py` 与 `docs/openapi.yaml` 已同步；没有第三方调用方需要兼容。
