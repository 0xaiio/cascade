import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
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

# 自带选择对话框的窗口特征（tkinter 顶层窗口；标题与 `_CHOOSER_TITLE` 一致才认领）。
CHOOSER_WINDOW_CLASS = "TkTopLevel"
CHOOSER_TITLE = "打开方式"

# 等对话框出现的时间。实测 0.1~0.4s；超过这个数还没窗口，就当作「弹不出来」。
OPEN_WITH_APPEAR_TIMEOUT_SECONDS = 3.0


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
    """选择对话框的结局：`cancelled` = 用户点了取消；`error` = 对话框/打开动作本身出错。"""

    cancelled: bool = False
    error: str | None = None
    launched: str | None = None


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
    """弹一个「打开方式」对话框，由用户挑一个程序来打开这个文件。

    **为什么不再用系统的那个窗口。** 系统本来有这个 UI，但它不是稳定的依赖：本机实测
    Win11 的「选取应用」弹层（`OpenAs_RunDLL` 那条路）调用返回 0 却什么窗口都没有；改用文档化的
    `SHOpenWithDialog` 之后，无论标志位怎么给、文件是什么类型（`.txt` / `.mp4` / `.vtt`），
    弹出来的都是一个 680x118 的提示框 ——「若要更改默认应用，请转到"设置">"应用">"默认应用"。」，
    里面**没有可选程序列表**。也就是说这套系统 UI 在这台机器上整体降级了，任何"请它出来"的
    做法都拿不到选择器。

    所以这里自己画一个对话框（tkinter，与目录选择器同一套依赖），但**不自己编程序清单**：
    候选来自系统注册表里为这个后缀登记过的处理器（见 `_program_candidates`），列表为空时
    （`.vtt` / `.srt` 通常就是空的）由用户「浏览…」挑一个可执行文件。选择与启动都在本进程内
    完成，**不接受前端传入的程序路径**。

    对话框是模态的，跑在自己的线程里；接口这边等到「窗口真的出现」就返回，**窗口没出现就当
    失败**抛 `LocalOpenError`，绝不 204 空转。

    其它平台没有等价入口（macOS 的 `open -a` 要的是应用名，Linux 的 `xdg-open` 只有默认），
    于是退化成「用默认程序打开」。见 `docs/technical.md#打开字幕文件`。
    """
    if os.name != "nt":
        open_path_with_default_app(path)
        return
    _open_windows_chooser(path)


def _open_windows_chooser(path: Path) -> None:
    """Windows 上的「挑程序」：起自带对话框，并**确认它真的出来了**。

    四条都是踩出来的：
    1. 已经有一个窗口在等用户时不再叠一个 —— 连点几下会得到一摞一模一样的模态窗口；
    2. 对话框跑在自己的线程里，这里只等到「窗口出现」为止（否则接口会挂到用户点完）；
    3. 窗口始终没出现 = 失败；**静默 204 是最糟的结果**（界面看起来点过了，其实什么都没发生）；
    4. 用户点「取消」不是失败，也不该弹错误。
    """
    if _chooser_window():
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
    while time.monotonic() < deadline:
        window = _chooser_window()
        if window:
            _ensure_dialog_foreground(window)
            return
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
    """对话框 + 启动被选中的程序。整段异常都要落到 `outcome.error`，线程不许静默结束。"""
    try:
        executable = _ask_program(path)
    except Exception as exc:  # 例如环境里没有 tkinter
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


def _ask_program(path: Path) -> Path | None:
    """画「打开方式」对话框并等用户选择；返回选中的可执行文件，取消则返回 `None`。"""
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
    root.geometry("620x360")
    root.minsize(480, 260)
    root.attributes("-topmost", True)

    chosen: dict[str, Path | None] = {"exe": None}

    tk.Label(
        root,
        text=f"用哪个程序打开「{_shorten(path.name, 64)}」？",
        anchor="w",
        justify="left",
        wraplength=580,
    ).pack(fill="x", padx=14, pady=(14, 6))

    body = tk.Frame(root)
    body.pack(fill="both", expand=True, padx=14)
    listbox = tk.Listbox(body, activestyle="none", exportselection=False)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    for candidate in candidates:
        listbox.insert("end", f"{candidate.name}    —    {candidate.executable}")
    if candidates:
        listbox.selection_set(0)

    hint = (
        "这些是系统为这个后缀登记过的程序。"
        if candidates
        else "系统还没有为这个后缀登记任何程序，点「浏览…」挑一个（记事本、VS Code、VLC 都行）。"
    )
    tk.Label(root, text=hint, anchor="w", justify="left", wraplength=580, fg="#4a5a70").pack(
        fill="x", padx=14, pady=(8, 0)
    )
    tk.Label(
        root,
        text="想让它以后默认用某个程序打开，请到「设置 > 应用 > 默认应用」。",
        anchor="w",
        justify="left",
        wraplength=580,
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
        picked = filedialog.askopenfilename(
            parent=root,
            title="选择用来打开它的程序",
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


def _program_candidates(ext: str) -> list[ProgramCandidate]:
    """系统为这个后缀登记过的处理器（读注册表），去重、只留真实存在的可执行文件。

    三个来源都是**系统自己的登记**，不是我们编的清单：
      - `HKCU\\...\\FileExts\\<ext>\\OpenWithList`（用户用过的，带 MRU 顺序）；
      - `HKCR\\<ext>\\OpenWithList`（按 exe 名登记）；
      - `HKCR\\<ext>\\OpenWithProgids`（按 ProgID 登记，命令在 `HKCR\\<ProgID>\\shell\\open\\command`）。
    """
    if os.name != "nt" or not ext:
        return []
    try:
        import winreg
    except Exception:
        return []

    extension = ext if ext.startswith(".") else f".{ext}"
    entries: list[ProgramCandidate] = []
    seen: set[str] = set()

    def add(name: str, executable: str | Path) -> None:
        exe = Path(os.path.expandvars(str(executable).strip().strip('"')))
        if not exe.is_file():
            return
        key = str(exe).lower()
        if key in seen:
            return
        seen.add(key)
        # `AppX4ztfk9wxr86nxmzzq47px0nh0e58b8fw` 这种是包标识，不是给人看的名字。
        label = exe.stem if not name or name.startswith("AppX") else name
        entries.append(ProgramCandidate(label, exe))

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
                    if name.isdigit():  # `a` / `b` / `MRUList` 这些不是程序项
                        add(Path(str(value)).stem, str(value))
        except OSError:
            pass

    read_exe_names(
        winreg.HKEY_CURRENT_USER,
        rf"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{extension}\\OpenWithList",
    )
    read_exe_names(winreg.HKEY_CLASSES_ROOT, rf"{extension}\\OpenWithList")

    progids: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{extension}\\OpenWithProgids") as key:
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
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\\shell\\open\\command") as key:
                command = str(winreg.QueryValue(key, None) or "")
        except OSError:
            continue
        add(_progid_label(winreg, progid), _command_executable(command))

    return entries


def _command_executable(command: str) -> str:
    """从 `"C:\\Program Files\\App\\app.exe" "%1"` 这种命令里取出可执行文件路径。"""
    text = command.strip()
    if text.startswith('"'):
        return text.split('"')[1]
    return text.split(" ")[0] if text else ""


def _progid_label(winreg: object, progid: str) -> str:
    """ProgID 的显示名：优先它自己的默认值，其次 `\\Application` 子键的默认值。"""
    for key_path in (progid, rf"{progid}\\Application"):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:  # type: ignore[attr-defined]
                value = winreg.QueryValue(key, None)  # type: ignore[attr-defined]
        except OSError:
            continue
        if value:
            return str(value)
    return progid


def _chooser_window() -> int | None:
    """本进程里已经画出来的选择对话框（按类名 + 标题认领；没有就 `None`）。"""
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        return None

    found: list[int] = []
    process_id = os.getpid()

    def enum_handler(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != process_id:
                return True
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 256)
            if name.value != CHOOSER_WINDOW_CLASS:
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value == CHOOSER_TITLE:
                found.append(hwnd)
                return False
        except Exception:
            return True
        return True

    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(enum_handler)
    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return None
    return found[0] if found else None


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
