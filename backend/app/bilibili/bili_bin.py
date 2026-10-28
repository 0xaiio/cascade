"""投稿要用到的**外部可执行文件**：`biliup` 的探测/命令组装/执行，以及 ffmpeg 抽封面。

**所有对 `biliup` 的依赖都收敛在这里。** 上游项目 `biliup-rs` 已归档（迁移到 `biliup/biliup`），
将来换工具时只需要改本模块与它的单测 —— 这是把它单独成模块的唯一理由。

ffmpeg 只用来抽封面，且**路径由调用方传进来**（来源是 `YtDlpService.ffmpeg_executable()`）：
再写一份探测就是两处真相，会出现「诊断面板说 ffmpeg 可用、抽封面却失败」这种最难查的不一致。

Windows 特有的一条坑（arXiv 项目实测）：winget 未开「开发者模式」时创建符号链接会失败，
在 `%LOCALAPPDATA%\\Microsoft\\WinGet\\Links\\biliup.exe` 留下一个 **0 字节空壳**。
该目录在用户 PATH 里优先级最高，于是 `shutil.which` 找得到它，而 `CreateProcess`
会报「不是此 OS 平台的有效应用」。所以每一级命中都必须验 PE 头。
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .bili_meta import UploadMetadata, effective_source, normalize_copyright
from .bili_policy import TIMEOUT_MARKER

# biliup 上传大文件可能耗时数分钟；上限只兜住「卡死不返回」，不限制正常上传。
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 1800.0

WINGET_LINK_PATH = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\biliup.exe"))
WINGET_PACKAGE_GLOB = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")

INSTALL_HINT = "winget install --id ForgQi.biliup-rs --exact"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# 行回调：上传过程中把 biliup 的每一行吐出去，用于粗粒度进度与日志尾部。
LineSink = Callable[[str], None]
# 进程执行接缝。测试注入一个假 runner 就能完全离线。
Runner = Callable[[Sequence[str], float, LineSink | None], CommandResult]


def is_pe(path: str | os.PathLike[str]) -> bool:
    """是不是一个非空的 Windows PE（MZ 头）。0 字节空壳在这里被挡住。"""
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


def _first_valid(candidates: Sequence[str | None]) -> str | None:
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and is_pe(candidate):
            return candidate
    return None


def find_biliup(explicit: str | None = None) -> str | None:
    """显式配置 > PATH > winget 包目录。每一级都过 `is_pe`，避免拿到 0 字节空壳。"""
    if explicit:
        return explicit if Path(explicit).is_file() and is_pe(explicit) else None
    from_path = [shutil.which("biliup"), shutil.which("biliup.exe")]
    package_hits = glob.glob(
        os.path.join(WINGET_PACKAGE_GLOB, "ForgQi.biliup-rs*", "**", "biliup.exe"), recursive=True
    )
    return _first_valid([*from_path, *package_hits])


def detect_broken_stub(
    explicit: str | None = None,
    *,
    link_path: Path | None = None,
    package_dir: str | None = None,
) -> tuple[str | None, str | None]:
    """返回 `(空壳路径, 可用于替换的真实二进制)`；没有空壳时返回 `(None, None)`。

    判据刻意收得很窄：**必须**是那个具体路径、存在、不是 PE，且能找到一个真实的 PE 来替换。
    这样它不会把「用户自己放了个别的文件在那儿」误判成需要修复。
    """
    link = link_path or WINGET_LINK_PATH
    try:
        exists = link.exists()
    except OSError:
        return None, None
    if not exists or is_pe(link):
        return None, None
    real = find_biliup(explicit)
    if not real:
        root = package_dir or WINGET_PACKAGE_GLOB
        real = _first_valid(
            glob.glob(os.path.join(root, "ForgQi.biliup-rs*", "**", "biliup.exe"), recursive=True)
        )
    if not real or os.path.abspath(real) == os.path.abspath(link):
        return None, None
    return str(link), real


def repair_broken_stub(
    explicit: str | None = None,
    *,
    link_path: Path | None = None,
    package_dir: str | None = None,
) -> str | None:
    """用真实二进制覆盖 0 字节空壳。**不删除任何文件**（覆盖一个坏掉的空文件是原位修复）。"""
    link, real = detect_broken_stub(explicit, link_path=link_path, package_dir=package_dir)
    if not link or not real:
        return None
    try:
        shutil.copy(real, link)
    except OSError as exc:  # noqa: BLE001 - 修复失败不该让体检整体失败
        return f"PATH 上的 biliup.exe 是一个 0 字节空文件，自动修复失败：{exc}。可手动把 {real} 复制到 {link}。"
    return f"PATH 上的 biliup.exe 原本是 0 字节空文件，已用 {real} 覆盖修复。"


def biliup_version(exe: str, runner: Runner | None = None) -> str | None:
    run = runner or run_command
    try:
        result = run([exe, "--version"], 30.0, None)
    except Exception:  # noqa: BLE001 - 版本查不到不影响投稿
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else None


# ---------------------------------------------------------------- 命令组装
def build_upload_argv(
    exe: str,
    cookie_path: str | os.PathLike[str],
    video_path: str | os.PathLike[str],
    meta: UploadMetadata,
    *,
    cover_path: str | os.PathLike[str] | None = None,
    submit: str = "web",
) -> list[str]:
    """组装投稿命令。

    `--submit` 仍然照传（值域是 `client|app|web`），但要清楚一件事：**在 0.2.4 里它不起作用**。
    上游 `uploader.rs` 的 `match submit` 两个分支都调 `submit_by_app`（web 分支被注释掉了，
    旁边留着一句「说不定会适配 web 呢」），而且 `biliup.exe` 里根本没有 `x/vu/web/add` 这个串
    —— 只找得到 `x/vu/app/add`。所以实际提交永远走 App 接口。
    早先这里写着「`--submit web` 是必须的，因为 cookie 登录拿不到 access_token」，**那两句都不对**：
    真正拦住投稿的不是提交接口，是 `login_by_cookies` 在开工前那次令牌校验（见 `bili_auth`）。

    `--copyright` 取自元数据（**默认 2＝转载**），`--source` 只在转载稿上出现 ——
    两者的判据都在 `bili_meta.effective_source`，这里不另写一遍。
    上游把整个 `Studio`（含 `copyright` / `source`）当 JSON body 发给 `x/vu/app/add`，
    所以这两个参数是**逐字段生效**的，不是 biliup 只在本地用一下。
    """
    argv: list[str] = [
        exe,
        "-u",
        str(cookie_path),
        "upload",
        str(video_path),
        "--submit",
        submit,
        "--title",
        meta.title,
        "--tid",
        meta.tid,
        "--tag",
        meta.tags,
        "--desc",
        meta.desc,
        "--copyright",
        normalize_copyright(meta.copyright),
    ]
    source = effective_source(meta.copyright, meta.source)
    if source:
        argv += ["--source", source]
    if cover_path and Path(cover_path).is_file():
        argv += ["--cover", str(cover_path)]
    return argv


# ---------------------------------------------------------------- 进度解析
# biliup 的输出是给人看的，没有结构化进度；这里只做「粗粒度、单调不回退」的估计。
# 宁可粒度粗，也不要编一个看起来精确却在骗人的百分比。
_PROGRESS_STAGES: tuple[tuple[str, float], ...] = (
    ("pre_upload", 0.20),
    ("Upload completed", 0.60),
    ("APP接口投稿成功", 0.95),
    ('"code": 0', 0.95),
)


def parse_progress(output_line: str, current: float) -> float:
    for needle, value in _PROGRESS_STAGES:
        if needle in output_line:
            return max(current, value)
    return current


# ---------------------------------------------------------------- 执行
def run_command(argv: Sequence[str], timeout: float, on_line: LineSink | None = None) -> CommandResult:
    """跑一个子进程，逐行读并回调，**并在 `timeout` 内一定返回**。

    读取放在后台线程、`wait()` 放在主线程，是为了让截止时间真的成立。早先是「主线程
    `for line in process.stdout` 读完再 `wait(timeout)`」—— 阻塞在管道上时那段 `wait`
    根本执行不到，于是一个**不打印任何输出就挂住**的投稿会让 `timeout` 形同虚设，
    记录永远停在「上传中」（实测：30 秒的 sleep 跑满全程、返回码 0、没有超时标记）。
    而「挂死」恰恰是这个超时唯一要防的东西。

    读数线程是 daemon 且有界 join：子进程若留下继承管道的孙进程，管道 EOF 不会到来，
    主线程也不能跟着一起等下去。
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # 合并：biliup 把进度写 stdout、错误写 stderr，顺序有意义
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_parts.append(line)
            if on_line is not None:
                try:
                    on_line(line.rstrip("\r\n"))
                except Exception:  # noqa: BLE001 - 回调出问题不该打断上传
                    pass

    reader = threading.Thread(target=pump, name="biliup-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=max(1.0, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            process.wait(timeout=30)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要把进程收掉
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
        stderr_parts.append(str(exc))
    reader.join(timeout=30)
    if timed_out:
        stderr_parts.append(f"\n{TIMEOUT_MARKER} after {timeout:.0f}s\n")
    code = process.returncode if process.returncode is not None else 1
    return CommandResult(returncode=code, stdout="".join(stdout_parts), stderr="".join(stderr_parts))


def upload(
    exe: str,
    cookie_path: str | os.PathLike[str],
    video_path: str | os.PathLike[str],
    meta: UploadMetadata,
    *,
    cover_path: str | os.PathLike[str] | None = None,
    timeout: float = DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    on_line: LineSink | None = None,
    runner: Runner | None = None,
) -> CommandResult:
    argv = build_upload_argv(exe, cookie_path, video_path, meta, cover_path=cover_path)
    if runner is not None:
        return runner(argv, timeout, on_line)
    return run_command(argv, timeout, on_line)


def extract_cover_frame(
    ffmpeg_path: str | os.PathLike[str],
    video_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    at_seconds: float = 1.0,
    runner: Runner | None = None,
) -> Path | None:
    """从视频抽一帧当封面。

    抽第 1 秒而不是第 0 秒：第 0 帧经常是黑场或片头卡，作为封面等于没有封面。
    返回 `None` 表示抽不出来（视频太短、文件损坏、没有 ffmpeg）——**调用方必须容忍它**，
    封面是可选项，不该让一次没有封面的投稿整体失败。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        str(ffmpeg_path),
        "-y",
        "-ss",
        str(at_seconds),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out),
    ]
    run = runner or run_command
    try:
        run(argv, 120.0, None)
    except Exception:  # noqa: BLE001 - 抽封面失败必须降级，不能中断投稿
        return None
    return out if out.is_file() and out.stat().st_size > 0 else None
