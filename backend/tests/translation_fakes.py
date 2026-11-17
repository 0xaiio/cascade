"""翻译服务的测试替身。绝不联网，行为完全由用例决定。

与 `bili_fakes` 同样的纪律：假实现必须能表达真实实现会出现的**坏回复**
（缺条、错位、重复序号、空译文），否则对齐校验永远不会被测到。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.translation.provider import (
    TranslationError,
    TranslationItem,
    TranslationProvider,
    TranslationReply,
)


class FakeTranslationProvider:
    """按调用次序给回复；可指定某一发抛错或返回坏结果。"""

    def __init__(self, *, replies: Sequence[Sequence[TranslationReply]] | None = None) -> None:
        self.replies: list[list[TranslationReply]] = [list(r) for r in (replies or [])]
        self.calls: list[tuple[tuple[TranslationItem, ...], str]] = []
        self.errors: list[Exception | None] = []

    def queue(self, replies: Sequence[TranslationReply], error: Exception | None = None) -> None:
        self.replies.append(list(replies))
        self.errors.append(error)

    def translate(self, items: Sequence[TranslationItem], *, context: str = "") -> list[TranslationReply]:
        self.calls.append((tuple(items), context))
        index = len(self.calls) - 1
        if index < len(self.errors) and self.errors[index] is not None:
            raise self.errors[index]  # type: ignore[misc]
        if index >= len(self.replies):
            # 默认行为：照原样回一条中文，方便只关心「跑通」的用例少写夹具。
            return [TranslationReply(index=item.index, text=f"译文{item.index + 1}") for item in items]
        return list(self.replies[index])


def echo_translator(items: Sequence[TranslationItem], *, context: str = "") -> list[TranslationReply]:
    """一个满足 Protocol 的函数式替身，用来证明契约不要求类。"""
    return [TranslationReply(index=item.index, text=f"译：{item.text}") for item in items]


# 让类型检查器验证假实现确实满足协议（运行时无副作用）。
_PROTOCOL_CHECK: TranslationProvider = FakeTranslationProvider()
TRANSLATION_ERROR_CHECK = TranslationError
