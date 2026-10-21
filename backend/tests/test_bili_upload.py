"""`bili_upload`：把「一条下载好的视频」变成「B站 上可对账的稿件」。

本模块被刻意设计成纯函数式（不写库、不发事件、不读全局配置），代价是 `UploadContext`
要显式传进来；收益是**每一个分支都能离线跑**。用例因此集中钉四件在真实账号上极难复现、
在本地却很容易写错的事（也就是模块文档里那份「业务判断清单」）：

1. `biliup` 返回 0 **不等于**已发布 —— 必须过对账，查不到就停在「待核对」；
2. 单条失败**不中断**整批；
3. 进合集的条目**必须同时有 aid 与 cid**；
4. 已有 `season_id` 时**不再新建**合集。

再加一组「附加动作不能越级」的约束：封面抽不出来、评论发不出去，都不该把一次成功的投稿
判成失败。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.bili_bin import CommandResult
from app.bili_http import HttpError
from app.bili_meta import (
    COPYRIGHT_SELF,
    VIDEO_NAME_MAX,
    MetadataTemplate,
    UploadMetadata,
    default_collection_template,
    default_single,
)
from app.bili_policy import (
    SEASON_CREATE_FAILED,
    SEASON_EPISODES_FAILED,
    SEASON_SECTION_UNRESOLVED,
    UPLOAD_CDN_DROPPED,
)
from app.bili_upload import (
    COMMENT_MARKER,
    DEFAULT_COLLECTION_GAP_SECONDS,
    LOG_TAIL_LINES,
    CollectionResult,
    SeasonOutcome,
    UploadContext,
    UploadResult,
    UploadTarget,
    group_into_season,
    prepare_cover,
    render_collection,
    reverify,
    run_collection,
    run_single,
    tail_text,
    validate_collection,
)
from app.models import BiliSeasonStatus, BiliUploadStatus
from bili_fakes import (
    UNSET,
    FakeBiliTransport,
    RecordingSleeper,
    ScriptedRunner,
    command_runner,
    section_id_for,
    write_video,
)

TITLE = "演示标题"
JAR = {"SESSDATA": "sess", "bili_jct": "jct", "DedeUserID": "42"}


# ---------------------------------------------------------------- 夹具与局部替身
class RecordingCommandRunner:
    """既跑上传也跑抽封面，并把 argv 记下来。

    真实的 `extract_cover_frame` 判定成功的依据是**输出文件真的存在且非空**，
    所以替身必须把文件造出来。不造的话「封面被带上」这条路径永远走不到 ——
    用例会以为自己在验证封面，实际验证的是「没有封面」。
    """

    def __init__(self, *, ffmpeg: str = "ffmpeg", returncode: int = 0, stdout: str = "APP接口投稿成功") -> None:
        self.ffmpeg = ffmpeg
        self.returncode = returncode
        self.stdout = stdout
        self.argvs: list[list[str]] = []

    @property
    def upload_argv(self) -> list[str]:
        return next(argv for argv in self.argvs if argv[0] != self.ffmpeg)

    @property
    def cover_calls(self) -> list[list[str]]:
        return [argv for argv in self.argvs if argv[0] == self.ffmpeg]

    def __call__(self, argv, timeout, on_line):
        self.argvs.append(list(argv))
        if argv[0] == self.ffmpeg:
            out = Path(argv[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\xff\xd8\xff\x00")
            return CommandResult(0, "", "")
        if on_line is not None:
            for line in ("start pre_upload", "Upload completed", self.stdout):
                on_line(line)
        return CommandResult(self.returncode, self.stdout, "")


def make_context(transport, *, runner, tmp_path: Path, **overrides) -> UploadContext:
    base = {
        "biliup_exe": "biliup.exe",
        "cookie_path": tmp_path / "cookies.json",
        "jar": dict(JAR),
        "transport": transport,
        "runner": runner,
        "sleeper": RecordingSleeper(),
        "verify_delay": 0.0,
    }
    base.update(overrides)
    return UploadContext(**base)


def archive(*, aid: int | None = 111, bvid: str | None = "BV1xx", title: str = TITLE, state: object = 0, cover: str | None = None) -> dict:
    row: dict = {"title": title}
    if aid is not None:
        row["aid"] = aid
    if bvid is not None:
        row["bvid"] = bvid
    if state is not None:
        row["state"] = state
    if cover is not None:
        row["cover"] = cover
    return row


def single_meta(**overrides) -> UploadMetadata:
    kwargs = {"job_title": "演示任务", "item_title": TITLE, "source_url": "https://youtu.be/x"}
    kwargs.update(overrides)
    return default_single(**kwargs)


# ================================================================ run_single
def test_run_single_happy_path_reaches_published_with_aid_bvid_and_cid(tmp_path):
    """一次成功投稿要同时拿到 aid/bvid（对账给的）与 cid（稿件详情给的，归集必需）。"""
    transport = FakeBiliTransport(archives=[archive(aid=111, bvid="BV1xx", state=0, cover="https://c/1.jpg")])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.status == BiliUploadStatus.published.value
    assert (result.aid, result.bvid, result.cid) == (111, "BV1xx", 7001)
    assert result.verified_note == "已发布，可以正常观看"
    assert result.episode_ready is True
    assert result.ok is True
    assert result.error is None and result.advice is None


def test_run_single_reports_the_progress_it_measured_even_without_a_listener(tmp_path):
    """进度是**落库字段**，不该取决于「这一刻有没有人在听」。

    把进度回调当条件时，同一个投稿在后端观察到 0.6、在直连调用里观察到 0.0 —— 又是一处两处真相。
    """
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    context = make_context(transport, runner=command_runner(), tmp_path=tmp_path)
    assert context.on_progress is None
    result = run_single(context, UploadTarget(1, TITLE, video), single_meta())
    assert result.progress == pytest.approx(0.95)


def test_run_single_streams_progress_monotonically(tmp_path):
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    seen: list[float] = []
    context = make_context(transport, runner=command_runner(), tmp_path=tmp_path, on_progress=seen.append)
    run_single(context, UploadTarget(1, TITLE, video), single_meta())
    assert seen == [pytest.approx(0.20), pytest.approx(0.60), pytest.approx(0.95)]


# ================================================================ CC 字幕
def _subtitle(tmp_path: Path, name: str = "a.en.srt") -> Path:
    path = tmp_path / name
    path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nHello\n",
        encoding="utf-8",
    )
    return path


def test_run_single_uploads_the_downloaded_subtitle(tmp_path):
    """视频上传成功后，字幕要一并提交，且挂在**分P**（`oid` = cid）上。"""
    transport = FakeBiliTransport(archives=[archive(aid=111, bvid="BV1xx", state=0)])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video, subtitle_path=_subtitle(tmp_path), subtitle_language="en"),
        single_meta(),
    )
    assert result.status == BiliUploadStatus.published.value
    assert result.subtitle_state == "posted"
    assert result.subtitle_lan == "en"
    assert transport.called("/x/v2/dm/subtitle/draft/save") == 1
    form = transport.subtitles[0]
    assert form["oid"] == 7001  # cid，不是 aid
    assert form["aid"] == 111
    assert form["bvid"] == "BV1xx"


def test_run_single_maps_the_language_onto_the_platform_whitelist(tmp_path):
    """`zh-Hans` → `zh`。平台拒收 `zh-CN` / `en-US`（实测 `79011 不合法的语言`）。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video, subtitle_path=_subtitle(tmp_path, "a.zh-Hans.srt"), subtitle_language="zh-Hans"),
        single_meta(),
    )
    assert result.subtitle_lan == "zh"
    assert transport.subtitles[0]["lan"] == "zh"


def test_failed_subtitle_never_turns_a_published_video_into_a_failure(tmp_path):
    """字幕是附加物：它失败时**稿件状态必须照旧是已发布**，只有结论被记下来。"""
    transport = FakeBiliTransport(
        archives=[archive(aid=111, bvid="BV1xx", state=0)], subtitle_code=79011, subtitle_message="不合法的语言"
    )
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video, subtitle_path=_subtitle(tmp_path), subtitle_language="en"),
        single_meta(),
    )
    assert result.status == BiliUploadStatus.published.value
    assert result.ok is True
    assert result.subtitle_state == "failed"
    assert "79011" in result.subtitle_message


def test_without_a_subtitle_the_step_is_skipped_not_failed(tmp_path):
    """下载阶段就判成「没有可用字幕」时，只跳过字幕，视频流程照常完成。"""
    transport = FakeBiliTransport(archives=[archive(aid=111, bvid="BV1xx", state=0)])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video),
        single_meta(),
    )
    assert result.status == BiliUploadStatus.published.value
    assert result.subtitle_state == "skipped"
    assert transport.called("/x/v2/dm/subtitle/draft/save") == 0


def test_subtitle_that_cannot_get_a_cid_is_failed_so_it_can_be_retried(tmp_path):
    """审核中的稿件取不到分P编号（`x/web-interface/view` 返回 -404），字幕必然传不了。

    **结论必须是 `failed` 而不是 `skipped`。** 只有 `failed` 才让
    `bili_manager.retry_upload` 打开「只补传字幕、绝不重投视频」那条路；记成 `skipped`，
    用户点重试只会被告知「这一条已经上传成功」，这条字幕就永久丢了。

    实测依据（2026-10-19，本机库）：两条 `reviewing` 稿件 `aid`/`bvid` 都有、`cid` 为空、
    下载条目里 `en` 人工字幕的 `.vtt` 就在磁盘上 —— 修复前就是被记成 `skipped` 的。
    """
    transport = FakeBiliTransport(archives=[archive(aid=111, bvid="BV1xx", state=-30)], video=None)
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video, subtitle_path=_subtitle(tmp_path), subtitle_language="en"),
        single_meta(),
    )
    assert result.status == BiliUploadStatus.reviewing.value
    assert result.ok is True
    assert result.cid is None
    assert result.subtitle_state == "failed"
    assert "分P编号" in result.subtitle_message
    assert "重传字幕" in result.subtitle_message
    assert transport.called("/x/v2/dm/subtitle/draft/save") == 0


def test_subtitle_is_skipped_when_the_video_never_got_verified(tmp_path):
    """未核实的稿件连存不存在都不知道，拿不到 aid/cid，字幕无处可挂。"""
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video, subtitle_path=_subtitle(tmp_path), subtitle_language="en"),
        single_meta(),
    )
    assert result.status == BiliUploadStatus.unverified.value
    assert result.subtitle_state == "skipped"
    assert transport.called("/x/v2/dm/subtitle/draft/save") == 0


def test_subtitle_is_skipped_when_the_file_is_gone(tmp_path):
    """磁盘上的东西随时可能被删 —— 拿一个不存在的路径去上传只是把失败推迟一步。"""
    transport = FakeBiliTransport(archives=[archive(aid=111, bvid="BV1xx", state=0)])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video, subtitle_path=tmp_path / "gone.srt", subtitle_language="en"),
        single_meta(),
    )
    assert result.status == BiliUploadStatus.published.value
    assert result.subtitle_state == "skipped"
    assert transport.called("/x/v2/dm/subtitle/draft/save") == 0


def test_reverify_can_reattach_a_subtitle(tmp_path):
    """视频已发布、字幕失败时，重试只能补传字幕 —— 重投会在账号上多一条重复稿件。"""
    transport = FakeBiliTransport(archives=[archive(aid=111, bvid="BV1xx", state=0)])
    result = reverify(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        title=TITLE,
        subtitle_path=_subtitle(tmp_path),
        subtitle_language="en",
    )
    assert result.status == BiliUploadStatus.published.value
    assert result.subtitle_state == "posted"


def test_run_single_prefers_the_platform_cover_over_the_local_frame(tmp_path):
    """本地抽的封面被 B站 转码/裁切过，**平台那个才是最终生效的**。"""
    transport = FakeBiliTransport(
        archives=[archive(cover="https://i0.hdslb.com/bfs/archive/from-platform.jpg")],
        video={"cid": 7001, "pic": "https://i0.hdslb.com/bfs/archive/final.jpg"},
    )
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.cover_url == "https://i0.hdslb.com/bfs/archive/final.jpg"


def test_run_single_falls_back_to_the_archive_cover_when_details_have_none(tmp_path):
    transport = FakeBiliTransport(
        archives=[archive(cover="https://i0.hdslb.com/bfs/archive/from-platform.jpg")],
        video={"cid": 7001, "pic": ""},
    )
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.cover_url == "https://i0.hdslb.com/bfs/archive/from-platform.jpg"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (0, BiliUploadStatus.published.value),
        (-1, BiliUploadStatus.reviewing.value),
        (-30, BiliUploadStatus.reviewing.value),
        (-2, BiliUploadStatus.rejected.value),
        (-16, BiliUploadStatus.rejected.value),
        ("未来的某一档", BiliUploadStatus.unverified.value),
    ],
)
def test_run_single_folds_platform_state_into_our_status(tmp_path, state, expected):
    transport = FakeBiliTransport(archives=[archive(state=state)])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.status == expected


def test_run_single_does_not_claim_ok_for_a_rejected_archive(tmp_path):
    """被退回的稿件存在于平台上，但绝不能说成「可以收工」。"""
    transport = FakeBiliTransport(archives=[archive(state=-2)])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.ok is False
    assert "审核不通过" in (result.verified_note or "")


def test_run_single_reports_unverified_when_the_command_succeeded_but_nothing_is_found(tmp_path):
    """**本功能唯一不能妥协的一条。** `biliup` 返回 0 只说明命令跑完了。"""
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.status == BiliUploadStatus.unverified.value
    assert result.error == "已提交，但还没在 B站 看到它"
    assert "重新核对" in (result.advice or "")
    # 没核实的条目**不能**进合集：没有 aid/cid，加进去只会被平台拒。
    assert (result.aid, result.cid) == (None, None)
    assert result.episode_ready is False
    assert result.ok is False


def test_run_single_marks_failed_and_skips_verification_when_the_command_fails(tmp_path):
    """命令失败时**不查对账** —— 那等于拿一次注定为空的查询去拖时间。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    runner = command_runner(returncode=1, stdout="code 601 您上传视频过快", lines=("code 601 您上传视频过快",))
    result = run_single(make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.status == BiliUploadStatus.failed.value
    assert "太快" in (result.error or "")
    assert transport.called("/x/web/archives") == 0


def test_run_single_reports_a_transport_drop_as_a_drop_not_as_an_unknown(tmp_path):
    """真实失败（Ep 130）的输出必须原样走到界面上：结论是「传输断了」，不是「没能识别出原因」。

    这条钉的是**接线**：`classify_failure` 拿到的是 `command.output`（原始输出，含 URL 与错误链），
    而不是落库时脱敏过的 `log_tail`。脱敏只抹掉查询串（`?<redacted>`），主机名还在 ——
    判据在两种形态下都成立，所以同一份夹具在 `test_bili_policy` 与这里都能用。

    另外：命令失败时**不许**去对账 —— 一次注定为空的查询只会拖慢界面。
    """
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    raw = (
        ' INFO biliup::uploader::line: pre_upload: {"profile":"ugcupos/bup","r":"upos"}\n'
        "Error: error sending request for url (https://upos-cs-upcdnbda2.bilivideo.com/ugcever/x.mp4)\n"
        "Caused by:\n"
        "    0: client error (SendRequest)\n"
        "    1: connection error\n"
        "    2: peer closed connection without sending TLS close_notify\n"
    )
    runner = command_runner(returncode=1, stdout=raw, lines=("start pre_upload", raw))
    result = run_single(
        make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta()
    )
    assert result.status == BiliUploadStatus.failed.value
    assert result.error == UPLOAD_CDN_DROPPED[0]
    assert "不会多出一条重复稿件" in (result.advice or "")
    assert transport.called("/x/web/archives") == 0


def test_run_single_writes_the_raw_output_into_the_log_on_failure(tmp_path, caplog):
    """失败时原始输出必须进**日志文件**，不能只留在库里的 `log_tail`。

    用户的下一步动作就是照我们给的失败建议去翻 app.log；过去那里只有我们自己那一行
    「bili 投稿未成功」，biliup 的原文一行都没有 —— 照做的人一定扑空。
    这条用例同时钉住那个可 grep 的前缀（建议文案里写的就是它）。
    """
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    raw = 'Error: {"code":-400,"data":null,"message":"请求错误","ttl":1}'
    runner = command_runner(returncode=1, stdout=raw, lines=(raw, "start pre_upload"))
    with caplog.at_level(logging.WARNING, logger="app.bili_upload"):
        result = run_single(
            make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta()
        )
    assert result.status == BiliUploadStatus.failed.value
    logged = [record.getMessage() for record in caplog.records if record.name == "app.bili_upload"]
    assert any(message.startswith("biliup 输出 | ") and "请求错误" in message for message in logged), logged


def test_run_single_never_logs_the_raw_output_of_a_successful_upload(tmp_path, caplog):
    """成功时不必把整段输出刷进日志 —— 那会把排障用的那几行淹掉。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    with caplog.at_level(logging.WARNING, logger="app.bili_upload"):
        run_single(
            make_context(transport, runner=command_runner(), tmp_path=tmp_path),
            UploadTarget(1, TITLE, video),
            single_meta(),
        )
    assert not [r for r in caplog.records if "biliup 输出" in r.getMessage()]


def test_run_single_rejects_a_missing_video_without_running_anything(tmp_path):
    transport = FakeBiliTransport()
    runner = ScriptedRunner([])
    result = run_single(make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, "x", tmp_path / "none.mp4"), single_meta(item_title="x"))
    assert result.status == BiliUploadStatus.failed.value
    assert result.error == "这个视频还没有下载好，没有可以上传的文件"
    assert "重启" in (result.advice or "")
    assert runner.calls == []
    assert transport.calls == []


def test_run_single_keeps_the_raw_output_tail_for_troubleshooting(tmp_path):
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.log_tail is not None
    assert "APP接口投稿成功" in result.log_tail


def test_run_single_copies_the_metadata_onto_the_result(tmp_path):
    """结果字段与 `models.BiliUpload` 一一对应；漏拷一个就等于界面上少一列。"""
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    meta = UploadMetadata(title="标题甲", desc="简介乙", tags="a,b", tid="122")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(7, "标题甲", video, item_id="it-7"), meta)
    assert (result.index, result.item_id) == (7, "it-7")
    assert (result.title, result.desc, result.tags, result.tid) == ("标题甲", "简介乙", "a,b", "122")
    assert result.video_path == str(video)


def test_run_single_passes_the_submit_shape_into_the_command(tmp_path):
    """投稿命令必须走 `--submit web` 并带上封面；这里从 argv 层面确认它真的被传下去了。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    runner = RecordingCommandRunner()
    run_single(
        make_context(transport, runner=runner, tmp_path=tmp_path, ffmpeg_path="ffmpeg", cover_dir=tmp_path / "covers"),
        UploadTarget(1, TITLE, video),
        single_meta(),
    )
    argv = runner.upload_argv
    assert argv[argv.index("--submit") + 1] == "web"
    assert argv[argv.index("--cover") + 1] == str(tmp_path / "covers" / "bili_01.jpg")
    assert str(video) in argv


def test_run_single_uses_the_cover_key_it_is_given(tmp_path):
    """默认按序号命名只适用于单条：多个任务并存时 `bili_01.jpg` 会互相覆盖，
    落库的 `cover_path` 就指向了别人的画面。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    runner = RecordingCommandRunner()
    run_single(
        make_context(transport, runner=runner, tmp_path=tmp_path, ffmpeg_path="ffmpeg", cover_dir=tmp_path / "covers"),
        UploadTarget(1, TITLE, video),
        single_meta(),
        cover_key="upload-abc",
    )
    assert runner.cover_calls[0][-1] == str(tmp_path / "covers" / "upload-abc.jpg")


def test_run_single_can_skip_the_cover_entirely(tmp_path):
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    runner = RecordingCommandRunner()
    result = run_single(
        make_context(transport, runner=runner, tmp_path=tmp_path, ffmpeg_path="ffmpeg", cover_dir=tmp_path / "covers"),
        UploadTarget(1, TITLE, video),
        single_meta(),
        make_cover=False,
    )
    assert result.cover_path is None
    assert runner.cover_calls == []
    assert "--cover" not in runner.upload_argv


def test_run_single_uses_a_caller_supplied_cover_without_extracting_one(tmp_path):
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    cover = tmp_path / "given.jpg"
    cover.write_bytes(b"\xff\xd8\xff")
    runner = RecordingCommandRunner()
    result = run_single(
        make_context(transport, runner=runner, tmp_path=tmp_path, ffmpeg_path="ffmpeg", cover_dir=tmp_path / "covers"),
        UploadTarget(1, TITLE, video),
        single_meta(),
        cover_path=cover,
    )
    assert result.cover_path == str(cover)
    assert runner.cover_calls == []


def test_run_single_does_not_fail_when_no_ffmpeg_is_available(tmp_path):
    """封面是可选项：没有 ffmpeg 时少一张封面，不该让整次投稿失败。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.cover_path is None
    assert result.status == BiliUploadStatus.published.value


# ---------------------------------------------------------------- 置顶评论这一附加动作
def test_run_single_sends_the_comment_after_a_published_upload(tmp_path):
    transport = FakeBiliTransport(archives=[archive()], comment_rpid=9001)
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video),
        single_meta(),
        comment="自动生成的置顶评论",
    )
    assert result.comment == "评论已发送并置顶"
    assert transport.called("/x/v2/reply/add") == 1
    assert transport.called("/x/v2/reply/top") == 1
    assert transport.comments[0]["message"] == "自动生成的置顶评论"


def test_run_single_sends_the_comment_for_a_reviewing_upload_too(tmp_path):
    """审核中的稿件也有 aid（评论区已经能用），所以评论照发。"""
    transport = FakeBiliTransport(archives=[archive(state=-30)])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video),
        single_meta(),
        comment="自动生成的置顶评论",
    )
    assert result.status == BiliUploadStatus.reviewing.value
    assert result.comment == "评论已发送并置顶"


def test_run_single_skips_the_comment_when_there_is_no_aid(tmp_path):
    """「待核对」时连 oid 都没有，发评论就无从谈起。"""
    transport = FakeBiliTransport(archives=[])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video),
        single_meta(),
        comment="自动生成的置顶评论",
    )
    assert result.comment is None
    assert transport.called("/x/v2/reply/add") == 0


def test_run_single_skips_the_comment_when_only_aid_is_known(tmp_path):
    """**只有 aid 没有 cid 时也不发。** 这条不是为评论本身，而是把 `episode_ready`
    的判据（aid **与** cid 都要）钉在一个可观察的行为上。"""
    transport = FakeBiliTransport(archives=[archive()], video={})
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video),
        single_meta(),
        comment="自动生成的置顶评论",
    )
    assert result.aid == 111 and result.cid is None
    assert result.episode_ready is False
    assert transport.called("/x/v2/reply/add") == 0


def test_run_single_does_not_send_a_comment_when_none_was_asked_for(tmp_path):
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    result = run_single(make_context(transport, runner=command_runner(), tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())
    assert result.comment is None
    assert transport.called("/x/v2/reply/add") == 0


def test_run_single_does_not_fail_the_upload_when_the_comment_fails(tmp_path):
    """评论是一条附加动作，失败只能污染自己那一格。"""
    transport = FakeBiliTransport(archives=[archive()], comment_code=-412, comment_message="请求被拦截")
    video = write_video(tmp_path / "a.mp4")
    result = run_single(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        UploadTarget(1, TITLE, video),
        single_meta(),
        comment="自动生成的置顶评论",
    )
    assert result.status == BiliUploadStatus.published.value
    assert "评论没有发出去" in (result.comment or "")


def test_comment_marker_is_ascii_so_it_cannot_collide_with_a_chinese_comment():
    assert COMMENT_MARKER.isascii()


# ================================================================ reverify
def test_reverify_only_rechecks_and_never_resubmits(tmp_path):
    """**最容易写错、代价也最大的一个分支。** 「待核对」的稿件投稿命令已经成功过，
    把它和失败态一起「重试」会让账号上多一条重复稿件。"""
    transport = FakeBiliTransport(archives=[archive()])
    runner = ScriptedRunner([])
    result = reverify(make_context(transport, runner=runner, tmp_path=tmp_path), title=TITLE, index=3, item_id="it-3")
    assert runner.calls == []
    assert transport.called("/x/web/archives") == 1
    assert result.status == BiliUploadStatus.published.value
    assert (result.index, result.item_id) == (3, "it-3")


def test_reverify_stays_unverified_when_the_archive_is_still_missing(tmp_path):
    transport = FakeBiliTransport(archives=[])
    result = reverify(make_context(transport, runner=ScriptedRunner([]), tmp_path=tmp_path), title=TITLE)
    assert result.status == BiliUploadStatus.unverified.value
    assert result.error == "已提交，但还没在 B站 看到它"


def test_reverify_picks_up_the_cid_needed_for_grouping(tmp_path):
    """从「待核对」变成「已发布」的同时要把 cid 补上，否则合集那一步还是加不进去。"""
    transport = FakeBiliTransport(archives=[archive()])
    result = reverify(make_context(transport, runner=ScriptedRunner([]), tmp_path=tmp_path), title=TITLE)
    assert result.episode_ready is True


def test_reverify_does_not_send_a_comment(tmp_path):
    """重新核对不是重新上传，附加动作不该被顺带重放。"""
    transport = FakeBiliTransport(archives=[archive()])
    reverify(make_context(transport, runner=ScriptedRunner([]), tmp_path=tmp_path), title=TITLE)
    assert transport.called("/x/v2/reply/add") == 0


# ================================================================ 归集
def ready(
    index: int,
    *,
    aid: int | None = UNSET,
    cid: int | None = UNSET,
    title: str | None = None,
    cover_url: str | None = None,
) -> UploadResult:
    """一条「已经拿到 aid/cid」的稿件。

    `cid=None` 用来构造「只有 aid」那种进不了合集的条目 —— 所以两个参数都走 `UNSET` 哨兵，
    不能拿 `None` 兼作「用默认值」。
    """
    return UploadResult(
        index=index,
        item_id=None,
        status=BiliUploadStatus.published.value,
        title=title or f"演示标题{index}",
        aid=100 + index if aid is UNSET else aid,
        cid=7000 + index if cid is UNSET else cid,
        cover_url=cover_url,
    )


def test_group_into_season_stays_pending_and_touches_nothing_when_no_episode_is_ready(tmp_path):
    """没有可归集的条目时**一个请求都不发** —— 否则就是在平台上留下一个空合集。"""
    transport = FakeBiliTransport()
    outcome = group_into_season(make_context(transport, runner=command_runner(), tmp_path=tmp_path), [ready(1, cid=None)], season_title="合集", season_desc="说明")
    assert outcome.status == BiliSeasonStatus.pending.value
    assert outcome.season_id is None
    assert outcome.error == "视频还在 B站 排队审核，暂时没法加进合集"
    assert transport.calls == []


def test_group_into_season_creates_then_adds_and_reports_submitted(tmp_path):
    transport = FakeBiliTransport(season_id=990011)
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        [ready(1, cover_url="https://c/1.jpg"), ready(2)],
        season_title="合集",
        season_desc="说明",
    )
    assert outcome.status == BiliSeasonStatus.grouped.value
    assert outcome.season_id == 990011
    assert outcome.cover_url == "https://c/1.jpg"
    assert outcome.note == "合集已提交，B站 对合集有人工审核，通过前可能暂时看不到"
    assert transport.called("/season/add") == 1
    assert transport.called("/season/section/episodes/add") == 1
    assert transport.calls[-1]["json_body"]["episodes"] == [
        {"aid": 101, "cid": 7001, "title": "演示标题1", "charging_pay": 0},
        {"aid": 102, "cid": 7002, "title": "演示标题2", "charging_pay": 0},
    ]


def test_group_into_season_sends_the_section_id_not_the_season_id(tmp_path):
    """**本缺陷的回归断言（界面路径的入口）。**

    `group_into_season` 拿到的是 `create_season` 的返回值 —— 那是**合集 id**，而归集接口的
    `sectionId` 要的是**分区 id**。这两件事在旧实现里是同一个变量，于是每次「新建合集」的
    归集都会 404，且重试不自愈（同一个错值会再发一遍）。
    """
    transport = FakeBiliTransport(season_id=990011)
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        [ready(1)],
        season_title="合集",
        season_desc="说明",
    )
    section_id = transport.calls[-1]["json_body"]["sectionId"]
    assert section_id == section_id_for(990011)
    assert section_id != outcome.season_id == 990011


def test_group_into_season_reuses_an_existing_season_and_never_creates_a_second(tmp_path):
    """重试不产生第二个同名合集 —— 用户根本不知道多了一个。

    同时钉住「加入已有合集」这条路也要换号：用户选的是合集，接口要的是分区。
    """
    transport = FakeBiliTransport(seasons=[{"id": 555, "title": "选中的合集"}])
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        [ready(1)],
        season_title="合集",
        season_desc="说明",
        existing_season_id=555,
    )
    assert outcome.status == BiliSeasonStatus.grouped.value
    assert outcome.season_id == 555
    assert transport.called("/season/add") == 0
    assert transport.called("/season/section/episodes/add") == 1
    assert transport.calls[-1]["json_body"]["sectionId"] == section_id_for(555)


def test_group_into_season_stops_when_the_section_cannot_be_read(tmp_path):
    """读不到分区就**一个归集请求都不发**。

    退化成「拿合集 id 顶一下」得到的是 `-404 啥都木有`：用户既看不出是权限问题还是网络问题，
    点「重试归集」也只会再撞一次同一堵墙。所以这种情况必须落成一条说得清的结论，
    并且把 `season_id` 带回去 —— 否则重试会重新建一个同名合集。
    """
    transport = FakeBiliTransport(seasons=[])
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        [ready(1)],
        season_title="合集",
        season_desc="说明",
        sleeper=RecordingSleeper(),
    )
    assert outcome.status == BiliSeasonStatus.failed.value
    assert outcome.error == SEASON_SECTION_UNRESOLVED[0]
    assert "重试归集" in (outcome.advice or "")
    assert outcome.season_id == 990011
    assert outcome.log_tail is not None
    assert transport.called("/season/section/episodes/add") == 0


def test_group_into_season_reports_a_create_failure_without_a_season_id(tmp_path):
    transport = FakeBiliTransport(season_add_error=HttpError("连接被重置"))
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path), [ready(1)], season_title="合集", season_desc="说明"
    )
    assert outcome.status == BiliSeasonStatus.failed.value
    assert outcome.season_id is None
    assert outcome.error == SEASON_CREATE_FAILED[0]
    assert "重试归集" in (outcome.advice or "")
    assert outcome.log_tail is not None


def test_group_into_season_keeps_the_season_id_when_only_adding_failed(tmp_path):
    """**这条支撑「只补加」。** 合集已经建好，重试时只要它还在，就不用再建一个。"""
    transport = FakeBiliTransport(episodes_error=HttpError("连接被重置"))
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path), [ready(1)], season_title="合集", season_desc="说明"
    )
    assert outcome.status == BiliSeasonStatus.failed.value
    assert outcome.season_id == 990011
    assert outcome.error == SEASON_EPISODES_FAILED[0]
    assert "不会重复建合集" in (outcome.advice or "")


def test_group_into_season_excludes_items_missing_a_cid(tmp_path):
    """缺 cid 的条目加进去会被平台拒；宁可少加一条，也不要整批失败。"""
    transport = FakeBiliTransport()
    group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        [ready(1), ready(2, cid=None), ready(3)],
        season_title="合集",
        season_desc="说明",
    )
    tids = [item["cid"] for item in transport.calls[-1]["json_body"]["episodes"]]
    assert tids == [7001, 7003]


def test_group_into_season_passes_an_empty_cover_when_nothing_has_one(tmp_path):
    transport = FakeBiliTransport()
    outcome = group_into_season(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path), [ready(1)], season_title="合集", season_desc="说明"
    )
    assert outcome.cover_url is None
    assert transport.calls[0]["form"]["cover"] == ""


# ================================================================ 模板渲染与校验
def test_render_collection_fills_every_placeholder_per_item(tmp_path):
    template = MetadataTemplate(title_template="{index}. {title}", desc_template="{job} 第 {index}/{count} 集\n{source}", tags="固定", tid="122")
    targets = [UploadTarget(1, "甲", write_video(tmp_path / "a.mp4"), source_url="https://s/1"), UploadTarget(2, "乙", write_video(tmp_path / "b.mp4"), source_url="https://s/2")]
    metas = render_collection(template, targets, job_title="任务")
    assert [m.title for m in metas] == ["01. 甲", "02. 乙"]
    assert metas[0].desc == "任务 第 01/2 集\nhttps://s/1"
    assert metas[1].desc == "任务 第 02/2 集\nhttps://s/2"


def test_render_collection_merges_template_tags_with_the_items_own(tmp_path):
    """只用模板标签会让合集里所有条目挂上一模一样的标签；只用派生标签又会丢掉用户写的固定标签。"""
    template = MetadataTemplate(title_template="{title}", desc_template="{title}", tags="固定标签", tid="122")
    targets = [UploadTarget(1, "Python 入门教程", write_video(tmp_path / "a.mp4"))]
    metas = render_collection(template, targets, job_title="任务")
    assert "固定标签" in metas[0].tags
    assert "Python" in metas[0].tags


def test_render_collection_matches_the_count_to_the_batch(tmp_path):
    template = default_collection_template("合集")
    targets = [UploadTarget(i, f"第{i}集", write_video(tmp_path / f"{i}.mp4")) for i in (1, 2, 3)]
    metas = render_collection(template, targets, job_title="合集")
    assert [meta.desc.splitlines()[0] for meta in metas] == [
        "合集 · 第 01/3 集",
        "合集 · 第 02/3 集",
        "合集 · 第 03/3 集",
    ]


def test_validate_collection_points_at_the_offending_item(tmp_path):
    """只报「有 N 条不合格」是不够的：用户面对几十条视频，需要知道改哪一条。"""
    template = MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122")
    targets = [
        UploadTarget(1, "正常", write_video(tmp_path / "a.mp4"), source_url="https://youtu.be/a"),
        UploadTarget(2, "超" * 90, write_video(tmp_path / "b.mp4"), source_url="https://youtu.be/b"),
    ]
    problems = validate_collection(template, targets, job_title="任务")
    assert len(problems) == 1
    assert problems[0].startswith("第 2 条「" + "超" * 90 + "」：标题超出 10 个字")


def test_validate_collection_accepts_a_clean_batch(tmp_path):
    template = default_collection_template("合集")
    targets = [UploadTarget(1, "第一集", write_video(tmp_path / "a.mp4"), source_url="https://youtu.be/a")]
    assert validate_collection(template, targets, job_title="合集") == []


def test_validate_collection_blames_the_items_that_have_no_source(tmp_path):
    """合集是**逐条**判的：默认转载，于是每一条都要有自己的原始链接。

    这条链上的失败长这样：某条视频不是从链接下来的（来源为空）→ 服务端把整批拒掉。
    泛泛说一句「有 1 条不合格」等于让用户在几十条里自己找，所以必须指到条目上。
    """
    template = default_collection_template("合集")
    targets = [
        UploadTarget(1, "有来源", write_video(tmp_path / "a.mp4"), source_url="https://youtu.be/a"),
        UploadTarget(2, "没来源", write_video(tmp_path / "b.mp4")),
    ]
    problems = validate_collection(template, targets, job_title="合集")
    assert len(problems) == 1
    assert problems[0].startswith("第 2 条「没来源」：")
    assert "没填原视频链接" in problems[0]


def test_validate_collection_is_clean_when_the_template_is_self_made(tmp_path):
    """切成自制之后，来源为空不再是问题 —— 否则用户会看到一个摘不掉的错误。"""
    template = MetadataTemplate("{title}", "{title}", "标签", "122", COPYRIGHT_SELF)
    targets = [UploadTarget(1, "自己拍的", write_video(tmp_path / "a.mp4"))]
    assert validate_collection(template, targets, job_title="任务") == []


def test_validate_collection_reports_unknown_placeholders(tmp_path):
    template = MetadataTemplate(title_template="{title} {date}", desc_template="{title}", tags="标签", tid="122")
    targets = [UploadTarget(1, "甲", write_video(tmp_path / "a.mp4"), source_url="https://youtu.be/a")]
    problems = validate_collection(template, targets, job_title="任务")
    assert len(problems) == 1
    assert "{date}" in problems[0] and "占位符" in problems[0]


# ================================================================ 整批编排
def batch(tmp_path) -> tuple[list[UploadTarget], list[dict]]:
    titles = ["演示标题一", "演示标题二", "演示标题三"]
    targets = [
        UploadTarget(index=i, title=title, video_path=write_video(tmp_path / f"v{i}.mp4"), source_url=f"https://youtu.be/{i}")
        for i, title in enumerate(titles, start=1)
    ]
    archives = [archive(aid=100 + i, bvid=f"BV{i}00", title=title, state=0) for i, title in enumerate(titles, start=1)]
    return targets, archives


def run_batch(context, targets, *, comment=None, gap_seconds=DEFAULT_COLLECTION_GAP_SECONDS, on_upload=None) -> CollectionResult:
    return run_collection(
        context,
        job_title="演示合集",
        template=default_collection_template("演示合集"),
        targets=targets,
        season_title="演示合集",
        season_desc="合集说明",
        gap_seconds=gap_seconds,
        comment=comment,
        on_upload=on_upload,
    )


def test_run_collection_waits_between_items_but_not_after_the_last(tmp_path):
    """条间间隔是抗 601 频控的唯一手段；最后一条之后再等只是白等。"""
    targets, archives = batch(tmp_path)
    sleeper = RecordingSleeper()
    transport = FakeBiliTransport(archives=archives)
    context = make_context(transport, runner=command_runner(), tmp_path=tmp_path, sleeper=sleeper, verify_delay=0.0)
    run_batch(context, targets, gap_seconds=20.0)
    assert sleeper.waits == [20.0, 20.0]


def test_run_collection_can_run_without_any_gap(tmp_path):
    targets, archives = batch(tmp_path)
    sleeper = RecordingSleeper()
    context = make_context(
        FakeBiliTransport(archives=archives), runner=command_runner(), tmp_path=tmp_path, sleeper=sleeper, verify_delay=0.0
    )
    run_batch(context, targets, gap_seconds=0.0)
    assert sleeper.waits == []


def test_run_collection_keeps_going_after_a_single_failure(tmp_path):
    """一只坏鸡蛋不该毁掉整篮：一条视频坏了或一次频控，后面几十条仍要发出去。"""
    targets, archives = batch(tmp_path)
    runner = ScriptedRunner(
        [
            CommandResult(0, "APP接口投稿成功", ""),
            CommandResult(1, "error: not logged in", ""),
            CommandResult(0, "APP接口投稿成功", ""),
        ]
    )
    result = run_batch(make_context(FakeBiliTransport(archives=archives), runner=runner, tmp_path=tmp_path), targets, gap_seconds=0.0)
    assert [item.status for item in result.uploads] == [
        BiliUploadStatus.published.value,
        BiliUploadStatus.failed.value,
        BiliUploadStatus.published.value,
    ]
    assert len(result.uploads) == 3


def test_run_collection_groups_only_the_ready_items(tmp_path):
    """第 2 条登录失效 → 缺 aid/cid，只能看、不能进合集；归集 2 条而不是 3 条。"""
    targets, archives = batch(tmp_path)
    runner = ScriptedRunner(
        [
            CommandResult(0, "APP接口投稿成功", ""),
            CommandResult(1, "error: not logged in", ""),
            CommandResult(0, "APP接口投稿成功", ""),
        ]
    )
    transport = FakeBiliTransport(archives=archives)
    result = run_batch(make_context(transport, runner=runner, tmp_path=tmp_path), targets, gap_seconds=0.0)
    assert result.season.status == BiliSeasonStatus.grouped.value
    episodes = transport.calls[-1]["json_body"]["episodes"]
    assert [item["aid"] for item in episodes] == [101, 103]


def test_run_collection_adds_the_season_after_the_last_upload(tmp_path):
    """归集是整批的最后一步：加条目的请求必须排在全部稿件之后。

    建合集与归集之间还夹着一次**读合集列表**（把合集 id 换成分区 id），所以这里比的是
    「写请求」的先后 —— 拿最后两条 URL 去比会被那次读打断，看起来像是顺序错了。
    """
    targets, archives = batch(tmp_path)
    transport = FakeBiliTransport(archives=archives)
    run_batch(make_context(transport, runner=command_runner(), tmp_path=tmp_path), targets, gap_seconds=0.0)
    writes = [
        url.rsplit("/x2/creative/web/", 1)[-1]
        for url in transport.urls
        if url.endswith("/season/add") or url.endswith("/season/section/episodes/add")
    ]
    assert writes == ["season/add", "season/section/episodes/add"]
    assert transport.urls[-1].endswith("/season/section/episodes/add")


def test_run_collection_reports_pending_when_everything_failed(tmp_path):
    """全军覆没时**不许**去建一个空合集。"""
    targets, _archives = batch(tmp_path)
    transport = FakeBiliTransport(archives=[])
    runner = command_runner(returncode=1, stdout="no space left on device", lines=("no space left on device",))
    result = run_batch(make_context(transport, runner=runner, tmp_path=tmp_path), targets, gap_seconds=0.0)
    assert [item.status for item in result.uploads] == [BiliUploadStatus.failed.value] * 3
    assert result.season.status == BiliSeasonStatus.pending.value
    assert not any(url.endswith("/season/add") for url in transport.urls)


def test_run_collection_notifies_after_each_item(tmp_path):
    """逐条落账靠这个回调；缺一条就等于界面上有一条永远停在「上传中」。"""
    targets, archives = batch(tmp_path)
    seen: list[tuple[int, str]] = []
    run_batch(
        make_context(FakeBiliTransport(archives=archives), runner=command_runner(), tmp_path=tmp_path),
        targets,
        gap_seconds=0.0,
        on_upload=lambda item: seen.append((item.index, item.status)),
    )
    assert seen == [(1, "published"), (2, "published"), (3, "published")]


def test_run_collection_forwards_the_comment_to_every_item(tmp_path):
    targets, archives = batch(tmp_path)
    transport = FakeBiliTransport(archives=archives)
    run_batch(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        targets,
        gap_seconds=0.0,
        comment="自动生成的置顶评论",
    )
    assert transport.called("/x/v2/reply/add") == 3
    assert all(call["message"] == "自动生成的置顶评论" for call in transport.comments)


def test_run_collection_uses_the_rendered_titles_when_verifying(tmp_path):
    """对账匹配用的是**渲染后**的标题，不是视频文件名 —— 两者在合集里通常不同。"""
    targets = [UploadTarget(1, "原标题", write_video(tmp_path / "a.mp4"))]
    transport = FakeBiliTransport(archives=[archive(title="01. 原标题", state=0)])
    template = MetadataTemplate(title_template="{index}. {title}", desc_template="{title}", tags="标签", tid="122")
    result = run_collection(
        make_context(transport, runner=command_runner(), tmp_path=tmp_path),
        job_title="合集",
        template=template,
        targets=targets,
        season_title="合集",
        season_desc="说明",
        gap_seconds=0.0,
    )
    assert result.uploads[0].status == BiliUploadStatus.published.value


# ================================================================ 输出尾部
def test_tail_text_keeps_only_the_last_lines():
    text = "\n".join(f"line {i}" for i in range(LOG_TAIL_LINES + 30))
    rows = tail_text(text).splitlines()
    assert len(rows) == LOG_TAIL_LINES
    assert rows[-1] == f"line {LOG_TAIL_LINES + 29}"


def test_tail_text_drops_blank_lines():
    assert tail_text("a\n\n\n   \nb") == "a\nb"


def test_tail_text_returns_empty_for_empty_output():
    """空输出返回空串（而不是 None），让落库侧统一按空值处理。"""
    assert tail_text("") == ""
    assert tail_text("   \n  ") == ""


def test_tail_text_scrubs_credentials():
    """日志尾部会被写进数据库并显示在界面上，凭据不能跟着进去。"""
    text = tail_text("Cookie: SESSDATA=abcdef123456; bili_jct=deadbeef")
    assert "abcdef123456" not in text
    assert "deadbeef" not in text
    assert "<redacted>" in text


def test_prepare_cover_returns_none_without_ffmpeg_or_a_cover_dir(tmp_path):
    video = write_video(tmp_path / "a.mp4")
    context = make_context(FakeBiliTransport(), runner=command_runner(), tmp_path=tmp_path)
    assert prepare_cover(context, key="k", video_path=video) is None


def test_prepare_cover_returns_none_for_a_video_that_is_not_there(tmp_path):
    context = make_context(
        FakeBiliTransport(), runner=command_runner(), tmp_path=tmp_path, ffmpeg_path="ffmpeg", cover_dir=tmp_path / "covers"
    )
    assert prepare_cover(context, key="k", video_path=tmp_path / "none.mp4") is None


def test_prepare_cover_writes_into_the_cover_dir_named_by_the_key(tmp_path):
    video = write_video(tmp_path / "a.mp4")
    context = make_context(
        FakeBiliTransport(),
        runner=RecordingCommandRunner(),
        tmp_path=tmp_path,
        ffmpeg_path="ffmpeg",
        cover_dir=tmp_path / "covers",
    )
    assert prepare_cover(context, key="abc", video_path=video) == tmp_path / "covers" / "abc.jpg"


def test_season_outcome_defaults_to_pending():
    """`CollectionResult` 的默认赛季是「等待归集」，不是「已归集」。"""
    assert CollectionResult().season.status == BiliSeasonStatus.pending.value
    assert SeasonOutcome(status=BiliSeasonStatus.failed.value).season_id is None


# ================================================================ 文件名长度
def _long_video(tmp_path: Path, stem_length: int) -> Path:
    """造一个**文件名很长**的视频：B站 的 80 字符上限也会算到它头上。"""
    return write_video(tmp_path / (("a" * stem_length) + ".mp4"))


class _LinkProbeRunner(RecordingCommandRunner):
    """跑上传时顺手看一眼「这一瞬间那个路径存不存在、多大」。"""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, bool, int]] = []

    def __call__(self, argv, timeout, on_line):
        if argv[0] != self.ffmpeg:
            path = Path(next(item for item in argv if str(item).endswith(".mp4")))
            self.seen.append((path.name, path.exists(), path.stat().st_size if path.exists() else 0))
        return super().__call__(argv, timeout, on_line)


def test_a_long_file_name_is_uploaded_through_a_short_hard_link(tmp_path):
    """B站 把**视频文件名**也算进「标题」的 80 字符上限（`21104 标题过长`）。

    实测：stem=80 的稿件通过、stem=85 的报 21104，而两者的**标题**分别只有 54 / 71 字符 ——
    所以这条错误与标题无关，真正超限的是下载器加过 ` [YouTube ID]` 的文件名。
    修法是上传时换一个短的**硬链接**名字，原文件一个字不动、也不复制字节。
    """
    transport = FakeBiliTransport(archives=[archive()])
    video = _long_video(tmp_path, 85)
    runner = _LinkProbeRunner()

    result = run_single(make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())

    assert result.status == BiliUploadStatus.published.value
    name, exists, size = runner.seen[0]
    assert name.startswith("cascade-") and name.endswith(".mp4")
    # 平台看到的是短名字：这才是 21104 的成因被真正消掉的地方。
    assert len(Path(name).stem) <= VIDEO_NAME_MAX
    # 上传那一刻链接必须真的指向同一份内容（硬链接，不是空壳）。
    assert exists and size == video.stat().st_size
    # 收尾：临时名字删掉，原文件还在。
    assert not (video.parent / name).exists()
    assert video.exists()


def test_a_normal_file_name_is_passed_through_untouched(tmp_path):
    """文件名本来就合规时不该多造一个链接：用户目录里少一个来路不明的文件。"""
    transport = FakeBiliTransport(archives=[archive()])
    video = write_video(tmp_path / "a.mp4")
    runner = _LinkProbeRunner()

    run_single(make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())

    assert runner.seen[0][0] == "a.mp4"
    assert [path.name for path in tmp_path.glob("cascade-*")] == []


def test_a_failed_hard_link_falls_back_to_the_original_name(tmp_path, monkeypatch, caplog):
    """硬链接失败（FAT / 跨卷 / 权限）时退回原名，**不去复制一个几 GB 的文件**。

    退回后平台仍可能用 21104 拒掉，但那句话已经写明「把源文件改名短一点」——
    拿一次可能的失败换「绝不偷偷复制大文件」，这笔账是划得来的。
    """
    def boom(*_args, **_kwargs):
        raise OSError("hard link not supported")

    monkeypatch.setattr("app.bili_upload.os.link", boom)
    transport = FakeBiliTransport(archives=[archive()])
    video = _long_video(tmp_path, 85)
    runner = _LinkProbeRunner()

    with caplog.at_level(logging.WARNING):
        run_single(make_context(transport, runner=runner, tmp_path=tmp_path), UploadTarget(1, TITLE, video), single_meta())

    assert runner.seen[0][0] == video.name
    assert any("短文件名不可用" in record.message for record in caplog.records)
