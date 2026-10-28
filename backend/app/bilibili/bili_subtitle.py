"""B站 CC 字幕：**字幕文件 → 平台可关闭字幕**这一段。

单独成模块的理由与 `bili_bin` 一样 —— 它是「B站 侧的字幕细节」的唯一落点：

- 格式转换：yt-dlp 写出的是 **SRT / VTT**，B站 CC 吃的是它自己的 **BCC**（一段 JSON，
  时间是秒、内容是 `body[].content`）。两边都变，各写一遍必然漂移。
- 语言标识：yt-dlp 给的是**轨代码**（`zh-Hans` / `en-US`），B站 要的是它的
  **语言标识**（`zh-CN` / `en-US`）。这个映射也只该有一处。
- 上传：走 `bili_api` 注入的 `Transport`，所以整条链路可以离线测。

**为什么必须是 CC（可关闭）字幕**：需求要的是「观众能自己关掉」。B站 的 AI 字幕
（`lan` 前缀 `ai-`）由平台生成、UP 主传不了；能传的只有 CC 字幕，它挂在播放器里
由观众开关 —— 这正是本模块唯一要产出的东西。

**上传失败一律降级**：字幕是稿件的附加物，字幕没传上去不能把一条已经发布成功的稿件
说成失败。所以这里不抛异常，只把结论交回去（`SubtitleOutcome`）。
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .bili_api import save_subtitle
from .bili_http import Transport
from ..download.subtitle_plan import CHINESE, ENGLISH, language_family

logger = logging.getLogger(__name__)

# B站 的语言标识。**取值以平台白名单为准**，不是照着常见的 `zh-CN` / `en-US` 猜的。
#
# 实测（2026-10-18，真实账号 + 真实稿件，`submit=false` 只存草稿，不改线上内容）：
# 传 `zh-CN` / `en-US` / `zh-Hans` / `en-GB` 一律被拒为 `code=79011 不合法的语言`；
# 而 `zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` / `ko` / `yue` / `fr` 返回 `code=0`。
#
# 也就是说：B站 这一列用的是 **ISO-639-1 裸码 + 少数几个地区/文字变体**，
# 与「语言-地区」形态（BCP-47）不是一回事。需求里举例的 `zh-CN` / `en-US`
# **在 B站 上是不合法取值** —— 照抄那两个值会让每一次字幕上传都以 79011 失败。
LAN_CHINESE = "zh"
LAN_ENGLISH_BILI = "en"
# 判不出语言时的兜底。取英文：本项目的兜底主语言也是英文，两者必须一致，
# 否则会出现「下载按英文处理、上传按中文标识」这种自相矛盾。
DEFAULT_LAN = LAN_ENGLISH_BILI

# 繁体相关变体**平台本身就认**，原样传回去（不必都压成一条轨）。
# `zh-MO`（澳门）没有对应的白名单取值，归到 `zh-HK`。
_TRADITIONAL_CHINESE = {
    "zh-hant": "zh-Hant",
    "zh-tw": "zh-TW",
    "zh-hk": "zh-HK",
    "zh-mo": "zh-HK",
}

# 界面上的语言名。**平台码不进界面**：`en` / `zh-Hant` 是发给接口的值，用户读的是「英文」。
# 取值覆盖面就是 `bilibili_language` 可能产出的那几个（它只放行中英两族），其余一律原样回显。
LAN_DISPLAY: dict[str, str] = {
    LAN_CHINESE: "中文",
    "zh-Hant": "繁体中文",
    "zh-TW": "繁体中文",
    "zh-HK": "繁体中文",
    LAN_ENGLISH_BILI: "英文",
}

# BCC 的默认样式。**这几个值取自 B站 播放器对 CC 字幕的默认值**：不写会有缺省值，
# 但写出来是为了让「字幕长什么样」在本模块可见 —— 观众可关、可调，样式不是产品差异点。
BCC_FONT_SIZE = 0.4
BCC_FONT_COLOR = "#FFFFFF"
BCC_BACKGROUND_ALPHA = 0.5
BCC_BACKGROUND_COLOR = "#9C27B0"
BCC_STROKE = "none"
# `location=2` = 底部。CC 字幕默认就在底部，这是播放器识别「这是字幕不是弹幕」的一部分。
BCC_LOCATION = 2

# 上传状态。**落库的就是这三个值**，与 `bili_policy` 那套稿件状态刻意不共用 ——
# 字幕失败不该让稿件徽标变色。
SUBTITLE_POSTED = "posted"
SUBTITLE_SKIPPED = "skipped"
SUBTITLE_FAILED = "failed"

SUBTITLE_SUFFIXES = (".srt", ".vtt")

_TIMING_SEPARATOR = "-->"
# `HH:MM:SS.mmm` / `MM:SS.mmm`，分隔符 `,` 与 `.` 都认（SRT 用逗号、VTT 用点）。
_TIMESTAMP = re.compile(r"(?:(\d{1,3}):)?(\d{1,3}):(\d{2})(?:[.,](\d{1,3}))?")
# VTT 的行内标签：`<c>` `</c>` `<v Speaker>` `<00:00:01.000>` `<i>` 等等。
_INLINE_TAG = re.compile(r"<[^>]*>")


def bilibili_language(code: str | None) -> str:
    """把 yt-dlp 的字幕轨代码映射成 B站 **认可**的语言标识。

    只认中英两族（与下载侧同一条约束）；认不出来退到 `en`。

    为什么 `zh-Hans` 要压成 `zh` 而 `zh-Hant` 原样保留：平台白名单就长这样
    （见上面的实测注释），`zh-Hans` 不在里面而 `zh-Hant` 在。
    """
    text = (code or "").strip()
    family = language_family(text)
    if family == ENGLISH:
        return LAN_ENGLISH_BILI
    if family == CHINESE:
        base = text.lower()
        if base.endswith("-orig"):
            base = base[: -len("-orig")]
        return _TRADITIONAL_CHINESE.get(base, LAN_CHINESE)
    return DEFAULT_LAN


def language_display(lan: str | None) -> str:
    """平台语言码 → 用户读得懂的名字。**认不出来就原样返回，不猜**（与状态表同一条原则）。"""
    code = (lan or "").strip()
    if not code:
        return "未知语言"
    return LAN_DISPLAY.get(code, code)


def parse_timestamp(text: str) -> float | None:
    """`00:01:02,500` → 62.5。**解析不出来返回 `None`**，不猜 0 —— 时间错了整条字幕就废了。"""
    match = _TIMESTAMP.fullmatch(text.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = match.group(4) or ""
    milliseconds = int(fraction.ljust(3, "0")) if fraction else 0
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    content: str


def _clean_line(line: str) -> str:
    """去掉 VTT 的行内标签与 HTML 实体。

    YouTube 的自动字幕是「滚动」的：一条 cue 里会带 `<c>` 之类的着色标签，
    原样传上去就是一排尖括号。
    """
    return html.unescape(_INLINE_TAG.sub("", line)).strip()


def parse_subtitle_text(text: str) -> list[Cue]:
    """解析 SRT 或 VTT 文本。**两种格式共用一套解析**：差别只有时间分隔符与文件头。

    容错点都是有实测依据的，不是「以防万一」：

    - VTT 有 `WEBVTT` 抬头与 `NOTE` / `STYLE` / `REGION` 块 —— 靠「有没有 `-->`」跳过；
    - VTT 的时间行后面跟着 `align:start position:0%` 这类设置 —— 只取第一个 token；
    - SRT 的时间行前面是序号行 —— 同上，只看带 `-->` 的那一行；
    - 内容里可能有 `<c>` 标签与 `&amp;` 实体 —— 见 `_clean_line`。
    """
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\r?\n", text.replace("\ufeff", "")):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if _TIMING_SEPARATOR in line), None)
        if timing_index is None:
            continue
        left, _, right = lines[timing_index].partition(_TIMING_SEPARATOR)
        start = parse_timestamp(left.strip().split()[0]) if left.strip() else None
        end = parse_timestamp(right.strip().split()[0]) if right.strip() else None
        if start is None or end is None:
            continue
        content = "\n".join(
            row for row in (_clean_line(line) for line in lines[timing_index + 1 :]) if row
        )
        if not content or end <= start:
            continue
        cues.append(Cue(start=start, end=end, content=content))
    return cues


def merge_repeated_cues(cues: list[Cue]) -> list[Cue]:
    """合并**相邻且内容完全相同**的 cue（把前一条的结束时间延到后一条）。

    YouTube 的自动字幕会把同一句切成多条重叠的滚动 cue，原样传上去就是字幕在原地
    反复出现一次。内容不同的一律不合并 —— 那不是重复，是两条字幕。
    """
    merged: list[Cue] = []
    for cue in cues:
        if merged and merged[-1].content == cue.content:
            previous = merged[-1]
            merged[-1] = Cue(start=previous.start, end=max(previous.end, cue.end), content=previous.content)
            continue
        merged.append(cue)
    return merged


# 滚动字幕的**过渡条**：YouTube 的 VTT 在每个滚动窗口之间插一条极短的 cue，内容是上一窗口里
# 的一行。实测 Ep 130（`C++ Weekly - Ep 130`，5:53）的 241 条 cue 里，有 **119 条时长正好
# 0.010 s**，且每一行文字都已被前后相邻的 cue 完整覆盖。
#
# 它在播放器里只活一帧（60fps 下不足一帧），却把条数抬高了近一倍：239 条 cue 对应 122 句台词。
# 这是「字幕条数」这个数字看起来莫名其妙、也是字幕在换行处闪一下的根源。
TRANSITION_MAX_SECONDS = 0.05


def _lines_of(cue: Cue) -> set[str]:
    return {row.strip() for row in cue.content.splitlines() if row.strip()}


def drop_transition_cues(cues: list[Cue]) -> list[Cue]:
    """丢掉滚动字幕的过渡条：**时长极短**且文字**已被相邻 cue 完整覆盖**。

    两个条件缺一不可，各自都放过一类误删：

    - 只按时长删 ⇒ 会误删「真的只有几十毫秒、但内容独一无二」的短字幕；
    - 只按「文字在邻居里出现过」删 ⇒ 会误删正常重复 —— 同一句话隔几秒再说一次，
      那是新的一条字幕，不是过渡条。
    """
    kept: list[Cue] = []
    for index, cue in enumerate(cues):
        if cue.end - cue.start <= TRANSITION_MAX_SECONDS:
            neighbour: set[str] = set()
            if index:
                neighbour |= _lines_of(cues[index - 1])
            if index + 1 < len(cues):
                neighbour |= _lines_of(cues[index + 1])
            if _lines_of(cue) and _lines_of(cue) <= neighbour:
                continue
        kept.append(cue)
    return kept


def to_bcc(cues: list[Cue]) -> str:
    """把 cue 列表序列化成 B站 的 BCC（JSON 字符串）。

    `from` / `to` 是**秒**（浮点），这是 BCC 与 SRT 最大的差别 —— 传错单位会让
    整条字幕挤在开头几秒。
    """
    payload = {
        "font_size": BCC_FONT_SIZE,
        "font_color": BCC_FONT_COLOR,
        "background_alpha": BCC_BACKGROUND_ALPHA,
        "background_color": BCC_BACKGROUND_COLOR,
        "Stroke": BCC_STROKE,
        "body": [
            {"from": round(cue.start, 3), "to": round(cue.end, 3), "location": BCC_LOCATION, "content": cue.content}
            for cue in cues
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def load_bcc(path: Path) -> tuple[str, int]:
    """读一个字幕文件并转成 BCC，返回 `(BCC 文本, 段数)`。**读不出来就抛** ——
    让调用方决定是「跳过」还是「记为失败」，这里不替它做决定。

    三步固定顺序：解析 → 丢过渡条 → 合并同文。先丢再合：过渡条本身就是「上一行」，先合会把
    它的结束时间并进上一条，之后就再也看不出它是过渡条了（`merge_repeated_cues` 只判整条同文，
    而「上一行 / 上一行+新一行」永远不相等）。
    """
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    cues = merge_repeated_cues(drop_transition_cues(parse_subtitle_text(text)))
    return to_bcc(cues), len(cues)


@dataclass(frozen=True)
class SubtitleOutcome:
    """一次字幕上传的结论。**它不参与稿件状态的判定。**"""

    state: str
    lan: str = ""
    message: str = ""
    cue_count: int = 0


def upload_subtitle(
    transport: Transport,
    jar: dict[str, str],
    *,
    aid: int,
    bvid: str,
    cid: int,
    path: Path,
    lan: str,
) -> SubtitleOutcome:
    """把一个字幕文件传成稿件的 CC 字幕。**任何失败都变成 `failed` 结论，不抛异常。**

    为什么必须吞掉异常：这一步挂在「视频已经上传成功」之后。抛出去会让
    `bili_upload.run_single` 走失败分支，于是**一条已经发布成功的稿件被标成失败**，
    而用户看到的错误还是「字幕没传上去」—— 两件事混在一条结论里，没法解释。
    """
    file_path = Path(path)
    if not file_path.is_file():
        return SubtitleOutcome(state=SUBTITLE_SKIPPED, lan=lan, message="字幕文件不存在，跳过字幕上传")
    if file_path.suffix.lower() not in SUBTITLE_SUFFIXES:
        return SubtitleOutcome(
            state=SUBTITLE_SKIPPED,
            lan=lan,
            message=f"字幕格式 {file_path.suffix} 不是可上传的 SRT/VTT，跳过",
        )

    try:
        data, count = load_bcc(file_path)
    except Exception as exc:  # noqa: BLE001 - 转格式失败 = 这一次上传失败，不是流程错误
        logger.warning("bili 字幕转换失败：file=%s error=%s", file_path.name, exc)
        return SubtitleOutcome(state=SUBTITLE_FAILED, lan=lan, message=f"字幕转换失败：{exc}")

    if not count:
        return SubtitleOutcome(state=SUBTITLE_SKIPPED, lan=lan, message="字幕文件里没有可用字幕，跳过上传")

    try:
        save_subtitle(transport, jar, aid=aid, bvid=bvid, cid=cid, lan=lan, data=data)
    except Exception as exc:  # noqa: BLE001 - 同 `group_into_season`：失败必须落成结论
        logger.warning("bili 字幕上传失败：bvid=%s lan=%s error=%s", bvid, lan, exc)
        return SubtitleOutcome(state=SUBTITLE_FAILED, lan=lan, message=str(exc), cue_count=count)

    logger.info("bili 字幕上传完成：bvid=%s lan=%s 段数=%s", bvid, lan, count)
    # 界面只讲**结果**：语言说人话（「英文」而不是 `en`），段数进日志。
    # 段数是转换器的中间量 —— 它既不是字数也不是句数（Ep 130：239 段对应 122 句），
    # 摆在界面上只会引出一个答不上来的问题（见 ai/bug-fix/022）。
    return SubtitleOutcome(
        state=SUBTITLE_POSTED,
        lan=lan,
        message=f"CC 字幕已上传（{language_display(lan)}），观众可在播放器里开关",
        cue_count=count,
    )
