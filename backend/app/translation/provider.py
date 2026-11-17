"""翻译服务的最小契约。**只有数据结构与 Protocol，没有任何 IO。**

放在 L3 领域层是因为它的使用者（分块、回填、编排）都在这一层；具体实现
（`local.py`，HTTP 调本机模型服务）在步骤 2 才加进来，届时也一样只依赖这里的类型。

为什么不直接把模型返回的 `dict` 往下游传：那样「模型说它是第 3 条」与
「我们确实拿到了第 3 条」会混在一起，而对齐是本功能最贵的一条判据。
这里把回复收敛成带类型的 `TranslationReply`，校验责任明确落在 `align.py`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class TranslationError(Exception):
    """翻译服务没能给出可用回复。带一个可给用户看的短原因。"""


@dataclass(frozen=True)
class TranslationItem:
    """一条待译文本。`index` 是**原字幕的条目序号**（0 基），不是分块内的序号。"""

    index: int
    text: str


@dataclass(frozen=True)
class TranslationReply:
    """一条译文，必须与请求的 `index` 一一对应。"""

    index: int
    text: str


class TranslationProvider(Protocol):
    """把一组条目交给某个翻译后端，拿回同样的条目集合。

    约定（实现方必须遵守）：

    - 返回的 `index` 集合必须与入参完全相同，不多、不少、不重复；
    - 原文非空时译文也必须非空（原文为空时允许返回空译文，条目仍要保留）；
    - 不返回时间码、序号说明或任何解释性文字；
    - 做不到就抛 `TranslationError`，**不要返回半份结果**（整块重试优于错位回填）。
    """

    def translate(
        self, items: Sequence[TranslationItem], *, context: str = ""
    ) -> list[TranslationReply]: ...


@dataclass(frozen=True)
class TranslationChunk:
    """一次请求对应的条目集合。`context` 只是给模型的上文提示，不参与回填。"""

    index: int
    items: tuple[TranslationItem, ...]
    context: str = ""


def replies_from_raw(raw: object) -> list[TranslationReply]:
    """把模型返回的 JSON 结构转成类型化回复；**结构不对就抛**，不猜、不修补。

    只接受 `[{"i": int, "text": str}, …]`。`bool` 是 `int` 的子类，必须显式排除 ——
    否则 `i=true` 会悄悄变成序号 1，正好制造最危险的错位。
    """
    if not isinstance(raw, list):
        raise TranslationError("模型返回的不是条目列表")
    replies: list[TranslationReply] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise TranslationError("模型返回的条目不是对象")
        index = entry.get("i")
        text = entry.get("text")
        if isinstance(index, bool) or not isinstance(index, int):
            raise TranslationError("模型返回的条目序号不是整数")
        if not isinstance(text, str):
            raise TranslationError("模型返回的译文不是文本")
        replies.append(TranslationReply(index=index, text=text))
    return replies
