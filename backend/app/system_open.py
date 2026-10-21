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

# `SHOpenWithDialog` 的三个开关（shlobj.h）：
#   ALLOW_REGISTRATION —— 窗口里可以勾「始终使用此应用」；
#   HIDE_REGISTRATION  —— 不显示那行「以后都用这个打开」的注册提示（勾选框仍可用）；
#   EXEC               —— 挑完直接执行，不用再让调用方自己起进程。
OAIF_ALLOW_REGISTRATION = 0x00000001
OAIF_EXEC = 0x00000002
OAIF_HIDE_REGISTRATION = 0x00000020

# 系统「打开方式」对话框的窗口类名（shell32 在本进程内绘制，不经过 explorer 托管的弹层）。
OPEN_WITH_DIALOG_CLASS = "Shell_Dialog"

# 等窗口出现的时间。实测冷启动 0.1~0.4s；超过这个数还没窗口，就当作「弹不出来」。
OPEN_WITH_APPEAR_TIMEOUT_SECONDS = 2.5


@dataclass(frozen=True)
class VideoPlayerCandidate:
    name: str
    command: list[str]
    robust: bool


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
    """让**系统**弹出「打开方式」窗口，由用户在系统自己的列表里挑一个程序。

    列表里是**系统注册的全部处理器**。自己维护一份编辑器清单只会是它的子集
    （用户装了什么我们无从得知），还要为每个平台各维护一份 —— 所以这里不造第二个列表。

    用户在窗口里勾「始终使用此应用」，系统就把这个后缀的关联记下来了
    （`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\.srt\\UserChoice`），
    **之后用文件关联打开时就是那个程序**。也就是说「记住上一次用的查看器」由系统负责，
    应用侧不另存一份 —— 两处各记一份时，先信谁就会变成一个说不清的问题。

    **窗口必须在「我们自己的进程里」画出来**：Win11 的「选取应用」是 explorer 托管的现代
    弹层，`rundll32 shell32.dll,OpenAs_RunDLL` 只是请它出来；那个宿主一旦失效（本机实测过：
    调用返回 0、`OpenWith.exe` 起来了、窗口一个也没有），用户看到的就是「点了没反应」。
    所以这里用文档化的 `SHOpenWithDialog` —— 对话框（窗口类 `Shell_Dialog`）由 shell32 在
    本进程内绘制，不经过那个宿主。

    它是**模态**的：调用会一直阻塞到用户选完或取消，所以放在自己的线程里，接口这边等到
    「窗口真的出现」就返回；**窗口没出现就当失败**抛 `LocalOpenError`，绝不再 204 空转。

    其它平台没有等价入口（macOS 的 `open -a` 要的是应用名，Linux 的 `xdg-open` 只有默认），
    于是退化成「用默认程序打开」。见 `docs/technical.md#打开字幕文件`。
    """
    if os.name != "nt":
        open_path_with_default_app(path)
        return
    _open_windows_chooser(path)


def _open_windows_chooser(path: Path) -> None:
    """Windows 上的「挑程序」：在本进程里画 `SHOpenWithDialog`，并**确认它真的画出来了**。

    三步都必要：
    1. 已经有一个窗口在等用户时不再叠一个 —— 连点几下会得到一摞一模一样的模态窗口；
    2. 模态调用放进自己的线程，这里只等到「窗口出现」为止（否则接口会挂到用户点完）；
    3. 窗口始终没出现 = 失败。**静默 204 是最糟的结果**：界面看起来点过了，其实什么都没发生。

    **前台**：窗口刚创建时它自己在激活；这时去抢会被前台锁拒绝，而且被拒之后它就不再自行
    激活（本机实测：提前抢 → 前台始终是浏览器；不碰它 → 对话框自己上来）。所以先给它时间
    自己上来，确实没上来才用 `_ensure_dialog_foreground` 里的 attach 手法。
    """
    if _open_with_dialog_window():
        raise LocalOpenError(
            "已经有一个「打开方式」窗口在等你选择：先在那个窗口里选完（或取消），再点一次。"
        )

    outcome = OpenWithOutcome()
    worker = threading.Thread(
        target=_run_open_with_dialog,
        args=(path, outcome),
        name="open-with-dialog",
        daemon=True,
    )
    worker.start()

    deadline = time.monotonic() + OPEN_WITH_APPEAR_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        window = _open_with_dialog_window()
        if window:
            _ensure_dialog_foreground(window)
            return
        if not worker.is_alive():
            break
        time.sleep(0.03)

    if outcome.hresult:
        detail = f"（HRESULT {outcome.hresult & 0xFFFFFFFF:#010x}）"
    elif outcome.error:
        detail = f"（{outcome.error}）"
    else:
        detail = ""
    raise LocalOpenError(
        "系统没能弹出「打开方式」窗口"
        f"{detail}。可以先点「打开视频所在文件夹」，在资源管理器里右键那个字幕文件 → 打开方式；"
        "或者把该后缀的默认程序在系统设置里指定一次。"
    )


@dataclass
class OpenWithOutcome:
    """`SHOpenWithDialog` 的结果。有值 = 调用已返回；`None` = 还在等用户选。"""

    hresult: int | None = None
    error: str | None = None


class _OpenAsInfo(ctypes.Structure):
    _fields_ = [
        ("pcszFile", wintypes.LPCWSTR),
        ("pcszClass", wintypes.LPCWSTR),
        ("oaifInFlags", ctypes.c_int),
    ]


def _run_open_with_dialog(path: Path, outcome: OpenWithOutcome) -> None:
    """在工作线程里调 `SHOpenWithDialog`：模态，会一直阻塞到用户选完或取消。"""
    ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    try:
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    except Exception:
        pass
    try:
        info = _OpenAsInfo(
            str(path),
            None,
            OAIF_ALLOW_REGISTRATION | OAIF_HIDE_REGISTRATION | OAIF_EXEC,
        )
        outcome.hresult = int(ctypes.windll.shell32.SHOpenWithDialog(None, ctypes.byref(info)))
    except Exception as exc:  # ctypes 层的失败也要如实报出来，别让线程静默结束
        outcome.error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            ole32.CoUninitialize()
        except Exception:
            pass


def _open_with_dialog_window() -> int | None:
    """本进程里已经画出来的「打开方式」窗口句柄（没有就 `None`）。"""
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
            if name.value == OPEN_WITH_DIALOG_CLASS:
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
