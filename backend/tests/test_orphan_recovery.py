"""启动时对「上一次进程退出留下的孤儿条目」的收敛规则。

分两层测：
- 直接调 `recover_orphaned_items`，逐个规则验（快，且能钉住边界）；
- 走 `TestClient` 的 lifespan，验**真的会动起来** —— 只验规则不验接线，
  就会出现「函数写对了但没人调用」（这正是本缺陷的形态）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import AppSettings
from app.db import create_app_engine, init_db
from app.main import create_app
from app.models import Job, JobEvent, JobItem
from app.orphan_recovery import (
    CANCELLED_ITEM_ERROR,
    DANGLING_ITEM_ERROR,
    FAIL_POLICY,
    INTERRUPTED_WHILE_QUEUED_ERROR,
    INTERRUPTED_WHILE_RUNNING_ERROR,
    RESUME_POLICY,
    normalize_policy,
    recover_orphaned_items,
)
from app.schemas import DownloadOptions

from fakes import FakeYtDlpService


def make_settings(tmp_path: Path, **overrides) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
        **overrides,
    )


def open_session(tmp_path: Path, **overrides) -> Session:
    engine = create_app_engine(make_settings(tmp_path, **overrides))
    init_db(engine)
    return Session(engine)


def seed(
    session: Session,
    job_id: str,
    *,
    job_status: str,
    item_statuses: list[str],
    created_at: datetime | None = None,
    download_dir: Path | None = None,
) -> None:
    session.add(
        Job(
            id=job_id,
            url=f"https://youtu.be/{job_id}",
            title=f"Job {job_id}",
            status=job_status,
            options_json=DownloadOptions(mode="video_only", resolution="1080p").model_dump_json(),
            total_items=len(item_statuses),
            download_dir=str(download_dir or ""),
            created_at=created_at or datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc),
        )
    )
    for index, item_status in enumerate(item_statuses, start=1):
        session.add(
            JobItem(
                id=f"{job_id}-item-{index}",
                job_id=job_id,
                source_url=f"https://youtu.be/{job_id}-{index}",
                title=f"Item {job_id} {index}",
                index=index,
                status=item_status,
                progress=87.0,
                downloaded_bytes=1_048_576,
                total_bytes=2_097_152,
                speed=2048,
                eta=30,
                error="stale error" if item_status == "failed" else None,
            )
        )
    session.commit()


def statuses(session: Session, job_id: str) -> list[str]:
    items = session.exec(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)).all()
    return [item.status for item in items]


# --------------------------------------------------------------------- 规则


def test_queued_and_running_items_are_requeued_with_their_progress_cleared(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        seed(session, "job-a", job_status="running", item_statuses=["running", "queued"])

        report = recover_orphaned_items(session, policy=RESUME_POLICY)
        session.commit()

        assert report.requeued_item_ids == ["job-a-item-1", "job-a-item-2"]
        assert report.total == 2
        assert statuses(session, "job-a") == ["queued", "queued"]
        item = session.get(JobItem, "job-a-item-1")
        assert item is not None
        # 87% 是「上次」的进度，`.part` 是否还在只有 yt-dlp 知道 —— 留着它就是在撒谎。
        assert item.progress == 0.0
        assert item.downloaded_bytes is None
        assert item.total_bytes is None
        assert item.speed is None
        assert item.eta is None
        assert item.started_at is None
        assert item.finished_at is None


def test_a_clean_database_produces_an_empty_report_and_is_idempotent(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        seed(session, "job-done", job_status="succeeded", item_statuses=["succeeded"])
        seed(session, "job-bad", job_status="failed", item_statuses=["failed"])

        report = recover_orphaned_items(session, policy=RESUME_POLICY)
        session.commit()

        assert report.total == 0
        assert statuses(session, "job-done") == ["succeeded"]
        assert statuses(session, "job-bad") == ["failed"]

        # 同一份数据扫两遍不能把第二轮变成「又发现一个孤儿」。
        second = recover_orphaned_items(session, policy=RESUME_POLICY)
        assert second.total == 0


def test_paused_items_stay_paused_no_matter_what_the_policy_says(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        seed(session, "job-paused", job_status="paused", item_statuses=["running", "queued", "succeeded"])

        report = recover_orphaned_items(session, policy=RESUME_POLICY)
        session.commit()

        assert report.requeued_item_ids == []
        assert sorted(report.paused_item_ids) == ["job-paused-item-1", "job-paused-item-2"]
        assert statuses(session, "job-paused") == ["paused", "paused", "succeeded"]


def test_a_job_that_was_being_cancelled_is_not_resumed(tmp_path: Path) -> None:
    """`cancel()` 只把**排队中**的任务状态改成 cancelled；运行中的取消只存在内存里。

    进程一退出那个集合就没了，所以判据必须落回库中可见的事实：有已取消的条目 ⇒ 这个任务
    正在被取消。否则用户明明点了取消，重启后它又自己下起来了。
    """
    with open_session(tmp_path) as session:
        seed(session, "job-cancelling", job_status="running", item_statuses=["cancelled", "running"])

        report = recover_orphaned_items(session, policy=RESUME_POLICY)
        session.commit()

        assert report.requeued_item_ids == []
        assert report.cancelled_item_ids == ["job-cancelling-item-2"]
        assert statuses(session, "job-cancelling") == ["cancelled", "cancelled"]
        item = session.get(JobItem, "job-cancelling-item-2")
        assert item is not None and item.error == CANCELLED_ITEM_ERROR
        job = session.get(Job, "job-cancelling")
        assert job is not None
        assert job.status == "cancelled"


def test_items_whose_job_vanished_are_cancelled_instead_of_queueing_forever(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        session.add(
            JobItem(
                id="dangling-item",
                job_id="job-that-no-longer-exists",
                source_url="https://youtu.be/dangling",
                title="Dangling",
                index=1,
                status="queued",
            )
        )
        session.commit()

        report = recover_orphaned_items(session, policy=RESUME_POLICY)
        session.commit()

        assert report.requeued_item_ids == []
        assert report.cancelled_item_ids == ["dangling-item"]
        item = session.get(JobItem, "dangling-item")
        assert item is not None
        assert item.error == DANGLING_ITEM_ERROR


def test_fail_policy_marks_items_failed_and_says_which_state_they_came_from(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        seed(session, "job-fail", job_status="running", item_statuses=["running", "queued"])

        report = recover_orphaned_items(session, policy=FAIL_POLICY)
        session.commit()

        assert report.requeued_item_ids == []
        assert report.failed_item_ids == ["job-fail-item-1", "job-fail-item-2"]
        assert statuses(session, "job-fail") == ["failed", "failed"]
        running_item = session.get(JobItem, "job-fail-item-1")
        queued_item = session.get(JobItem, "job-fail-item-2")
        assert running_item is not None and running_item.error == INTERRUPTED_WHILE_RUNNING_ERROR
        assert queued_item is not None and queued_item.error == INTERRUPTED_WHILE_QUEUED_ERROR
        assert running_item.finished_at is not None


def test_requeue_order_follows_the_original_order_not_the_id_order(tmp_path: Path) -> None:
    """332 条的积压要按当初的下单顺序跑，否则用户看到的是随机顺序。"""
    with open_session(tmp_path) as session:
        later = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        earlier = later - timedelta(hours=2)
        seed(session, "job-z-first", job_status="queued", item_statuses=["queued", "queued"], created_at=earlier)
        seed(session, "job-a-second", job_status="queued", item_statuses=["queued"], created_at=later)

        report = recover_orphaned_items(session, policy=RESUME_POLICY)
        session.commit()

        assert report.requeued_item_ids == [
            "job-z-first-item-1",
            "job-z-first-item-2",
            "job-a-second-item-1",
        ]


def test_unknown_policy_values_fall_back_to_resume() -> None:
    # 写错一个字不该让应用起不来，但也不能悄悄变成别的意思。
    assert normalize_policy("fail") == FAIL_POLICY
    assert normalize_policy(" FAIL ") == FAIL_POLICY
    assert normalize_policy("resume") == RESUME_POLICY
    assert normalize_policy("cancel") == RESUME_POLICY
    assert normalize_policy(None) == RESUME_POLICY


# --------------------------------------------------------------- 启动链路上


def wait_for_job_status(client: TestClient, job_id: str, status: str) -> dict:
    for _ in range(60):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach {status}: {payload['status']}")


def test_startup_alone_makes_orphaned_items_download_again(tmp_path: Path) -> None:
    """端到端的核心断言：**不做任何操作**，只是启动应用，孤儿的任务自己跑完。

    这条用例在修复前必然失败，断言是 `did not reach succeeded` —— 与生产上
    「重启后任务永远不动」完全同形。
    """
    service = FakeYtDlpService()
    with open_session(tmp_path) as session:
        seed(session, "job-orphan", job_status="running", item_statuses=["running", "queued"])

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        payload = wait_for_job_status(client, "job-orphan", "succeeded")
        assert [item["status"] for item in payload["items"]] == ["succeeded", "succeeded"]
        assert len(service.downloads) == 2

        with open_session(tmp_path) as session:
            events = session.exec(
                select(JobEvent).where(JobEvent.event_type == "jobs_recovered")
            ).all()
            # 事件是「恢复真的发生过」的正面证据：只看到状态变成 succeeded，
            # 分不清是恢复跑的、还是有别的路径把它跑完了。
            assert [event.job_id for event in events] == ["job-orphan"]


def test_startup_with_fail_policy_never_touches_the_downloader(tmp_path: Path) -> None:
    service = FakeYtDlpService()
    with open_session(tmp_path) as session:
        seed(session, "job-fail-policy", job_status="running", item_statuses=["running"])

    with TestClient(
        create_app(settings=make_settings(tmp_path, orphan_recovery="fail"), ytdlp_service=service)
    ) as client:
        payload = wait_for_job_status(client, "job-fail-policy", "failed")
        assert payload["items"][0]["error"] == INTERRUPTED_WHILE_RUNNING_ERROR
        # 「标记失败」的判据是**一次下载都没有发起**，不是「状态字段恰好是 failed」。
        assert service.downloads == []


def test_startup_leaves_paused_work_paused(tmp_path: Path) -> None:
    service = FakeYtDlpService()
    with open_session(tmp_path) as session:
        seed(session, "job-still-paused", job_status="paused", item_statuses=["running"])

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        time.sleep(0.2)
        payload = client.get("/api/jobs/job-still-paused").json()
        assert payload["status"] == "paused"
        assert payload["items"][0]["status"] == "paused"
        assert service.downloads == []
