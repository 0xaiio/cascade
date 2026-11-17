"""双语字幕的纯渲染层：两行进同一条 cue，结构与 B站 BCC 完全一致。

同步不是靠"把两条轨的时间调一样"实现的 —— 双语两行根本没有各自的时间戳，
它们共用唯一一个 `from`/`to`。因此这里的断言都钉在「一条 cue、一个时间、两行内容」上。
"""

from __future__ import annotations

import json

import pytest

from app.bilibili.bili_subtitle import BCC_FONT_SIZE, BCC_LOCATION
from app.translation.align import AlignedCue
from app.translation.provider import TranslationError
from app.translation.render import (
    bilingual_content,
    render_bilingual_bcc,
    render_bilingual_upload_srt,
)

CUES = [
    AlignedCue(0, 1.25, 3.5, "Hello world", "你好世界", False),
    AlignedCue(1, 4.0, 6.75, "Second\nline", "第二行\n译文", False),
]


def test_content_is_one_cue_with_two_lines_in_english_first_order() -> None:
    assert bilingual_content("Hello world", "你好世界") == "Hello world\n你好世界"
    assert bilingual_content("Hello", "你好", source_first=False) == "你好\nHello"


def test_internal_newlines_are_flattened_so_the_player_shows_exactly_two_lines() -> None:
    got = bilingual_content("Second\nline", "第二行\n译文")
    assert got.splitlines() == ["Second line", "第二行 译文"]


@pytest.mark.parametrize(("source", "translated"), [("", "你好"), ("Hello", ""), ("  ", "你好")])
def test_missing_either_language_is_rejected(source: str, translated: str) -> None:
    with pytest.raises(TranslationError, match="都不为空"):
        bilingual_content(source, translated)


def test_upload_srt_has_one_timing_block_per_cue_and_two_text_lines() -> None:
    text = render_bilingual_upload_srt(CUES)
    blocks = [block for block in text.split("\n\n") if block.strip()]
    assert len(blocks) == len(CUES)
    assert "00:00:01,250 --> 00:00:03,500" in blocks[0]
    assert blocks[0].splitlines()[2:] == ["Hello world", "你好世界"]
    assert blocks[1].splitlines()[2:] == ["Second line", "第二行 译文"]
    assert "\n\n1\n" not in text[1:]  # 没有把两行拆成两条 cue


def test_bcc_uses_the_platform_shape_with_two_line_content() -> None:
    payload = json.loads(render_bilingual_bcc(CUES))
    assert payload["font_size"] == BCC_FONT_SIZE
    assert set(payload) == {"font_size", "font_color", "background_alpha", "background_color", "Stroke", "body"}
    assert len(payload["body"]) == 2
    first = payload["body"][0]
    assert first["from"] == 1.25 and first["to"] == 3.5  # 秒，不是毫秒
    assert first["location"] == BCC_LOCATION
    assert first["content"] == "Hello world\n你好世界"
    assert all(len(entry["content"].splitlines()) == 2 for entry in payload["body"])


def test_bcc_and_srt_agree_on_every_timestamp() -> None:
    """两条导出路径必须由同一份 AlignedCue 驱动，不能各自算时间。"""
    payload = json.loads(render_bilingual_bcc(CUES))
    srt = render_bilingual_upload_srt(CUES)
    for cue, entry in zip(CUES, payload["body"], strict=True):
        assert f"{cue.start:.3f}" in str(entry["from"]) or entry["from"] == round(cue.start, 3)
        assert f"{cue.end:.3f}" in str(entry["to"]) or entry["to"] == round(cue.end, 3)
    assert "00:00:04,000 --> 00:00:06,750" in srt
