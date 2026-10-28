from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from .fallback_policy import build_resolution_fallback
from ..contracts.models import Job, JobItem, utc_now
from .output_paths import discover_existing_output_path, existing_subtitle_file, resolve_existing_output_path
from ..contracts.schemas import DownloadOptions, JobItemRead, JobRead, ResolutionFallback


def read_job(session: Session, job_id: str) -> JobRead | None:
    job = session.get(Job, job_id)
    if not job:
        return None
    items = session.exec(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)).all()
    return JobRead(
        id=job.id,
        url=job.url,
        title=job.title,
        status=job.status,
        progress=job.progress,
        speed=job.speed,
        eta=job.eta,
        total_items=job.total_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        current_item_title=job.current_item_title,
        error=_job_error(job, items),
        download_dir=job.download_dir,
        actual_resolution=_actual_resolution(items),
        actual_format=_actual_format(items),
        resolution_fallback=_job_resolution_fallback(items),
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        elapsed_seconds=elapsed_seconds(job.started_at, job.finished_at),
        notify_on_complete=_notify_on_complete(job),
        items=[_read_job_item(item, job.download_dir) for item in items],
    )


def _notify_on_complete(job: Job) -> bool:
    """创建这个任务时勾没勾「完成后通知」。

    它只是 `DownloadOptions` 的一个字段，只落在 `options_json` 里。读模型不把它抬出来，
    前端就只剩「本次会话里提交的任务」这一份记忆 —— 刷新一次页面，同一个任务跑到终态时
    就不再提醒了（SSE 每条事件只是重拉 `/api/jobs`，拿到的就是这份读模型）。

    解析失败一律退回 `False`：那是「没能确认用户要提醒」，于是**不提醒**。反过来猜成
    `True` 会在用户没要求的时候响，是更坏的那种错。
    """
    try:
        return DownloadOptions.model_validate_json(job.options_json).notify_on_complete
    except ValueError:
        return False


def _read_job_item(item: JobItem, job_download_dir: str | None) -> JobItemRead:
    base_dir = Path(job_download_dir) if job_download_dir else None
    output_path = _project_output_path(item, job_download_dir)
    return JobItemRead(
        id=item.id,
        job_id=item.job_id,
        source_url=item.source_url,
        title=item.title,
        index=item.index,
        status=item.status,
        progress=item.progress,
        downloaded_bytes=item.downloaded_bytes,
        total_bytes=item.total_bytes,
        duration=item.duration,
        speed=item.speed,
        eta=item.eta,
        output_path=output_path,
        actual_width=item.actual_width,
        actual_height=item.actual_height,
        actual_format=item.actual_format,
        requested_resolution=item.requested_resolution,
        fallback_resolution=item.fallback_resolution,
        fallback_reason=item.fallback_reason,
        resolution_fallback=build_resolution_fallback(
            item.requested_resolution,
            item.fallback_resolution,
            item.status,
            item.fallback_reason,
        ),
        subtitle_language=item.subtitle_language,
        subtitle_source=item.subtitle_source,
        subtitle_path=_project_subtitle_path(item, output_path, base_dir),
        error=item.error,
        created_at=item.created_at,
        updated_at=item.updated_at,
        started_at=item.started_at,
        finished_at=item.finished_at,
        elapsed_seconds=elapsed_seconds(item.started_at, item.finished_at),
    )


def _project_output_path(item: JobItem, job_download_dir: str | None) -> str | None:
    base_dir = Path(job_download_dir) if job_download_dir else None
    if item.output_path:
        resolved = resolve_existing_output_path(Path(item.output_path), base_dir)
        return str(resolved or item.output_path)
    discovered = discover_existing_output_path(item.source_url, base_dir)
    return str(discovered) if discovered else None


def _project_subtitle_path(item: JobItem, output_path: str | None, base_dir: Path | None) -> str | None:
    """条目已下载到磁盘的字幕文件；没有就是 `None`。

    **前端拿它决定「打开字幕」按钮能不能点**，所以只报「真的存在」的那种：
    把一条已经不存在的旧记录回显出去，界面上就是一个点下去必然 409 的按钮。

    视频路径复用上面刚算好的 `output_path`，**不再自己发现一遍** —— 这份读模型每次轮询
    都会为每个条目跑一次，多扫一遍目录是白花的钱。定位规则本身仍在
    `output_paths.existing_subtitle_file`（唯一计算处）。
    """
    video = Path(output_path) if output_path else None
    path = existing_subtitle_file(item.subtitle_path, video, base_dir)
    return str(path) if path else None


def _actual_resolution(items: list[JobItem]) -> str | None:
    resolutions = {
        (item.actual_width, item.actual_height)
        for item in items
        if item.actual_width is not None and item.actual_height is not None
    }
    if not resolutions:
        return None
    if len(resolutions) > 1:
        return "混合分辨率"
    width, height = next(iter(resolutions))
    return f"{width}x{height}"


def _actual_format(items: list[JobItem]) -> str | None:
    formats = {item.actual_format for item in items if item.actual_format}
    if not formats:
        return None
    if len(formats) > 1:
        return "混合格式"
    return next(iter(formats))


def _job_resolution_fallback(items: list[JobItem]) -> ResolutionFallback | None:
    if len(items) != 1:
        return None
    item = items[0]
    return build_resolution_fallback(
        item.requested_resolution,
        item.fallback_resolution,
        item.status,
        item.fallback_reason,
    )


def _job_error(job: Job, items: list[JobItem]) -> str | None:
    if len(items) == 1 and items[0].status == "failed" and items[0].error:
        return items[0].error
    return job.error


def elapsed_seconds(started_at: datetime | None, finished_at: datetime | None) -> int:
    """已用时（秒）。**公开名**：`bili_read_model` 也用它算「这次上传跑了多久」——
    两处各写一遍的话，「未结束时取当前时间」这条规则迟早会在其中一处漏掉。"""
    if not started_at:
        return 0
    start = _as_aware_utc(started_at)
    finish = _as_aware_utc(finished_at) if finished_at else utc_now()
    return max(0, int((finish - start).total_seconds()))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
