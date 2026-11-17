"""把对齐结果渲染成可交付的字幕文件。

**只渲染，不翻译、不改时间**：`AlignedCue` 的时间来自源字幕，这里只是把它们排成
SRT / 对照 SRT 的文本。导出前必须过 `validate_alignment`，校验不通过就不写文件。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from ..bilibili.bili_subtitle import to_bcc_entries
from .align import AlignedCue, Cue, validate_alignment
from .provider import TranslationError


def _timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(cues: Sequence[AlignedCue]) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.translation}\n"
        )
    return "\n".join(blocks)


def bilingual_content(source: str, translation: str, *, source_first: bool = True) -> str:
    """把原文与译文放进**同一条 cue 的 content**，两行共用一个时间轴。

    默认英上中下（用户确认的默认值）。两边各自先把内部换行规整成空格，
    否则源文件里的 VTT 换行会让播放器显示三行以上；规整只改排版，不改词序。
    任一边为空都拒绝 —— 那不是双语，是单语加一行空白。
    """

    def flatten(text: str) -> str:
        return " ".join(part.strip() for part in text.splitlines() if part.strip()).strip()

    original, translated = flatten(source), flatten(translation)
    if not original or not translated:
        raise TranslationError("双语字幕需要原文和译文都不为空")
    first, second = (original, translated) if source_first else (translated, original)
    return f"{first}\n{second}"


def render_bilingual_upload_srt(
    cues: Sequence[AlignedCue], *, source_first: bool = True
) -> str:
    """导出给 B站 用的双语 SRT：一条 cue、两行内容。

    上传链路仍走 `bili_subtitle.upload_subtitle`，它读 SRT 时 `parse_subtitle_text`
    会把两行合并进同一个 `content`，再由 `to_bcc_entries` 原样提交。
    """
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        content = bilingual_content(cue.source, cue.translation, source_first=source_first)
        blocks.append(
            f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{content}\n"
        )
    return "\n".join(blocks)


def render_bilingual_bcc(cues: Sequence[AlignedCue], *, source_first: bool = True) -> str:
    """双语 BCC：与 `to_bcc` 共用同一份结构，只有 `content` 变成两行。"""
    return to_bcc_entries(
        [
            (cue.start, cue.end, bilingual_content(cue.source, cue.translation, source_first=source_first))
            for cue in cues
        ]
    )


def render_bilingual_srt(cues: Sequence[AlignedCue]) -> str:
    """原上译下，给用户核对用。**不上传 B站**（BCC 只放译文）。"""
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n"
            f"{cue.source}\n{cue.translation}\n"
        )
    return "\n".join(blocks)


def export_files(
    source: Sequence[Cue],
    aligned: Sequence[AlignedCue],
    source_path: str | Path,
) -> tuple[str, str]:
    """写出 `<原名>.zh.srt` 与 `<原名>.bilingual.srt`，返回两条路径。

    先校验后写：时间或条数对不上时抛 `TranslationError`，绝不产出「看起来合法但错位」的字幕。
    """
    errors = validate_alignment(source, aligned)
    if errors:
        raise TranslationError("；".join(errors[:5]))
    if any(not cue.translation.strip() for cue in aligned):
        raise TranslationError("还有条目没有译文，不能导出")

    path = Path(source_path)
    srt_path = path.with_name(f"{path.stem}.zh.srt")
    bilingual_path = path.with_name(f"{path.stem}.bilingual.srt")
    srt_path.write_text(render_srt(aligned), encoding="utf-8-sig")
    bilingual_path.write_text(render_bilingual_srt(aligned), encoding="utf-8-sig")
    return str(srt_path), str(bilingual_path)
