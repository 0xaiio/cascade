"""字幕翻译的编排与 HTTP 链路：全是假 provider、真 DB、真文件。

重点钉三件事：
1. 后台分块跑完后**逐条对齐、原文未动**；
2. 人工修订不会被重跑覆盖；失败只补失败块；
3. 导出前校验会挡住错位/空译文，绝不产出「看起来合法但错了」的字幕。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path

from app.translation.provider import TranslationError, TranslationItem, TranslationReply
from test_api import make_client, seed_job

SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello world\n\n"
    "2\n00:00:03,000 --> 00:00:04,500\nSecond line\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\nThird line\n"
)


class ScriptedProvider:
    def __init__(
        self,
        *,
        fail: Sequence[int] = (),
        fail_calls: Sequence[int] = (),
        drop_one_once: bool = False,
    ) -> None:
        self.fail = set(fail)
        self.fail_calls = set(fail_calls)
        self.drop_one_once = drop_one_once
        self.calls: list[list[int]] = []

    def translate(self, items: Sequence[TranslationItem], *, context: str = "") -> list[TranslationReply]:
        indices = [item.index for item in items]
        self.calls.append(indices)
        if self.fail.intersection(indices) or len(self.calls) in self.fail_calls:
            raise TranslationError("本地模型暂时不可用")
        if self.drop_one_once and len(self.calls) == 1 and len(items) > 1:
            # 模拟本机 8B 偶发漏条：返回的编号是合法子集，但不完整。
            return [TranslationReply(index=item.index, text=f"译文{item.index + 1}") for item in items[:-1]]
        return [TranslationReply(index=item.index, text=f"译文{item.index + 1}") for item in items]


def setup_job(tmp_path: Path, provider: ScriptedProvider, model_server_ensurer=None, system_opener=None):
    client = make_client(
        tmp_path,
        translation_provider_factory=lambda: provider,
        model_server_ensurer=model_server_ensurer,
        system_opener=system_opener,
    )
    seed_job(tmp_path, "job-1", "任务", 1)
    saved = client.get("/api/jobs").json()[0]["items"][0]
    subtitle = Path(saved["output_path"]).with_suffix(".en.srt")
    subtitle.parent.mkdir(parents=True, exist_ok=True)
    subtitle.write_text(SRT, encoding="utf-8")
    client.post(
        "/api/jobs/job-1",
        json={
            "url": "https://youtu.be/x",
            "resolution": "1440p",
            "options": {},
        },
    ) if False else None  # 不新建任务，只更新磁盘产物路径（见下）
    from sqlmodel import Session
    from app.contracts.models import JobItem

    engine = client.app.state.db_engine
    with Session(engine) as session:
        item = session.get(JobItem, saved["id"])
        item.subtitle_path = str(subtitle)
        item.subtitle_language = "en"
        item.subtitle_source = "human"
        session.add(item)
        session.commit()
    return client, subtitle


def wait_state(client, translation_id: str, states: set[str], timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/translation/jobs/{translation_id}").json()
        if body["state"] in states:
            return body
        time.sleep(0.02)
    raise AssertionError(f"翻译没有进入 {states}：{body['state']}")


def test_create_translates_every_cue_and_keeps_the_source_untouched(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    client, subtitle = setup_job(tmp_path, provider)
    before = subtitle.read_bytes()

    created = client.post("/api/translation/jobs", json={"job_id": "job-1"})
    assert created.status_code == 202
    translation_id = created.json()["id"]
    done = wait_state(client, translation_id, {"succeeded"})

    assert done["total"] == 3 and done["translated"] == 3 and done["failed"] == 0
    assert [cue["translation"] for cue in done["cues"]] == ["译文1", "译文2", "译文3"]
    assert [cue["source"] for cue in done["cues"]] == ["Hello world", "Second line", "Third line"]
    assert subtitle.read_bytes() == before  # 原文件一个字节都没动
    assert done["source_sha256"]


def test_missing_cue_is_retried_alone_instead_of_failing_the_chunk(tmp_path: Path) -> None:
    """本机模型偶发漏条：合法子集要补问一次，不能整块判失败、更不能猜一条填上。"""
    provider = ScriptedProvider(drop_one_once=True)
    client, _ = setup_job(tmp_path, provider)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    done = wait_state(client, created["id"], {"succeeded", "failed"})
    assert done["state"] == "succeeded"
    assert done["failed"] == 0
    assert [cue["translation"] for cue in done["cues"]] == ["译文1", "译文2", "译文3"]
    assert provider.calls == [[0, 1, 2], [2]]


def test_chunks_are_small_enough_for_the_local_model(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    client, _ = setup_job(tmp_path, provider)
    # 多造几条，确保确实分了多块。
    from sqlmodel import Session
    from app.translation.store import load_cues
    from app.contracts.models import JobItem

    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert provider.calls and all(len(call) <= 8 for call in provider.calls)


def test_timestamps_are_copied_not_recomputed(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    done = wait_state(client, created["id"], {"succeeded"})
    assert [(cue["start"], cue["end"]) for cue in done["cues"]] == [
        (1.0, 2.0),
        (3.0, 4.5),
        (5.0, 6.0),
    ]


def test_human_edit_survives_a_retry_and_is_marked(tmp_path: Path) -> None:
    provider = ScriptedProvider(fail_calls=[1])  # 第一次整块失败，重试时全部成功
    client, _ = setup_job(tmp_path, provider)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    failed = wait_state(client, created["id"], {"failed"})
    assert failed["failed"] == 3
    assert [cue["state"] for cue in failed["cues"]] == ["failed", "failed", "failed"]

    edited = client.patch(
        f"/api/translation/jobs/{created['id']}/cues/0", json={"text": "我自己改的译文"}
    ).json()
    assert edited["cues"][0]["edited"] is True

    calls_before = len(provider.calls)
    retried = client.post(f"/api/translation/jobs/{created['id']}/retry")
    assert retried.status_code == 202
    wait_state(client, created["id"], {"succeeded"})
    body = client.get(f"/api/translation/jobs/{created['id']}").json()
    assert body["cues"][0]["translation"] == "我自己改的译文"
    assert body["cues"][1]["translation"] == "译文2"
    # 重跑只补失败块，没把已译的两条再发一次模型。
    # 人工修订的那条已经锁定，重试连请求都不发它。
    assert provider.calls[calls_before:] == [[1, 2]]


def test_cancel_stops_before_the_next_chunk(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    client, _ = setup_job(tmp_path, provider)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    assert client.post(f"/api/translation/jobs/{created['id']}/cancel").status_code == 200
    final = wait_state(client, created["id"], {"cancelled", "succeeded"})
    assert final["state"] in {"cancelled", "succeeded"}


def test_export_writes_srt_and_bilingual_without_touching_the_source(tmp_path: Path) -> None:
    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})

    exported = client.post(f"/api/translation/jobs/{created['id']}/export")
    assert exported.status_code == 200
    body = exported.json()
    assert body["cue_count"] == 3
    srt = Path(body["srt_path"])
    bilingual = Path(body["bilingual_path"])
    assert srt.is_file() and bilingual.is_file()
    assert "00:00:01,000 --> 00:00:02,000" in srt.read_text(encoding="utf-8-sig")
    assert "译文1" in srt.read_text(encoding="utf-8-sig")
    assert "Hello world" in bilingual.read_text(encoding="utf-8-sig")
    assert "译文1" in bilingual.read_text(encoding="utf-8-sig")
    assert srt != subtitle and subtitle.is_file()


def test_export_refuses_an_untranslated_cue(tmp_path: Path) -> None:
    provider = ScriptedProvider(fail=[0, 1, 2])
    client, _ = setup_job(tmp_path, provider)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"failed"})
    response = client.post(f"/api/translation/jobs/{created['id']}/export")
    assert response.status_code == 409
    assert "译文为空" in response.json()["detail"]


def test_delete_removes_the_record_and_the_json_but_not_the_source(tmp_path: Path) -> None:
    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.delete(f"/api/translation/jobs/{created['id']}").status_code == 204
    assert client.get(f"/api/translation/jobs/{created['id']}").status_code == 404
    assert subtitle.is_file()


def test_retry_when_nothing_failed_is_refused(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(f"/api/translation/jobs/{created['id']}/retry").status_code == 409


def test_progress_events_are_published(tmp_path: Path) -> None:
    import asyncio

    client, _ = setup_job(tmp_path, ScriptedProvider())
    broker = client.app.state.event_broker
    published: list[dict] = []

    async def record(payload: dict) -> None:
        published.append(payload)

    original = broker.publish
    broker.publish = record  # type: ignore[method-assign]
    try:
        created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
        wait_state(client, created["id"], {"succeeded"})
        time.sleep(0.1)
        types = [message.get("type") for message in published]
        assert "subtitle_translation_progress" in types
        assert "subtitle_translation_finished" in types
    finally:
        broker.publish = original  # type: ignore[method-assign]


# ---------------------------------------------------------------- B站 双轨
def test_single_upload_can_attach_both_original_and_translated_tracks(tmp_path: Path) -> None:
    """原轨 en 与译轨 zh 是两条 CC 字幕：同一稿件并存，观众可在播放器切换/关闭。"""
    from app.bilibili.bili_meta import default_single
    from app.bilibili.bili_upload import UploadContext, UploadTarget, run_single
    from bili_fakes import FakeBiliTransport, RecordingSleeper, command_runner, write_video

    transport = FakeBiliTransport(
        archives=[{"aid": 111, "bvid": "BV1xx", "title": "演示标题", "state": 0}]
    )
    translated = tmp_path / "a.zh.srt"
    translated.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
    target = UploadTarget(
        1,
        "演示标题",
        write_video(tmp_path / "a.mp4"),
        subtitle_path=tmp_path / "a.en.srt",
        subtitle_language="en",
        translated_subtitle_path=translated,
    )
    (tmp_path / "a.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8"
    )
    context = UploadContext(
        biliup_exe="biliup.exe",
        cookie_path=tmp_path / "cookies.json",
        jar={"SESSDATA": "s", "bili_jct": "jct", "DedeUserID": "42"},
        transport=transport,
        runner=command_runner(),
        sleeper=RecordingSleeper(),
        verify_delay=0.0,
    )
    result = run_single(context, target, default_single(job_title="任务", item_title="演示标题", source_url="https://youtu.be/x"))

    assert result.status == "published"
    assert {entry["lan"] for entry in transport.subtitles} == {"en", "zh"}
    assert all(entry["oid"] == 7001 for entry in transport.subtitles)  # 都挂分P，不是 aid
    assert result.subtitle_lan == "en" and result.subtitle_state == "posted"
    assert result.translated_subtitle_lan == "zh" and result.translated_subtitle_state == "posted"


def test_upload_bili_endpoint_adds_only_the_zh_track(tmp_path: Path) -> None:
    """翻译完成后点「上传中文字幕」：只补 zh 轨，绝不重跑投稿命令。"""
    from sqlmodel import Session

    from app.bilibili.bili_read_model import read_upload
    from app.contracts.models import BiliUpload, BiliUploadStatus, utc_now

    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(f"/api/translation/jobs/{created['id']}/export").status_code == 200

    engine = client.app.state.db_engine
    with Session(engine) as session:
        row = BiliUpload(
            id="upload-1",
            job_id="job-1",
            item_id=created["item_id"],
            title="演示",
            status=BiliUploadStatus.published.value,
            aid=111,
            bvid="BV1xx",
            cid=7001,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(row)
        session.commit()

    manager = client.app.state.api_context.bili
    calls: list[tuple[str, object]] = []

    def fake_attach(upload_id: str, path: Path):
        calls.append((upload_id, path))
        with Session(engine) as session:
            return read_upload(session.get(BiliUpload, upload_id), index=1)

    manager.attach_translation = fake_attach  # type: ignore[method-assign]
    response = client.post(f"/api/translation/jobs/{created['id']}/upload-bili")
    assert response.status_code == 200
    exported = client.get(f"/api/translation/jobs/{created['id']}").json()
    assert calls == [("upload-1", Path(exported["output_path"]))]
    assert response.json()["aid"] == 111 and response.json()["bvid"] == "BV1xx"


def test_upload_bili_endpoint_refuses_before_the_video_is_published(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(f"/api/translation/jobs/{created['id']}/export").status_code == 200
    response = client.post(f"/api/translation/jobs/{created['id']}/upload-bili")
    assert response.status_code == 409
    assert "找不到已投稿成功" in response.json()["detail"]


# ---------------------------------------------------------------- 双语模式
def test_bilingual_mode_exports_one_cue_with_two_lines_and_bcc(tmp_path: Path) -> None:
    """默认仅中文；切到双语后导出三件套，两行共用同一条时间轴。"""
    import json

    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    assert created["display_mode"] == "zh"
    wait_state(client, created["id"], {"succeeded"})

    changed = client.patch(f"/api/translation/jobs/{created['id']}/mode", json={"mode": "bilingual"})
    assert changed.status_code == 200 and changed.json()["display_mode"] == "bilingual"
    assert client.patch(
        f"/api/translation/jobs/{created['id']}/mode", json={"mode": "ai-zh"}
    ).status_code == 422

    exported = client.post(f"/api/translation/jobs/{created['id']}/export")
    assert exported.status_code == 200
    body = exported.json()
    assert body["bcc_path"] and Path(body["bcc_path"]).is_file()
    upload_srt = Path(body["srt_path"])
    blocks = [block for block in upload_srt.read_text(encoding="utf-8-sig").split("\n\n") if block.strip()]
    assert len(blocks) == 3
    assert blocks[0].splitlines()[2:] == ["Hello world", "译文1"]
    payload = json.loads(Path(body["bcc_path"]).read_text(encoding="utf-8"))
    assert len(payload["body"]) == 3
    assert payload["body"][0]["content"] == "Hello world\n译文1"
    assert payload["body"][0]["from"] == 1.0 and payload["body"][0]["to"] == 2.0
    assert subtitle.read_text(encoding="utf-8").startswith("1\n")


def test_switching_back_to_zh_writes_the_plain_chinese_track(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    client.patch(f"/api/translation/jobs/{created['id']}/mode", json={"mode": "bilingual"})
    bilingual = client.post(f"/api/translation/jobs/{created['id']}/export").json()
    assert bilingual["bcc_path"]

    client.patch(f"/api/translation/jobs/{created['id']}/mode", json={"mode": "zh"})
    plain = client.post(f"/api/translation/jobs/{created['id']}/export").json()
    assert plain["bcc_path"] is None
    assert Path(plain["srt_path"]).name.endswith(".zh.srt")
    assert Path(plain["srt_path"]).read_text(encoding="utf-8-sig").count("译文1") == 1


def test_bilingual_upload_reaches_bili_as_two_lines_in_one_cue(tmp_path: Path) -> None:
    """导出的双语 SRT 经真实 upload_subtitle 读回后，B站 表单里的 content 必须是两行、一个时间。"""
    import json

    from app.bilibili.bili_upload import UploadContext, attach_translated_subtitle
    from bili_fakes import FakeBiliTransport

    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    client.patch(f"/api/translation/jobs/{created['id']}/mode", json={"mode": "bilingual"})
    exported = client.post(f"/api/translation/jobs/{created['id']}/export").json()

    transport = FakeBiliTransport()
    context = UploadContext(
        biliup_exe="biliup.exe",
        cookie_path=tmp_path / "cookies.json",
        jar={"SESSDATA": "s", "bili_jct": "jct", "DedeUserID": "42"},
        transport=transport,
    )
    state, message, count = attach_translated_subtitle(
        context, aid=111, bvid="BV1xx", cid=7001, path=Path(exported["srt_path"]), lan="zh"
    )
    assert state == "posted" and count == 3
    assert transport.subtitles, message
    form = transport.subtitles[0]
    assert form["lan"] == "zh" and form["oid"] == 7001 and form["submit"] == "true"
    body = json.loads(form["data"])["body"]
    assert len(body) == 3
    assert body[0]["content"] == "Hello world\n译文1"
    assert body[0]["from"] == 1.0 and body[0]["to"] == 2.0


def test_bilingual_upload_reply_names_the_mode_and_overwrite(tmp_path: Path) -> None:
    """回执必须说清是同屏双语、同 lan 覆盖，避免用户以为会多一条轨。"""
    from sqlmodel import Session

    from app.bilibili.bili_read_model import read_upload
    from app.contracts.models import BiliUpload, BiliUploadStatus, utc_now

    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    client.patch(f"/api/translation/jobs/{created['id']}/mode", json={"mode": "bilingual"})
    assert client.post(f"/api/translation/jobs/{created['id']}/export").status_code == 200

    engine = client.app.state.db_engine
    with Session(engine) as session:
        session.add(
            BiliUpload(
                id="upload-bi", job_id="job-1", item_id=created["item_id"], title="演示",
                status=BiliUploadStatus.published.value, aid=111, bvid="BV1xx", cid=7001,
                created_at=utc_now(), updated_at=utc_now(),
            )
        )
        session.commit()

    manager = client.app.state.api_context.bili

    def fake_attach(upload_id: str, path: Path):
        with Session(engine) as session:
            row = session.get(BiliUpload, upload_id)
            row.translated_subtitle_state = "posted"
            row.translated_subtitle_lan = "zh"
            row.translated_subtitle_message = "CC 字幕已上传（3 条，zh）"
            session.add(row)
            session.commit()
            return read_upload(row, index=1)

    manager.attach_translation = fake_attach  # type: ignore[method-assign]
    body = client.post(f"/api/translation/jobs/{created['id']}/upload-bili").json()
    assert "中英双语" in body["translated_subtitle_message"]
    assert "覆盖" in body["translated_subtitle_message"]


# ---------------------------------------------------------------- 本机模型服务
def test_unavailable_model_server_gives_one_actionable_conclusion(tmp_path: Path) -> None:
    """服务没起时**一条模型请求都不发**，每条 cue 拿到同一句可操作结论，而不是 WinError。"""
    import json

    from app.translation.model_server import ModelServerError

    def cannot_start() -> str | None:
        raise ModelServerError("本机找不到可用的 GGUF 模型文件。请把模型放进 D:/models")

    provider = ScriptedProvider()
    client, _ = setup_job(tmp_path, provider, model_server_ensurer=cannot_start)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    done = wait_state(client, created["id"], {"failed"})

    assert provider.calls == []
    assert "GGUF" in done["error"]
    assert all(cue["state"] == "failed" and "GGUF" in cue["error"] for cue in done["cues"])
    assert "10061" not in json.dumps(done, ensure_ascii=False)


def test_model_server_is_ensured_before_any_translation_request(tmp_path: Path) -> None:
    events: list[str] = []

    def ensure() -> str | None:
        events.append("ensure")
        return "已启动本机模型服务（PID 4242）"

    provider = ScriptedProvider()
    client, _ = setup_job(tmp_path, provider, model_server_ensurer=ensure)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    done = wait_state(client, created["id"], {"succeeded"})
    assert events == ["ensure"]
    assert done["translated"] == 3 and provider.calls


# ---------------------------------------------------------------- 状态复用与入口
def test_job_item_reports_the_translated_subtitle_path(tmp_path: Path) -> None:
    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(f"/api/translation/jobs/{created['id']}/export").status_code == 200
    exported = client.get(f"/api/translation/jobs/{created['id']}").json()["output_path"]

    item = client.get("/api/jobs").json()[0]["items"][0]
    assert item["subtitle_path"] == str(subtitle)
    assert item["translated_subtitle_path"] == exported

    Path(exported).unlink()
    item = client.get("/api/jobs").json()[0]["items"][0]
    assert item["translated_subtitle_path"] is None  # 文件没了就不给可点入口


def test_lookup_reuses_a_finished_translation_without_a_new_request(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    client, _ = setup_job(tmp_path, provider)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    calls = len(provider.calls)

    looked = client.get("/api/translation/lookup", params={"job_id": "job-1", "item_id": created["item_id"]})
    assert looked.status_code == 200
    assert looked.json()["id"] == created["id"]
    assert looked.json()["state"] == "succeeded"
    assert len(provider.calls) == calls  # 查历史不算翻译


def test_lookup_without_any_translation_is_404(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    assert client.get("/api/translation/lookup", params={"job_id": "job-1"}).status_code == 404


def test_open_translated_subtitle_hands_the_generated_file_to_the_os(tmp_path: Path) -> None:
    opened: list[Path] = []
    client, _ = setup_job(tmp_path, ScriptedProvider(), system_opener=lambda p: opened.append(Path(p)))
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    client.post(f"/api/translation/jobs/{created['id']}/export")

    response = client.post(
        "/api/translation/open", params={"job_id": "job-1", "item_id": created["item_id"]}
    )
    assert response.status_code == 204
    expected = client.get(f"/api/translation/jobs/{created['id']}").json()["output_path"]
    assert opened == [Path(expected)]


def test_open_translated_subtitle_without_export_is_409(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(
        "/api/translation/open", params={"job_id": "job-1", "item_id": created["item_id"]}
    ).status_code == 409


# ---------------------------------------------------------------- 状态复用与入口
def test_job_item_reports_the_translated_subtitle_path(tmp_path: Path) -> None:
    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(f"/api/translation/jobs/{created['id']}/export").status_code == 200
    exported = client.get(f"/api/translation/jobs/{created['id']}").json()["output_path"]

    item = client.get("/api/jobs").json()[0]["items"][0]
    assert item["subtitle_path"] == str(subtitle)
    assert item["translated_subtitle_path"] == exported

    Path(exported).unlink()
    item = client.get("/api/jobs").json()[0]["items"][0]
    assert item["translated_subtitle_path"] is None  # 文件没了就不给可点入口


def test_lookup_reuses_a_finished_translation_without_a_new_request(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    client, _ = setup_job(tmp_path, provider)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    calls = len(provider.calls)

    looked = client.get("/api/translation/lookup", params={"job_id": "job-1", "item_id": created["item_id"]})
    assert looked.status_code == 200
    assert looked.json()["id"] == created["id"]
    assert looked.json()["state"] == "succeeded"
    assert len(provider.calls) == calls  # 查历史不算翻译


def test_lookup_without_any_translation_is_404(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    assert client.get("/api/translation/lookup", params={"job_id": "job-1"}).status_code == 404


def test_open_translated_subtitle_hands_the_generated_file_to_the_os(tmp_path: Path) -> None:
    opened: list[Path] = []
    client, _ = setup_job(tmp_path, ScriptedProvider(), system_opener=lambda p: opened.append(Path(p)))
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    client.post(f"/api/translation/jobs/{created['id']}/export")

    response = client.post(
        "/api/translation/open", params={"job_id": "job-1", "item_id": created["item_id"]}
    )
    assert response.status_code == 204
    expected = client.get(f"/api/translation/jobs/{created['id']}").json()["output_path"]
    assert opened == [Path(expected)]


def test_open_translated_subtitle_without_export_is_409(tmp_path: Path) -> None:
    client, _ = setup_job(tmp_path, ScriptedProvider())
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    wait_state(client, created["id"], {"succeeded"})
    assert client.post(
        "/api/translation/open", params={"job_id": "job-1", "item_id": created["item_id"]}
    ).status_code == 409
