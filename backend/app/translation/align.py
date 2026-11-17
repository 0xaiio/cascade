"""字幕解析、分块与**逐条对齐**。本功能最贵的判据全在这一层。

时间轴不属于翻译：译文只是原文每一条的「翻译属性」。因此本模块
**没有任何写 `start` / `end` 的代码路径** —— 时间值只会从源 `Cue` 原样搬到
`AlignedCue`。这是「逐条对齐」能成立的结构性保证，而不是靠测试碰巧守住的约定。

解析复用 `bili_subtitle.parse_subtitle_text`（SRT / VTT 共用一套，已处理 WEBVTT 抬头、
`<c>` 行内标签与两种时间戳分隔符）。这里刻意**不调用** `merge_repeated_cues`：
YouTube 自动字幕的滚动重复条也必须逐条对应原文，合并只能发生在导出 BCC 的另一条路径上。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..bilibili.bili_subtitle import Cue, parse_subtitle_text
from .provider import TranslationChunk, TranslationError, TranslationItem, TranslationReply

DEFAULT_MAX_CUES = 32
DEFAULT_MAX_CHARS = 4000
# 中文字幕一行的经验宽度。B站 CC 在底部显示，太宽会横穿整个画面。
DEFAULT_WRAP_WIDTH = 18


@dataclass(frozen=True)
class AlignedCue:
    """一条「原文 + 译文」的对齐结果。时间来自源字幕，不参与翻译。"""

    index: int
    start: float
    end: float
    source: str
    translation: str
    edited: bool = False


def parse_subtitle_file(path: str | Path) -> tuple[Cue, ...]:
    """读一个 SRT/VTT 文件。**只读**，不做任何合并、去重或时间规整。"""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return tuple(parse_subtitle_text(text))


def make_chunks(
    cues: Sequence[Cue],
    *,
    max_cues: int = DEFAULT_MAX_CUES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[TranslationChunk, ...]:
    """按「条数 + 字符数」双约束切块，边界只落在整条 cue 上。

    `context` 是紧邻该块之前的一条**原文**，给模型保持语气与术语用；它不是待译条目，
    回填时按序号集合校验，因此不可能被误当成译文写进去。
    """
    if max_cues < 1 or max_chars < 1:
        raise ValueError("分块上限必须为正数")
    chunks: list[TranslationChunk] = []
    current: list[TranslationItem] = []
    chars = 0
    for index, cue in enumerate(cues):
        item = TranslationItem(index=index, text=cue.content)
        # 单条超长时独占一块，否则会出现「加不进去又永远不 flush」的死循环。
        if current and (len(current) >= max_cues or chars + len(item.text) > max_chars):
            chunks.append(_chunk(len(chunks), current, cues))
            current, chars = [], 0
        current.append(item)
        chars += len(item.text)
    if current:
        chunks.append(_chunk(len(chunks), current, cues))
    return tuple(chunks)


def _chunk(index: int, items: list[TranslationItem], cues: Sequence[Cue]) -> TranslationChunk:
    first = items[0].index
    context = cues[first - 1].content if first > 0 else ""
    return TranslationChunk(index=index, items=tuple(items), context=context)


def apply_chunk(chunk: TranslationChunk, replies: Sequence[TranslationReply]) -> dict[int, str]:
    """校验一块译文并给出 `{原序号: 译文}`；**整块不对就整块拒绝**。

    备选做法「逐条尽量回填」会让一条错位译文污染整条时间轴，而且界面上看不出来 ——
    那正是本功能最不能接受的失败模式。宁可整块重试。
    """
    expected = [item.index for item in chunk.items]
    got = [reply.index for reply in replies]
    if len(got) != len(expected):
        raise TranslationError(f"这一块应返回 {len(expected)} 条，实际 {len(got)} 条")
    if len(set(got)) != len(got):
        raise TranslationError("这一块返回了重复的条目序号")
    if set(got) != set(expected):
        missing = sorted(set(expected) - set(got))
        extra = sorted(set(got) - set(expected))
        raise TranslationError(f"条目序号对不上：缺 {missing}，多 {extra}")

    sources = {item.index: item.text for item in chunk.items}
    result: dict[int, str] = {}
    for reply in replies:
        text = reply.text.strip()
        if sources[reply.index].strip() and not text:
            raise TranslationError(f"第 {reply.index + 1} 条原文非空，译文却是空的")
        result[reply.index] = text
    return result


_LINE_BREAK = "\n"


def wrap_chinese(text: str, width: int = DEFAULT_WRAP_WIDTH) -> str:
    """给**译文**内部断行，仍是一条 cue。

    只在宽度处断，不在原文已有的换行上做合并；标点后优先断行，读起来更顺。
    原文行原样保留 —— 我们没有资格改断句之外的任何东西。
    """
    if width < 1:
        raise ValueError("断行宽度必须为正数")
    out: list[str] = []
    for line in text.split(_LINE_BREAK):
        if len(line) <= width:
            out.append(line)
            continue
        pieces: list[str] = []
        buffer = ""
        for char in line:
            buffer += char
            if len(buffer) >= width and char in "，。！？；：、,.!?;: ":
                pieces.append(buffer.strip())
                buffer = ""
            elif len(buffer) >= width + 6:  # 没有标点时硬断，避免无限变长
                pieces.append(buffer.strip())
                buffer = ""
        if buffer.strip():
            pieces.append(buffer.strip())
        out.append(_LINE_BREAK.join(p for p in pieces if p))
    return _LINE_BREAK.join(out)


def assemble(
    cues: Sequence[Cue],
    translations: dict[int, str],
    *,
    edits: dict[int, str] | None = None,
    wrap: bool = True,
) -> tuple[AlignedCue, ...]:
    """把译文按序号挂回原文，**时间原样复制**。`edits` 优先且不会被 `wrap` 改写。

    人工修订是用户自己的断句，自动断行不能动它 —— 否则用户改完再看又变了，
    等于把「已改」标记做成谎言。
    """
    edits = edits or {}
    aligned: list[AlignedCue] = []
    for index, cue in enumerate(cues):
        edited = index in edits
        text = edits[index] if edited else translations.get(index, "")
        if wrap and not edited and text:
            text = wrap_chinese(text)
        aligned.append(
            AlignedCue(
                index=index,
                start=cue.start,
                end=cue.end,
                source=cue.content,
                translation=text,
                edited=edited,
            )
        )
    return tuple(aligned)


def validate_alignment(cues: Sequence[Cue], aligned: Sequence[AlignedCue]) -> tuple[str, ...]:
    """导出前的逐条硬性校验。返回空元组表示通过；**不自动修**，只报错。"""
    errors: list[str] = []
    if len(cues) != len(aligned):
        errors.append(f"条数不一致：原文 {len(cues)}，译文 {len(aligned)}")
    for index, (source, result) in enumerate(zip(cues, aligned, strict=False)):
        if result.index != index:
            errors.append(f"第 {index + 1} 条序号错位（{result.index}）")
        if result.start != source.start or result.end != source.end:
            errors.append(f"第 {index + 1} 条时间轴被改动")
        if source.content.strip() and not result.translation.strip():
            errors.append(f"第 {index + 1} 条译文为空")
    return tuple(errors)
