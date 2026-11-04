"""E-01 · 磁盘预算在**接口与任务生命周期**上的落点。

三件事必须同时成立，单测纯函数证明不了：

1. 解析结果带一条提示，但提示本身不阻断；
2. 点「下载」时用**本次所选清晰度**复核，不足则 409 且**库里不留任务**；
3. 每条视频真正开始前再核一次真实大小，不足则该条判失败、一个字节都不写，
   腾出空间后重启同一条能恢复（状态确实变了，不是界面上的假动作）。

`free_space_bytes` 一律用 monkeypatch 注入：测试不能依赖本机某个分区恰好剩多少。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.contracts.config import AppSettings
from app.contracts.models import Job, JobItem, JobStatus
from app.contracts.schemas import AnalyzeResponse, DownloadOptions, FormatOption
from app.download import disk_budget
from app.download.job_manager import JobManager, new_id
from app.infra.db import create_app_engine, init_db
from app.infra.events import EventBroker
from app.main import create_app

from fakes import FakeYtDlpService

MIB = 1024 * 1024
SOURCE_URL = "https://youtu.be/boom"


class SizedFakeService(FakeYtDlpService):
    """解析给出带大小的格式清单；预检给出这一条的真实大小。"""

    def __init__(self, filesize: int | None = 400 * MIB) -> None:
        super().__init__()
        self.filesize = filesize

    def extract_metadata(self, url, cookies_path=None):
        analysis = super().extract_metadata(url, cookies_path=cookies_path)
        if "playlist" in url:
            return analysis
        return AnalyzeResponse(
            **{
                **analysis.model_dump(),
                "formats": [
                    FormatOption(format_id="22", label="720p", height=720, filesize=400 * MIB),
                    FormatOption(format_id="18", label="360p", height=360, filesize=50 * MIB),
                ],
            }
        )

    def prepare_download(self, url, options, cookies_path=None):
        return SimpleNamespace(
            is_selectable=True,
            width=1280,
            height=720,
            actual_format="mp4",
            filesize=self.filesize,
            duration=None,
            subtitle=None,
        )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def _client(tmp_path: Path, service: FakeYtDlpService) -> TestClient:
    return TestClient(create_app(settings=_settings(tmp_path), ytdlp_service=service))


# ---------------------------------------------------------------- 解析 / 建任务


def test_analyze_attaches_a_readable_budget_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 4 * 1024**3)
    with _client(tmp_path, SizedFakeService()) as client:
        response = client.post("/api/analyze", json={"url": SOURCE_URL})

    assert response.status_code == 200, response.text
    budget = response.json()["disk_budget"]
    assert budget["status"] == "ok"
    assert budget["estimated_bytes"] == 400 * MIB
    assert budget["free_bytes"] == 4 * 1024**3
    assert "MiB" in budget["message"] or "GiB" in budget["message"]


def test_analyze_hint_is_only_a_hint_even_when_it_says_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 10 * MIB)
    with _client(tmp_path, SizedFakeService()) as client:
        response = client.post("/api/analyze", json={"url": SOURCE_URL})

    assert response.status_code == 200
    assert response.json()["disk_budget"]["status"] == "insufficient"


def test_create_job_blocks_with_409_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 10 * MIB)
    with _client(tmp_path, SizedFakeService()) as client:
        blocked = client.post("/api/jobs", json={"url": SOURCE_URL, "options": {"resolution": "720p"}})
        assert blocked.status_code == 409, blocked.text
        detail = blocked.json()["detail"]
        assert "磁盘空间不足" in detail and "清理" in detail and "MiB" in detail
        assert client.get("/api/jobs").json() == []


def test_create_job_uses_the_requested_resolution_not_the_stored_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """600 MiB 可用：360p（50+512）过得去，720p（400+512）过不去。

    解析提示按设置默认值算，建任务必须按**这一次**的选项重算 —— 两处不能共用一个数字。
    """
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 600 * MIB)
    with _client(tmp_path, SizedFakeService()) as client:
        low = client.post("/api/jobs", json={"url": SOURCE_URL, "options": {"resolution": "360p"}})
        high = client.post("/api/jobs", json={"url": f"{SOURCE_URL}-high", "options": {"resolution": "720p"}})

    assert low.status_code == 201, low.text
    assert high.status_code == 409, high.text
    assert len(low.json()["items"]) == 1


def test_create_job_is_not_blocked_when_the_size_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平台没给 filesize（真实 playlist 的 flat 条目就是如此）：只提示，不阻断。"""
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 0)
    with _client(tmp_path, FakeYtDlpService()) as client:
        response = client.post("/api/jobs", json={"url": SOURCE_URL})

    assert response.status_code == 201, response.text


def test_create_job_is_not_blocked_when_the_volume_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: None)
    with _client(tmp_path, SizedFakeService()) as client:
        response = client.post("/api/jobs", json={"url": SOURCE_URL})

    assert response.status_code == 201, response.text


# ---------------------------------------------------------------- 逐条生命周期


def _make_manager(tmp_path: Path, service: SizedFakeService) -> tuple[JobManager, object]:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    engine = create_app_engine(settings)
    init_db(engine)
    return JobManager(engine, settings, service, EventBroker()), engine


@pytest.fixture()
def runtime(tmp_path: Path):
    service = SizedFakeService(filesize=400 * MIB)
    manager, engine = _make_manager(tmp_path, service)
    return manager, engine, service


def _seed(engine: object, tmp_path: Path, *, status: str = JobStatus.queued.value) -> str:
    with Session(engine) as session:  # type: ignore[arg-type]
        job = Job(
            id=new_id(),
            url=SOURCE_URL,
            title="Boom",
            options_json=DownloadOptions().model_dump_json(),
            total_items=1,
            download_dir=str(tmp_path),
            status=status,
        )
        session.add(job)
        session.commit()
        session.add(
            JobItem(
                id=new_id(),
                job_id=job.id,
                source_url=SOURCE_URL,
                title="Boom",
                index=1,
                status=status,
            )
        )
        session.commit()
        return job.id


def _run(manager: JobManager, engine: object, tmp_path: Path, options: DownloadOptions) -> JobItem:
    with Session(engine) as session:  # type: ignore[arg-type]
        job = session.exec(select(Job)).one()
        item = session.exec(select(JobItem)).one()
        manager._run_item(session, job, item, options, tmp_path)
        session.refresh(item)
        return item


def test_full_disk_fails_the_item_before_a_single_byte_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime
) -> None:
    manager, engine, service = runtime
    _seed(engine, tmp_path)
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 10 * MIB)

    item = _run(manager, engine, tmp_path, DownloadOptions(resolution="720p"))
    assert item.status == JobStatus.failed.value
    assert "磁盘空间不足" in (item.error or "")
    assert "清理" in (item.error or "")
    assert item.total_bytes == 400 * MIB
    assert service.downloads == [], "被阻断的条目不许启动 yt-dlp（否则又会留一个半截 .part）"


def test_item_succeeds_when_space_is_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    manager, engine, service = runtime
    _seed(engine, tmp_path)
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 8 * 1024**3)

    item = _run(manager, engine, tmp_path, DownloadOptions(resolution="720p"))
    assert item.status == JobStatus.succeeded.value
    assert len(service.downloads) == 1


def test_cleaning_space_then_restarting_the_same_item_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime
) -> None:
    """生命周期：failed → succeeded，且第二次校验用的是同一条的真实大小。"""
    manager, engine, service = runtime
    _seed(engine, tmp_path)
    free = {"bytes": 10 * MIB}
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: free["bytes"])

    first = _run(manager, engine, tmp_path, DownloadOptions(resolution="720p"))
    assert first.status == JobStatus.failed.value

    free["bytes"] = 8 * 1024**3
    second = _run(manager, engine, tmp_path, DownloadOptions(resolution="720p"))
    assert second.status == JobStatus.succeeded.value
    assert len(service.downloads) == 1


def test_existing_output_is_not_blocked_by_a_full_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime
) -> None:
    """`skip_existing` 命中时 yt-dlp 不会写新文件；按满盘判失败是误伤。"""
    manager, engine, service = runtime
    _seed(engine, tmp_path)
    (tmp_path / "Boom [boom].mp4").write_bytes(b"already here")
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 0)

    assert _run(manager, engine, tmp_path, DownloadOptions(skip_existing=True)).status == JobStatus.succeeded.value
    assert len(service.downloads) == 1


def test_unknown_per_item_size_never_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    manager, engine, service = runtime
    _seed(engine, tmp_path)
    service.filesize = None
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 0)

    assert _run(manager, engine, tmp_path, DownloadOptions()).status == JobStatus.succeeded.value


def test_paused_stays_paused_the_guard_does_not_resume_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime
) -> None:
    """暂停是用户按的：空间校验只作用于正在跑的条目，不构成一次自动恢复。"""
    manager, engine, _service = runtime
    job_id = _seed(engine, tmp_path, status=JobStatus.paused.value)
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 0)

    asyncio.run(manager.pause(job_id))
    with Session(engine) as session:  # type: ignore[arg-type]
        assert session.exec(select(JobItem)).one().status == JobStatus.paused.value
        assert session.exec(select(Job)).one().status == JobStatus.paused.value
