"""按需拉起**本机**模型服务。

为什么需要这一层：翻译按钮点下去时，`llama-server` 很可能根本没运行（机器重启过、
用户关过窗口、应用上一轮退出时顺手停了）。过去这会让每条字幕都收到一句原始的
`urlopen error [WinError 10061]` —— 用户既不知道发生了什么，也不知道该做什么。
现在由翻译编排在真正调用模型之前先确认服务可用，不可用就用**已安装的**
`llama-server` 和**本机已有的** GGUF 自动拉起。

安全边界（与 `local.validate_local_base_url` 一致）：

* 只连 loopback 地址；地址不是本机时在 `local` 层就已被拒绝，这里不会再去启动进程。
* 只执行 PATH 里的 `llama-server`（或 `YTDL_TRANSLATION_SERVER` 指定的可执行文件），
  不用 `shell=True`，不拼接命令字符串。
* 只绑定 `127.0.0.1`，端口取自设置里的 `translation_base_url`。
* 服务进程以 detached 方式启动：它是用户机器上的另一个程序，不随本应用退出而消失，
  下次点翻译可以直接复用。日志写到 `data/logs/model-server.log`，不污染启动终端。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ..contracts.config import AppSettings

DEFAULT_START_TIMEOUT_SECONDS = 150.0


class ModelServerError(RuntimeError):
    """服务没就绪，消息是可直接展示给用户的结论。"""


def _candidate_model_paths() -> list[Path]:
    configured = os.environ.get("YTDL_TRANSLATION_MODEL_PATH", "").strip()
    paths: list[Path] = []
    if configured:
        paths.append(Path(configured))
    # 这些是常见的本机模型落点；只做存在性检查，不会从网上拉任何东西。
    roots = [
        Path("D:/models"),
        Path.home() / ".cache" / "llama.cpp",
        Path.home() / ".lmstudio" / "models",
        Path.cwd() / "models",
    ]
    for root in roots:
        if root.is_dir():
            paths.extend(sorted(root.rglob("*.gguf"), key=lambda p: p.name.lower()))
    return paths


def find_model() -> Path:
    """找到本机已有的 GGUF。找不到就明确说缺什么，不猜一个不存在的路径。"""
    for path in _candidate_model_paths():
        if path.is_file():
            return path
    raise ModelServerError(
        "本机找不到可用的 GGUF 模型文件。请把 Qwen3-8B 的 GGUF 放进 D:/models "
        "（或 ~/.cache/llama.cpp），也可以用环境变量 YTDL_TRANSLATION_MODEL_PATH 指定绝对路径。"
    )


def find_server_binary() -> str:
    configured = os.environ.get("YTDL_TRANSLATION_SERVER", "").strip()
    binary = configured or shutil.which("llama-server")
    if not binary:
        raise ModelServerError(
            "本机没有 llama-server。请先安装 llama.cpp 并确保 `llama-server` 在 PATH 中，"
            "或用环境变量 YTDL_TRANSLATION_SERVER 指定它的绝对路径。"
        )
    return binary


def is_healthy(base_url: str, *, timeout: float = 2.0) -> bool:
    """`llama-server` 加载完成才回 200；加载期间回 503，所以轮询到 200 才算就绪。"""
    parts = urllib.parse.urlsplit(base_url)
    health = f"{parts.scheme}://{parts.netloc}/health"
    try:
        with urllib.request.urlopen(health, timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - 探针的任何失败都只是「还没就绪」
        return False


def _launch(binary: str, model: Path, port: int, log_path: Path, *, cpu_only: bool) -> subprocess.Popen:
    flags = ["-m", str(model), "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "4096", "--no-webui"]
    # 本机实测 Vulkan 后端 12 秒加载完 8B；没有 Vulkan 时退回 CPU 也能跑，只是慢。
    if not cpu_only:
        flags.extend(["-dev", "Vulkan0"])
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return subprocess.Popen(
            [binary, *flags],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )


def _wait_until_healthy(
    base_url: str, proc: subprocess.Popen, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_healthy(base_url):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(1.0)
    return False


def ensure_model_server(settings: AppSettings, *, timeout: float = DEFAULT_START_TIMEOUT_SECONDS) -> str | None:
    """服务没起就拉起来。返回 `None` 表示本来就在跑，否则返回一句就绪说明。

    失败时抛 `ModelServerError`，消息会经后端翻成 409 / 任务结论，
    **不会**把 `WinError` 原文丢给用户。
    """
    base_url = settings.translation_base_url
    if is_healthy(base_url):
        return None

    binary = find_server_binary()
    model = find_model()
    parts = urllib.parse.urlsplit(base_url)
    port = parts.port or 8080
    log_path = settings.data_dir / "logs" / "model-server.log"

    proc = _launch(binary, model, port, log_path, cpu_only=False)
    if not _wait_until_healthy(base_url, proc, timeout):
        # Vulkan 不可用是本机常见情况；退回 CPU 再试一次，不让用户去猜显卡参数。
        if proc.poll() is None:
            proc.terminate()
        fallback = _launch(binary, model, port, log_path, cpu_only=True)
        if not _wait_until_healthy(base_url, fallback, timeout):
            raise ModelServerError(
                f"本机模型服务没能就绪（端口 {port}）。请看 {log_path} 末尾的报错；"
                "若显卡后端不可用，应用已自动试过 CPU 回退。"
            )
        return f"已启动本机模型服务（CPU 回退，PID {fallback.pid}）"
    return f"已启动本机模型服务（PID {proc.pid}）"
