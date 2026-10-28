import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time


class LocalOpenError(RuntimeError):
    pass


# 「这个后缀还没有关联程序」。Windows 上点一个没人认领的 `.srt` 时，`os.startfile`
# 抛的就是 `OSError(WinError 1155)` —— 它不是打开失败，只是「还没人认领」。
ERROR_NO_ASSOCIATION = 1155

# 自带选择对话框的窗口特征（tkinter 顶层窗口；标题与 `CHOOSER_TITLE` 一致才认领）。
CHOOSER_WINDOW_CLASS = "TkTopLevel"
CHOOSER_TITLE = "打开方式"

# 系统 `SHOpenWithDialog` 的窗口类名。**降级提示框用的是同一个类名**，所以只认类名会把
# 「若要更改默认应用…」那个提示框当成选择器 —— 高度才是判据（见下）。
SHELL_DIALOG_CLASS = "Shell_Dialog"
# 实测：降级提示框恒为 118px；真正的选择器在 500px 以上。用 300 留足 DPI 缩放的余量。
SHELL_NOTICE_MAX_HEIGHT = 300

# 系统「选取应用」弹层的宿主（`OpenAs_RunDLL` 拉起的那一个）。标题随系统语言变化。
OPEN_WITH_HOST_EXE = "openwith.exe"
OPEN_WITH_HOST_TITLES = ("选取应用", "Open With")

# `SHOpenWithDialog` 的三个开关（shlobj.h）：允许登记、隐藏注册提示、挑完直接执行。
OAIF_ALLOW_REGISTRATION = 0x00000001
OAIF_EXEC = 0x00000002
OAIF_HIDE_REGISTRATION = 0x00000020

# 等「选取应用」弹层出现的时间。实测健康时 <1s；超时就往下走一级。
OPEN_WITH_HOST_WAIT_SECONDS = 1.6
# 第一级已经弹出「若要更改默认应用…」提示框时，只给它一半时间：两条路走的是**同一套**系统 UI，
# 那套 UI 降级时这条路也不会起来（实测 0/6 次）。留给它一个短窗口，是怕万一两者会分叉。
OPEN_WITH_HOST_WAIT_AFTER_NOTICE_SECONDS = 0.8

# `OpenWithList` 里的键名是 `a` / `b` / `c`（程序项）与 `MRUList`（记录它们的先后顺序）。
# 只有 `MRUList` 不是程序项 —— 判据是**键名等于它**，不是「键名是不是数字」。
OPEN_WITH_MRU_VALUE = "MRUList"

# `AppX4ztf…`、`Toolbox.Air.b9908000-346e-4c93-ac4a-3f3799308eae`、`<包名>!App`：
# 这些是包标识 / ProgID，把原文印进对话框等于让用户读一串十六进制。
_MACHINE_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

# 整条链等窗口出现的总时限（系统两路 + 自带对话框），超过就当失败。
OPEN_WITH_APPEAR_TIMEOUT_SECONDS = 6.0


@dataclass(frozen=True)
class VideoPlayerCandidate:
    name: str
    command: list[str]
    robust: bool


@dataclass(frozen=True)
class ProgramCandidate:
    """一个「能打开这个文件」的程序：显示名 + 可执行文件。"""

    name: str
    executable: Path


@dataclass
class ChooserOutcome:
    """「挑程序」这条链的结局。

    `cancelled` = 用户点了取消；`error` = 对话框/启动动作本身出错；
    `launched` = 最终启动的可执行文件；`shell_degraded` = 系统对话框退化成了提示框（已被关掉）。
    """

    cancelled: bool = False
    error: str | None = None
    launched: str | None = None
    shell_degraded: bool = False


def open_path_with_default_app(path: Path) -> None:
    target = str(path)
    if os.name == "nt":
        if path.is_dir():
            _open_windows_folder(path)
            return
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except OSError as exc:
            if getattr(exc, "winerror", None) != ERROR_NO_ASSOCIATION:
                raise
            # 没关联程序时**不要**把 1155 抛给用户：资源管理器自己也是弹「打开方式」。
            open_path_with_chooser(path)
        return
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    _open_process(command, title_hint=path.name)


def open_path_with_chooser(path: Path) -> None:
    """弹一个「打开方式」，由用户挑一个程序来打开这个文件。

    **优先交给系统**，因为它给的清单比我们全（含 Store 应用、以及「始终使用此应用」），
    这是用户唯一认可的形态。系统的两条入口都试，都不可用才退回自带对话框：

    1. `SHOpenWithDialog`（文档化 API，在本进程内画 `Shell_Dialog`）；
    2. 系统「选取应用」弹层（`OpenAs_RunDLL`，窗口归 `OpenWith.exe`）—— 它在这台机器上曾经可用，
       后来整机降级；两条路的失效方式不同，所以都要试。

    判据不是「有没有窗口」，而是**窗口里有没有东西可选**：降级后的提示框也是 `Shell_Dialog`，
    只是高度只有 118px（真正的选择器在 500px 以上）。见 `SHELL_NOTICE_MAX_HEIGHT`。

    三级都不成时用自带对话框，清单同样不硬编码：系统为该后缀登记的处理程序 + 本机真实存在的
    常见编辑器（见 `_editor_candidates`），外加「浏览…」。选择与启动都在本进程内完成，
    **不接受前端传入的程序路径**。

    其它平台没有等价入口（macOS 的 `open -a` 要的是应用名，Linux 的 `xdg-open` 只有默认），
    于是退化成「用默认程序打开」。见 `docs/technical.md#打开字幕文件`。
    """
    if os.name != "nt":
        open_path_with_default_app(path)
        return
    _open_windows_chooser(path)


def _open_windows_chooser(path: Path) -> None:
    """起「挑程序」这条链，并**确认真的有一个可选东西的窗口出来了**。

    这里只做四件事：
    1. 已经有一个窗口在等用户时不再叠一个 —— 连点几下会得到一摞一模一样的模态窗口；
    2. 开一个线程跑整条链（每个入口都是模态的，不能占用请求线程）；
    3. 在本线程盯窗口：系统提示框（高度不足）就地关掉、让链继续往下走；
    4. 超时仍没有任何窗口 = 失败；用户点「取消」不算失败。
    """
    if _chooser_window() or _shell_dialog_window() or _open_with_host_window():
        raise LocalOpenError(
            "已经有一个「打开方式」窗口在等你选择：先在那个窗口里选完（或取消），再点一次。"
        )

    outcome = ChooserOutcome()
    worker = threading.Thread(
        target=_run_chooser,
        args=(path, outcome),
        name="open-with-chooser",
        daemon=True,
    )
    worker.start()

    deadline = time.monotonic() + OPEN_WITH_APPEAR_TIMEOUT_SECONDS
    closed_shell: set[int] = set()
    while time.monotonic() < deadline:
        ours = _chooser_window()
        if ours:
            _ensure_dialog_foreground(ours)
            return
        host = _open_with_host_window()
        if host:
            _ensure_dialog_foreground(host)
            return
        shell = _shell_dialog_window()
        if shell and shell not in closed_shell:
            if _window_height(shell) >= SHELL_NOTICE_MAX_HEIGHT:
                _ensure_dialog_foreground(shell)
                return
            # 降级成「若要更改默认应用…」的提示框：关掉它，让链走下一级。
            # 关掉后窗口还会停留一小会儿（关闭动画），所以把它记下来，别反复关、反复 sleep。
            outcome.shell_degraded = True
            closed_shell.add(shell)
            _close_window(shell)
            continue
        if not worker.is_alive():
            break
        time.sleep(0.03)

    if outcome.cancelled:
        return
    detail = f"（{outcome.error}）" if outcome.error else ""
    raise LocalOpenError(
        "没能弹出「打开方式」窗口"
        f"{detail}。可以先点「打开视频所在文件夹」，在资源管理器里右键那个字幕文件 → 打开方式；"
        "或者把该后缀的默认程序在系统设置里指定一次。"
    )


def _run_chooser(path: Path, outcome: ChooserOutcome) -> None:
    """整条链：系统的两条入口 → 自带对话框。异常一律落进 `outcome.error`，线程不许静默结束。"""
    try:
        if _show_shell_chooser(path, outcome):
            return
        wait = (
            OPEN_WITH_HOST_WAIT_AFTER_NOTICE_SECONDS
            if outcome.shell_degraded
            else OPEN_WITH_HOST_WAIT_SECONDS
        )
        if _show_host_chooser(path, wait):
            return
        executable = _ask_program(path)
    except Exception as exc:  # 例如环境里没有 tkinter、或 shell32 导出缺失
        outcome.error = f"{type(exc).__name__}: {exc}"
        return
    if executable is None:
        outcome.cancelled = True
        return
    try:
        _open_process([str(executable), str(path)], title_hint=path.name)
        outcome.launched = str(executable)
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {exc}"


def _show_shell_chooser(path: Path, outcome: ChooserOutcome) -> bool:
    """第一级：`SHOpenWithDialog`。窗口健康（有程序列表）时返回 True。

    它**返回即代表窗口已经关掉**（用户选完、取消，或者被我们判定降级后关掉）；所以这里只能靠
    `outcome.shell_degraded` 区分「用户处理完了」与「那只是个提示框」。
    """
    try:
        info = _OpenAsInfo(
            str(path),
            None,
            OAIF_ALLOW_REGISTRATION | OAIF_HIDE_REGISTRATION | OAIF_EXEC,
        )
        ctypes.windll.shell32.SHOpenWithDialog(None, ctypes.byref(info))  # type: ignore[attr-defined]
    except Exception:
        return False
    return not outcome.shell_degraded


def _show_host_chooser(path: Path, wait_seconds: float) -> bool:
    """第二级：系统「选取应用」弹层（`OpenAs_RunDLL`）。窗口在 `wait_seconds` 内出现即成功。

    这条路请的是 explorer 托管的现代弹层，窗口不归我们 Popen 到的进程 —— 所以按**宿主 exe 名**认领
    （`OPEN_WITH_HOST_EXE`），再自己把它置前。
    """
    before = set(_visible_windows())
    # 直接 Popen：`_open_process` 还会按 pid/标题再轮询 1s 找窗口，而这条路要找的是**别人**的
    # 窗口（宿主 exe 不是我们起的那个进程），那段轮询必然空转 —— 白等 1 秒。
    subprocess.Popen(
        ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        hwnd = _open_with_host_window(exclude=before)
        if hwnd:
            _ensure_dialog_foreground(hwnd)
            return True
        time.sleep(0.05)
    return False


def _ask_program(path: Path) -> Path | None:
    """第三级：自带「打开方式」对话框。返回选中的可执行文件，取消则返回 `None`。"""
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except Exception as exc:
        raise LocalOpenError(
            "这个环境没有图形界面组件（tkinter），没法弹出「打开方式」窗口。"
            "可以先点「打开视频所在文件夹」，在资源管理器里右键那个字幕文件 → 打开方式。"
        ) from exc

    candidates = _program_candidates(path.suffix)
    root = tk.Tk()
    root.title(CHOOSER_TITLE)
    root.geometry("680x420")
    root.minsize(520, 300)
    root.attributes("-topmost", True)

    chosen: dict[str, Path | None] = {"exe": None}

    tk.Label(
        root,
        text=f"用哪个程序打开「{_shorten(path.name, 72)}」？",
        anchor="w",
        justify="left",
        wraplength=640,
    ).pack(fill="x", padx=14, pady=(14, 6))

    body = tk.Frame(root)
    body.pack(fill="both", expand=True, padx=14)
    listbox = tk.Listbox(body, activestyle="none", exportselection=False, width=1)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    for candidate in candidates:
        listbox.insert("end", f"{candidate.name}    —    {candidate.executable}")
    if candidates:
        listbox.selection_set(0)
        listbox.activate(0)

    hint = (
        "列表里是系统为这个后缀登记的程序，以及本机能找到的常用编辑器。"
        if candidates
        else "没找到能直接打开它的程序，点「浏览…」自己挑一个 exe。"
    )
    tk.Label(root, text=hint, anchor="w", justify="left", wraplength=640, fg="#4a5a70").pack(
        fill="x", padx=14, pady=(8, 0)
    )
    tk.Label(
        root,
        text="想让它以后默认用某个程序打开，请到「设置 > 应用 > 默认应用」。",
        anchor="w",
        justify="left",
        wraplength=640,
        fg="#6b7a8f",
    ).pack(fill="x", padx=14)

    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=14, pady=12)

    accept = lambda: _accept(listbox, candidates, chosen, root)  # noqa: E731
    open_button = tk.Button(
        buttons,
        text="打开",
        width=10,
        state="normal" if candidates else "disabled",
        command=accept,
    )
    open_button.pack(side="right")
    tk.Button(buttons, text="取消", width=10, command=root.destroy).pack(side="right", padx=6)

    def browse() -> None:
        # 普通用户不知道程序装在哪：从 Program Files 起，并且默认只看 exe。
        start = _program_files_dir()
        picked = filedialog.askopenfilename(
            parent=root,
            title="选择用来打开它的程序（.exe）",
            initialdir=str(start) if start else None,
            filetypes=[("程序", "*.exe"), ("所有文件", "*.*")],
        )
        if picked:
            chosen["exe"] = Path(picked)
            root.destroy()

    tk.Button(buttons, text="浏览…", width=10, command=browse).pack(side="left")
    listbox.bind("<Double-Button-1>", lambda _event: accept())
    listbox.bind("<Return>", lambda _event: accept())
    root.bind("<Return>", lambda _event: accept())
    root.bind("<Escape>", lambda _event: root.destroy())
    if candidates:
        listbox.focus_set()

    root.grab_set()
    root.lift()
    root.focus_force()
    try:
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return chosen["exe"]


def _accept(
    listbox: object,
    candidates: list[ProgramCandidate],
    chosen: dict[str, Path | None],
    root: object,
) -> None:
    selection = listbox.curselection()  # type: ignore[attr-defined]
    if not selection:
        return
    chosen["exe"] = candidates[selection[0]].executable
    root.destroy()  # type: ignore[attr-defined]


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _program_files_dir() -> Path | None:
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        raw = os.environ.get(variable)
        if raw and Path(raw).is_dir():
            return Path(raw)
    return None


def _program_candidates(ext: str) -> list[ProgramCandidate]:
    """对话框里的候选：**系统为该后缀登记的处理程序**在前，**本机常见编辑器**在后。

    两组都只保留真实存在的可执行文件，按路径去重（同一程序命中多个路径时保留先出现的那个）。
    """
    entries: list[ProgramCandidate] = []
    seen_paths: set[str] = set()
    seen_names: set[str] = set()

    def add(name: str, executable: str | Path, *, collapse_by_name: bool = False) -> None:
        exe = Path(os.path.expandvars(str(executable).strip().strip('"')))
        if not exe.is_file():
            return
        key = str(exe).lower()
        if key in seen_paths:
            return
        if collapse_by_name and name.lower() in seen_names:
            return  # 比如记事本同时命中 System32 与 WindowsApps 的别名
        seen_paths.add(key)
        seen_names.add(name.lower())
        # `AppX4ztfk9wxr86nxmzzq47px0nh0e58b8fw`、`Toolbox.Air.b9908000-…` 这种是包标识 /
        # ProgID，不是给人看的名字：一律退回可执行文件名。
        label = exe.stem if _looks_like_machine_id(name) else name
        entries.append(ProgramCandidate(label, exe))

    for name, executable in _registered_handlers(ext):
        add(name, executable, collapse_by_name=True)
    for name, executable in _editor_candidates():
        add(name, executable, collapse_by_name=True)
    return entries


def _registered_handlers(ext: str) -> list[tuple[str, str]]:
    """系统注册表里为这个后缀登记过的处理器，返回 `[(显示名, 可执行文件路径)]`。

    三个来源都是**系统自己的登记**，不是我们编的清单：
      - `HKCU\\...\\FileExts\\<ext>\\OpenWithList`（用户用过的，带 MRU 顺序）；
      - `HKCR\\<ext>\\OpenWithList`（按 exe 名登记）；
      - `HKCR\\<ext>\\OpenWithProgids`（按 ProgID 登记，命令在 `HKCR\\<ProgID>\\shell\\open\\command`）。

    **前两个来源的值不是路径，必须先解析**：实测（本机 2026-10-28）它们是裸 exe 名
    （`QQ.exe`、`Everything.exe`）或 AppX 包标识（`Microsoft.WindowsNotepad_8wekyb3d8bbwe!App`），
    对前者 `Path(value).is_file()` 同样是假。所以取值一律过 `_resolve_executable`；解析不出来的
    （已卸载、AppX 包）直接丢掉 —— 留在清单里只会让用户点一个打不开的条目。
    见 ai/bug-fix/029。
    """
    if os.name != "nt" or not ext:
        return []
    try:
        import winreg
    except Exception:
        return []

    extension = ext if ext.startswith(".") else f".{ext}"
    raw_values: list[str] = []

    def read_exe_names(root_key: int, sub_key: str) -> None:
        try:
            with winreg.OpenKey(root_key, sub_key) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    # 这里原本写的是 `if name.isdigit()`，把「不是程序项」判断成了「键名是数字」。
                    # 真实键名是 `a` / `b` / `c`（程序项）与 `MRUList`（它们的顺序）——
                    # 一个都不是数字，于是这两个来源**永远产出为空**。
                    if name == OPEN_WITH_MRU_VALUE:
                        continue
                    raw_values.append(str(value))
        except OSError:
            pass

    read_exe_names(
        winreg.HKEY_CURRENT_USER,
        rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{extension}\OpenWithList",
    )
    read_exe_names(winreg.HKEY_CLASSES_ROOT, rf"{extension}\OpenWithList")

    found: list[tuple[str, str]] = []
    for raw in raw_values:
        executable = _resolve_executable(raw)
        if executable is not None:
            found.append((executable.stem, str(executable)))

    progids: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{extension}\OpenWithProgids") as key:
            index = 0
            while True:
                try:
                    name, _value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                progids.append(name)
    except OSError:
        progids = []

    for progid in progids:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as key:
                command = str(winreg.QueryValue(key, None) or "")
        except OSError:
            continue
        executable = _resolve_executable(_command_executable(command))
        if executable is None:
            # 命令为空（本机 `.mp4` 的三个 AppX ProgID 都是空串）或指向已卸载的程序。
            continue
        # 显示名取不到时退回可执行文件名，**不要**把 ProgID 原文印给用户。
        found.append((_progid_label(winreg, progid) or executable.stem, str(executable)))

    return found


def _resolve_executable(value: str) -> Path | None:
    """把注册表里的取值解析成一个**真实存在**的可执行文件；解析不出来返回 `None`。

    三种形态实测（本机 2026-10-28）：
      - 完整路径 / 带引号的命令行首段 —— 直接判存在；
      - 裸 exe 名（`QQ.exe`）—— 先靠 `PATH`，再靠 `App Paths` 登记；
      - AppX 包标识（`…!App`）—— 不能按路径启动，只能放弃。
    """
    text = value.strip().strip('"').strip()
    if not text or "!" in text:
        return None
    if "\\" in text or "/" in text:
        path = Path(text)
        return path if path.is_file() else None  # 带目录却不是文件 = 已经卸载
    resolved = shutil.which(text)
    if resolved:
        return Path(resolved)
    return _app_path_executable(text)


def _app_path_executable(exe_name: str) -> Path | None:
    """`App Paths` 里登记的 exe → 完整路径，这是裸 exe 名的**第二个**解析来源。"""
    try:
        import winreg
    except Exception:
        return None
    for sub_key, root_name in _APP_PATHS_ROOTS:
        root = getattr(winreg, root_name)
        try:
            with winreg.OpenKey(root, sub_key + "\\" + exe_name) as entry:
                value = str(winreg.QueryValue(entry, None) or "")
        except OSError:
            continue
        candidate = value.strip().strip('"')
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _looks_like_machine_id(text: str) -> bool:
    """`AppX…` / `<包名>!App` / 带 GUID 的 ProgID：是标识，不是给人看的名字。"""
    value = text.strip()
    if not value:
        return False
    if value.lower().startswith("appx"):
        return True
    if "!" in value:
        return True
    return _MACHINE_ID_RE.search(value) is not None


# 本机常见编辑器：品牌名 → 可能的安装位置（支持 `*` 通配）。
# 只列**真实存在**的那些，所以这张表是「候选来源」而不是「界面清单」——
# 用户没装的自然不出现，装了但这里没登记的仍可「浏览…」。
_EDITOR_LOCATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("记事本", (r"%SystemRoot%\System32\notepad.exe", r"%LOCALAPPDATA%\Microsoft\WindowsApps\notepad.exe")),
    ("写字板", (r"%ProgramFiles%\Windows NT\Accessories\wordpad.exe", r"%SystemRoot%\System32\write.exe")),
    (
        "Visual Studio Code",
        (
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"%ProgramFiles%\Microsoft VS Code\Code.exe",
        ),
    ),
    (
        "CLion",
        (
            r"%ProgramFiles%\JetBrains\CLion*\bin\clion64.exe",
            r"%LOCALAPPDATA%\Programs\CLion\bin\clion64.exe",
            r"%LOCALAPPDATA%\JetBrains\Toolbox\apps\CLion\**\bin\clion64.exe",
        ),
    ),
    (
        "IntelliJ IDEA",
        (
            r"%ProgramFiles%\JetBrains\IntelliJ IDEA*\bin\idea64.exe",
            r"%LOCALAPPDATA%\Programs\IntelliJ IDEA\bin\idea64.exe",
        ),
    ),
    (
        "PyCharm",
        (
            r"%ProgramFiles%\JetBrains\PyCharm*\bin\pycharm64.exe",
            r"%LOCALAPPDATA%\Programs\PyCharm\bin\pycharm64.exe",
        ),
    ),
    ("Visual Studio", (r"%ProgramFiles%\Microsoft Visual Studio\*\*\Common7\IDE\devenv.exe",)),
    (
        "Notepad++",
        (r"%ProgramFiles%\Notepad++\notepad++.exe", r"%ProgramFiles(x86)%\Notepad++\notepad++.exe"),
    ),
    (
        "Sublime Text",
        (
            r"%ProgramFiles%\Sublime Text\sublime_text.exe",
            r"%ProgramFiles%\Sublime Text 3\sublime_text.exe",
            r"%LOCALAPPDATA%\Programs\Sublime Text\sublime_text.exe",
        ),
    ),
    ("gVim", (r"%ProgramFiles%\Vim\vim*\gvim.exe", r"%ProgramFiles(x86)%\Vim\vim*\gvim.exe")),
    (
        "Emacs",
        (r"%LOCALAPPDATA%\Programs\Emacs\*\bin\runemacs.exe", r"%ProgramFiles%\Emacs\*\bin\runemacs.exe"),
    ),
    (
        "Pulsar / Atom",
        (r"%LOCALAPPDATA%\Programs\Pulsar\Pulsar.exe", r"%LOCALAPPDATA%\Programs\atom\atom.exe"),
    ),
    ("Geany", (r"%ProgramFiles%\Geany\bin\geany.exe",)),
    ("SciTE", (r"%ProgramFiles%\SciTE\SciTE.exe",)),
    ("Kate", (r"%ProgramFiles%\Kate\bin\kate.exe",)),
    ("TextPad", (r"%ProgramFiles%\TextPad*\TextPad.exe",)),
    ("UltraEdit", (r"%ProgramFiles%\UltraEdit\uedit64.exe", r"%ProgramFiles%\IDM Computer Solutions\UltraEdit\uedit64.exe")),
)

# `App Paths` 里只认这几个可执行文件名：那里的条目动辄上百，全列出来是噪音。
_EDITOR_EXE_NAMES = {
    "notepad.exe": "记事本",
    "wordpad.exe": "写字板",
    "write.exe": "写字板",
    "code.exe": "Visual Studio Code",
    "clion64.exe": "CLion",
    "clion.exe": "CLion",
    "idea64.exe": "IntelliJ IDEA",
    "pycharm64.exe": "PyCharm",
    "devenv.exe": "Visual Studio",
    "notepad++.exe": "Notepad++",
    "sublime_text.exe": "Sublime Text",
    "gvim.exe": "gVim",
    "emacs.exe": "Emacs",
    "runemacs.exe": "Emacs",
    "pulsar.exe": "Pulsar / Atom",
    "atom.exe": "Pulsar / Atom",
    "geany.exe": "Geany",
    "scite.exe": "SciTE",
    "kate.exe": "Kate",
    "textpad.exe": "TextPad",
    "uedit64.exe": "UltraEdit",
}

_APP_PATHS_ROOTS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", "HKEY_LOCAL_MACHINE"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths", "HKEY_LOCAL_MACHINE"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", "HKEY_CURRENT_USER"),
)


def _editor_candidates() -> list[tuple[str, str]]:
    """本机上真实存在的常见文本编辑器，返回 `[(品牌名, 可执行文件路径)]`。

    两个来源：按品牌列的安装位置（`_EDITOR_LOCATIONS`，支持通配），以及 `App Paths` 里
    命中 `_EDITOR_EXE_NAMES` 的条目。两处都只做只读查询，且**只保留存在的文件**。
    """
    if os.name != "nt":
        return []
    found: list[tuple[str, str]] = []

    for name, patterns in _EDITOR_LOCATIONS:
        for pattern in patterns:
            hits = _expand_pattern(pattern)
            if hits:
                found.append((name, str(hits[0])))
                break

    try:
        import winreg
    except Exception:
        return found

    for sub_key, root_name in _APP_PATHS_ROOTS:
        root = getattr(winreg, root_name)
        try:
            with winreg.OpenKey(root, sub_key) as key:
                index = 0
                while True:
                    try:
                        exe_name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    label = _EDITOR_EXE_NAMES.get(exe_name.lower())
                    if not label:
                        continue
                    try:
                        with winreg.OpenKey(key, exe_name) as entry:
                            value = str(winreg.QueryValue(entry, None) or "")
                    except OSError:
                        continue
                    if not value:
                        continue
                    # `App Paths` 的默认值常带引号（`"C:\…\Code.exe"`），也可能指向已卸载的程序。
                    # 与 `_registered_handlers` 一样在这里就解析干净，别把「剥引号」留给下游。
                    executable = _resolve_executable(value)
                    if executable is not None:
                        found.append((label, str(executable)))
        except OSError:
            continue
    return found


def _expand_pattern(pattern: str) -> list[Path]:
    """展开 `%VAR%` 与 `*` 通配，只返回真实存在的文件（通配按名字排序，保证结果稳定）。"""
    text = os.path.expandvars(pattern)
    if "*" in text or "?" in text:
        try:
            import glob

            return [Path(item) for item in sorted(glob.glob(text, recursive=True)) if Path(item).is_file()]
        except Exception:
            return []
    path = Path(text)
    return [path] if path.is_file() else []


def _command_executable(command: str) -> str:
    """从 `"C:\\Program Files\\App\\app.exe" "%1"` 这种命令里取出可执行文件路径。"""
    text = command.strip()
    if text.startswith('"'):
        return text.split('"')[1]
    return text.split(" ")[0] if text else ""


def _progid_label(winreg: object, progid: str) -> str:
    """ProgID 的显示名：优先它自己的默认值，其次 `\\Application` 子键的默认值。

    **取不到就返回空串**，由调用方退回可执行文件名。此前是 `return progid`，于是
    `Toolbox.Air.b9908000-346e-4c93-ac4a-3f3799308eae` 这种没有默认值的 ProgID 被原样
    印进对话框 —— 用户看到的是一串 GUID。
    """
    for key_path in (progid, rf"{progid}\Application"):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:  # type: ignore[attr-defined]
                value = winreg.QueryValue(key, None)  # type: ignore[attr-defined]
        except OSError:
            continue
        if value:
            return str(value)
    return ""


def _visible_windows() -> dict[int, tuple[int, str, str, int]]:
    """当前可见的顶层窗口：`{hwnd: (pid, 标题, 类名, 高度)}`。

    **不在这里取 exe 名**：那要对每个窗口 `OpenProcess` 一次（本机约 600 个窗口、几十毫秒），
    而调用方每 30ms 轮询一次。要 exe 名的地方用 `_process_exe_name` 惰性查 + 按 pid 缓存。
    """
    if os.name != "nt":
        return {}
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        return {}

    rows: dict[int, tuple[int, str, str, int]] = {}

    def enum_handler(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(300)
            user32.GetWindowTextW(hwnd, title, 300)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            rows[hwnd] = (pid.value, title.value, cls.value, rect.bottom - rect.top)
        except Exception:
            return True
        return True

    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(enum_handler)
    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return {}
    return rows


def _process_exe_name(process_id: int, cache: dict[int, str] | None = None) -> str:
    """进程可执行文件名（小写、去目录）。取不到返回空串；传 `cache` 时按 pid 复用。"""
    if cache is not None and process_id in cache:
        return cache[process_id]
    name = ""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, process_id)
        if handle:
            try:
                buf = ctypes.create_unicode_buffer(512)
                size = ctypes.c_ulong(512)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value).lower()
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        name = ""
    if cache is not None:
        cache[process_id] = name
    return name


def _chooser_window() -> int | None:
    """本进程里已经画出来的自带对话框（按类名 + 标题 + pid 认领；没有就 `None`）。"""
    for hwnd, (pid, title, cls, _height) in _visible_windows().items():
        if pid == os.getpid() and cls == CHOOSER_WINDOW_CLASS and title == CHOOSER_TITLE:
            return hwnd
    return None


def _shell_dialog_window() -> int | None:
    """本进程里由 shell32 画出来的对话框（`SHOpenWithDialog` 的产物，含降级提示框）。"""
    for hwnd, (pid, _title, cls, _height) in _visible_windows().items():
        if pid == os.getpid() and cls == SHELL_DIALOG_CLASS:
            return hwnd
    return None


def _open_with_host_window(exclude: set[int] | None = None) -> int | None:
    """系统「选取应用」弹层的窗口：宿主 exe 是 `OpenWith.exe`（标题因语言而异）。

    先按标题/类名这**不含系统调用**的特征快筛，再用（带缓存的）exe 名确认 —— 免得为几百个窗口
    各开一次进程句柄。
    """
    cache: dict[int, str] = {}
    for hwnd, (pid, title, cls, _height) in _visible_windows().items():
        if exclude and hwnd in exclude:
            continue
        if cls.startswith("Open With") or title in OPEN_WITH_HOST_TITLES:
            return hwnd
        if cls == "Shell_Dialog":
            continue
        if pid and _process_exe_name(pid, cache) == OPEN_WITH_HOST_EXE:
            return hwnd
    return None


def _window_height(hwnd: int) -> int:
    rect = wintypes.RECT()
    try:
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))  # type: ignore[attr-defined]
    except Exception:
        return 0
    return rect.bottom - rect.top


def _close_window(hwnd: int) -> None:
    try:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


class _OpenAsInfo(ctypes.Structure):
    _fields_ = [
        ("pcszFile", wintypes.LPCWSTR),
        ("pcszClass", wintypes.LPCWSTR),
        ("oaifInFlags", ctypes.c_int),
    ]


def _ensure_dialog_foreground(hwnd: int, timeout: float = 1.2) -> bool:
    """让对话框真的出现在最前面。

    顺序**不能反**（本机实测）：窗口刚创建时它自己在激活，此时 `SetForegroundWindow` 会被
    前台锁拒绝，而**被拒一次之后它就不再自行激活**，于是稳稳地开在浏览器后面 —— 先抢反而更糟。
    所以先给它 `timeout` 秒自己上来；只有确实没上来，才用 `AttachThreadInput` 接管输入队列再抢
    （这是绕过前台锁的常规做法）。返回最终是否前台，仅用于探针/日志，不影响接口结果。
    """
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except Exception:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.04)

    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    current_thread = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if foreground_thread:
            attached = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return False
    finally:
        if attached:
            try:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            except Exception:
                pass
    return user32.GetForegroundWindow() == hwnd


def open_video_with_best_player(path: Path, actual_format: str | None = None) -> None:
    player = select_video_player(path, actual_format)
    if player is None:
        format_label = actual_format or path.suffix.lstrip(".") or "未知"
        raise LocalOpenError(
            "找不到可确认能解码当前视频的播放器。"
            f"当前视频格式：{format_label}。"
            "建议安装 VLC media player 或 mpv 后重试。"
        )
    _open_process([*player.command, str(path)], title_hint=path.name)


def select_video_player(path: Path, actual_format: str | None = None) -> VideoPlayerCandidate | None:
    needs_robust_player = _needs_robust_video_player(path, actual_format)
    candidates = _video_player_candidates()
    for candidate in candidates:
        if candidate.robust:
            return candidate
    if not needs_robust_player:
        return next((candidate for candidate in candidates if not candidate.robust), None)
    return None


def _open_windows_folder(path: Path) -> None:
    process = subprocess.Popen(
        ["explorer.exe", "/n,", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _bring_windows_window_to_front(process.pid, path.name)


def _open_process(command: list[str], title_hint: str | None = None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _bring_windows_window_to_front(process.pid, title_hint)


def _video_player_candidates() -> list[VideoPlayerCandidate]:
    candidates: list[VideoPlayerCandidate] = []
    robust_commands = _robust_video_player_commands()
    candidates.extend(VideoPlayerCandidate(name, command, True) for name, command in robust_commands)
    candidates.extend(VideoPlayerCandidate(name, command, False) for name, command in _common_video_player_commands())
    return candidates


def _robust_video_player_commands() -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    for name, executable in [
        ("VLC media player", "vlc"),
        ("mpv", "mpv"),
        ("ffplay", "ffplay"),
    ]:
        resolved = shutil.which(executable)
        if resolved:
            commands.append((name, [resolved]))

    if os.name == "nt":
        for name, path in _windows_known_player_paths():
            if path.exists():
                commands.append((name, [str(path)]))
    elif sys.platform == "darwin":
        for app_name in ["IINA", "VLC"]:
            if (Path("/Applications") / f"{app_name}.app").exists():
                commands.append((app_name, ["open", "-a", app_name]))

    return _deduplicate_commands(commands)


def _common_video_player_commands() -> list[tuple[str, list[str]]]:
    if os.name != "nt":
        return []
    commands: list[tuple[str, list[str]]] = []
    for root in _windows_program_roots():
        wmplayer = root / "Windows Media Player" / "wmplayer.exe"
        if wmplayer.exists():
            commands.append(("Windows Media Player", [str(wmplayer)]))
    return _deduplicate_commands(commands)


def _windows_known_player_paths() -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for root in _windows_program_roots():
        paths.extend(
            [
                ("VLC media player", root / "VideoLAN" / "VLC" / "vlc.exe"),
                ("mpv", root / "mpv" / "mpv.exe"),
                ("PotPlayer", root / "DAUM" / "PotPlayer" / "PotPlayerMini64.exe"),
                ("MPC-HC", root / "MPC-HC" / "mpc-hc64.exe"),
                ("MPC-BE", root / "MPC-BE x64" / "mpc-be64.exe"),
            ]
        )
    return paths


def _windows_program_roots() -> list[Path]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData"),
    ]
    return [Path(root) for root in roots if root]


def _deduplicate_commands(commands: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    seen: set[tuple[str, ...]] = set()
    result: list[tuple[str, list[str]]] = []
    for name, command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        result.append((name, command))
    return result


def _needs_robust_video_player(path: Path, actual_format: str | None = None) -> bool:
    label = f"{path.suffix} {actual_format or ''}".lower()
    robust_hints = ("webm", "mkv", "vp9", "av01", "av1", "opus", "hevc", "h265")
    return any(hint in label for hint in robust_hints)


def _bring_windows_window_to_front(process_id: int | None, title_hint: str | None = None) -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        return

    title_hint_lower = title_hint.lower() if title_hint else None
    for _ in range(20):
        hwnd = _find_window_handle(user32, process_id, title_hint_lower)
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                return
            return
        time.sleep(0.05)


def _find_window_handle(user32: object, process_id: int | None, title_hint_lower: str | None) -> int | None:
    handles: list[int] = []

    def enum_handler(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            title_value = title.value.lower()
            pid_matches = process_id is not None and window_pid.value == process_id
            title_matches = bool(title_hint_lower and title_hint_lower in title_value)
            if pid_matches or title_matches:
                handles.append(hwnd)
                return False
        except Exception:
            return True
        return True

    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(enum_handler)
    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return None
    return handles[0] if handles else None
