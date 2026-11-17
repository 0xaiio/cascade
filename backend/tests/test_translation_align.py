"""字幕翻译的纯领域层：解析、分块、逐条对齐、断行、导出前校验。

这一层不联网、不读库、不碰前端。用例刻意覆盖**错位**这种最危险的失败：
译文串到相邻条目上时，时间轴看起来仍是合法 SRT，用户却看到整段错字幕 ——
所以断言必须钉在「序号集合完全一致」上，而不是只看条数。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bilibili.bili_subtitle import Cue
from app.translation.align import (
    AlignedCue,
    apply_chunk,
    assemble,
    make_chunks,
    parse_subtitle_file,
    validate_alignment,
    wrap_chinese,
)
from app.translation.provider import (
    TranslationChunk,
    TranslationError,
    TranslationItem,
    TranslationReply,
    replies_from_raw,
)
from translation_fakes import FakeTranslationProvider


def cues(count: int, *, text: str = "Hello world") -> tuple[Cue, ...]:
    return tuple(
        Cue(start=index * 2 + 0.25, end=index * 2 + 1.75, content=f"{text} {index + 1}")
        for index in range(count)
    )


def replies_for(items: list[TranslationItem]) -> list[TranslationReply]:
    return [TranslationReply(index=item.index, text=f"译文{item.index + 1}") for item in items]


# ---------------------------------------------------------------- 解析
def test_parse_srt_keeps_every_cue_including_rolling_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "a.en.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nSame line\n\n"
        "2\n00:00:01,900 --> 00:00:03,000\nSame line\n",
        encoding="utf-8",
    )
    got = parse_subtitle_file(path)
    # 翻译态绝不合并：滚动重复条也要逐条对应，合并只属于导出 BCC 那条路径。
    assert len(got) == 2
    assert [cue.content for cue in got] == ["Same line", "Same line"]
    assert (got[0].start, got[0].end) == (1.0, 2.0)


def test_parse_vtt_strips_tags_and_tolerates_bom(tmp_path: Path) -> None:
    path = tmp_path / "a.en.vtt"
    path.write_text(
        "\ufeffWEBVTT\n\nNOTE this is ignored\n\n"
        "00:00:01.500 --> 00:00:02.500 align:start position:0%\n"
        "<c>Hello</c> &amp; goodbye\n",
        encoding="utf-8",
    )
    got = parse_subtitle_file(path)
    assert len(got) == 1
    assert got[0].content == "Hello & goodbye"
    assert got[0].start == 1.5


def test_source_file_is_only_read(tmp_path: Path) -> None:
    path = tmp_path / "a.en.srt"
    original = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    path.write_text(original, encoding="utf-8")
    before = path.stat().st_mtime_ns
    parse_subtitle_file(path)
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime_ns == before


# ---------------------------------------------------------------- 分块
def test_chunks_cover_every_index_exactly_once() -> None:
    chunks = make_chunks(cues(7), max_cues=3, max_chars=1000)
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert [item.index for chunk in chunks for item in chunk.items] == list(range(7))
    assert [len(chunk.items) for chunk in chunks] == [3, 3, 1]


def test_chunk_carries_one_previous_source_line_as_context() -> None:
    chunks = make_chunks(cues(5), max_cues=2, max_chars=1000)
    assert chunks[0].context == ""
    assert chunks[1].context == "Hello world 2"
    assert chunks[2].context == "Hello world 4"


def test_char_budget_splits_before_cue_count() -> None:
    source = cues(6, text="x" * 30)  # 每条 30+ 字符
    chunks = make_chunks(source, max_cues=100, max_chars=70)
    assert [len(chunk.items) for chunk in chunks] == [2, 2, 2]


def test_oversized_single_cue_gets_its_own_chunk_without_looping() -> None:
    source = (Cue(0.0, 1.0, "x" * 20000),)
    chunks = make_chunks(source, max_cues=4, max_chars=10)
    assert len(chunks) == 1
    assert chunks[0].items[0].index == 0


def test_invalid_chunk_limits_raise() -> None:
    with pytest.raises(ValueError):
        make_chunks(cues(2), max_cues=0)
    with pytest.raises(ValueError):
        make_chunks(cues(2), max_chars=0)


# ---------------------------------------------------------------- 回填校验
def test_apply_chunk_accepts_a_complete_reply() -> None:
    chunk = make_chunks(cues(3))[0]
    got = apply_chunk(chunk, replies_for(list(chunk.items)))
    assert got == {0: "译文1", 1: "译文2", 2: "译文3"}


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        ([TranslationReply(index=0, text="甲"), TranslationReply(index=1, text="乙")], "应返回 3 条"),
        (
            [
                TranslationReply(index=0, text="甲"),
                TranslationReply(index=1, text="乙"),
                TranslationReply(index=1, text="乙"),
            ],
            "重复",
        ),
        (
            [
                TranslationReply(index=0, text="甲"),
                TranslationReply(index=1, text="乙"),
                TranslationReply(index=9, text="丙"),
            ],
            "序号对不上",
        ),
        (
            [
                TranslationReply(index=0, text="甲"),
                TranslationReply(index=1, text="乙"),
                TranslationReply(index=2, text=""),
            ],
            "却是空的",
        ),
    ],
)
def test_apply_chunk_rejects_the_whole_chunk_on_bad_replies(
    bad: list[TranslationReply], needle: str
) -> None:
    chunk = make_chunks(cues(3))[0]
    with pytest.raises(TranslationError, match=needle):
        apply_chunk(chunk, bad)


def test_apply_chunk_allows_an_empty_translation_when_the_source_is_empty() -> None:
    source = (Cue(0.0, 1.0, "Hello"), Cue(1.0, 2.0, ""))
    chunk = make_chunks(source)[0]
    got = apply_chunk(
        chunk,
        [TranslationReply(index=0, text="你好"), TranslationReply(index=1, text="")],
    )
    assert got == {0: "你好", 1: ""}


def test_raw_reply_parser_refuses_bool_index_and_non_string_text() -> None:
    good = [{"i": 0, "text": "你好"}]
    assert replies_from_raw(good) == [TranslationReply(index=0, text="你好")]

    with pytest.raises(TranslationError, match="不是整数"):
        replies_from_raw([{"i": True, "text": "你好"}])
    with pytest.raises(TranslationError, match="不是文本"):
        replies_from_raw([{"i": 0, "text": 7}])
    with pytest.raises(TranslationError, match="不是条目列表"):
        replies_from_raw(json.loads('{"i":0,"text":"你好"}'))


# ---------------------------------------------------------------- 断行与装配
def test_wrap_chinese_keeps_every_character_but_adds_breaks() -> None:
    text = "这是一段没有标点的很长很长的中文译文用来测试断行是否丢字" * 2
    wrapped = wrap_chinese(text, width=10)
    assert wrapped != text
    assert wrapped.replace("\n", "") == text


def test_wrap_chinese_prefers_punctuation_and_keeps_existing_breaks() -> None:
    text = "第一句话，第二句话，第三句话。\n另起一行也照旧处理，不要合并。"
    wrapped = wrap_chinese(text, width=8)
    # 断在标点之后，而不是把「第三句话」劈成两半。
    assert wrapped.splitlines()[0].endswith("，")
    assert wrapped.replace("\n", "") == text.replace("\n", "")


def test_wrap_rejects_a_zero_width() -> None:
    with pytest.raises(ValueError):
        wrap_chinese("你好", width=0)


def test_assemble_copies_timestamps_verbatim_and_keeps_source() -> None:
    source = cues(3)
    aligned = assemble(source, {0: "译文1", 2: "译文3"})
    assert len(aligned) == 3
    for original, result in zip(source, aligned, strict=True):
        assert (result.start, result.end) == (original.start, original.end)
        assert result.source == original.content
    assert aligned[1].translation == ""  # 没译出来就留空，绝不拿原文冒充译文


def test_edits_win_and_are_never_auto_wrapped() -> None:
    source = cues(1, text="x" * 60)
    edited = "用户自己断的\n第二行"
    aligned = assemble(source, {0: "机器译文"}, edits={0: edited})
    assert aligned[0].translation == edited
    assert aligned[0].edited is True


# ---------------------------------------------------------------- 硬性对齐校验
def test_validate_alignment_passes_a_clean_result() -> None:
    source = cues(3)
    assert validate_alignment(source, assemble(source, {i: f"译文{i + 1}" for i in range(3)})) == ()


def test_validate_alignment_catches_shifted_index_and_changed_time() -> None:
    source = cues(2)
    good = assemble(source, {0: "甲", 1: "乙"})
    shifted = (
        AlignedCue(index=1, start=good[0].start, end=good[0].end, source=good[0].source, translation="甲"),
        AlignedCue(index=1, start=good[1].start, end=good[1].end, source=good[1].source, translation="乙"),
    )
    errors = validate_alignment(source, shifted)
    assert any("序号错位" in error for error in errors)

    moved_time = (
        AlignedCue(index=0, start=9.0, end=good[0].end, source=good[0].source, translation="甲"),
        good[1],
    )
    assert any("时间轴被改动" in error for error in validate_alignment(source, moved_time))


def test_validate_alignment_catches_count_and_empty_translation() -> None:
    source = cues(2)
    assert any("条数不一致" in error for error in validate_alignment(source, assemble(source, {0: "甲"})[:1]))
    assert any("译文为空" in error for error in validate_alignment(source, assemble(source, {0: "甲"})))


# ---------------------------------------------------------------- 假 provider 走通全链路
def test_fake_provider_drives_the_whole_domain_path() -> None:
    source = cues(5)
    chunks = make_chunks(source, max_cues=2)
    provider = FakeTranslationProvider()
    translations: dict[int, str] = {}
    for chunk in chunks:
        replies = provider.translate(chunk.items, context=chunk.context)
        translations.update(apply_chunk(chunk, replies))

    assert len(provider.calls) == 3
    assert provider.calls[1][1] == "Hello world 2"  # context 只是提示，不在回填集合里
    aligned = assemble(source, translations)
    assert validate_alignment(source, aligned) == ()
    assert [item.translation for item in aligned] == [f"译文{i + 1}" for i in range(5)]


def test_fake_provider_can_simulate_a_bad_chunk_without_network() -> None:
    provider = FakeTranslationProvider()
    provider.queue(
        replies=[TranslationReply(index=0, text="甲"), TranslationReply(index=5, text="乙")]
    )
    items = [TranslationItem(index=0, text="A"), TranslationItem(index=1, text="B")]
    bad_replies = provider.translate(items)
    # 替身只负责「给什么回复」，拒绝错位是 align 的职责 —— 两者分开才测得出校验。
    with pytest.raises(TranslationError, match="序号对不上"):
        apply_chunk(
            TranslationChunk(index=0, items=tuple(items)),
            bad_replies,
        )
