"""字幕翻译 / 双语 / 上传闭环的回归取证用例。

每条用例都先写「用户能看到的现象」，再去钉实现；不是给既有函数补覆盖率。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

import pytest

from app.contracts.models import BiliUpload, BiliUploadStatus, JobItem, utc_now
from app.translation.align import AlignedCue, Cue
from app.translation.provider import TranslationError
from app.translation.render import export_bilingual_files, render_bilingual_srt
from test_translation_api import ScriptedProvider, setup_job, wait_state


def _relocate_subtitle_into_tmp(tmp_path: Path, client, subtitle: Path) -> Path:
    """`setup_job` 的 read model 给的是相对路径，导出会落到 cwd。

    测试只把字幕挪进自己的 tmp 目录并改 DB 指向它，避免跑一次用例就在 `backend/`
    留下 `1.en.zh.srt` 这类产物。
    """
    target = tmp_path / "captions" / "a.en.srt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subtitle.read_bytes())
    subtitle.unlink(missing_ok=True)
    with Session(client.app.state.db_engine) as session:
        item = session.exec(select(JobItem).where(JobItem.job_id == "job-1")).first()
        assert item is not None
        item.subtitle_path = str(target)
        session.add(item)
        session.commit()
    return target


def _seed_published_upload(tmp_path: Path, client, item_id: str) -> None:
    engine = client.app.state.db_engine
    with Session(engine) as session:
        session.add(
            BiliUpload(
                id="upload-1",
                job_id="job-1",
                item_id=item_id,
                title="演示",
                status=BiliUploadStatus.published.value,
                aid=111,
                bvid="BV1xx",
                cid=7001,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()


def test_editing_a_cue_then_uploading_must_not_send_the_stale_export(tmp_path: Path) -> None:
    """「导出 → 改一条译文 → 直接上传」挂到 B站 的必须是**改之后**的文本。

    `edit()` 只改逐条 JSON，不重写 `row.output_path` 指向的 SRT；而 `upload-bili`
    直接把这个路径交给 `attach_translation`。于是用户看到「已上传」，
    平台上挂的却是编辑前的旧译文 —— 界面说对了，产物是旧的，没有任何报错。
    """
    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    _relocate_subtitle_into_tmp(tmp_path, client, subtitle)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    translation_id = created["id"]
    wait_state(client, translation_id, {"succeeded"})
    assert client.post(f"/api/translation/jobs/{translation_id}/export").status_code == 200

    edited = client.patch(
        f"/api/translation/jobs/{translation_id}/cues/0",
        json={"text": "人工修订的译文"},
    )
    assert edited.status_code == 200
    _seed_published_upload(tmp_path, client, created["item_id"])

    seen: list[str] = []
    manager = client.app.state.api_context.bili
    engine = client.app.state.db_engine

    def fake_attach(upload_id: str, path: Path):
        seen.append(Path(path).read_text(encoding="utf-8-sig"))
        from app.bilibili.bili_read_model import read_upload

        with Session(engine) as session:
            row = session.get(BiliUpload, upload_id)
            row.translated_subtitle_state = "posted"
            session.add(row)
            session.commit()
            return read_upload(row, index=1)

    manager.attach_translation = fake_attach  # type: ignore[method-assign]

    response = client.post(f"/api/translation/jobs/{translation_id}/upload-bili")

    assert response.status_code == 200
    assert seen, "上传路径没被调用"
    assert "人工修订的译文" in seen[0], f"上传的是旧文件：{seen[0]!r}"


def test_human_review_srt_shows_exactly_two_lines_like_the_upload_track() -> None:
    """双语有两条导出路径，播放器里看到的都必须是「一条 cue、两行」。

    `bilingual_content` 把内部换行压成空格，上传 SRT / BCC 因此恰好两行；
    给用户核对的 `bilingual.srt` 却直接拼原文与译文，源 VTT 的换行会让一条 cue
    变成四行以上。同一个「双语」在两个文件里长得不一样，核对就失去了意义。
    """
    cues = [AlignedCue(0, 1.0, 2.0, "Second\nline", "第二行\n译文", False)]
    blocks = [block for block in render_bilingual_srt(cues).split("\n\n") if block.strip()]

    assert len(blocks) == 1
    assert blocks[0].splitlines()[2:] == ["Second line", "第二行 译文"]


def test_export_bilingual_names_the_untranslated_cue_consistently(tmp_path: Path) -> None:
    """导出拒绝空译文时，错误信息要指向**那一条没译完**，而不是另一层的措辞。

    `export_bilingual_files` 在检查出空译文后又遍历所有 cue 调 `bilingual_content`，
    返回值没人用；若那条先抛，用户看到的是「需要原文和译文都不为空」，
    会以为自己漏填了原文。实际的硬性校验在 `validate_alignment`，它给的是条目号。
    """
    source = [Cue(1.0, 2.0, "Hello")]
    aligned = [AlignedCue(0, 1.0, 2.0, "Hello", "", False)]

    with pytest.raises(TranslationError, match="第 1 条译文为空"):
        export_bilingual_files(source, aligned, tmp_path / "a.en.srt")


def test_progress_event_carries_the_job_id(tmp_path: Path) -> None:
    """进度事件必须带 `job_id` / `item_id`，否则 SSE 刷新认不出是哪一条在动。

    `_run` 在 `with Session(...)` 块外读 `row.job_id`；若属性在 commit 后过期，
    这里会以 DetachedInstanceError 收场，翻译线程直接断掉，界面永远停在 running。
    """
    import asyncio
    import time

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
        progress = [p for p in published if p.get("type") == "subtitle_translation_progress"]
        assert progress, "没有进度事件"
        assert all(p["job_id"] == "job-1" for p in progress)
        assert all(p["item_id"] == created["item_id"] for p in progress)
    finally:
        broker.publish = original  # type: ignore[method-assign]


def test_end_to_end_edit_and_bilingual_switch_reach_bili_as_two_lines(tmp_path: Path, monkeypatch) -> None:
    """端到端（可行范围内）：真 HTTP → 真翻译编排 → 编辑 → 切双语 → 真上传链路 → 假平台端点。

    真机验收需要拉起本机 8B 模型跑满一轮（约 3.8 token/s，数十分钟）并真的改动 B站
    稿件，不适合放进测试套件；这里用假 provider 与假 transport 替换这两个外部依赖，
    其余（DB、逐条 JSON、SRT/BCC 写盘、路由、BiliManager、字幕解析与表单拼装）全是真的。
    """
    import json

    from app.bilibili.bili_upload import UploadContext
    from bili_fakes import FakeBiliTransport

    client, subtitle = setup_job(tmp_path, ScriptedProvider())
    subtitle = _relocate_subtitle_into_tmp(tmp_path, client, subtitle)
    created = client.post("/api/translation/jobs", json={"job_id": "job-1"}).json()
    translation_id = created["id"]
    wait_state(client, translation_id, {"succeeded"})
    assert subtitle.read_text(encoding="utf-8").startswith("1\n")  # 源文件未动

    edited = client.patch(
        f"/api/translation/jobs/{translation_id}/cues/0", json={"text": "我改过的译文"}
    )
    assert edited.status_code == 200 and edited.json()["cues"][0]["edited"] is True

    switched = client.patch(
        f"/api/translation/jobs/{translation_id}/mode", json={"mode": "bilingual"}
    )
    assert switched.status_code == 200 and switched.json()["display_mode"] == "bilingual"
    _seed_published_upload(tmp_path, client, created["item_id"])

    transport = FakeBiliTransport()
    platform_context = UploadContext(
        biliup_exe="biliup.exe",
        cookie_path=tmp_path / "cookies.json",
        jar={"SESSDATA": "s", "bili_jct": "jct", "DedeUserID": "42"},
        transport=transport,
    )
    # 只替换「外部依赖的装配」：登录文件与平台 transport。上传编排、字幕解析、
    # 表单拼装、DB 与文件写读仍是真的。
    monkeypatch.setattr(
        client.app.state.api_context.bili, "_context", lambda **kwargs: platform_context
    )

    response = client.post(f"/api/translation/jobs/{translation_id}/upload-bili")

    assert response.status_code == 200, f"detail={response.text}"
    assert response.json()["translated_subtitle_state"] == "posted"
    assert "中英双语" in response.json()["translated_subtitle_message"]
    assert len(transport.subtitles) == 1
    form = transport.subtitles[0]
    assert form["lan"] == "zh" and form["oid"] == 7001
    body = json.loads(form["data"])["body"]
    assert len(body) == 3
    # 一条 cue、一个时间轴、两行；第一行是原文，第二行是**改过之后**的译文。
    assert body[0]["content"] == "Hello world\n我改过的译文"
    assert body[0]["from"] == 1.0 and body[0]["to"] == 2.0
    assert all(len(entry["content"].splitlines()) == 2 for entry in body)


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False
        self.waited = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> None:
        self.waited = True


def test_failed_vulkan_launch_is_waited_before_the_cpu_fallback(monkeypatch, tmp_path: Path) -> None:
    """Vulkan 进程必须先 terminate **并 wait**，再用同一端口启动 CPU 回退。

    不 wait 就抢同一个端口，回退进程会撞上「地址已被占用」而启动失败 ——
    用户看到的是「已试过 CPU 回退」这句假结论，而两个进程都还在机器上。
    """
    from app.contracts.config import AppSettings
    from app.translation import model_server

    procs = [_FakeProc(101), _FakeProc(102)]
    launched: list[bool] = []

    def fake_launch(binary, model, port, log_path, *, cpu_only):
        launched.append(cpu_only)
        return procs[len(launched) - 1]

    monkeypatch.setattr(model_server, "is_healthy", lambda base_url, **kwargs: False)
    monkeypatch.setattr(model_server, "find_server_binary", lambda: "llama-server")
    monkeypatch.setattr(model_server, "find_model", lambda: tmp_path / "model.gguf")
    monkeypatch.setattr(model_server, "_launch", fake_launch)
    monkeypatch.setattr(model_server, "_wait_until_healthy", lambda base_url, proc, timeout: False)
    monkeypatch.setattr(model_server, "time", __import__("time"))

    settings = AppSettings(data_dir=tmp_path / "data", translation_base_url="http://127.0.0.1:8080/v1")
    with pytest.raises(model_server.ModelServerError):
        model_server.ensure_model_server(settings, timeout=0.001)

    assert launched == [False, True]
    assert procs[0].terminated and procs[0].waited, "Vulkan 进程要先停干净再回退"
    # 两个后端都起不来时，CPU 回退进程也不能留在后台占着端口。
    assert procs[1].terminated, "CPU 回退失败后进程没清理，下次重试会端口冲突"
