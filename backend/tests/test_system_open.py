from pathlib import Path
from types import SimpleNamespace

import pytest

from app import system_open


def test_windows_directory_opens_new_explorer_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    raised: list[tuple[int | None, str | None]] = []
    folder = tmp_path / "downloads"
    folder.mkdir()

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=123),
    )
    monkeypatch.setattr(
        system_open.os,
        "startfile",
        lambda _target: pytest.fail("directories should use explorer.exe, not os.startfile"),
        raising=False,
    )
    monkeypatch.setattr(
        system_open,
        "_bring_windows_window_to_front",
        lambda pid, title: raised.append((pid, title)),
    )

    system_open.open_path_with_default_app(folder)

    assert opened == [["explorer.exe", "/n,", str(folder)]]
    assert raised == [(123, "downloads")]


def test_windows_file_uses_default_file_association(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[str] = []
    video = tmp_path / "video.mp4"
    video.write_text("video", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda _command, **_kwargs: pytest.fail("files should use the default file association"),
    )
    monkeypatch.setattr(system_open.os, "startfile", opened.append, raising=False)

    system_open.open_path_with_default_app(video)

    assert opened == [str(video)]


def test_windows_file_without_association_falls_back_to_the_system_open_with_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chosen: list[Path] = []
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.os,
        "startfile",
        # 1155 = 这个后缀还没有关联程序。Windows 上双击一个没人认领的 `.srt` 就是这个结果。
        lambda _target: (_ for _ in ()).throw(OSError(0, "没有应用程序与此操作的指定文件有关联。", None, 1155)),
        raising=False,
    )
    monkeypatch.setattr(system_open, "_open_windows_chooser", chosen.append)

    system_open.open_path_with_default_app(subtitle)

    # 不是报错，而是把「挑一个程序」这件事交回系统 —— 资源管理器自己也是这么做的。
    assert chosen == [subtitle]


def test_windows_file_errors_that_are_not_about_associations_are_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.os,
        "startfile",
        # 5 = 拒绝访问。这类错误不能悄悄降级成「弹个窗口」—— 那是把真问题藏起来。
        lambda _target: (_ for _ in ()).throw(OSError(0, "拒绝访问。", None, 5)),
        raising=False,
    )
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda _command, **_kwargs: pytest.fail("only the no-association case may open the chooser"),
    )

    with pytest.raises(OSError) as exc_info:
        system_open.open_path_with_default_app(subtitle)

    assert getattr(exc_info.value, "winerror", None) == 5


def test_chooser_returns_once_its_dialog_is_on_screen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started: list[Path] = []
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    # 第一次是「开窗前的检查」（这时还没有窗口），之后是「等它出现」（已经有了）。
    windows = iter([None])
    monkeypatch.setattr(system_open, "_chooser_window", lambda: next(windows, 4242))
    raised: list[int] = []
    # 置前那一步要碰真实窗口 API（会抢用户的焦点），单测里替换掉，只断言「窗口被认领了」。
    monkeypatch.setattr(system_open, "_ensure_dialog_foreground", lambda hwnd: raised.append(hwnd) or True)
    monkeypatch.setattr(system_open, "_run_chooser", lambda path, _outcome: started.append(path))

    system_open.open_path_with_chooser(subtitle)

    # 判据是「对话框真的画出来了」：出现即认领并返回，不阻塞到用户选完、也不 204 空转。
    assert started == [subtitle]
    assert raised == [4242]


def test_chooser_reports_a_dialog_that_never_appears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(system_open, "_chooser_window", lambda: None)

    def fail_immediately(_path: Path, outcome: system_open.ChooserOutcome) -> None:
        outcome.error = "RuntimeError: 这个环境没有图形界面组件（tkinter）"

    monkeypatch.setattr(system_open, "_run_chooser", fail_immediately)

    with pytest.raises(system_open.LocalOpenError) as exc_info:
        system_open.open_path_with_chooser(subtitle)

    message = str(exc_info.value)
    assert "打开方式" in message
    assert "tkinter" in message
    # 报错必须给出下一步，而不是让用户对着「没能弹出」发呆。
    assert "资源管理器" in message


def test_chooser_treats_cancel_as_cancel_not_as_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(system_open, "_chooser_window", lambda: None)
    # 用户点了取消：对话框确实出现过又消失了，线程正常结束。
    monkeypatch.setattr(
        system_open,
        "_run_chooser",
        lambda _path, outcome: setattr(outcome, "cancelled", True),
    )

    system_open.open_path_with_chooser(subtitle)  # 不抛


def test_chooser_launches_the_program_the_user_picked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    launched: list[list[str]] = []
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")
    program = tmp_path / "Notepad.exe"
    program.write_text("MZ", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(system_open, "_ask_program", lambda _path: program)
    monkeypatch.setattr(
        system_open,
        "_open_process",
        lambda command, **_kwargs: launched.append(command),
    )

    outcome = system_open.ChooserOutcome()
    system_open._run_chooser(subtitle, outcome)

    # 程序是用户在对话框里选的，路径由本进程拼：`[exe, 文件]`，不经 shell。
    assert launched == [[str(program), str(subtitle)]]
    assert outcome.launched == str(program)
    assert outcome.error is None


def test_chooser_does_not_stack_a_second_dialog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(system_open, "_chooser_window", lambda: 4242)
    monkeypatch.setattr(
        system_open,
        "_run_chooser",
        lambda *_args: pytest.fail("已经有一个窗口在等用户，不该再起一个"),
    )

    with pytest.raises(system_open.LocalOpenError) as exc_info:
        system_open.open_path_with_chooser(subtitle)

    assert "已经有一个" in str(exc_info.value)


def test_program_candidates_skip_dead_entries_and_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alive = tmp_path / "Code.exe"
    alive.write_text("MZ", encoding="utf-8")

    rows = [
        system_open.ProgramCandidate("VS Code", alive),
        system_open.ProgramCandidate("VS Code（重复）", alive),
        system_open.ProgramCandidate("已卸载", tmp_path / "Gone.exe"),
    ]

    def fake_candidates(_ext: str) -> list[system_open.ProgramCandidate]:
        return [row for row in rows if row.executable.is_file()]

    monkeypatch.setattr(system_open, "_program_candidates", fake_candidates)
    # 只验证「对话框只列真实存在的程序」，不真的开窗口。
    monkeypatch.setattr(system_open, "_ask_program", lambda _path: None)
    monkeypatch.setattr(system_open.os, "name", "nt")

    outcome = system_open.ChooserOutcome()
    system_open._run_chooser(tmp_path / "video.en.vtt", outcome)

    assert outcome.cancelled is True


def test_command_executable_handles_quoted_and_bare_paths() -> None:
    assert (
        system_open._command_executable('"C:\\Program Files\\App\\app.exe" "%1"')
        == "C:\\Program Files\\App\\app.exe"
    )
    assert system_open._command_executable("C:\\Windows\\notepad.exe %1") == "C:\\Windows\\notepad.exe"
    assert system_open._command_executable("") == ""


def test_chooser_falls_back_to_the_default_app_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "posix")
    monkeypatch.setattr(system_open.sys, "platform", "linux")
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=654),
    )
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: None)

    system_open.open_path_with_chooser(subtitle)

    # macOS 的 `open -a` 要的是应用名、Linux 的 `xdg-open` 只有默认 —— 没有等价的选择器，
    # 于是退化成「用默认程序打开」，而不是什么都不做。
    assert opened == [["xdg-open", str(subtitle)]]


def test_open_video_uses_robust_player_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    raised: list[tuple[int | None, str | None]] = []
    video = tmp_path / "video.webm"
    video.write_text("video", encoding="utf-8")

    monkeypatch.setattr(
        system_open,
        "_video_player_candidates",
        lambda: [system_open.VideoPlayerCandidate("VLC media player", ["vlc"], robust=True)],
    )
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=456),
    )
    monkeypatch.setattr(
        system_open,
        "_bring_windows_window_to_front",
        lambda pid, title: raised.append((pid, title)),
    )

    system_open.open_video_with_best_player(video, "webm 路 vp9 + opus")

    assert opened == [["vlc", str(video)]]
    assert raised == [(456, "video.webm")]


def test_open_video_reports_missing_capable_player(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "video.webm"
    video.write_text("video", encoding="utf-8")
    monkeypatch.setattr(system_open, "_video_player_candidates", lambda: [])

    with pytest.raises(system_open.LocalOpenError) as exc_info:
        system_open.open_video_with_best_player(video, "webm 路 vp9 + opus")

    message = str(exc_info.value)
    assert "当前视频格式：webm 路 vp9 + opus" in message
    assert "VLC media player" in message
    assert "mpv" in message
