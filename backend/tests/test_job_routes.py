"""任务路由的「查不到就 404」这一族约定。

`routers/jobs.py` 里每个带 `{job_id}` 的接口都以同三行开头（`session.get` → 404 → 继续）。
它们是**契约**而不只是防御：前端拿到 404 会去重拉任务列表，拿到 500 就只会显示「操作失败」。
此前没有任何用例覆盖它们（覆盖率 82%），于是「某个接口忘了 404 就落到 500」不会被发现。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts.config import AppSettings
from app.main import create_app

from fakes import FakeYtDlpService


def make_client(tmp_path: Path) -> TestClient:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )
    return TestClient(create_app(settings=settings, ytdlp_service=FakeYtDlpService()))


MISSING = "job-does-not-exist"
MISSING_ITEM = "item-does-not-exist"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/jobs/{MISSING}"),
        ("POST", f"/api/jobs/{MISSING}/cancel"),
        ("POST", f"/api/jobs/{MISSING}/pause"),
        ("POST", f"/api/jobs/{MISSING}/restart"),
        ("DELETE", f"/api/jobs/{MISSING}"),
        ("POST", f"/api/jobs/{MISSING}/play"),
        ("POST", f"/api/jobs/{MISSING}/open-folder"),
        ("POST", f"/api/jobs/{MISSING}/open-subtitle"),
    ],
)
def test_every_job_scoped_route_answers_404_for_an_unknown_job(tmp_path: Path, method: str, path: str) -> None:
    with make_client(tmp_path) as client:
        response = client.request(method, path)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Job not found."


@pytest.mark.parametrize(
    "path",
    [
        f"/api/jobs/{MISSING}/items/{MISSING_ITEM}/restart",
        f"/api/jobs/{MISSING}/items/{MISSING_ITEM}/play",
        f"/api/jobs/{MISSING}/items/{MISSING_ITEM}/open-folder",
        f"/api/jobs/{MISSING}/items/{MISSING_ITEM}/open-subtitle",
    ],
)
def test_item_scoped_routes_answer_404_for_an_unknown_item(tmp_path: Path, path: str) -> None:
    with make_client(tmp_path) as client:
        response = client.post(path)

    assert response.status_code == 404, response.text
    # 条目级接口说的是「条目找不到」——两条 404 的文案不同，界面据此决定重拉整表还是就地提示。
    assert response.json()["detail"] in {"Job item not found.", "Job not found."}


def test_a_batch_action_on_unknown_jobs_is_404_not_silently_ok(tmp_path: Path) -> None:
    """批量接口只跳过查不到的 id；一个都没命中时要明确报 404，不能回「成功了，影响 0 个」。"""
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/jobs/batch",
            json={"action": "pause", "job_ids": [MISSING, "another-missing"]},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No matching jobs found."
