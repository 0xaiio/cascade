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
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: raised.append((pid, title)))

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
    opened: list[list[str]] = []
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
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=789),
    )
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: None)

    system_open.open_path_with_default_app(subtitle)

    # 不是报错，而是把「挑一个程序」这件事交回系统 —— 资源管理器自己也是这么做的。
    assert opened == [["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(subtitle)]]


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


def test_chooser_opens_the_system_dialog_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    raised: list[tuple[int | None, str | None]] = []
    subtitle = tmp_path / "video.en.vtt"
    subtitle.write_text("subs", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=321),
    )
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: raised.append((pid, title)))
    monkeypatch.setattr(
        system_open.os,
        "startfile",
        lambda _target: pytest.fail("chooser must not go through the default association"),
        raising=False,
    )

    system_open.open_path_with_chooser(subtitle)

    assert opened == [["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(subtitle)]]
    assert raised == [(321, "video.en.vtt")]


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
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: raised.append((pid, title)))

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
