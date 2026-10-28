"""`bili_subtitle`：SRT/VTT → BCC 转换、语言标识映射、以及「字幕失败不影响稿件」。

钉四件在真实账号上很贵、在本地很容易写错的事：

1. 时间单位是**秒**不是毫秒 —— 传错单位整条字幕会挤在开头几秒，而平台返回 `code=0`，
   没有任何报错能提示你；
2. 语言标识用的是**平台白名单**（`zh` / `en` / `zh-Hant` …），不是常见的 `zh-CN` / `en-US`
   —— 后者实测被拒为 `code=79011 不合法的语言`；
3. 上传失败**一律降级**，绝不能把一条已发布的稿件说成失败；
4. VTT 的行内标签要剥掉 —— YouTube 的自动字幕带 `<c>`，原样传上去就是一排尖括号；
5. 滚动字幕的**10 毫秒过渡条**要丢掉 —— 它让段数虚高一倍（Ep 130：239 段 / 122 句），
   也是「CC 字幕已上传（239 条，en）」这句话里那个数字看不懂的原因。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bilibili.bili_api import BiliApiError
from app.bilibili.bili_subtitle import (
    DEFAULT_LAN,
    LAN_CHINESE,
    LAN_ENGLISH_BILI,
    SUBTITLE_FAILED,
    SUBTITLE_POSTED,
    SUBTITLE_SKIPPED,
    TRANSITION_MAX_SECONDS,
    bilibili_language,
    drop_transition_cues,
    language_display,
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

# 滚动字幕的真实片段：从本机 `C++ Weekly - Ep 130` 的 `.en.vtt` 里**逐字**抄下来的前四块。
# 形状是「滚屏条 / 10 毫秒过渡条 / 滚屏条 / 10 毫秒过渡条」交替：
#   0.03-3.05 A          ← 滚屏条（带 <c> 与行内时间戳）
#   3.05-3.06 A          ← 过渡条（10 毫秒，内容就是上一行）
#   3.06-5.12 A ↵ B      ← 滚屏条（上一行 + 新一行）
#   5.12-5.13 B          ← 过渡条
# 唯一与原文不同的是**去掉了原文件里那些「只有一个空格」的行**（它们本来就被 `_clean_line`
# 与 `strip()` 过滤，结果等价）—— 保留它们会让本文件带上行尾空白，`git diff --check` 会红。
ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.030 --> 00:00:03.050 align:start position:0%
hello<00:00:00.690><c> and</c><00:00:00.989><c> welcome</c><00:00:01.530><c> to</c><00:00:01.560><c> C++</c><00:00:01.949><c> weekly</c><00:00:02.610><c> I'm</c><00:00:02.760><c> your</c>

00:00:03.050 --> 00:00:03.060 align:start position:0%
hello and welcome to C++ weekly I'm your

00:00:03.060 --> 00:00:05.120 align:start position:0%
hello and welcome to C++ weekly I'm your
host<00:00:03.210><c> Jason</c><00:00:03.600><c> Turner</c><00:00:03.959><c> I</c><00:00:04.230><c> am</c><00:00:04.589><c> available</c><00:00:04.830><c> for</c>

00:00:05.120 --> 00:00:05.130 align:start position:0%
host Jason Turner I am available for
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


# ---------------------------------------------------------------- 过渡条
def test_rolling_transition_cues_are_dropped() -> None:
    """真实滚动字幕：4 段里 2 段是 10 毫秒过渡条，留下的正是「一句一条」。"""
    cues = parse_subtitle_text(ROLLING_VTT)
    assert len(cues) == 4
    kept = drop_transition_cues(cues)
    assert [(cue.start, cue.end, cue.content) for cue in kept] == [
        (0.03, 3.05, "hello and welcome to C++ weekly I'm your"),
        (3.06, 5.12, "hello and welcome to C++ weekly I'm your\nhost Jason Turner I am available for"),
    ]


def test_a_short_cue_with_unique_text_is_kept() -> None:
    """**只按时长删是错的**：真的只有几十毫秒、内容却独一无二的短字幕必须留下。"""
    cues = parse_subtitle_text(
        "00:00:01.000 --> 00:00:02.000\nfirst line\n\n"
        "00:00:02.000 --> 00:00:02.030\nHey!\n\n"
        "00:00:02.030 --> 00:00:03.000\nsecond line\n"
    )
    assert [cue.content for cue in drop_transition_cues(cues)] == ["first line", "Hey!", "second line"]


def test_a_short_line_is_kept_when_the_same_text_only_appears_far_away() -> None:
    """判据只看**紧邻**的两条：同一句话在别处（隔几秒）出现过，不构成「过渡条」的理由。

    一段 30 毫秒、内容独一无二于邻居的短字幕必须留下 —— 这正是「只按内容出现过就删」会踩的坑。
    """
    cues = parse_subtitle_text(
        "00:00:01.000 --> 00:00:02.000\nsure\n\n"
        "00:00:02.000 --> 00:00:02.030\nagain\n\n"
        "00:00:02.030 --> 00:00:03.000\nother\n\n"
        "00:00:04.000 --> 00:00:05.000\nagain\n"
    )
    assert [cue.content for cue in drop_transition_cues(cues)] == ["sure", "again", "other", "again"]


def test_the_transition_threshold_stays_below_a_visible_frame() -> None:
    """阈值是**实测归纳**的：Ep 130 里 119 条过渡条时长正好 0.010 s。

    取 0.05 s（3 帧）为界：再放大就会开始吞掉真字幕。
    """
    assert 0.01 <= TRANSITION_MAX_SECONDS <= 0.05


def test_load_bcc_drops_the_transitions_end_to_end(tmp_path: Path) -> None:
    """端到端：**239 段 → 122 段**这件事必须发生在 `load_bcc` 这条唯一入口上。"""
    path = tmp_path / "clip.en.vtt"
    path.write_text(ROLLING_VTT, encoding="utf-8")
    data, count = load_bcc(path)
    assert count == 2
    body = json.loads(data)["body"]
    assert [item["content"] for item in body] == [
        "hello and welcome to C++ weekly I'm your",
        "hello and welcome to C++ weekly I'm your\nhost Jason Turner I am available for",
    ]
    assert all(item["to"] - item["from"] > TRANSITION_MAX_SECONDS for item in body)


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


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("en", "英文"),
        ("zh", "中文"),
        ("zh-Hant", "繁体中文"),
        ("zh-TW", "繁体中文"),
        ("zh-HK", "繁体中文"),
        (None, "未知语言"),
        ("", "未知语言"),
        ("xx", "xx"),
    ],
)
def test_language_display_never_leaks_a_platform_code(code: str | None, text: str) -> None:
    """界面说「英文」不说 `en`。认不出来的码**原样回显**，不猜成别的语言。"""
    assert language_display(code) == text


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


def test_the_success_message_says_what_happened_not_how_many_cues(tmp_path: Path) -> None:
    """**这条是用户报的那个问题的回归**：界面上那句「CC 字幕已上传（239 条，en）」。

    `239` 是转换器的段数（既不是字数也不是句数），`en` 是发给接口的平台码 —— 两个中间量
    都不该出现在结论里。结论只讲发生了什么、观众能做什么。
    """
    transport = StubTransport({"code": 0, "data": None})
    outcome = upload_subtitle(
        transport, JAR, aid=111, bvid="BV1", cid=222, path=_write(tmp_path), lan="en"
    )
    assert outcome.state == SUBTITLE_POSTED
    assert outcome.message == "CC 字幕已上传（英文），观众可在播放器里开关"
    assert "en" not in outcome.message
    assert "条" not in outcome.message
    # 段数没有丢，只是留在字段与日志里（排障要看它）。
    assert outcome.cue_count == 2


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
