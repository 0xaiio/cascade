"""`routers/bilibili`：13 个 operation 的状态码与形状。

路由是 L0 入口层，它**唯一**的职责是「取参 → 调辅助 → 定状态码」。所以这里的用例几乎全部
在钉状态码口径（与仓库既有的 `cookie_import_status_code` 一致）：

- **409 = 前置条件不满足**：没装工具、没登录、视频还没下载好、正在上传中；
- **400 = 请求本身不对**：元数据超限、只有一个视频却走合集流程；
- **404 = 记录不存在**；
- **502 = 上游取不到数据**（扫码二维码 / 轮询），不是我们的 bug，不该是 500。

跑法上刻意用**真的** `BiliManager` + 假的 `transport`/`runner`：路由与编排之间的接线
（谁负责校验、谁负责 409）正是这一层最容易错的地方，换成假 manager 就测不到了。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.bili_auth import TvToken, write_cookie_file
from app.bili_bin import is_pe
from app.bili_manager import BiliManager
from app.config import AppSettings
from app.db import create_app_engine, init_db
from app.events import EventBroker
from app.main import create_app
from app.models import BiliUpload, Job, JobItem
from app.bili_read_model import IN_FLIGHT_STATUSES
from bili_fakes import FakeBiliTransport, RecordingSleeper, command_runner
from fakes import FakeYtDlpService

GOOD_JAR = {"SESSDATA": "sess", "bili_jct": "jct", "DedeUserID": "42"}
# 一份完整的登录态 = cookies + 投稿令牌。只写 cookies 的文件 biliup 会**拒绝开工**
# （它启动时先拿令牌去校验），所以夹具默认带上令牌，缺令牌是单独一条要构造的分支。
TV_TOKEN = TvToken(
    access_token="tv-access-token", refresh_token="tv-refresh-token", expires_in=15552000, mid=42
)


def write_login(settings: AppSettings, jar: dict[str, str] = GOOD_JAR, token: TvToken | None = TV_TOKEN) -> None:
    write_cookie_file(settings.bili_cookie_path, jar, 42, token)


def make_settings(tmp_path: Path, *, biliup: Path | None = None) -> AppSettings:
    exe = biliup if biliup is not None else tmp_path / "biliup.exe"
    if biliup is None:
        exe.write_bytes(b"MZ" + b"\x00" * 64)
        assert is_pe(exe)
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        biliup_path=str(exe),
        bili_upload_gap_seconds=0.0,
        default_concurrency=1,
    )


class Client:
    """一个装好了真 BiliManager 的 TestClient。"""

    def __init__(self, tmp_path: Path, *, transport=None, runner=None, biliup: Path | None = None) -> None:
        self.tmp_path = tmp_path
        self.settings = make_settings(tmp_path, biliup=biliup)
        self.settings.ensure_directories()
        self.transport = transport if transport is not None else FakeBiliTransport()
        self.runner = runner if runner is not None else command_runner()
        self.engine = create_app_engine(self.settings)
        init_db(self.engine)
        self.bili = BiliManager(
            self.engine,
            self.settings,
            service=FakeYtDlpService(),
            broker=EventBroker(),
            transport=self.transport,
            runner=self.runner,
            sleeper=RecordingSleeper(),
        )
        write_login(self.settings)
        self.app = create_app(
            settings=self.settings,
            ytdlp_service=FakeYtDlpService(),
            bili_manager=self.bili,
            sanitize_environment_variables=False,
        )
        self.client = TestClient(self.app)
        # 进 ctx 才会跑 lifespan（建队列、把中断记录收敛），与真实启动一致。
        self.client.__enter__()

    def close(self) -> None:
        self.client.__exit__(None, None, None)

    # -- 造数据 ------------------------------------------------------------
    def seed_job(self, job_id: str, title: str, count: int, *, files: bool = True) -> list[dict]:
        rows: list[dict] = []
        with Session(self.engine) as session:
            session.add(
                Job(
                    id=job_id,
                    url=f"https://youtu.be/{job_id}",
                    title=title,
                    options_json="{}",
                    download_dir=str(self.settings.download_dir),
                )
            )
            for index in range(1, count + 1):
                path = self.settings.download_dir / f"{job_id}_{index}.mp4"
                if files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"\x00" * 32)
                session.add(
                    JobItem(
                        id=f"{job_id}-i{index}",
                        job_id=job_id,
                        source_url=f"https://youtu.be/{job_id}{index}",
                        title=f"{title} 第 {index} 集",
                        index=index,
                        status="succeeded",
                        output_path=str(path),
                    )
                )
                rows.append({"id": f"{job_id}-i{index}", "index": index, "title": f"{title} 第 {index} 集", "path": str(path)})
            session.commit()
        return rows

    def upload_status(self, upload_id: str) -> str | None:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            return None if row is None else row.status

    def wait_until_finished(self, upload_id: str, *, timeout: float = 10.0) -> str:
        """等这条稿件离开「在途」。

        **不能拿 `queue.empty()` 当完成信号**：worker 一 `get()` 走队列就空了，那时它还在跑。
        判据只能是数据库里的状态。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.upload_status(upload_id)
            if status is not None and status not in IN_FLIGHT_STATUSES:
                return status
            time.sleep(0.02)
        raise AssertionError(f"上传没有在 {timeout}s 内结束（当前状态 {self.upload_status(upload_id)}）")


def make_client(tmp_path: Path, **kwargs) -> Client:
    return Client(tmp_path, **kwargs)


@pytest.fixture
def client(tmp_path):
    made = make_client(tmp_path)
    try:
        yield made
    finally:
        made.close()


# ================================================================ 体检
def test_status_reports_ready_when_everything_is_in_place(client):
    response = client.client.get("/api/bilibili/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["biliup"]["found"] is True
    assert body["login"]["present"] is True
    assert body["partitions"] and body["limits"]["tag_item"] == 20


def test_status_can_skip_the_account_probe(client):
    """`probe_account=false` 只做离线体检 —— 没网络的环境里也要能看出「工具装没装」。"""
    response = client.client.get("/api/bilibili/status", params={"probe_account": "false"})
    assert response.status_code == 200
    assert response.json()["login"]["account_checked"] is False
    assert client.transport.called("/x/web-interface/nav") == 0


def test_status_reports_a_blocker_when_biliup_is_missing(tmp_path):
    made = make_client(tmp_path, biliup=tmp_path / "nope.exe")
    try:
        body = made.client.get("/api/bilibili/status").json()
        assert body["ready"] is False
        assert any("biliup" in item["message"] for item in body["blockers"])
    finally:
        made.close()


def test_repair_tool_returns_a_fresh_health_report(client):
    response = client.client.post("/api/bilibili/tools/repair")
    assert response.status_code == 200
    assert "biliup" in response.json()


# ================================================================ 扫码登录
def test_qrcode_returns_svg_text(client):
    """返回的是内联用的 SVG 文本，不是位图 —— 前端直接塞进页面。"""
    response = client.client.post("/api/bilibili/login/qrcode")
    assert response.status_code == 200
    body = response.json()
    assert body["qrcode_key"] == "qr-key-1"
    assert "<svg" in body["svg"]


def test_qrcode_reports_an_upstream_failure_as_502(tmp_path):
    """取不到二维码是我们的上游出了问题，不是 500（那会让用户以为应用坏了）。"""
    made = make_client(tmp_path, transport=FakeBiliTransport(qr_generate_error=RuntimeError("连不上")))
    try:
        response = made.client.post("/api/bilibili/login/qrcode")
        assert response.status_code == 502
        assert "登录二维码" in response.json()["detail"]
    finally:
        made.close()


def test_poll_passes_through_a_waiting_state_and_writes_nothing(tmp_path):
    """**只有 `confirmed` 才落盘。** 等待中就把凭据写下去，用户会看到一个半登录状态。"""
    made = make_client(tmp_path, transport=FakeBiliTransport(poll_code=86101))
    made.settings.bili_cookie_path.unlink()
    try:
        response = made.client.get("/api/bilibili/login/poll", params={"qrcode_key": "qr-key-1"})
        assert response.status_code == 200
        assert response.json()["state"] == "waiting"
        assert response.json()["logged_in"] is False
        assert not made.settings.bili_cookie_path.exists()
    finally:
        made.close()


def test_poll_writes_the_jar_only_on_confirmed(tmp_path):
    callback = "https://passport.biligame.com/crossDomain?" + urlencode(
        {"SESSDATA": "sess%2Cvalue", "bili_jct": "jct-value", "DedeUserID": "42"}
    )
    made = make_client(tmp_path, transport=FakeBiliTransport(poll_code=0, poll_url=callback))
    made.settings.bili_cookie_path.unlink()
    try:
        body = made.client.get("/api/bilibili/login/poll", params={"qrcode_key": "qr-key-1"}).json()
        assert body["state"] == "confirmed"
        assert body["logged_in"] is True
        assert body["account"]["mid"] == 42
        saved = json.loads(made.settings.bili_cookie_path.read_text(encoding="utf-8"))
        names = {item["name"]: item["value"] for item in saved["cookie_info"]["cookies"]}
        # `SESSDATA` 自带的 `%2C` 必须被解码 —— 不解码会写进去一个用不了的凭据。
        assert names["SESSDATA"] == "sess,value"
    finally:
        made.close()


def test_poll_requires_a_qrcode_key(client):
    assert client.client.get("/api/bilibili/login/poll").status_code == 422


def test_poll_reports_an_upstream_failure_as_502(tmp_path):
    made = make_client(tmp_path, transport=FakeBiliTransport(poll_error=RuntimeError("连不上")))
    try:
        response = made.client.get("/api/bilibili/login/poll", params={"qrcode_key": "k"})
        assert response.status_code == 502
        assert "扫码状态" in response.json()["detail"]
    finally:
        made.close()


# ================================================================ 导入 / 清除登录
def test_import_cookies_accepts_any_file_and_reports_what_it_read(tmp_path):
    """**这里不校验格式**：先落盘，再由体检说清楚「读出来了什么、缺哪项」。
    上传时拒收会让用户卡在「传不上去、也验不了」。"""
    made = make_client(tmp_path)
    try:
        payload = json.dumps({"cookie_info": {"cookies": [{"name": "SESSDATA", "value": "x"}]}})
        response = made.client.post("/api/bilibili/cookies", files={"file": ("c.json", payload, "application/json")})
        assert response.status_code == 200
        body = response.json()
        assert body["login"]["present"] is True
        assert body["login"]["missing_required"] == ["bili_jct"]
        assert body["ready"] is False
    finally:
        made.close()


def test_import_cookies_of_a_broken_file_still_returns_200(tmp_path):
    made = make_client(tmp_path)
    try:
        response = made.client.post("/api/bilibili/cookies", files={"file": ("c.json", b"not json at all", "application/json")})
        assert response.status_code == 200
        assert response.json()["login"]["present"] is False
    finally:
        made.close()


def test_clear_cookies_is_idempotent(client):
    first = client.client.delete("/api/bilibili/cookies")
    assert first.status_code == 200
    assert first.json()["login"]["present"] is False
    # 本来就没有也返回当前体检结论，而不是 404。
    second = client.client.delete("/api/bilibili/cookies")
    assert second.status_code == 200
    assert second.json()["login"]["present"] is False


def test_importing_drops_an_in_progress_qr_session(client):
    """换了登录态，那次扫码会话就作废 —— 否则「旧账号 cookie + 新账号扫码结果」会混在一起落盘。"""
    client.client.post("/api/bilibili/login/qrcode")
    session = client.bili.login_session()
    assert session is not None
    client.client.post("/api/bilibili/cookies", files={"file": ("c.json", b"{}", "application/json")})
    assert client.bili._login is None


# ================================================================ 预填
def test_prefill_gives_a_single_set_of_values(client):
    client.seed_job("job1", "单视频任务", 1)
    body = client.client.get("/api/bilibili/prefill", params={"job_id": "job1", "item_id": "job1-i1"}).json()
    assert body["kind"] == "single"
    assert body["metadata"]["title"] == "单视频任务 第 1 集"
    assert body["items"][0]["ready"] is True
    assert body["season_desc"] == ""


def test_prefill_gives_a_template_plus_a_preview_for_a_collection(client):
    client.seed_job("job2", "合集任务", 3)
    body = client.client.get("/api/bilibili/prefill", params={"job_id": "job2"}).json()
    assert body["kind"] == "collection"
    assert body["template"]["title"] == "{title}"
    assert len(body["items"]) == 3
    assert body["season_desc"]


def test_prefill_404s_for_an_unknown_job(client):
    assert client.client.get("/api/bilibili/prefill", params={"job_id": "nope"}).status_code == 404


def test_prefill_422s_without_a_job_id(client):
    assert client.client.get("/api/bilibili/prefill").status_code == 422


# ================================================================ 读回
def test_summary_only_lists_jobs_that_have_records(client):
    """没有记录的任务**不在这里** —— 界面默认就是「未上传」，不必知道这件事。"""
    client.seed_job("job1", "任务", 1)
    assert client.client.get("/api/bilibili/uploads/summary").json()["jobs"] == []


def test_job_uploads_returns_an_empty_view_for_a_job_without_records(client):
    client.seed_job("job1", "任务", 1)
    body = client.client.get("/api/bilibili/uploads", params={"job_id": "job1"}).json()
    assert body["job_id"] == "job1"
    assert body["badge"] == "none"
    assert body["uploads"] == []
    assert body["season"] is None


def test_job_uploads_404s_for_an_unknown_job(client):
    assert client.client.get("/api/bilibili/uploads", params={"job_id": "nope"}).status_code == 404


# ================================================================ 单视频上传
def single_payload(client, job_id: str = "job1", item_id: str = "job1-i1", **overrides) -> dict:
    payload = {
        "job_id": job_id,
        "item_id": item_id,
        "metadata": {"title": "单视频任务 第 1 集", "desc": "简介", "tags": "标签", "tid": "122"},
        "make_cover": False,
        "comment": "置顶评论",
    }
    payload.update(overrides)
    return payload


def test_create_upload_returns_the_whole_job_view(client):
    """界面只有一个图标，所以一次要拿到**整个任务**的全貌。"""
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client))
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "job1"
    assert len(body["uploads"]) == 1
    assert body["uploads"][0]["title"] == "单视频任务 第 1 集"


def test_create_upload_rejects_over_limit_metadata_with_400(client):
    """400：请求本身不对。而且问题要**带回来**，不是一句「参数错误」。"""
    client.seed_job("job1", "任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client, metadata={"title": "超" * 90, "desc": "", "tags": "", "tid": "122"}))
    assert response.status_code == 400
    assert "标题超出 10 个字" in response.json()["detail"]


def test_create_upload_rejects_an_over_limit_comment_with_400(client):
    client.seed_job("job1", "任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client, comment="评" * 1001))
    assert response.status_code == 400
    assert "评论超出" in response.json()["detail"]


def test_create_upload_rejects_an_invalid_partition_with_400(client):
    client.seed_job("job1", "任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client, metadata={"title": "t", "desc": "", "tags": "", "tid": "知识"}))
    assert response.status_code == 400
    assert "分区" in response.json()["detail"]


def test_create_upload_409s_when_the_video_file_is_missing(client):
    """**409 而不是 404**：条目在库里存在，只是产物还没就绪。"""
    client.seed_job("job1", "任务", 1, files=False)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client, item_id=None))
    assert response.status_code == 409


def test_create_upload_409s_when_biliup_is_missing(client):
    client.seed_job("job1", "任务", 1)
    client.settings.biliup_path = str(client.tmp_path / "nope.exe")
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "missing_biliup"
    assert response.json()["detail"]["advice"]


def test_create_upload_409s_when_not_logged_in(client):
    client.seed_job("job1", "任务", 1)
    client.settings.bili_cookie_path.unlink()
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "missing_cookie"


def test_create_upload_404s_for_an_unknown_item(client):
    client.seed_job("job1", "任务", 1)
    assert client.client.post("/api/bilibili/uploads", json=single_payload(client, item_id="nope")).status_code == 404


# ================================================================ 合集上传
def collection_payload(job_id: str = "job2", **overrides) -> dict:
    payload = {
        "job_id": job_id,
        "template": {"title": "{title}", "desc": "{job} 第 {index}/{count} 集", "tags": "合集标签", "tid": "122"},
        "season_title": "合集任务",
        "season_desc": "合集说明",
        "make_cover": False,
        "comment": "",
    }
    payload.update(overrides)
    return payload


def test_create_collection_returns_one_row_per_item_plus_a_season(client):
    client.seed_job("job2", "合集任务", 3)
    response = client.client.post("/api/bilibili/collections", json=collection_payload())
    assert response.status_code == 200
    body = response.json()
    assert len(body["uploads"]) == 3
    assert body["season"] is not None
    assert body["season"]["title"] == "合集任务"


def test_create_collection_rejects_a_one_video_job_with_400(client):
    """400 而不是 409：这不是「现在不能做」，是这个请求本身不该走合集流程。"""
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post("/api/bilibili/collections", json=collection_payload("job1"))
    assert response.status_code == 400
    assert "只" in response.json()["detail"] and "单视频" in response.json()["detail"]


def test_create_collection_rejects_an_over_limit_template_with_400(client):
    client.seed_job("job2", "合集任务", 3)
    response = client.client.post(
        "/api/bilibili/collections",
        json=collection_payload(template={"title": "{title}", "desc": "简" * 260, "tags": "标签", "tid": "122"}),
    )
    assert response.status_code == 400
    assert "第 1 条" in response.json()["detail"]


def test_create_collection_reports_the_titles_that_are_not_downloaded(client):
    """合集里有几十条时，先把「哪几条没准备好」一次说清，比让用户一条条试出来有用得多。"""
    client.seed_job("job2", "合集任务", 2, files=False)
    response = client.client.post("/api/bilibili/collections", json=collection_payload())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "missing_video"
    assert "2 条视频还没下载好" in response.json()["detail"]["message"]


def test_create_collection_falls_back_to_the_job_title(client):
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post("/api/bilibili/collections", json=collection_payload(season_title=""))
    assert response.status_code == 200
    assert response.json()["season"]["title"] == "合集任务"


def test_create_collection_404s_for_an_unknown_job(client):
    assert client.client.post("/api/bilibili/collections", json=collection_payload("nope")).status_code == 404


# ================================================================ 重试
def test_retry_upload_404s_for_an_unknown_record(client):
    response = client.client.post("/api/bilibili/uploads/nope/retry")
    assert response.status_code == 404
    assert "找不到" in response.json()["detail"]


def test_retry_upload_reports_none_for_a_published_record(client):
    """幂等：已经成功的稿件点重试不能再投一次。标题要与替身里的稿件对上，才会走到 published。"""
    client.seed_job("job1", "任务", 1)
    created = client.client.post(
        "/api/bilibili/uploads",
        json=single_payload(client, metadata={"title": "演示标题", "desc": "", "tags": "", "tid": "122"}),
    ).json()
    upload_id = created["uploads"][0]["id"]
    assert client.wait_until_finished(upload_id) == "published"

    response = client.client.post(f"/api/bilibili/uploads/{upload_id}/retry")
    assert response.status_code == 200
    assert response.json()["action"] == "none"
    assert "没有重复投稿" in response.json()["message"]


def test_retry_season_404s_for_an_unknown_record(client):
    response = client.client.post("/api/bilibili/collections/nope/group")
    assert response.status_code == 404
    assert "找不到" in response.json()["detail"]


def test_retry_season_returns_the_season_view(client):
    client.seed_job("job2", "合集任务", 2)
    body = client.client.post("/api/bilibili/collections", json=collection_payload()).json()
    season_id = body["season"]["id"]
    response = client.client.post(f"/api/bilibili/collections/{season_id}/group")
    assert response.status_code == 200
    assert response.json()["season"]["id"] == season_id
