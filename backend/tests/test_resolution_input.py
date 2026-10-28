"""清晰度取值的**入口校验**：只能进 `best` 或 `<高度>p`，其余当场报错。

为什么值得单开一个文件：这条规则要挡在**三个**入口上，而且其中一个（重启）不被
`DownloadOptions` 的字段校验覆盖 —— `JobManager._options_with_resolution()` 用的是
`model_copy(update=...)`，pydantic 不会重新校验。只测创建任务的入口，重启那条会静默漏过去，
症状与 ai/bug-fix/026 描述的完全一致（用户要 720p，实际下最高画质）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.contracts.config import AppSettings
from app.infra.db import create_app_engine, init_db
from app.main import create_app
from app.contracts.models import Job, JobItem
from app.contracts.schemas import DownloadOptions

from fakes import FakeYtDlpService


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=FakeYtDlpService()))


def stored_options(tmp_path: Path, job_id: str) -> DownloadOptions:
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        job = session.get(Job, job_id)
        assert job is not None
        return DownloadOptions.model_validate_json(job.options_json)


def stored_item_options(tmp_path: Path, item_id: str) -> DownloadOptions:
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        item = session.get(JobItem, item_id)
        assert item is not None and item.options_json is not None
        return DownloadOptions.model_validate_json(item.options_json)


def detail_text(response) -> str:
    return json.dumps(response.json(), ensure_ascii=False)


def test_a_resolution_with_a_capital_p_is_accepted_and_normalized(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/upper", "options": {"mode": "video_only", "resolution": "1080P"}},
        )

        assert response.status_code == 201, response.text
        assert stored_options(tmp_path, response.json()["id"]).resolution == "1080p"


def test_a_resolution_that_is_not_best_or_height_p_is_rejected(tmp_path: Path) -> None:
    """`hd` 这种写法以前会让格式选择器回落成 `bv*+ba/b`：用户以为选了高清，实际下最高画质。"""
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/bad", "options": {"mode": "video_only", "resolution": "hd"}},
        )

        assert response.status_code == 422
        body = detail_text(response)
        assert "清晰度只接受 best" in body
        # 报错要指出**去哪儿改**：请求体、设置里的默认清晰度，或环境变量。
        assert "YTDL_DEFAULT_RESOLUTION" in body


def test_a_missing_p_suffix_is_rejected_rather_than_guessed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/no-p", "options": {"mode": "video_only", "resolution": "1080"}},
        )

    assert response.status_code == 422
    assert "1080" in detail_text(response)


def test_the_default_resolution_setting_is_validated_and_normalized(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        rejected = client.put("/api/settings", json={"default_resolution": "HD"})
        assert rejected.status_code == 422

        accepted = client.put("/api/settings", json={"default_resolution": "720P"})
        assert accepted.status_code == 200
        assert accepted.json()["default_resolution"] == "720p"


def test_restarting_at_a_different_resolution_goes_through_the_same_gate(tmp_path: Path) -> None:
    """`model_copy(update=…)` 不触发校验，所以重启这条路径必须自己挡。"""
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/restart", "options": {"mode": "video_only", "resolution": "1080p"}},
        )
        job_id = created.json()["id"]

        rejected = client.post(f"/api/jobs/{job_id}/restart", json={"resolution": "hd"})
        assert rejected.status_code == 422
        assert stored_options(tmp_path, job_id).resolution == "1080p"

        accepted = client.post(f"/api/jobs/{job_id}/restart", json={"resolution": "720P"})
        assert accepted.status_code == 200
        assert stored_options(tmp_path, job_id).resolution == "720p"


def test_restarting_a_single_item_uses_the_same_gate(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/restart-item", "options": {"mode": "video_only", "resolution": "1080p"}},
        )
        job_id = created.json()["id"]
        item_id = created.json()["items"][0]["id"]

        rejected = client.post(f"/api/jobs/{job_id}/items/{item_id}/restart", json={"resolution": "1080"})
        assert rejected.status_code == 422

        accepted = client.post(f"/api/jobs/{job_id}/items/{item_id}/restart", json={"resolution": "720P"})
        assert accepted.status_code == 200
        # 单条重启把清晰度写进**条目**的 options（任务级不动）——这才是那条路径的落点。
        assert stored_item_options(tmp_path, item_id).resolution == "720p"
        assert stored_options(tmp_path, job_id).resolution == "1080p"
