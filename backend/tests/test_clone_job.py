"""E-04 · 「克隆任务」的接口与生命周期契约。

选的是克隆而不是 `PATCH`：预检之后才知道该降到哪个清晰度，那时任务已经建好；
改原任务要处理 queued 之外的状态、预检时序与 026 的 `model_copy` 陷阱，
而克隆不动历史、不碰 B站 记录，已有成品由 `skip_existing` 命中。

这里逐条钉住：**两条建任务路径共用同一套预检**（E-01 的 409 不能被绕过）、
**覆盖项重新走入口校验**（026）、**原任务一个字段都不变**、以及坏数据/解析失败等边界。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.contracts.config import AppSettings
from app.contracts.models import Job, JobEvent, JobItem, JobStatus
from app.contracts.schemas import CloneJobRequest, DownloadOptions, FormatOption
from pydantic import ValidationError
from app.download import disk_budget
from app.infra.db import create_app_engine, init_db
from app.main import create_app

from fakes import FakeYtDlpService

JOB_ID = "job-source"
SOURCE_URL = "https://youtube.com/playlist?list=abc"
MIB = 1024 * 1024


class SizedAnalysisService(FakeYtDlpService):
    """解析结果带 `filesize`，让 E-01 的磁盘预算能给出确定结论。"""

    def extract_metadata(self, url, cookies_path=None):
        base = super().extract_metadata(url, cookies_path=cookies_path)
        return base.model_copy(
            update={"formats": [FormatOption(format_id="22", label="720p", height=720, filesize=400 * MIB)]}
        )


class FailingAnalysisService(FakeYtDlpService):
    def extract_metadata(self, url, cookies_path=None):
        raise RuntimeError("YouTube 暂时不可达")


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def _seed(
    tmp_path: Path,
    *,
    status: str = JobStatus.failed.value,
    options: DownloadOptions | None = None,
    options_json: str | None = None,
) -> None:
    engine = create_app_engine(_settings(tmp_path))
    init_db(engine)
    effective = options or DownloadOptions(
        resolution="1080p",
        mode="video_subtitles",
        subtitle_languages=["en"],
        playlist_items=[2],
        skip_existing=True,
        retries=7,
    )
    with Session(engine) as session:
        session.add(
            Job(
                id=JOB_ID,
                url=SOURCE_URL,
                title="Source batch",
                status=status,
                options_json=options_json if options_json is not None else effective.model_dump_json(),
                total_items=1,
                download_dir=str(tmp_path / "downloads" / "Source batch"),
            )
        )
        session.add(
            JobItem(
                id="source-item",
                job_id=JOB_ID,
                source_url="https://youtu.be/two",
                title="Two",
                index=2,
                status=status,
            )
        )
        session.commit()


def _client(tmp_path: Path, service: FakeYtDlpService | None = None) -> TestClient:
    return TestClient(create_app(settings=_settings(tmp_path), ytdlp_service=service or FakeYtDlpService()))


def _jobs(engine) -> list[Job]:
    with Session(engine) as session:
        return list(session.exec(select(Job)).all())


def _engine(tmp_path: Path):
    return create_app_engine(_settings(tmp_path))


# ---------------------------------------------------------------- 正常流程


def test_clone_creates_a_new_job_with_the_same_url_and_options(tmp_path: Path) -> None:
    _seed(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={})

    assert response.status_code == 201, response.text
    clone = response.json()
    assert clone["id"] != JOB_ID
    assert clone["url"] == SOURCE_URL
    assert clone["status"] == JobStatus.queued.value
    assert len(clone["items"]) == 1  # 沿用 `playlist_items=[2]`

    with Session(_engine(tmp_path)) as session:
        source = session.get(Job, JOB_ID)
        assert source is not None
        assert source.options_json == DownloadOptions(
            resolution="1080p", mode="video_subtitles", subtitle_languages=["en"],
            playlist_items=[2], skip_existing=True, retries=7
        ).model_dump_json()
        assert json.loads(session.exec(select(JobEvent).where(JobEvent.event_type == "job_cloned")).one().payload_json)["source_job_id"] == JOB_ID


def test_clone_leaves_the_source_job_and_its_items_untouched(tmp_path: Path) -> None:
    _seed(tmp_path, status=JobStatus.succeeded.value)
    with _client(tmp_path) as client:
        client.post(f"/api/jobs/{JOB_ID}/clone", json={"resolution": "720p"})

    with Session(_engine(tmp_path)) as session:
        source = session.get(Job, JOB_ID)
        item = session.get(JobItem, "source-item")
        assert source is not None and item is not None
        assert source.status == JobStatus.succeeded.value
        assert source.url == SOURCE_URL
        assert DownloadOptions.model_validate_json(source.options_json).resolution == "1080p"
        assert item.status == JobStatus.succeeded.value
        assert item.title == "Two"


def test_clone_applies_only_the_requested_overrides(tmp_path: Path) -> None:
    _seed(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(
            f"/api/jobs/{JOB_ID}/clone",
            json={"resolution": "720p", "mode": "video_only"},
        )

    assert response.status_code == 201, response.text
    clone_id = response.json()["id"]
    with Session(_engine(tmp_path)) as session:
        options = DownloadOptions.model_validate_json(session.get(Job, clone_id).options_json)
        assert (options.resolution, options.mode) == ("720p", "video_only")
        # 没传的项全部沿用原任务，不是 `DownloadOptions()` 的出厂默认。
        assert options.subtitle_languages == ["en"]
        assert options.playlist_items == [2]
        assert options.skip_existing is True
        assert options.retries == 7


# ---------------------------------------------------------------- 026 入口校验


def test_the_request_model_is_where_resolution_is_validated() -> None:
    """钉住 026 的校验**位置**：覆盖项在到达业务层之前就已归一化 / 拒绝。

    这条是 E-04 变异验证的结论：把业务层的整份 `model_validate` 换成
    `model_copy(update=…)`，下面所有接口用例照样全绿 —— 因为真正的入口在这里。
    """
    assert CloneJobRequest.model_validate({"resolution": " 720P "}).resolution == "720p"
    assert CloneJobRequest.model_validate({}).resolution is None
    for bad in ("hd", "1080", "1440p60"):
        with pytest.raises(ValidationError):
            CloneJobRequest.model_validate({"resolution": bad})


def test_clone_normalizes_unambiguous_resolution_variants(tmp_path: Path) -> None:
    _seed(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={"resolution": " 720P "})

    assert response.status_code == 201, response.text
    with Session(_engine(tmp_path)) as session:
        job = session.exec(select(Job).where(Job.id != JOB_ID)).one()
        assert DownloadOptions.model_validate_json(job.options_json).resolution == "720p"


@pytest.mark.parametrize("bad", ["hd", "1080", "1440p60"])
def test_clone_rejects_ambiguous_resolution_instead_of_falling_back_to_best(
    tmp_path: Path, bad: str
) -> None:
    """候选池点名的风险：`model_copy(update=…)` 不触发校验，克隆入口必须重新 validate。"""
    _seed(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={"resolution": bad})

    assert response.status_code == 422, response.text
    assert len(_jobs(_engine(tmp_path))) == 1, "被拒的克隆不许留下半个任务"


def test_clone_rejects_an_unknown_mode(tmp_path: Path) -> None:
    _seed(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={"mode": "audio_only"})
    assert response.status_code == 422
    assert len(_jobs(_engine(tmp_path))) == 1


# ---------------------------------------------------------------- 边界与生命周期


def test_clone_is_allowed_from_any_source_status_and_always_starts_queued(
    tmp_path: Path,
) -> None:
    """六种源状态都能克隆，且新任务一律 `queued`。

    **夹具陷阱**：`queued` / `running` 会被 `create_app` 启动时的孤儿恢复（FR-47）
    重新认领、worker 立刻跑成终态，所以这两档**不**断言源状态 —— 那不是克隆改的，
    把竞态写进断言只会得到一条整包必红、单跑必绿的用例。
    """
    settled = (
        JobStatus.paused.value,
        JobStatus.failed.value,
        JobStatus.succeeded.value,
        JobStatus.cancelled.value,
    )
    for status in (
        JobStatus.queued.value,
        JobStatus.running.value,
        *settled,
    ):
        case = tmp_path / status
        case.mkdir()
        _seed(case, status=status)
        with _client(case) as client:
            response = client.post(f"/api/jobs/{JOB_ID}/clone", json={})
        assert response.status_code == 201, (status, response.text)
        assert response.json()["status"] == JobStatus.queued.value
        assert response.json()["id"] != JOB_ID
        if status in settled:
            with Session(_engine(case)) as session:
                assert session.get(Job, JOB_ID).status == status


def test_clone_of_an_unknown_job_is_404(tmp_path: Path) -> None:
    _seed(tmp_path)
    with _client(tmp_path) as client:
        response = client.post("/api/jobs/does-not-exist/clone", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found."


def test_clone_of_a_job_with_broken_options_is_409_with_a_next_step(tmp_path: Path) -> None:
    _seed(tmp_path, options_json="{not json")
    with _client(tmp_path) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={})

    assert response.status_code == 409
    assert "新建一个任务" in response.json()["detail"]
    assert len(_jobs(_engine(tmp_path))) == 1


def test_clone_propagates_an_analysis_failure_as_400(tmp_path: Path) -> None:
    _seed(tmp_path)
    with _client(tmp_path, FailingAnalysisService()) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={})

    assert response.status_code == 400
    assert "YouTube 暂时不可达" in response.json()["detail"]
    assert len(_jobs(_engine(tmp_path))) == 1


def test_clone_cannot_bypass_the_disk_budget_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-01 的阻断必须在两条建任务路径上同时成立，克隆不是后门。"""
    _seed(tmp_path)
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 10 * MIB)
    with _client(tmp_path, SizedAnalysisService()) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={})

    assert response.status_code == 409, response.text
    assert "磁盘空间不足" in response.json()["detail"]
    assert len(_jobs(_engine(tmp_path))) == 1


def test_clone_writes_its_own_event_without_copying_source_history(tmp_path: Path) -> None:
    _seed(tmp_path)
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(JobEvent(job_id=JOB_ID, event_type="job_failed", payload_json="{}"))
        session.commit()

    with _client(tmp_path) as client:
        response = client.post(f"/api/jobs/{JOB_ID}/clone", json={})
    clone_id = response.json()["id"]

    with Session(engine) as session:
        clone_events = list(session.exec(select(JobEvent).where(JobEvent.job_id == clone_id)).all())
        assert {event.event_type for event in clone_events} >= {"job_cloned", "job_queued"}
        assert session.exec(select(JobEvent).where(JobEvent.job_id == JOB_ID)).all(), "源任务的历史仍在"
        assert all(event.event_type != "job_failed" for event in clone_events)
