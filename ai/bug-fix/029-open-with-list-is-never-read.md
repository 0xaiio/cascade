# 029 · 「打开方式」的候选清单永远收不到系统登记

| 项 | 值 |
| --- | --- |
| 发现时间 | 2026-10-28 19:5x +08:00（重构 [011](../refactor/011-flat-modules-into-feature-packages.md) 后的全量审查） |
| 修复时间 | 2026-10-28 20:1x ~ 20:4x +08:00 |
| 起点 commit | `281ada7` |
| 结论 | **已修**。三个来源里有两个**恒定为空**、第三个的显示名会把 GUID 印给用户。新增 33 条用例；还原修复点后其中 **26 条红** |

---

## 1. 问题

自带「打开方式」对话框的清单，本该是「系统为这个后缀登记的处理程序在前，本机常见编辑器在后」。
实测本机 `.md` 的清单里**一个系统登记的程序都没有**，而且有一条的名字是一串 GUID：

```
修复前（`git show HEAD` 的原函数，本机实测）
  Markdown Document
  Toolbox.Air.b9908000-346e-4c93-ac4a-3f3799308eae   ← 这是 ProgID，不是程序名
  Markdown 源文件
  WorkBuddy Document / WorkBuddy AI Document
  记事本 / CLion / IntelliJ IDEA / PyCharm / Visual Studio
```

而我用 `winreg` 直接读注册表，同一个后缀下有 `msedge.exe`、`Cursor.exe`、`Code.exe`：

```
.md  HKCU/FileExts -> [('a', 'msedge.exe'), ('MRUList', 'cba'), ('b', 'Cursor.exe'), ('c', 'Code.exe')]
.mp4 HKCU/FileExts -> [('a', 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic'),
                       ('MRUList', 'acb'), ('b', 'QQ.exe'), ('c', 'Everything.exe')]
```

两者之间的差额，是**三处**独立的失效叠在一起。

## 2. 原因

**一、过滤条件写反了。** `read_exe_names` 里是：

```python
if name.isdigit():  # `a` / `b` / `MRUList` 这些不是程序项
    found.append((Path(str(value)).stem, str(value)))
```

注释说的是「排除 `a`/`b`/`MRUList`」，代码却只接受**键名是数字**的项。而 `OpenWithList` 的
真实键名是 `a`、`b`、`c`（程序项）加 `MRUList`（记录它们的顺序）—— **一个都不是数字**。
于是 `HKCU\...\FileExts\<ext>\OpenWithList` 与 `HKCR\<ext>\OpenWithList` 这两个来源
**永远产出空列表**。判据本应是「键名等于 `MRUList`」，不是「键名是不是数字」。

**二、就算过滤对了，值也不是路径。** 那两个来源存的是**裸 exe 名**（`QQ.exe`、`Everything.exe`）
或 AppX 包标识（`…!App`）。`Path("QQ.exe").is_file()` 是假 —— 相对当前工作目录找。
所以第一处修好之后，这些项仍然会被下游的 `is_file()` 全部丢掉。要先用 `PATH`、再用
`App Paths` 把裸名解析成完整路径。

**三、显示名把 ProgID 原文印出来。** `_progid_label` 取不到显示名时 `return progid`，
于是 `Toolbox.Air.b9908000-346e-4c93-ac4a-3f3799308eae` 原样进了列表。包里已有的
「标识不是名字」处理只认 `AppX` 前缀，覆盖不到这种带 GUID 的 ProgID。

## 3. 为什么一直没被发现

`test_system_open.py` 里那条 `test_builtin_candidates_mix_registered_handlers_with_local_editors`
**把 `_registered_handlers` 整个换成了假的**：

```python
monkeypatch.setattr(system_open, "_registered_handlers", lambda _ext: [("记事本", str(notepad))])
```

被测的那一层从没被执行过 —— 于是「清单 = 系统登记 + 编辑器」这条合并逻辑是对的、
`_registered_handlers` 是空转的，两者互不干扰，测试全绿。这是「fake 与真实实现脱节」的
又一例：**替身一旦挡在缺陷所在的那一层，用例就只能证明它旁边那部分**。

## 4. 修法

1. `read_exe_names` 改成只跳过 `MRUList`（`OPEN_WITH_MRU_VALUE`），其余全部收下；
2. 新增 `_resolve_executable`：带分隔符的按路径判存在，裸 exe 名走 `shutil.which` →
   `_app_path_executable`（`App Paths` 的默认值），`…!App` 直接放弃（不能按路径启动）。
   解析不出来的**在源头丢掉**，不再往上游传 `(name, "")` 这种垃圾行；
3. `_progid_label` 取不到显示名返回空串，由调用方退回可执行文件名；
4. `_looks_like_machine_id` 统一判「标识 vs 名字」：`AppX…` 前缀、含 `!`、含 GUID 形状；
5. `_editor_candidates` 的 `App Paths` 取值同样过 `_resolve_executable`（它原来把带引号的
   原值往上传，靠下游 `add()` 剥引号 —— 同一个「交出干净路径」的约定，两处应当一致）。

顺带把该函数里 `rf"…\\…"` 双写的反斜杠归一成单写：在 raw f-string 里双写是多余的，
且同一文件的 `_APP_PATHS_ROOTS` 本来就是单写。

## 5. 验证

**数据层（真实注册表，不是替身）**：

```
修复前 .md 候选 10 条，无 msedge / Code，含 Toolbox.Air.b9908000-…
修复后 .md 候选 11 条：多出 msedge、Code（与编辑器清单里的 VS Code 同路径，去重后只留一条）
                      Toolbox.Air.b9908000-… → Air
```

**真实桌面探针**（`tmp_acceptance/probe_open_with_dialog_029.py`，跑完即删）：起 daemon 线程
调真的 `_ask_program`，主线程按类名 `TkTopLevel` + 标题「打开方式」认领窗口并关掉：

```
[1] 内置清单（.srt）6 条：记事本 / VS Code / CLion / IntelliJ IDEA / PyCharm / Visual Studio
[2] 把包标识 / ProgID 当名字的行：[]
[3] 5 秒内出现『打开方式』窗口：True
[4] 线程结果：{'picked': 'None'}          ← 关窗 = 取消，不是错误
```

**A/B**：把 `system_open.py` 还原成修复前，新用例 **26 / 33 红**（7 条绿的是「子键不存在」
「通配展开」这类本来就不受影响的）。修复后 33 全绿，`test_system_open.py` 原 18 条也全绿。

## 6. 未覆盖（如实说明）

1. **AppX 包标识仍然进不了自带对话框。** `Microsoft.WindowsNotepad_8wekyb3d8bbwe!App`
   要用 `IApplicationActivationManager` 才能启动，而我们的对话框是
   `subprocess.Popen([exe, path])`。这类项现在被**明确丢掉**而不是列出来点不动 ——
   系统那两级（`SHOpenWithDialog` / `OpenAs_RunDLL`）仍然能列出它们。
2. **裸 exe 名解析不出来的就没了。** 本机 `.mp4` 的 `QQ.exe` / `Everything.exe`
   既不在 `PATH` 也不在 `App Paths`，实测解析为 `None`。再往下只能全盘搜索，不值得。
3. **没有做「点开清单里每一项都能启动」的逐个验证** —— 那会把用户的桌面刷满窗口。
   目前只验到「路径真实存在」，这是 `Popen` 成功的前提，不是充分条件。
4. `HKCR\<ext>\OpenWithList` 在本机对 `.txt`/`.srt`/`.md` 都不存在（只有 `.mp4` 是个空键），
   所以「第二个来源」只在假注册表里被覆盖到，真机没走过。
