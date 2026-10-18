"""`bili_subtitle`：SRT/VTT → BCC 转换、语言标识映射、以及「字幕失败不影响稿件」。

钉四件在真实账号上很贵、在本地很容易写错的事：

1. 时间单位是**秒**不是毫秒 —— 传错单位整条字幕会挤在开头几秒，而平台返回 `code=0`，
   没有任何报错能提示你；
2. 语言标识用的是**平台白名单**（`zh` / `en` / `zh-Hant` …），不是常见的 `zh-CN` / `en-US`
   —— 后者实测被拒为 `code=79011 不合法的语言`；
3. 上传失败**一律降级**，绝不能把一条已发布的稿件说成失败；
4. VTT 的行内标签要剥掉 —— YouTube 的自动字幕带 `<c>`，原样传上去就是一排尖括号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bili_api import BiliApiError
from app.bili_subtitle import (
    DEFAULT_LAN,
    LAN_CHINESE,
    LAN_ENGLISH_BILI,
    SUBTITLE_FAILED,
    SUBTITLE_POSTED,
    SUBTITLE_SKIPPED,
    bilibili_language,
    load_bcc,
    merge_repeated_cues,
    parse_subtitle_text,
    parse_timestamp,
    to_bcc,
    upload_subtitle,
)
from bili_fakes import StubTransport

JAR = {"SESSDATA": "sess", "bili_jct": "jct", "DedeUserID": "42"}

SRT = """1
00:00:01,000 --> 00:00:03,500
Hello there

2
00:00:04,000 --> 00:00:06,000
Second line
with a break
"""

VTT = """WEBVTT
Kind: captions
Language: en

NOTE this block must be skipped

1
00:00:01.000 --> 00:00:03.500 align:start position:0%
<c>Hello</c> there

00:00:04.000 --> 00:00:06.000
Second &amp; line
"""


# ---------------------------------------------------------------- 时间
@pytest.mark.parametrize(
    "text,seconds",
    [
        ("00:00:01,000", 1.0),
        ("00:00:01.500", 1.5),
        ("00:01:02,250", 62.25),
        ("01:02,500", 62.5),  # MM:SS.mmm（SRT 允许省略小时）
        ("1:02:03,000", 3723.0),
        ("00:00:00.000", 0.0),
    ],
)
def test_timestamps_are_parsed_to_seconds(text: str, seconds: float) -> None:
    assert parse_timestamp(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "abc", "00-00-01", "1.5"])
def test_unparsable_timestamps_are_none_not_zero(text: str) -> None:
    """解析不出来返回 `None`，**不猜 0** —— 猜 0 会让一条坏字幕变成一条「从 0 秒开始」的字幕。"""
    assert parse_timestamp(text) is None


# ---------------------------------------------------------------- 解析
def test_srt_is_parsed() -> None:
    cues = parse_subtitle_text(SRT)
    assert [(cue.start, cue.end, cue.content) for cue in cues] == [
        (1.0, 3.5, "Hello there"),
        (4.0, 6.0, "Second line\nwith a break"),
    ]


def test_vtt_header_note_and_inline_tags_are_stripped() -> None:
    cues = parse_subtitle_text(VTT)
    assert [cue.content for cue in cues] == ["Hello there", "Second & line"]
    assert all("<" not in cue.content for cue in cues)


def test_blocks_without_a_timing_line_are_skipped() -> None:
    assert parse_subtitle_text("WEBVTT\n\nno timing here\n") == []


def test_cues_with_an_empty_or_inverted_range_are_dropped() -> None:
    assert parse_subtitle_text("00:00:05.000 --> 00:00:01.000\nx\n") == []
    assert parse_subtitle_text("00:00:01.000 --> 00:00:02.000\n\n") == []


def test_merge_only_touches_identical_neighbours() -> None:
    """YouTube 的自动字幕会把同一句切成多条重叠轨；内容不同的一律不合并（那是两条字幕）。"""
    cues = parse_subtitle_text(
        "00:00:01.000 --> 00:00:02.000\nsame\n\n"
        "00:00:02.000 --> 00:00:03.000\nsame\n\n"
        "00:00:03.000 --> 00:00:04.000\ndifferent\n"
    )
    merged = merge_repeated_cues(cues)
    assert len(merged) == 2
    assert (merged[0].start, merged[0].end) == (1.0, 3.0)
    assert merged[1].content == "different"


# ---------------------------------------------------------------- BCC
def test_bcc_uses_seconds_and_the_platform_shape() -> None:
    payload = json.loads(to_bcc(parse_subtitle_text(SRT)))
    assert payload["body"][0] == {"from": 1.0, "to": 3.5, "location": 2, "content": "Hello there"}
    assert set(payload) == {
        "font_size",
        "font_color",
        "background_alpha",
        "background_color",
        "Stroke",
        "body",
    }


def test_bcc_keeps_chinese_readable() -> None:
    payload = json.loads(to_bcc(parse_subtitle_text("00:00:01.000 --> 00:00:02.000\n中文\n")))
    assert payload["body"][0]["content"] == "中文"


def test_load_bcc_reads_a_real_file(tmp_path: Path) -> None:
    path = tmp_path / "clip.en.srt"
    path.write_text(SRT, encoding="utf-8")
    data, count = load_bcc(path)
    assert count == 2
    assert json.loads(data)["body"][0]["content"] == "Hello there"


def test_load_bcc_tolerates_a_bom(tmp_path: Path) -> None:
    path = tmp_path / "clip.en.vtt"
    path.write_text("\ufeff" + VTT, encoding="utf-8")
    _data, count = load_bcc(path)
    assert count == 2


# ---------------------------------------------------------------- 语言标识
@pytest.mark.parametrize(
    "code,expected",
    [
        ("en", "en"),
        ("en-US", "en"),
        ("en-orig", "en"),
        ("zh-Hans", "zh"),
        ("zh-CN", "zh"),
        ("zh", "zh"),
        ("zh-Hant", "zh-Hant"),
        ("zh-TW", "zh-TW"),
        ("zh-HK", "zh-HK"),
        ("zh-MO", "zh-HK"),
    ],
)
def test_language_codes_map_onto_the_platform_whitelist(code: str, expected: str) -> None:
    """**取值来自实测**：`zh-CN` / `en-US` / `zh-Hans` 会被平台拒为 `79011 不合法的语言`。

    需求里举例的 `zh-CN` / `en-US` 在这一列上**不合法**，所以这里绝不能照抄。
    """
    assert bilibili_language(code) == expected


def test_unknown_language_falls_back_to_english() -> None:
    assert bilibili_language(None) == DEFAULT_LAN == LAN_ENGLISH_BILI
    assert bilibili_language("ja") == DEFAULT_LAN
    assert bilibili_language("") == DEFAULT_LAN


def test_chinese_default_is_the_simplified_code() -> None:
    assert bilibili_language("zh") == LAN_CHINESE


# ---------------------------------------------------------------- 上传
def _write(tmp_path: Path, name: str = "clip.en.srt") -> Path:
    path = tmp_path / name
    path.write_text(SRT, encoding="utf-8")
    return path


def test_upload_posts_the_draft_save_endpoint(tmp_path: Path) -> None:
    transport = StubTransport({"code": 0, "data": None})
    outcome = upload_subtitle(
        transport, JAR, aid=111, bvid="BV1", cid=222, path=_write(tmp_path), lan="en"
    )
    assert outcome.state == SUBTITLE_POSTED
    assert outcome.cue_count == 2
    assert transport.calls[0]["url"].endswith("/x/v2/dm/subtitle/draft/save")

    form = transport.calls[0]["form"]
    assert form["aid"] == 111
    assert form["bvid"] == "BV1"
    # `oid` 必须是 cid 而不是 aid —— 混用会得到那句没有信息量的 `-404 啥都木有`。
    assert form["oid"] == 222
    assert form["lan"] == "en"
    # 两个布尔按小写字面量发：Python 的 `True` 经 urlencode 会变成 `True`，平台认不出。
    assert form["sign"] == "false"
    assert form["submit"] == "true"
    assert json.loads(form["data"])["body"][0]["content"] == "Hello there"


def test_upload_failure_becomes_a_conclusion_never_an_exception(tmp_path: Path) -> None:
    """字幕是附加物：上传失败**不能**把一条已发布的稿件说成失败。"""
    transport = StubTransport({"code": 79011, "message": "不合法的语言"})
    outcome = upload_subtitle(
        transport, JAR, aid=111, bvid="BV1", cid=222, path=_write(tmp_path), lan="zh-CN"
    )
    assert outcome.state == SUBTITLE_FAILED
    assert "79011" in outcome.message


def test_network_failure_also_stays_a_conclusion(tmp_path: Path) -> None:
    transport = StubTransport(error=BiliApiError(None, "boom", where="上传字幕"))
    outcome = upload_subtitle(
        transport, JAR, aid=111, bvid="BV1", cid=222, path=_write(tmp_path), lan="en"
    )
    assert outcome.state == SUBTITLE_FAILED


def test_missing_file_is_skipped_not_failed(tmp_path: Path) -> None:
    transport = StubTransport({"code": 0})
    outcome = upload_subtitle(
        transport, JAR, aid=111, bvid="BV1", cid=222, path=tmp_path / "gone.srt", lan="en"
    )
    assert outcome.state == SUBTITLE_SKIPPED
    assert transport.calls == []


def test_unsupported_extension_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "clip.en.ass"
    path.write_text(SRT, encoding="utf-8")
    transport = StubTransport({"code": 0})
    outcome = upload_subtitle(transport, JAR, aid=1, bvid="BV1", cid=2, path=path, lan="en")
    assert outcome.state == SUBTITLE_SKIPPED
    assert transport.calls == []


def test_empty_subtitle_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "clip.en.srt"
    path.write_text("WEBVTT\n\n", encoding="utf-8")
    transport = StubTransport({"code": 0})
    outcome = upload_subtitle(transport, JAR, aid=1, bvid="BV1", cid=2, path=path, lan="en")
    assert outcome.state == SUBTITLE_SKIPPED
    assert transport.calls == []


def test_missing_csrf_is_a_readable_failure(tmp_path: Path) -> None:
    """没有 bili_jct 时要给出可执行的说明，而不是一个空错误。"""
    transport = StubTransport({"code": 0})
    outcome = upload_subtitle(
        transport, {"SESSDATA": "sess"}, aid=1, bvid="BV1", cid=2, path=_write(tmp_path), lan="en"
    )
    assert outcome.state == SUBTITLE_FAILED
    assert "bili_jct" in outcome.message
