"""E-05 · 「已暂停多久」的后端行为与读模型契约。

暂停起点必须是**专用状态列**，不能拿 `updated_at` 顶替：后者是「最后一次改了什么」，
暂停之后任何一次字段写入都会把它推后，界面上的「已暂停 3 小时」就会悄悄变成「刚暂停」。

覆盖：写入 / 重复暂停不重置 / 重启清空 / 非暂停态不回显 / 历史任务按 `updated_at`
兜底但**明确标成近似** / API 响应带字段 / 补列迁移。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.contracts.config import AppSettings
from app.contracts.models import Job, JobItem, JobStatus, utc_now
from app.contracts.schemas import DownloadOptions
from app.download.job_manager import JobManager
from app.download.job_read_model import read_job
from app.infra.db import create_app_engine, init_db
from app.infra.events import EventBroker
from app.main import create_app

from fakes import FakeYtDlpService

JOB_ID = "job-paused-since"


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


def _seed(
    tmp_path: Path,
    *,
    status: str = JobStatus.running.value,
    paused_at=None,
    updated_at=None,
):
    engine = _engine(tmp_path)
    now = utc_now()
    with Session(engine) as session:
        session.add(
            Job(
                id=JOB_ID,
                url="https://youtu.be/paused",
                title="Long pause",
                status=status,
                options_json=DownloadOptions().model_dump_json(),
                total_items=1,
                download_dir=str(tmp_path / "downloads"),
                paused_at=paused_at,
                updated_at=updated_at or now,
            )
        )
        session.add(
            JobItem(
                id=f"{JOB_ID}-item",
                job_id=JOB_ID,
                source_url="https://youtu.be/paused",
                title="Long pause",
                index=1,
                status=status,
            )
        )
        session.commit()
    return engine


def _manager(tmp_path: Path, engine) -> JobManager:
    return JobManager(engine, _settings(tmp_path), FakeYtDlpService(), EventBroker())


async def _isolated_restart(manager: JobManager, *, item: bool = False) -> None:
    """跑 restart 但不让 worker 真的消费队列（否则状态又会被推到终态）。"""
    manager._queue = asyncio.Queue()

    async def noop_start() -> None:
        return None

    manager.start = noop_start  # type: ignore[method-assign]
    if item:
        await manager.restart_item(JOB_ID, f"{JOB_ID}-item")
    else:
        await manager.restart(JOB_ID)


# ---------------------------------------------------------------- 写入与清空


def test_pause_records_a_dedicated_start_time(tmp_path: Path) -> None:
    engine = _seed(tmp_path)
    asyncio.run(_manager(tmp_path, engine).pause(JOB_ID))

    with Session(engine) as session:
        job = session.get(Job, JOB_ID)
        assert job is not None
        assert job.status == JobStatus.paused.value
        assert job.paused_at is not None
        assert job.paused_at != job.created_at


def test_repeated_pause_does_not_reset_the_clock(tmp_path: Path) -> None:
    """用户反复点「暂停」不该把「已暂停 3 小时」清零。"""
    original = utc_now() - timedelta(hours=3)
    engine = _seed(tmp_path, status=JobStatus.paused.value, paused_at=original)
    manager = _manager(tmp_path, engine)

    asyncio.run(manager.pause(JOB_ID))
    asyncio.run(manager.pause(JOB_ID))

    with Session(engine) as session:
        # SQLite 存的是 naive UTC 墙上时间，比较时去掉时区。
        assert session.get(Job, JOB_ID).paused_at == original.replace(tzinfo=None)


def test_restart_clears_the_clock_and_exit_pause(tmp_path: Path) -> None:
    engine = _seed(tmp_path, status=JobStatus.paused.value, paused_at=utc_now() - timedelta(hours=5))
    manager = _manager(tmp_path, engine)
    manager._paused.add(JOB_ID)

    asyncio.run(_isolated_restart(manager))

    with Session(engine) as session:
        job = session.get(Job, JOB_ID)
        assert job.status == JobStatus.queued.value
        assert job.paused_at is None
    assert JOB_ID not in manager._paused


def test_item_restart_also_clears_the_clock(tmp_path: Path) -> None:
    engine = _seed(tmp_path, status=JobStatus.paused.value, paused_at=utc_now() - timedelta(hours=5))
    asyncio.run(_isolated_restart(_manager(tmp_path, engine), item=True))

    with Session(engine) as session:
        assert session.get(Job, JOB_ID).paused_at is None


# ---------------------------------------------------------------- 读模型


def test_read_model_returns_an_aware_utc_paused_at_for_a_paused_job(tmp_path: Path) -> None:
    started = utc_now() - timedelta(hours=3)
    engine = _seed(tmp_path, status=JobStatus.paused.value, paused_at=started)
    with Session(engine) as session:
        payload = read_job(session, JOB_ID)

    assert payload is not None
    assert payload.paused_at is not None
    assert payload.paused_at.tzinfo is not None
    assert payload.paused_since_approximate is False
    assert payload.model_dump(mode="json")["paused_at"].endswith("Z")


def test_read_model_hides_a_stale_clock_for_non_paused_jobs(tmp_path: Path) -> None:
    """任务已经跑完却残留 `paused_at` 时，界面不该再报「已暂停」。"""
    engine = _seed(
        tmp_path, status=JobStatus.succeeded.value, paused_at=utc_now() - timedelta(hours=9)
    )
    with Session(engine) as session:
        payload = read_job(session, JOB_ID)

    assert payload is not None
    assert payload.paused_at is None
    assert payload.paused_since_approximate is False


def test_legacy_paused_job_falls_back_to_updated_at_and_is_marked_approximate(tmp_path: Path) -> None:
    """补列之前的历史任务拿不到准确起点：用 `updated_at` 兜底，但必须标成「约」。"""
    fallback = utc_now() - timedelta(hours=7)
    engine = _seed(tmp_path, status=JobStatus.paused.value, paused_at=None, updated_at=fallback)
    with Session(engine) as session:
        payload = read_job(session, JOB_ID)

    assert payload is not None
    assert payload.paused_at is not None
    assert payload.paused_since_approximate is True
    assert payload.paused_at.replace(tzinfo=None) == fallback.replace(tzinfo=None)


# ---------------------------------------------------------------- API 与迁移


def test_pause_and_restart_responses_carry_the_new_fields(tmp_path: Path) -> None:
    # `failed` 不会被启动时的孤儿恢复认领，避免 worker 与断言抢状态。
    _seed(tmp_path, status=JobStatus.failed.value)
    with TestClient(create_app(settings=_settings(tmp_path), ytdlp_service=FakeYtDlpService())) as client:
        paused = client.post(f"/api/jobs/{JOB_ID}/pause")
        assert paused.status_code == 200, paused.text
        assert paused.json()["paused_at"] is not None
        assert paused.json()["paused_since_approximate"] is False

        restarted = client.post(f"/api/jobs/{JOB_ID}/restart")
        assert restarted.status_code == 200, restarted.text
        assert restarted.json()["paused_at"] is None


def test_init_db_adds_the_paused_at_column_to_an_existing_table(tmp_path: Path) -> None:
    """老库没有这一列：`_ensure_columns` 要能补上，不能只对新建的库有效。"""
    engine = _engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE job DROP COLUMN paused_at"))
        cols = {row[1] for row in connection.execute(text("PRAGMA table_info(job)"))}
        assert "paused_at" not in cols

    init_db(engine)

    with engine.begin() as connection:
        cols = {row[1] for row in connection.execute(text("PRAGMA table_info(job)"))}
    assert "paused_at" in cols
