# 023 · 「打开方式」对话框根本不会出现，接口却回 204

## 问题

点了任务行上的「打开字幕」（现在只有一颗按钮，tooltip「打开字幕文件；可选择打开方式」），
桌面上什么也没有：没有对话框、没有报错，任务行也干净 —— 只有后端日志里多了一条 `204`。

```
2026-10-21 23:56:17 INFO uvicorn.access | 127.0.0.1:59909 - "POST /api/jobs/27b66d8b-.../open-subtitle HTTP/1.1" 204
```

复现（真实接口 + 真实桌面）：

```
调用前：可见窗口 20 个，前台 hwnd=0x107E2 (WorkBuddy.exe)
POST http://127.0.0.1:8001/api/jobs/27b66d8b-.../open-subtitle -> 204，耗时 1.08s
RESULT: 调用后 6 秒内没有出现任何新的可见窗口 —— 系统那边没开窗口
```

`1.08s` 是「等窗口出现」的轮询跑满了，说明预期中的窗口从头到尾没出现。

## 原因

两层，第二层才是真因。

**一、我们请的是别人的宿主。** 旧实现走 `rundll32.exe shell32.dll,OpenAs_RunDLL <path>` ——
它自己并不画窗口，只是请 explorer 托管的现代弹层（`OpenWith.exe` 的「选取应用」）出来。
那个宿主失效时，**调用照样返回 0**：`OpenWith.exe` 起来了、窗口一个也没有。

**二、这个宿主在这台机器上确实会失效，而且不是偶发一下子。** 三条独立观测：

| 观测 | 命令/探针 | 结果 |
| --- | --- | --- |
| `OpenWith.exe` 进程一个都没被创建 | `probe_watch_openwith.py`（50ms 粒度盯 10s） | `期间出现的被盯进程 0 个` |
| 直接起宿主也没有窗口 | `probe_openwith_direct.py` | `10 秒内新增窗口 1 个`（是 msedge 的，不是对话框）；子进程仍在运行 |
| 同一时刻官方 API 能弹 | `probe_shopenwithdialog.py` | `Shell_Dialog` + `Shell_Dim`，且成为前台 |

同一台机器、同一份 `.vtt`、同一个后端进程：`OpenAs_RunDLL` 什么都没有，`SHOpenWithDialog` 正常。
所以问题不在路径、不在权限（进程是 Medium 完整性、会话 1、**未提权**）、也不在"能不能显示窗口"
（同进程的 `open-folder` 能正常弹出并置前资源管理器窗口）。

**三、失败被吞成了 204。** `open_local_path` 只把异常翻成 400；窗口没出现不算异常，
于是接口"成功"返回，前端没有任何可显示的东西。**界面上「什么都没发生」和「后端什么都没做」
在这里彻底无法区分** —— 这正是 [ui/004](../ui/004-the-recheck-button-looks-dead.md) 的同一类失败。

## 修复方案

1. **换成文档化 API，让窗口在我们自己的进程里画。** `shell32.SHOpenWithDialog` +
   `OPENASINFO{pcszFile, pcszClass=NULL, OAIF_ALLOW_REGISTRATION|OAIF_HIDE_REGISTRATION|OAIF_EXEC}`。
   窗口类名 `Shell_Dialog`，由 shell32 在本进程内绘制，不再经过 explorer 的现代宿主。
   它**不需要**我们指定程序 —— 列表仍是系统注册的全部处理器，设计原则（不自建编辑器清单、
   不记「上次用的」）一字未改。
2. **把「窗口有没有出现」变成判据。** 模态调用放进自己的 daemon 线程，接口只轮询
   [_open_with_dialog_window](../backend/app/system_open.py#L178)（本进程里有没有 `Shell_Dialog`），
   2.5s 内没有就抛 `LocalOpenError` → 400 → 前端把原因写在**那一行**：
   「系统没能弹出「打开方式」窗口（HRESULT 0x…）。可以先点「打开视频所在文件夹」，在资源管理器里右键
   那个字幕文件 → 打开方式；或者把该后缀的默认程序在系统设置里指定一次。」
3. **不再叠窗口。** 已有 `Shell_Dialog` 时直接报「已经有一个「打开方式」窗口在等你选择」——
   连点几下本来会得到一摞一模一样的模态窗口。
4. **前台只能等，不能抢。** 见下一节，这是本轮最反直觉的一条。

### 前台：先等它自己上来，超时才 attach

用户点的是浏览器（前台进程），画窗口的是后端（后台进程），Windows 的前台锁默认拒绝后台进程
`SetForegroundWindow`。但**提前抢比不抢更糟**——实测：

| 做法 | 前台时间线（真机、真实浏览器点击） |
| --- | --- |
| 窗口一出现就 `ShowWindow+SetForegroundWindow` | 始终 `Chrome_WidgetWin_1`（浏览器），对话框永远在后面 |
| 完全不干预 | `DIALOG`（第一次）/ `Chrome_WidgetWin_1 → DIALOG`（后续） |

解释：窗口刚创建时它自己在激活，这时抢会被拒，**被拒之后它就不再自行激活**。
所以顺序定为：先给它 1.2s 自己上来；确实没上来，才用
[_ensure_dialog_foreground](../backend/app/system_open.py#L215) 里 `AttachThreadInput`
接管前台线程输入队列再抢。真机连续三次：`DIALOG` / `Chrome_WidgetWin_1 → DIALOG` /
`Chrome_WidgetWin_1 → DIALOG` —— 第 2、3 次正是兜底那一步在起作用。

`ai/ui/006` 里那条「按进程名认领窗口」的判据随旧实现一起删除了：它解决的是「窗口在背后」，
而这次真机复测证明**窗口根本没被创建**，问题在更前面一层。

## 效果

| 判据 | 修复前 | 修复后 | 怎么量的 |
| --- | --- | --- | --- |
| 点一下之后有没有窗口 | 没有（6s 内 0 个新窗口） | 有：`Shell_Dialog` + `Shell_Dim` | `probe_open_subtitle_e2e.py` |
| 接口耗时 | 1.08s（轮询跑满） | **0.14s**（窗口出现即返回） | 同上 |
| 对话框是否前台 | ——（没有窗口） | 是（3/3 次，其中 2 次靠兜底） | `ui_open_subtitle_real.py` 的前台时间线 |
| 对话框还开着再点一次 | 204，叠第二个窗口 | 400 + 行内红字 | 浏览器探针读 `.local-action-error` |
| 窗口弹不出来时 | 204 空转，界面无任何提示 | 400 + 「没能弹出…」+ 下一步 | `test_chooser_reports_a_dialog_that_never_appears` |

真机截图：`tmp_acceptance/open-subtitle-real.png`（重复点击后行内那句红字）。

## 验证

**自动化**

- `test_system_open.py` 10 条：`Shell_Dialog` 出现即认领并返回、窗口不出现抛 `LocalOpenError`
  （文案含 HRESULT 与「资源管理器」这个下一步）、已有窗口时不再起第二个、
  `WinError 1155` 仍降级成选择器（现在断言的是 `_open_windows_chooser` 被调用，而不是起哪个进程）、
  非 Windows 退化成默认打开。全量后端 **1123 通过**。
- 前端无改动，`npm test` 279 条仍全绿（脚本未动）。

**真机（本轮的核心证据）**

| 探针 | 证明什么 |
| --- | --- |
| `probe_watch_openwith.py` | 旧实现下 `OpenWith.exe`/宿主进程**一个都没被创建**（不是"起了但没显示"） |
| `probe_openwith_direct.py` | 直接起宿主也没有窗口 → 坏的是宿主这一层，不是我们的调用方式 |
| `probe_shopenwithdialog.py` | 同一时刻 `SHOpenWithDialog` 能画出 `Shell_Dialog` → 换实现可行 |
| `probe_foreground_techniques.py` | 自然激活 vs 提前抢前台的差异（时间线） |
| `ui_open_subtitle_real.py` | 真实浏览器点击：对话框出现 + 前台 + 重复点击行内报错，7/7 通过（连跑 3 次） |
| `probe_open_subtitle_e2e.py`（8024，**真实数据库副本**） | 真实任务 `27b66d8b` 的字幕：204 / 0.14s / `Shell_Dialog` 出现 —— 换实现后真实数据也正常 |

**承重**：把 `_ensure_dialog_foreground` 的兜底段去掉（`CASCADE_RAISE_DIALOG=0` 的临时开关，
本轮已删除），前台时间线退化成「始终 `Chrome_WidgetWin_1`」；加回来恢复 `→ DIALOG`。
另外「提前抢」与「先等」的对照见上表 —— 两个方向都量过，不是推断。

## 风险与回滚

- **`SHOpenWithDialog` 是模态的**：用户不选，那个线程就一直挂着（daemon，不阻止进程退出）。
  每次点击最多起一个线程，且已有窗口时会拒绝第二次点击，不会累积。
- **接口在窗口弹不出来时要等 2.5s 才回 400**：这是刻意的 —— 宁可慢一点报错，也不要再出现「204 空转」。
- **`AttachThreadInput` 只在 1.2s 兜底时调用一次**，用完立即 detach，不改变我们与前台进程的长期输入关系。
- **旧实现的移除**：`process_name` / `_process_image_name` 一并删除，`_open_process` 回到两参数；
  回滚需要同时恢复 `ai/ui/006` 的实现与它的两条测试断言（`git show 2c5aafb`）。
- **本机仍处于"现代 OpenWith 宿主失效"的状态**：这不影响本应用（已不走它），但资源管理器里
  双击无关联文件、以及系统其它入口仍可能不弹窗。那是系统侧的问题，本轮不修。

## 未覆盖 / 如实说明

1. **没有在别的 Windows 版本上验过。** `SHOpenWithDialog` 自 Vista 起就有，但「经典对话框」在
   Win10/11 上的外观与交互未逐个版本实测；本轮只在这台 Win11 上量了窗口类名与前台行为。
2. **没有验证「用户在对话框里挑完程序、文件真的被打开」。** 探针只证明窗口出现、可交互（焦点在前台），
   挑程序那一步会真的启动一个外部应用，属于人工验收（`docs/testing.md` 12a）。
3. **接口耗时 0.14s 是"窗口出现"的时间**，不是"用户选完"的时间 —— 用户选完之前请求早已返回，
   所以「挑的程序有没有起来」这件事应用侧不知道，也不该假装知道。
4. **兜底置前（attach）是启发式**：它依赖「前台窗口属于另一个线程」这一前提；若前台是 0（无前台窗口），
   代码走 `BringWindowToTop+SetForegroundWindow`，这一支在本机没有单独构造出来测过。
5. **本机运行中的实例仍是旧代码**（`8001` 进程在本次修复前启动）：需要**重启应用**才生效；
   本轮的真机验证跑在同代码的独立实例（`8022` 临时数据、`8024` 真实数据库副本）上。
6. **屏保/锁屏时无法置前，也不该置前。** 用真实库副本那次，前台是 `ScreenSaverPlayer.exe`
   （当时机器闲置），对话框照样画出来了但没成为前台 —— 这是 Windows 的正确行为（用户不在看）。
   `_ensure_dialog_foreground` 的兜底在这种前台所有者面前也会失败，属预期。

## 关联

- [ui/006](../ui/006-the-subtitle-arrow-looks-dead.md)：撤掉下拉箭头、改成一颗按钮是同一串问题里的
  界面部分；它的「按进程名认领窗口」修法被本轮取代（原因见上）。
- [ui/004](../ui/004-the-recheck-button-looks-dead.md)：同一类失败——「做了」与「没做」在界面上
  无法区分。004 靠补加载态/落款时间，本条靠「窗口没出现就报错」。
- [bug-fix/009](009-local-dev-port-is-occupied-and-unconfigurable.md)：错误必须指出下一步；
  本轮把「没能弹出」与「下一步点哪里」写进同一句话。
