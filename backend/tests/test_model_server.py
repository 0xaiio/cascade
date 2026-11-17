"""本机模型服务的按需拉起。

这里不真的起 llama-server：所有进程与网络边界都用替身钉住。重点不是
「命令能不能跑」，而是三件事：已健康时不重复起、Vulkan 不行会回退 CPU、
失败时给用户的是下一步而不是 WinError 原文。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts.config import AppSettings
from app.translation import model_server
from app.translation.model_server import ModelServerError, ensure_model_server, find_model, find_server_binary


def settings_for(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        translation_base_url="http://127.0.0.1:8080/v1",
    )


class FakeProc:
    def __init__(self, pid: int = 4242, *, exits: bool = False) -> None:
        self.pid = pid
        self._exits = exits
        self.terminated = False

    def poll(self) -> int | None:
        return 1 if self._exits else None

    def terminate(self) -> None:
        self.terminated = True


def test_already_running_server_is_reused_without_launching(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(model_server, "is_healthy", lambda url: True)

    def forbidden(*args, **kwargs):
        raise AssertionError("服务已健康时不该再起进程")

    monkeypatch.setattr(model_server, "_launch", forbidden)
    assert ensure_model_server(settings_for(tmp_path)) is None


def test_missing_model_and_binary_say_what_to_install(monkeypatch) -> None:
    monkeypatch.setattr(model_server, "_candidate_model_paths", lambda: [])
    with pytest.raises(ModelServerError, match="GGUF"):
        find_model()

    monkeypatch.setattr(model_server.os.environ, "get", lambda key, default="": default)
    monkeypatch.setattr(model_server.shutil, "which", lambda name: None)
    with pytest.raises(ModelServerError, match="llama-server"):
        find_server_binary()


def test_vulkan_launch_is_used_first_and_reports_readiness(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_launch(binary, model, port, log, *, cpu_only):
        calls.append({"cpu_only": cpu_only, "port": port, "host_flag": "--host", "log": log})
        return FakeProc()

    # 入口检查：没起；随后 Vulkan 轮询：就绪。
    checks = iter([False, True])
    monkeypatch.setattr(
        model_server, "is_healthy", lambda url, timeout=2.0: next(checks, True)
    )
    monkeypatch.setattr(model_server, "find_server_binary", lambda: "llama-server")
    monkeypatch.setattr(model_server, "find_model", lambda: Path("D:/models/q.gguf"))
    monkeypatch.setattr(model_server, "_launch", fake_launch)
    monkeypatch.setattr(model_server.time, "sleep", lambda seconds: None)

    message = ensure_model_server(settings_for(tmp_path))
    assert message and "4242" in message
    assert [call["cpu_only"] for call in calls] == [False]


def test_cpu_fallback_runs_when_the_gpu_backend_exits(monkeypatch, tmp_path: Path) -> None:
    calls: list[bool] = []

    def fake_launch(binary, model, port, log, *, cpu_only):
        calls.append(cpu_only)
        return FakeProc(exits=not cpu_only)  # Vulkan 立刻退出，CPU 版活着

    # 入口没起 → Vulkan 轮询一次仍未就绪（于是发现进程退出）→ CPU 轮询就绪。
    checks = iter([False, False, True])
    monkeypatch.setattr(
        model_server, "is_healthy", lambda url, timeout=2.0: next(checks, True)
    )
    monkeypatch.setattr(model_server, "find_server_binary", lambda: "llama-server")
    monkeypatch.setattr(model_server, "find_model", lambda: Path("D:/models/q.gguf"))
    monkeypatch.setattr(model_server, "_launch", fake_launch)
    monkeypatch.setattr(model_server.time, "sleep", lambda seconds: None)

    message = ensure_model_server(settings_for(tmp_path))
    assert "CPU 回退" in message
    assert calls == [False, True]


def test_failure_names_the_log_and_never_leaks_winerror(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(model_server, "is_healthy", lambda url, timeout=2.0: False)
    monkeypatch.setattr(model_server, "find_server_binary", lambda: "llama-server")
    monkeypatch.setattr(model_server, "find_model", lambda: Path("D:/models/q.gguf"))
    monkeypatch.setattr(model_server, "_launch", lambda *a, **k: FakeProc(exits=True))
    monkeypatch.setattr(model_server.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(model_server.time, "monotonic", _fast_clock())

    with pytest.raises(ModelServerError) as caught:
        ensure_model_server(settings_for(tmp_path), timeout=0)
    text = str(caught.value)
    assert "model-server.log" in text
    assert "10061" not in text


def _fast_clock():
    ticks = iter([0.0, 1.0])
    return lambda: next(ticks, 1.0)


def test_launch_command_binds_loopback_only(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    class Recorder:
        def __init__(self, *args, **kwargs) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(model_server.subprocess, "Popen", Recorder)
    log = tmp_path / "data" / "logs" / "model-server.log"
    model_server._launch("llama-server", Path("D:/models/q.gguf"), 8080, log, cpu_only=False)

    args = captured["args"][0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "8080"
    assert "Vulkan0" in args
    assert captured["kwargs"].get("shell", False) is False
    assert log.parent.is_dir()
