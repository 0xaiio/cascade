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
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.bilibili.bili_auth import TvToken, write_cookie_file
from app.bilibili.bili_bin import is_pe
from app.bilibili.bili_manager import BiliManager
from app.bilibili.bili_meta import COPYRIGHT_REPRINT, COPYRIGHT_SELF
from app.contracts.config import AppSettings
from app.infra.db import create_app_engine, init_db
from app.infra.events import EventBroker
from app.main import create_app
from app.contracts.models import BiliUpload, Job, JobItem
from app.bilibili.bili_read_model import IN_FLIGHT_STATUSES
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


def test_prefill_says_it_is_not_reusing_anything_by_default(client):
    """没投过就**没有**复用 —— `reused` 说 `False`，界面才不会提示一句假话。"""
    client.seed_job("job1", "单视频任务", 1)
    body = client.client.get("/api/bilibili/prefill", params={"job_id": "job1", "item_id": "job1-i1"}).json()
    assert body["reused"] is False
    assert body["season_title"] == ""


def test_prefill_reuses_the_last_submission_over_http(client):
    """投过一次之后，预填回来的就是**上一次提交的那一份**，而不是按视频标题现算的默认值。

    这条走完整条 HTTP 链（路由 → manager → 库），钉的是「复用」这件事在接口层真的成立 ——
    单元测试只验到 `build_prefill`，中间任何一层把字段吞了就这里红。
    """
    client.seed_job("job1", "单视频任务", 1)
    payload = single_payload(
        client,
        metadata={
            "title": "上次写的标题",
            "desc": "上次写的简介",
            "tags": "上次,标签",
            "tid": "207",
            "copyright": COPYRIGHT_SELF,
            "source": "",
        },
        make_cover=False,
        comment="上次的置顶评论",
    )
    assert client.client.post("/api/bilibili/uploads", json=payload).status_code == 200

    body = client.client.get("/api/bilibili/prefill", params={"job_id": "job1", "item_id": "job1-i1"}).json()
    assert body["reused"] is True
    assert body["metadata"]["title"] == "上次写的标题"
    assert body["metadata"]["desc"] == "上次写的简介"
    assert body["metadata"]["tags"] == "上次,标签"
    assert body["metadata"]["tid"] == "207"
    assert body["metadata"]["copyright"] == COPYRIGHT_SELF
    assert body["comment"] == "上次的置顶评论"
    assert body["make_cover"] is False


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


def test_upload_timestamps_carry_a_timezone(client):
    """跨接口的时间必须自带偏移量（ai/bug-fix/020）。

    SQLite 不存时区，读回来的是**naive 的 UTC 墙上时间**；原样发出去的话，
    前端 `Date.parse` 会按本地时间解释它 —— 本机 UTC+8，于是 Ep 125 那条
    真实 52 秒的上传在界面上显示成「已提交 8:00:52」。

    判据是**串的形状**（末尾有 `Z`），不是「值对不对」：本机就在 UTC+8，
    一个测试进程里「算出来的时刻」与「解析出来的时刻」永远一致，验不出偏差；
    只有把「这个串有没有偏移量」钉住，才拦得住下一次有人忘了补。
    """
    client.seed_job("job1", "任务", 1)
    items = client.seed_job("job2", "任务", 1)
    client.client.post("/api/bilibili/uploads", json=single_payload(client))
    # 把状态推到等审核，`started_at` / `finished_at` 才都有值。
    with Session(client.engine) as session:
        row = session.exec(select(BiliUpload).where(BiliUpload.job_id == "job1")).one()
        row.status = "reviewing"
        row.started_at = datetime(2026, 10, 21, 10, 29, 38, 727960)  # 刻意不带 tzinfo
        row.finished_at = datetime(2026, 10, 21, 10, 30, 31, 280768)
        session.add(row)
        session.commit()

    body = client.client.get("/api/bilibili/uploads", params={"job_id": "job1"}).json()
    upload = body["uploads"][0]
    assert upload["started_at"] == "2026-10-21T10:29:38.727960Z"
    assert upload["finished_at"] == "2026-10-21T10:30:31.280768Z"
    # 时长仍由服务端算（两端同一个约定，差值与时区无关）—— 本条真实样本是 52 秒。
    assert upload["elapsed_seconds"] == 52
    assert items  # 另一个任务也被建了出来（保持本用例只关心时间形状）


# ================================================================ 单视频上传
def single_payload(client, job_id: str = "job1", item_id: str = "job1-i1", **overrides) -> dict:
    payload = {
        "job_id": job_id,
        "item_id": item_id,
        "metadata": {
            "title": "单视频任务 第 1 集",
            "desc": "简介",
            "tags": "标签",
            "tid": "122",
            # 界面一定会回传这两项（表单默认选中「转载」并把该视频的原始链接预填进去）。
            # 不带上它们，服务端会按「转载没填来源」拒掉 —— 那是**对的**行为（见 F16）。
            "copyright": COPYRIGHT_REPRINT,
            "source": "https://youtu.be/demo",
        },
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
        "template": {
            "title": "{title}",
            "desc": "{job} 第 {index}/{count} 集",
            "tags": "合集标签",
            "tid": "122",
            # 模板只带版权，**不带共用来源**：每条视频各用自己的原始链接（服务端逐条填）。
            "copyright": COPYRIGHT_REPRINT,
        },
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


# ================================================================ 归集方式
def test_list_seasons_returns_the_account_collections(client):
    client.transport.seasons = [
        {"id": 501, "title": "C++ Weekly", "intro": "每周一更", "cover": "http://c/1.jpg", "total": 22},
        {"id": 502, "title": "数据库系统讲义"},
    ]
    response = client.client.get("/api/bilibili/seasons")
    assert response.status_code == 200
    body = response.json()
    assert [item["season_id"] for item in body["seasons"]] == [501, 502]
    assert body["seasons"][0]["desc"] == "每周一更"
    assert body["seasons"][0]["total"] == 22
    # 缺字段的那条**仍然可选**，只是简介与条数是空的。
    assert body["seasons"][1]["desc"] == "" and body["seasons"][1]["total"] == 0
    assert body["error"] == ""


def test_list_seasons_reports_the_login_problem_instead_of_faking_an_empty_list(client):
    """未登录时返回 200 + 原因，而不是 401，也**不是**一个空列表。

    空列表在界面上会显示成「这个账号还没有合集」，用户于是跑去新建一个重复的 ——
    「查不到」与「真的没有」必须是两句话。
    """
    client.client.delete("/api/bilibili/cookies")
    response = client.client.get("/api/bilibili/seasons")
    assert response.status_code == 200
    body = response.json()
    assert body["seasons"] == []
    assert "登录" in body["error"]
    assert body["advice"]


def test_list_seasons_returns_an_empty_list_for_an_account_without_any(client):
    client.transport.seasons = []
    body = client.client.get("/api/bilibili/seasons").json()
    assert body["seasons"] == []
    # 没有合集**不是错**：`error` 必须留空，界面才知道该说「还没有」而不是「读不到」。
    assert body["error"] == ""


def test_join_an_existing_collection_without_an_id_is_400(client):
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post(
        "/api/bilibili/collections", json=collection_payload(group_mode="existing")
    )
    assert response.status_code == 400
    assert "没有选具体是哪个合集" in response.json()["detail"]


def test_a_new_collection_must_not_carry_a_season_id(client):
    """三条矛盾之一。静默取其一会让「新建」变成往老合集里塞视频，而界面看不出来。"""
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post(
        "/api/bilibili/collections", json=collection_payload(group_mode="new", season_id=4242)
    )
    assert response.status_code == 400
    assert "不该带已有合集的编号" in response.json()["detail"]


def test_no_collection_must_not_carry_a_season_id(client):
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post(
        "/api/bilibili/collections", json=collection_payload(group_mode="none", season_id=4242)
    )
    assert response.status_code == 400
    assert "不该指定合集" in response.json()["detail"]


def test_an_unknown_group_mode_is_400_not_silently_corrected(client):
    """非法值要报错。回退到 `new` 看着更「宽容」，但那是替用户决定了他没选的事。"""
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post(
        "/api/bilibili/collections", json=collection_payload(group_mode="随便写的")
    )
    assert response.status_code == 400
    assert "归集方式" in response.json()["detail"]


def test_a_collection_left_ungrouped_lands_in_a_terminal_state(client):
    """`skipped` 必须是终态：留 `pending` 会让 `_maybe_group` 照样去建合集。"""
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post("/api/bilibili/collections", json=collection_payload(group_mode="none"))
    assert response.status_code == 200
    season = response.json()["season"]
    assert season["status"] == "skipped"
    assert season["status_text"] != "状态未知"
    assert client.transport.called("/season/add") == 0


def test_joining_an_existing_collection_pins_its_id_without_building_a_new_one(client):
    client.seed_job("job2", "合集任务", 2)
    response = client.client.post(
        "/api/bilibili/collections",
        json=collection_payload(group_mode="existing", season_id=7788, season_title="这个标题不该被用上"),
    )
    assert response.status_code == 200
    assert response.json()["season"]["season_id"] == 7788
    assert client.transport.called("/season/add") == 0


# ---------------------------------------------------------------- 单视频的归集方式
#
# 单视频与合集共用同一组字段与同一份校验，所以这里只钉**两者口径不同**的地方：
# 默认值（`none` vs `new`）与「不归集时留不留记录」。


def test_single_upload_defaults_to_no_grouping_and_stores_no_season(client):
    """不传 `group_mode` 时必须**什么都不做**。

    改动前单视频从不进合集，所以默认值只能是 `none`。默认成 `new` 会让此后
    每条单视频投稿都在账号里多出一个只含它自己的合集 —— 而且不会有任何报错。
    """
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client))
    assert response.status_code == 200
    body = response.json()
    assert body["season"] is None
    assert body["uploads"][0]["status"] == "queued"
    assert client.transport.called("/season/add") == 0


def test_single_upload_can_join_an_existing_collection(client):
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post(
        "/api/bilibili/uploads",
        json=single_payload(client, group_mode="existing", season_id=7788),
    )
    assert response.status_code == 200
    assert response.json()["season"]["season_id"] == 7788
    assert client.transport.called("/season/add") == 0


def test_single_upload_creates_a_collection_when_asked(client):
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post(
        "/api/bilibili/uploads",
        json=single_payload(client, group_mode="new", season_title="我自己的合集"),
    )
    assert response.status_code == 200
    season = response.json()["season"]
    assert season["title"] == "我自己的合集"
    # `new` 时编号必须**还没有**：预置任何值都会让归集跳过建合集，
    # 然后对着一个不存在的编号加视频。
    assert season["season_id"] is None


def test_single_upload_falls_back_to_the_job_title_for_a_new_collection(client):
    """留空用任务名兜底 —— 与合集那边同一个口径，否则空标题会被 B站 打回来。"""
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client, group_mode="new"))
    assert response.status_code == 200
    assert response.json()["season"]["title"] == "单视频任务"


def test_single_upload_rejects_an_unknown_group_mode_with_400(client):
    """校验看的是**原始输入**：先归一再校验，非法值会被修成 `new` 而后静静执行。"""
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post("/api/bilibili/uploads", json=single_payload(client, group_mode="随便写的"))
    assert response.status_code == 400
    assert "归集方式" in response.json()["detail"]


def test_single_upload_rejects_existing_without_a_season_id_with_400(client):
    client.seed_job("job1", "单视频任务", 1)
    response = client.client.post(
        "/api/bilibili/uploads", json=single_payload(client, group_mode="existing")
    )
    assert response.status_code == 400
    assert "没有选具体是哪个合集" in response.json()["detail"]


def test_prefill_reports_the_default_group_mode_per_kind(client):
    """默认值由服务端给，不让前端按 `kind` 推 —— 推两份迟早会漂移。"""
    client.seed_job("job1", "单视频任务", 1)
    single = client.client.get("/api/bilibili/prefill", params={"job_id": "job1"}).json()
    assert single["kind"] == "single"
    assert single["default_group_mode"] == "none"

    client.seed_job("job2", "合集任务", 2)
    collection = client.client.get("/api/bilibili/prefill", params={"job_id": "job2"}).json()
    assert collection["kind"] == "collection"
    assert collection["default_group_mode"] == "new"


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
        json=single_payload(
            client,
            metadata={
                "title": "演示标题",
                "desc": "",
                "tags": "",
                "tid": "122",
                "copyright": COPYRIGHT_REPRINT,
                "source": "https://youtu.be/demo",
            },
        ),
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
    # `action` 是「这一次到底做了什么」，界面按它选配色（waiting 与 failed 必须分得开）。
    assert response.json()["action"] in {"grouped", "failed", "waiting"}
