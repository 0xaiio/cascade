"""API 层的共享辅助：请求校验包装、读模型包装、设置读写、cookies 导入错误映射、元数据解析策略。

这些函数原先都是 `main.py` 里的模块级私有函数（`_require_job` 之类），但它们**不是路由**
—— 它们是「把业务对象翻译成 HTTP 响应」的胶水。分开之后，路由模块只剩路径、入参与状态码。

不负责：产物路径解析与本地打开（在 `job_artifacts.py`），业务规则（在各自领域模块）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, select

from ..contracts.config import AppSettings
from ..download.job_read_model import read_job
from ..contracts.models import Job, JobItem, Setting
from ..common.paths import safe_path_name
from ..host.proxy import redact_proxy_credentials
from ..contracts.schemas import (
    COMPLETION_NOTIFICATION_MODES,
    DEFAULT_COMPLETION_NOTIFICATION,
    AnalyzeResponse,
    JobRead,
    SettingsRead,
    VideoEntry,
)
from ..download.error_advice import cookie_required_message
from ..download.ytdlp_service import YtDlpService


def read_job_or_404(session: Session, job_id: str) -> JobRead:
    payload = read_job(session, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return payload


def require_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def require_job_item(session: Session, job_id: str, item_id: str) -> JobItem:
    item = session.get(JobItem, item_id)
    if not item or item.job_id != job_id:
        raise HTTPException(status_code=404, detail="Job item not found.")
    return item


def single_job_item(session: Session, job: Job) -> JobItem:
    """单视频任务的唯一条目。合集任务没有「唯一产物」，只能让用户去点具体条目。"""
    items = session.exec(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.index)).all()
    if len(items) != 1:
        raise HTTPException(status_code=409, detail="合集任务请打开具体视频。")
    return items[0]


def selected_entries(url: str, analysis: AnalyzeResponse, playlist_items: list[int] | None) -> list[VideoEntry]:
    """把解析结果收敛成「要建几条 JobItem」。

    单视频不走 `entries`（yt-dlp 对单视频不给 entries），这里合成一条；
    playlist 传了 `playlist_items` 就按 index 过滤，传空列表 = 全选。
    """
    if analysis.is_playlist:
        selected = set(playlist_items or [])
        entries = analysis.entries
        if selected:
            entries = [entry for entry in entries if entry.index in selected]
        return entries
    return [
        VideoEntry(
            index=1,
            id=None,
            title=analysis.title,
            url=analysis.url or url,
            duration=analysis.duration,
            thumbnail=analysis.thumbnail,
        )
    ]


def job_download_dir(root_dir: Path, analysis: AnalyzeResponse, job_id: str) -> Path:
    """playlist 建同名子目录；单视频直接落根目录。目录名走 `safe_path_name`。"""
    if not analysis.is_playlist:
        return root_dir
    folder = safe_path_name(analysis.title, fallback=f"playlist-{job_id[:8]}")
    return root_dir / folder


def extract_metadata_with_cookies(
    service: YtDlpService,
    settings: AppSettings,
    url: str,
    cookies_enabled: bool,
) -> AnalyzeResponse:
    """解析元数据；cookies **只来自用户自己给的文件**。

    这里以前有一条「失败就自动去读本机浏览器、再重试一次」的捷径，已经移除：

    - 本机 Edge 的 cookie 是 v20 app-bound 加密，离线程序**必然解不开**；
    - 那条路的失败分支会 `taskkill /IM msedge.exe /F /T`，关掉用户所有 Edge 窗口。

    所以现在只做一件事：失败时把「下一步怎么做」写进消息，而不是替用户去碰浏览器
    （见 [ai/bug-fix/038](../ai/bug-fix/038-browser-cookie-import-is-gone.md)）。
    """
    cookies_path = settings.cookies_path if cookies_enabled and settings.cookies_path.exists() else None
    try:
        return service.extract_metadata(url, cookies_path=cookies_path)
    except Exception as exc:
        if not is_cookie_required_error(exc):
            raise
        raise RuntimeError(cookie_required_message(exc)) from exc


def is_cookie_required_error(exc: Exception) -> bool:
    return YtDlpService.is_cookie_required_error(exc)


def _clamp_int(raw: str, low: int, high: int, fallback: int) -> int:
    """把一个字符串读成区间内的整数。**读不出来就用兜底值**，不抛也不越界。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, value))


def set_setting(session: Session, key: str, value: str) -> None:
    setting = session.get(Setting, key) or Setting(key=key, value=value)
    setting.value = value
    session.add(setting)
    session.commit()


def apply_stored_settings(session: Session, settings: AppSettings, service: YtDlpService) -> None:
    """把 `Setting` 表里的持久化覆盖灌回 `AppSettings`（并把需要同步的字段推给 service）。

    为什么 `service` 也要跟着改：`default_subtitle_languages` / `aria2c_connections` / `proxy`
    是「请求没带就用设置」的兜底值，只改 settings 不推给服务就等于设置不生效。
    """
    stored = {setting.key: setting.value for setting in session.exec(select(Setting)).all()}
    if stored.get("download_dir"):
        settings.download_dir = Path(stored["download_dir"])
        service.download_dir = settings.download_dir
    if stored.get("default_concurrency"):
        settings.default_concurrency = int(stored["default_concurrency"])
    if stored.get("default_resolution"):
        settings.default_resolution = stored["default_resolution"]
    if "default_speed_limit_kbps" in stored:
        settings.default_speed_limit_kbps = int(stored["default_speed_limit_kbps"]) if stored["default_speed_limit_kbps"] else None
    if stored.get("default_retries"):
        settings.default_retries = int(stored["default_retries"])
    if stored.get("default_subtitle_languages") is not None:
        settings.default_subtitle_languages = [
            lang for lang in stored["default_subtitle_languages"].split(",") if lang
        ]
        # 同步给服务：它是「请求未指定语言」时的兜底集合，必须跟着设置走。
        service.default_subtitle_languages = settings.default_subtitle_languages
    if stored.get("aria2c_connections"):
        settings.aria2c_connections = int(stored["aria2c_connections"])
        service.aria2c_connections = settings.aria2c_connections
    if "proxy" in stored:
        # 空串 = 未配置（自动），不是「强制直连」——见 app/host/proxy.py 的 DIRECT_PROXY_VALUES。
        settings.proxy = stored["proxy"].strip() or None
        service.proxy = settings.proxy
    stored_notification = stored.get("completion_notification")
    if stored_notification in COMPLETION_NOTIFICATION_MODES:
        settings.completion_notification = stored_notification
    # B站 自动重试的三个旋钮。**值从库里读回时要做范围校验**：这是一张用户可写的表，
    # 写坏了（手改、或者老版本写过更大的值）不该让调度器按一个荒谬的间隔跑。
    if "bili_auto_retry_enabled" in stored:
        settings.bili_auto_retry_enabled = stored["bili_auto_retry_enabled"].strip().lower() not in (
            "0",
            "false",
            "off",
            "no",
        )
    if stored.get("bili_auto_retry_interval_seconds"):
        settings.bili_auto_retry_interval_seconds = _clamp_int(
            stored["bili_auto_retry_interval_seconds"], 10, 300, settings.bili_auto_retry_interval_seconds
        )
    if stored.get("bili_auto_retry_max_attempts"):
        settings.bili_auto_retry_max_attempts = _clamp_int(
            stored["bili_auto_retry_max_attempts"], 1, 10, settings.bili_auto_retry_max_attempts
        )


def settings_response(session: Session, settings: AppSettings, service: YtDlpService) -> SettingsRead:
    apply_stored_settings(session, settings, service)
    resolution = service.proxy_resolution()
    return SettingsRead(
        download_dir=str(settings.download_dir),
        default_concurrency=settings.default_concurrency,
        default_subtitle_languages=settings.default_subtitle_languages,
        default_resolution=settings.default_resolution,
        default_speed_limit_kbps=settings.default_speed_limit_kbps,
        default_retries=settings.default_retries,
        aria2c_connections=settings.aria2c_connections,
        proxy=settings.proxy,
        proxy_source=resolution.source,
        proxy_effective=redact_proxy_credentials(resolution.url),
        cookies_enabled=settings.cookies_path.exists(),
        ffmpeg=service.get_ffmpeg_status(),
        completion_notification=_completion_notification(settings.completion_notification),
        bili_auto_retry_enabled=settings.bili_auto_retry_enabled,
        bili_auto_retry_interval_seconds=settings.bili_auto_retry_interval_seconds,
        bili_auto_retry_max_attempts=settings.bili_auto_retry_max_attempts,
    )


def _completion_notification(value: str) -> str:
    """把提醒方式收敛到合法取值。

    兜底为什么放在这里而不是入口：`completion_notification` 还能从环境变量（`YTDL_`
    前缀）进来，那一路不经过 pydantic 的 `Literal` 校验。写错一个字母时，宁可退回默认
    也不要把非法值发给前端 —— 前端拿它去比字符串，不匹配就是「选了却不响」。
    """
    return value if value in COMPLETION_NOTIFICATION_MODES else DEFAULT_COMPLETION_NOTIFICATION


def select_directory_with_tkinter(initial_dir: Path) -> Path | None:
    """默认的目录选择器。tkinter 在无 GUI 的 Python 里不可用，因此失败必须变成可读的 400。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("Folder dialog is unavailable in this environment.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=str(initial_dir), title="选择下载目录")
    finally:
        root.destroy()
    return Path(selected) if selected else None
