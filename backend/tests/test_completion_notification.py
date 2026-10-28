"""「任务完成提醒」的后端契约。

提醒这件事本身发生在浏览器里（Web Audio + DOM 浮层），后端只负责两件：

1. 把用户选的**提醒方式**存下来、读回去（不然换一台机器打开同一个后端，偏好就丢了）；
2. 把每个任务**勾没勾**「完成后通知」抬进读模型 —— 它只活在 `options_json` 里，
   不抬出来，前端刷新一次页面就再也拿不到了。

第 2 条是本文件存在的直接原因：它正是「界面上有开关、后端有字段、但没有任何实现」
（`ai/plan/brainstorming.md` H-01）里缺的那一环。
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.contracts.config import AppSettings
from app.infra.db import create_app_engine, init_db
from app.main import create_app
from app.contracts.models import Job, JobItem, Setting
from app.contracts.schemas import DownloadOptions

from fakes import FakeYtDlpService
from test_api import make_client, seed_job


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def _seed_job_with_options(tmp_path: Path, job_id: str, options_json: str) -> None:
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                url=f"https://youtu.be/{job_id}",
                title=f"Job {job_id}",
                status="succeeded",
                options_json=options_json,
                total_items=1,
            )
        )
        session.add(
            JobItem(
                id=f"{job_id}-item",
                job_id=job_id,
                source_url=f"https://youtu.be/{job_id}",
                title=f"Job {job_id}",
                status="succeeded",
            )
        )
        session.commit()


# ------------------------------------------------------------------ 逐任务开关


def test_job_read_carries_the_notify_flag_from_stored_options(tmp_path: Path) -> None:
    seed_job(tmp_path, "job-notify-on", status="succeeded", options=None)

    payload = make_client(tmp_path).get("/api/jobs/job-notify-on").json()

    # 出厂的 `DownloadOptions.notify_on_complete` 是 True，所以这里必须是 True：
    # 若为 False，「设置里选了提醒方式」就会变成一句空话（选了却不响）。
    assert payload["notify_on_complete"] is True


def test_job_read_reports_a_job_that_opted_out(tmp_path: Path) -> None:
    seed_job(
        tmp_path,
        "job-notify-off",
        status="succeeded",
        options=DownloadOptions(notify_on_complete=False),
    )

    payload = make_client(tmp_path).get("/api/jobs/job-notify-off").json()

    assert payload["notify_on_complete"] is False


def test_job_read_falls_back_to_false_when_stored_options_are_unreadable(tmp_path: Path) -> None:
    _seed_job_with_options(tmp_path, "job-broken-options", "{not json")

    payload = make_client(tmp_path).get("/api/jobs/job-broken-options").json()

    # 解析不了 = 没能确认用户要提醒 = 不提醒。反过来说成 True，就会在用户没要求的时候响，
    # 那是更坏的那种错。
    assert payload["notify_on_complete"] is False


def test_job_read_ignores_unknown_keys_in_stored_options(tmp_path: Path) -> None:
    # 老版本存下来的任务可能带着这一版已经没有的字段；不能因为多了个键就把整条判成不可解析。
    _seed_job_with_options(
        tmp_path,
        "job-extra-keys",
        '{"mode":"video_only","resolution":"1080p","notify_on_complete":true,"retired_field":1}',
    )

    payload = make_client(tmp_path).get("/api/jobs/job-extra-keys").json()

    assert payload["notify_on_complete"] is True


# ---------------------------------------------------------------------- 提醒方式


def test_completion_notification_defaults_to_both(tmp_path: Path) -> None:
    settings = make_client(tmp_path).get("/api/settings").json()

    assert settings["completion_notification"] == "both"


def test_completion_notification_round_trips_through_the_api(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    updated = client.put("/api/settings", json={"completion_notification": "sound"})

    assert updated.status_code == 200
    assert updated.json()["completion_notification"] == "sound"
    assert client.get("/api/settings").json()["completion_notification"] == "sound"


def test_completion_notification_rejects_an_unknown_mode(tmp_path: Path) -> None:
    response = make_client(tmp_path).put("/api/settings", json={"completion_notification": "loud"})

    assert response.status_code == 422


def test_partial_settings_update_keeps_the_chosen_mode(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.put("/api/settings", json={"completion_notification": "toast"})

    client.put("/api/settings", json={"default_retries": 4})

    assert client.get("/api/settings").json()["completion_notification"] == "toast"


def test_a_corrupt_stored_mode_falls_back_instead_of_reaching_the_ui(tmp_path: Path) -> None:
    """落库值被写坏时退回默认，而不是把非法值发给前端。

    前端拿它去比字符串，不匹配的表现就是「选了却不响」—— 而这正是本功能要消灭的症状。
    """
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(Setting(key="completion_notification", value="loudly"))
        session.commit()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=FakeYtDlpService())) as client:
        assert client.get("/api/settings").json()["completion_notification"] == "both"


def test_an_invalid_mode_from_the_environment_never_reaches_the_ui(tmp_path: Path) -> None:
    """环境变量这一路不经过 pydantic 的 `Literal` 校验（`config.py` 里故意写成 `str`）。

    写错一个字母时宁可退回默认，也不要让前端拿到一个它不认识的值。
    """
    settings = make_settings(tmp_path)
    settings.completion_notification = "loudly"

    with TestClient(create_app(settings=settings, ytdlp_service=FakeYtDlpService())) as client:
        assert client.get("/api/settings").json()["completion_notification"] == "both"
