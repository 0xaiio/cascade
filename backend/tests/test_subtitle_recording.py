"""字幕下载结论的落库：**判据是磁盘上有没有那个文件**，不是计划说了什么。

yt-dlp 对「请求的语言不存在」是静默跳过（不报错），所以「计划说有字幕」与
「真的写出了文件」是两件事。上传环节读的是 `JobItem.subtitle_path`，存一个不存在的
路径等于让一次上传在最后一刻才发现没有字幕 —— 这里钉的就是这一条。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from app.config import AppSettings
from app.db import create_app_engine, init_db
from app.events import EventBroker
from app.job_manager import JobManager, new_id
from app.models import Job, JobItem, JobStatus
from app.schemas import DownloadOptions
from app.subtitle_plan import SubtitleOrigin, SubtitlePlan

from fakes import FakeYtDlpService

VIDEO = "作品 [abc123].mp4"


class SubtitleYtDlpService(FakeYtDlpService):
    """按 `plan` 给出预检结论，并在下载时把 `files` 里登记的文件造出来。

    **文件必须真的造出来**：`_record_subtitle_result` 的判据是 `Path.is_file()`，
    替身不写磁盘的话，用例会以为自己在验证「记到了字幕」，实际验证的是「没有字幕」。
    """

    def __init__(self, plan: SubtitlePlan, files: list[str]) -> None:
        super().__init__()
        self.plan = plan
        self.files = files
        self.requested_languages: list[str] = []

    def prepare_download(self, url, options, cookies_path=None):
        return PlanOnlyPreparation(plan=self.plan)

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.requested_languages = list(options.subtitle_languages)
        directory = Path(download_dir or ".")
        for name in self.files:
            (directory / name).write_text("x", encoding="utf-8")
        progress_hook({"status": "finished", "filename": str(directory / VIDEO)})
        (directory / VIDEO).write_text("x", encoding="utf-8")


class PlanOnlyPreparation:
    """只用得着 `subtitle` 一个字段的预检结论。`is_selectable` 为真表示「可以下」。 """

    def __init__(self, plan: SubtitlePlan) -> None:
        self.is_selectable = True
        self.width = None
        self.height = None
        self.actual_format = None
        self.subtitle = plan


def _make_manager(tmp_path: Path, plan: SubtitlePlan, files: list[str]):
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )
    settings.ensure_directories()
    engine = create_app_engine(settings)
    init_db(engine)
    service = SubtitleYtDlpService(plan, files)
    return JobManager(engine, settings, service, EventBroker()), engine, service


def _run(tmp_path: Path, plan: SubtitlePlan, files: list[str]):
    manager, engine, service = _make_manager(tmp_path, plan, files)
    options = DownloadOptions(resolution="720p", mode="video_subtitles")
    job = Job(
        id=new_id(),
        url="https://youtu.be/abc123",
        title="作品",
        options_json=options.model_dump_json(),
        total_items=1,
        download_dir=str(tmp_path),
    )
    item = JobItem(
        id=new_id(),
        job_id=job.id,
        source_url="https://youtu.be/abc123",
        title="作品",
        index=1,
        status=JobStatus.queued.value,
    )
    with Session(engine) as session:
        session.add(job)
        session.add(item)
        session.commit()
        manager._run_item(session, job, item, options, tmp_path)
        item_id = item.id
    with Session(engine) as session:
        return session.get(JobItem, item_id), service


def test_downloaded_subtitle_is_recorded_with_language_and_origin(tmp_path: Path) -> None:
    plan = SubtitlePlan(language="en", code="en", origin=SubtitleOrigin.human, reason="")
    stored, service = _run(tmp_path, plan, ["作品 [abc123].en.vtt"])

    assert stored.status == JobStatus.succeeded.value
    assert stored.subtitle_language == "en"
    assert stored.subtitle_source == "human"
    assert stored.subtitle_path is not None
    assert Path(stored.subtitle_path).name == "作品 [abc123].en.vtt"


def test_auto_mode_requests_only_the_planned_track(tmp_path: Path) -> None:
    """自动模式把 `subtitle_languages` 收敛成一条 —— 多条会把同一语言的好几个变体都写下来。"""
    plan = SubtitlePlan(language="zh", code="zh-Hans", origin=SubtitleOrigin.auto, reason="")
    _stored, service = _run(tmp_path, plan, ["作品 [abc123].zh-Hans.vtt"])
    assert service.requested_languages == ["zh-Hans"]


def test_plan_without_a_track_still_asks_for_that_family(tmp_path: Path) -> None:
    """计划里没有可用轨时请求**族名本身**，而不是空列表 —— 空列表会被 yt-dlp 当没给，
    转而按它自己的默认挑英文，把「跳过」悄悄变成「下英文」。"""
    plan = SubtitlePlan(language="zh", code=None, origin=SubtitleOrigin.none, reason="")
    _stored, service = _run(tmp_path, plan, [])
    assert service.requested_languages == ["zh"]


def test_missing_file_downgrades_the_plan_to_none(tmp_path: Path) -> None:
    """计划说有人工字幕，但磁盘上什么都没有 → 结论是 `none`，不能照抄计划。"""
    plan = SubtitlePlan(language="en", code="en", origin=SubtitleOrigin.human, reason="")
    stored, _service = _run(tmp_path, plan, [])

    assert stored.subtitle_language is None
    assert stored.subtitle_source == SubtitleOrigin.none.value
    assert stored.subtitle_path is None


def test_an_unsupported_language_file_is_not_recorded(tmp_path: Path) -> None:
    """只处理中英字幕：日语文件躺在旁边也不算「有字幕」。"""
    plan = SubtitlePlan(language="en", code="en", origin=SubtitleOrigin.human, reason="")
    stored, _service = _run(tmp_path, plan, ["作品 [abc123].ja.vtt"])

    assert stored.subtitle_source == SubtitleOrigin.none.value
    assert stored.subtitle_path is None


def test_a_rerun_clears_the_previous_conclusion(tmp_path: Path) -> None:
    """重跑一次可能换到另一条轨（或变成没有），留着上一轮的值会让上传拿到一个
    磁盘上已经不存在的路径。"""
    manager, engine, service = _make_manager(
        tmp_path, SubtitlePlan(language="en", code="en", origin=SubtitleOrigin.human, reason=""), []
    )
    options = DownloadOptions(resolution="720p")
    job = Job(
        id=new_id(),
        url="https://youtu.be/abc123",
        title="作品",
        options_json=options.model_dump_json(),
        total_items=1,
        download_dir=str(tmp_path),
    )
    item = JobItem(
        id=new_id(),
        job_id=job.id,
        source_url="https://youtu.be/abc123",
        title="作品",
        index=1,
        status=JobStatus.queued.value,
        # 上一轮留下的结论与路径。
        subtitle_language="zh",
        subtitle_source="human",
        subtitle_path=str(tmp_path / "stale.zh.vtt"),
    )
    with Session(engine) as session:
        session.add(job)
        session.add(item)
        session.commit()
        manager._run_item(session, job, item, options, tmp_path)
        item_id = item.id
    with Session(engine) as session:
        stored = session.get(JobItem, item_id)
    assert stored.subtitle_path is None
    assert stored.subtitle_source == SubtitleOrigin.none.value
