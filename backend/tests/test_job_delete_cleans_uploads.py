"""删任务时要连带清掉投稿侧的三张表（[ai/bug-fix/037](../ai/bug-fix/037-deleting-a-job-leaves-its-uploads.md)）。

`bili_upload` / `bili_season` / `bili_auto_retry` 都不随任务删除而消失，而
`GET /api/bilibili/uploads/summary` 取自前两张表的并集 —— 不清理的话，**已经不存在的任务**
会一直在摘要里出现，`bili_auto_retry` 还会继续拿它去问平台。

两条相反的判据：
- 有在途投稿 ⇒ **拒绝**（409）并把任务留着：投稿跑在另一个 worker 上，删了记录它会把结论写空；
- 没有在途 ⇒ 三张表一起清，且只清这一个任务的。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from app.contracts.models import BiliAutoRetry, BiliSeason, BiliUpload, Job, JobItem
from test_api import make_client, seed_job


def _seed_bili_records(client, job_id: str, *, upload_status: str = "published") -> None:
    """给任务种上三张表各一行 —— 这正是「删除后仍然留在库里」的那批记录。"""
    engine = client.app.state.db_engine
    with Session(engine) as session:
        item = session.exec(select(JobItem).where(JobItem.job_id == job_id)).first()
        session.add(
            BiliUpload(
                id=f"{job_id}-u1",
                job_id=job_id,
                item_id=item.id if item is not None else None,
                title="任务 第 1 集",
                status=upload_status,
            )
        )
        session.add(BiliSeason(id=f"{job_id}-s1", job_id=job_id, title="合集", status="grouped"))
        session.add(
            BiliAutoRetry(target_id=f"{job_id}-u1", kind="subtitle", job_id=job_id, state="waiting")
        )
        session.commit()


def _count(client, model, job_id: str) -> int:
    with Session(client.app.state.db_engine) as session:
        return len(session.exec(select(model).where(model.job_id == job_id)).all())


def _make_client(tmp_path: Path):
    client = make_client(tmp_path)
    seed_job(tmp_path, "job1", status="completed")
    return client


def test_deleting_a_job_removes_every_upload_record(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    _seed_bili_records(client, "job1")
    # 前提：摘要里看得见它 —— 否则这条用例证明不了「清理之后不再出现」。
    assert client.get("/api/bilibili/uploads/summary").json()["jobs"]

    assert client.delete("/api/jobs/job1").status_code == 204

    assert _count(client, BiliUpload, "job1") == 0
    assert _count(client, BiliSeason, "job1") == 0
    assert _count(client, BiliAutoRetry, "job1") == 0
    assert client.get("/api/bilibili/uploads/summary").json()["jobs"] == []


def test_deleting_is_refused_while_an_upload_is_in_flight(tmp_path: Path) -> None:
    """在途投稿时**拒绝**（409），任务与记录都留着 —— 删掉会让那次投稿的结果无处可写。"""
    client = _make_client(tmp_path)
    _seed_bili_records(client, "job1", upload_status="uploading")

    response = client.delete("/api/jobs/job1")

    assert response.status_code == 409
    assert "在上传或核对中" in response.json()["detail"]
    with Session(client.app.state.db_engine) as session:
        assert session.get(Job, "job1") is not None
    assert _count(client, BiliUpload, "job1") == 1


def test_deleting_the_last_item_also_removes_the_records(tmp_path: Path) -> None:
    """删光子视频 = 任务消失，投稿记录同样要清 —— 这条路不走 `DELETE /api/jobs/{id}`。"""
    client = _make_client(tmp_path)
    _seed_bili_records(client, "job1")
    with Session(client.app.state.db_engine) as session:
        item_ids = [item.id for item in session.exec(select(JobItem).where(JobItem.job_id == "job1")).all()]

    response = client.post("/api/jobs/job1/items/delete", json={"item_ids": item_ids, "delete_files": False})

    assert response.status_code == 200
    assert response.json()["job_deleted"] is True
    assert _count(client, BiliUpload, "job1") == 0
    assert _count(client, BiliSeason, "job1") == 0
    assert _count(client, BiliAutoRetry, "job1") == 0


def test_a_busy_job_blocks_the_whole_batch(tmp_path: Path) -> None:
    """批量删除先整批预检：不能删掉前几条、再因为某一条有在途投稿被拒。"""
    client = _make_client(tmp_path)
    seed_job(tmp_path, "job2", status="completed")
    _seed_bili_records(client, "job2", upload_status="submitted")

    response = client.post(
        "/api/jobs/batch",
        json={"job_ids": ["job1", "job2"], "action": "delete", "delete_files": False},
    )

    assert response.status_code == 409
    with Session(client.app.state.db_engine) as session:
        assert session.get(Job, "job1") is not None
        assert session.get(Job, "job2") is not None


def test_only_the_deleted_job_loses_its_records(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    seed_job(tmp_path, "job2", status="completed")
    _seed_bili_records(client, "job1")
    _seed_bili_records(client, "job2")

    assert client.delete("/api/jobs/job1").status_code == 204

    assert _count(client, BiliUpload, "job1") == 0
    assert _count(client, BiliUpload, "job2") == 1
    assert _count(client, BiliSeason, "job2") == 1
    assert _count(client, BiliAutoRetry, "job2") == 1
