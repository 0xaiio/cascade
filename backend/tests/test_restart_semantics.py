"""E-03 · 「重启」语义的**行为取证**。

用户手册与按钮提示现在逐条承诺了重启会做什么，这些承诺必须有代码侧回归钉住 ——
本项目的教训是「文档里每条数值/行为断言都要回源码核一遍」（见
[E-03](../ai/feature/E-03-restart-semantics.md)）。

覆盖四组：

1. 状态与字段：哪些被清空、哪些被保留（清晰度选项、`.part`、已下载文件、`JobEvent` 历史）；
2. 生命周期：`paused` → 重启 → `queued`，退出暂停集合；
3. 换清晰度：只有传 `resolution` 才改选项，且沿用 026 的入口归一化；
4. 条目级：只重置那一条、其它条目不动、事件类型不同。

`restart()` 会 `await self.start()` 并往内存队列里塞 item id。除最后一条端到端用例外，
这里把 `start()` 换成 no-op、队列替换成无人消费的 `asyncio.Queue`，只看**重启本身改了什么**；
让 worker 真的跑起来会把状态再推到 `running`/`succeeded`，反而量不到重置结果。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.contracts.config import AppSettings
from app.contracts.models import Job, JobEvent, JobItem, JobStatus, utc_now
from app.contracts.schemas import DownloadOptions
from app.download.job_manager import JobManager
from app.infra.db import create_app_engine, init_db
from app.infra.events import EventBroker
from app.main import create_app

from fakes import BlockingYtDlpService, FakeYtDlpService

JOB_ID = "job-restart"
ITEM_ID = f"{JOB_ID}-item"


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def _engine(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.ensure_directories()
    engine = create_app_engine(settings)
    init_db(engine)
    return engine


def _seed(tmp_path: Path, *, status: str = JobStatus.failed.value, resolution: str = "1080p", items: int = 1):
    engine = _engine(tmp_path)
    now = utc_now()
    with Session(engine) as session:
        session.add(
            Job(
                id=JOB_ID,
                url=f"https://youtu.be/{JOB_ID}",
                title="Restart me",
                status=status,
                options_json=DownloadOptions(resolution=resolution).model_dump_json(),
                progress=66.0,
                speed=2048.0,
                eta=30,
                total_items=items,
                completed_items=1 if items > 1 else 0,
                failed_items=1,
                current_item_title="half done",
                error="媒体流 403",
                download_dir=str(tmp_path / "downloads"),
                started_at=now,
                finished_at=now,
            )
        )
        for index in range(1, items + 1):
            session.add(
                JobItem(
                    id=ITEM_ID if index == 1 else f"{ITEM_ID}-{index}",
                    job_id=JOB_ID,
                    source_url=f"https://youtu.be/{JOB_ID}-{index}",
                    title=f"Item {index}",
                    index=index,
                    status=JobStatus.succeeded.value if index > 1 else status,
                    progress=66.0,
                    downloaded_bytes=5_000_000,
                    total_bytes=10_000_000,
                    speed=2048.0,
                    eta=30,
                    output_path=str(tmp_path / "downloads" / f"video-{index}.mp4"),
                    actual_width=1920,
                    actual_height=1080,
                    actual_format="mp4",
                    options_json=DownloadOptions(resolution="720p").model_dump_json(),
                    requested_resolution="1080p",
                    fallback_resolution="720p",
                    fallback_reason="requested_resolution_unselectable",
                    error="媒体流 403",
                    started_at=now,
                    finished_at=now,
                )
            )
        session.commit()
    return engine


def _manager(tmp_path: Path, engine) -> JobManager:
    return JobManager(engine, _settings(tmp_path), FakeYtDlpService(), EventBroker())


async def _restart(manager: JobManager, job_id: str = JOB_ID, resolution: str | None = None) -> None:
    """隔离 worker 地跑一次 `restart`：`start()` 不启动 worker，队列没人消费。"""
    manager._queue = asyncio.Queue()

    async def noop_start() -> None:
        return None

    manager.start = noop_start  # type: ignore[method-assign]
    await manager.restart(job_id, resolution=resolution)


async def _restart_item(manager: JobManager, item_id: str = ITEM_ID, resolution: str | None = None) -> bool:
    manager._queue = asyncio.Queue()

    async def noop_start() -> None:
        return None

    manager.start = noop_start  # type: ignore[method-assign]
    return await manager.restart_item(JOB_ID, item_id, resolution=resolution)


def _events(engine, event_type: str) -> list[JobEvent]:
    with Session(engine) as session:
        return list(session.exec(select(JobEvent).where(JobEvent.event_type == event_type)).all())


# ---------------------------------------------------------------- 状态与字段


def test_restart_resets_every_progress_field_but_keeps_the_options(tmp_path: Path) -> None:
    engine = _seed(tmp_path)
    asyncio.run(_restart(_manager(tmp_path, engine)))

    with Session(engine) as session:
        job = session.get(Job, JOB_ID)
        item = session.get(JobItem, ITEM_ID)
        assert job is not None and item is not None
        assert job.status == JobStatus.queued.value
        assert (job.progress, job.speed, job.eta) == (0.0, None, None)
        assert (job.completed_items, job.failed_items) == (0, 0)
        assert job.current_item_title is None and job.error is None
        assert job.started_at is None and job.finished_at is None
        assert DownloadOptions.model_validate_json(job.options_json).resolution == "1080p"

        assert item.status == JobStatus.queued.value
        assert (item.progress, item.downloaded_bytes, item.total_bytes) == (0.0, None, None)
        assert (item.speed, item.eta, item.output_path) == (None, None, None)
        assert (item.actual_width, item.actual_height, item.actual_format) == (None, None, None)
        assert item.options_json is None  # 条目级覆盖被清掉，回落到 job 级选项
        assert (item.requested_resolution, item.fallback_resolution, item.fallback_reason) == (None, None, None)
        assert item.error is None and item.started_at is None and item.finished_at is None


def test_restart_does_not_delete_partial_files_or_finished_files(tmp_path: Path) -> None:
    """重启只改数据库；`.part` 由下一次 yt-dlp 断点续传，不是从零重下。"""
    engine = _seed(tmp_path)
    download_dir = tmp_path / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    partial = download_dir / "Restart me [job-restart].mp4.part"
    finished = download_dir / "already-done.mp4"
    partial.write_bytes(b"half")
    finished.write_bytes(b"done")

    asyncio.run(_restart(_manager(tmp_path, engine)))

    assert partial.read_bytes() == b"half"
    assert finished.read_bytes() == b"done"


def test_restart_appends_one_event_and_keeps_the_history(tmp_path: Path) -> None:
    engine = _seed(tmp_path)
    with Session(engine) as session:
        session.add(JobEvent(job_id=JOB_ID, event_type="job_failed", payload_json="{}"))
        session.commit()

    asyncio.run(_restart(_manager(tmp_path, engine)))

    assert len(_events(engine, "job_restarted")) == 1
    assert len(_events(engine, "job_failed")) == 1


# ---------------------------------------------------------------- 生命周期


def test_paused_job_is_requeued_and_leaves_the_pause_set(tmp_path: Path) -> None:
    engine = _seed(tmp_path, status=JobStatus.paused.value)
    manager = _manager(tmp_path, engine)
    manager._paused.add(JOB_ID)
    assert JOB_ID in manager._paused

    asyncio.run(_restart(manager))

    assert JOB_ID not in manager._paused
    with Session(engine) as session:
        assert session.get(Job, JOB_ID).status == JobStatus.queued.value
        assert session.get(JobItem, ITEM_ID).status == JobStatus.queued.value


def test_restarting_an_unknown_job_is_a_no_op(tmp_path: Path) -> None:
    engine = _seed(tmp_path)
    asyncio.run(_restart(_manager(tmp_path, engine), job_id="does-not-exist"))

    assert _events(engine, "job_restarted") == []
    with Session(engine) as session:
        assert session.get(Job, JOB_ID).status == JobStatus.failed.value


# ---------------------------------------------------------------- 清晰度


def test_only_an_explicit_resolution_changes_the_job_options(tmp_path: Path) -> None:
    engine = _seed(tmp_path, resolution="1080p")
    manager = _manager(tmp_path, engine)
    asyncio.run(_restart(manager))
    with Session(engine) as session:
        assert DownloadOptions.model_validate_json(session.get(Job, JOB_ID).options_json).resolution == "1080p"

    asyncio.run(_restart(manager, resolution="720p"))
    with Session(engine) as session:
        options = DownloadOptions.model_validate_json(session.get(Job, JOB_ID).options_json)
        assert options.resolution == "720p"
        assert options.format_id is None  # 换清晰度要丢掉原来的具体格式


@pytest.mark.parametrize("bad", ["1080", "hd", "1440p60"])
def test_restart_rejects_ambiguous_resolution_at_the_entry(tmp_path: Path, bad: str) -> None:
    """026 的第三个入口：重启路由上的 `resolution` 同样要归一化/拒绝。"""
    _seed(tmp_path)
    with TestClient(create_app(settings=_settings(tmp_path), ytdlp_service=FakeYtDlpService())) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/restart", json={"resolution": bad})
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------- 条目级


def test_item_restart_resets_only_that_item_and_uses_its_own_event_type(tmp_path: Path) -> None:
    engine = _seed(tmp_path, items=2)
    manager = _manager(tmp_path, engine)
    other_id = f"{ITEM_ID}-2"
    with Session(engine) as session:
        before_other = session.get(JobItem, other_id)
        assert before_other is not None
        assert before_other.status == JobStatus.succeeded.value
        assert before_other.output_path is not None

    assert asyncio.run(_restart_item(manager)) is True

    with Session(engine) as session:
        restarted = session.get(JobItem, ITEM_ID)
        other = session.get(JobItem, other_id)
        job = session.get(Job, JOB_ID)
        assert restarted is not None and other is not None and job is not None
        assert restarted.status == JobStatus.queued.value
        assert restarted.output_path is None and restarted.total_bytes is None
        assert other.status == JobStatus.succeeded.value  # 别的条目没被碰
        assert other.output_path is not None
        assert job.status == JobStatus.queued.value
        assert job.completed_items == 1  # `_refresh_job_counts` 按仍在的条目重算
    assert len(_events(engine, "item_restarted")) == 1
    assert _events(engine, "job_restarted") == []


def test_item_restart_returns_false_for_an_item_of_another_job(tmp_path: Path) -> None:
    engine = _seed(tmp_path)
    manager = _manager(tmp_path, engine)
    with Session(engine) as session:
        session.add(JobItem(id="stranger", job_id="another-job", source_url="https://youtu.be/x", title="X", index=1))
        session.commit()

    assert asyncio.run(_restart_item(manager, item_id="stranger")) is False
    assert _events(engine, "item_restarted") == []


# ---------------------------------------------------------------- 会重跑预检


class CountingBlockingService(BlockingYtDlpService):
    def __init__(self) -> None:
        super().__init__()
        self.preparations: list[str] = []

    def prepare_download(self, url, options, cookies_path=None):
        self.preparations.append(url)
        return SimpleNamespace(
            is_selectable=True,
            width=1280,
            height=720,
            actual_format="mp4",
            filesize=None,
            duration=None,
            subtitle=None,
        )


def test_restart_really_reruns_the_probe_before_downloading(tmp_path: Path) -> None:
    """端到端：重启后 worker 会再走一次 `prepare_download`，不是只把状态改回去。"""
    _seed(tmp_path)
    service = CountingBlockingService()
    with TestClient(create_app(settings=_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/restart")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == JobStatus.queued.value
        # worker 被队列唤醒后会先预检再下载；Blocking 会在 download 里等 release。
        assert service.started.get(timeout=5)
        service.release.set()

    assert service.preparations, "重启后预检没有重跑"
