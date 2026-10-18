"""字幕下载计划：**只处理英文与中文**，按视频主语言选目标，人工优先、降级 ASR、都没有就跳过。

**纯逻辑模块**：不碰网络、不读库、不 import yt-dlp。输入是 yt-dlp 的两份字幕字典
（`subtitles` = 人工/官方上传，`automatic_captions` = 机器生成），输出一份
`SubtitlePlan`。这样「主语言怎么判」「人工和 ASR 谁优先」这些最容易写歪的分支，
全部可以在离线测试里跑出来 —— 真实账号/真实网络只能验证「接口通不通」，验证不了判定。

## 三条规则的依据

1. **只处理中英**：`language_family()` 是唯一入口，其余语种一律返回 `None`。
   过滤放在**映射字幕列表的同一处**（`YtDlpService._map_subtitles`）与这里共用，
   避免「界面列出了 20 种语言、下载时却只认两种」这种两处真相。

2. **主语言怎么判**：yt-dlp 会给**原始语音语言**那条 ASR 轨加 `-orig` 后缀 ——
   源码里写得很明确：`Add an "-orig" label to the original language so that it can be
   distinguished`（`yt_dlp/extractor/youtube/_video.py`，2026.8.19，约 4305 行）。
   所以 `zh-Hans-orig` 存在 == 这个视频说的是中文，`en-orig` 存在 == 说的是英文。
   这是**平台给的信号**，不是猜的；比按标题或频道猜可靠得多。

3. **人工优先、降级 ASR、都没有就跳过**：yt-dlp 自己就是这个顺序
   （`YoutubeDL.process_subtitles`：先把 `subtitles` 放进候选，再用
   `automatic_captions` **补**没有被占掉的语言）。所以我们只要把
   `subtitleslangs` 收敛成**一条**语言，优先级就是免费的；而「该语言一条都没有」时
   yt-dlp 用 `fullmatch` 匹配不到任何轨，`requested_langs` 为空 → 什么都不下、
   **也不报错**。这正是需求里「跳过下载，不中断流程」要的行为，不需要额外开关。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# 本项目处理的两个语言族。**值就是族名**（`en` / `zh`），不是某条具体轨的代码。
ENGLISH = "en"
CHINESE = "zh"
SUBTITLE_LANGUAGE_FAMILIES: tuple[str, ...] = (ENGLISH, CHINESE)

# 判定不出主语言时的兜底。取英文而不是「没有字幕」：本项目的既有默认就是英文
# （`FALLBACK_SUBTITLE_LANGUAGES`），改成「不下字幕」会让一批老任务悄悄丢掉字幕。
FALLBACK_PRIMARY_LANGUAGE = ENGLISH

# yt-dlp 给原始语音轨加的后缀（见模块文档第 2 条）。
ORIGINAL_TRACK_SUFFIX = "-orig"


class SubtitleOrigin(StrEnum):
    """字幕来源。**落库的就是这三个值**，界面与上传环节都读它。"""

    human = "human"
    auto = "auto"
    # 该视频没有可用的中英字幕。它是**一条结论**，不是「出错了」——
    # 与 `bili_upload` 里那些失败态分开，重试也变不出字幕来。
    none = "none"


@dataclass(frozen=True)
class SubtitlePlan:
    """一次下载的字幕结论。

    `language` 是**语言族**（`en` / `zh`），`code` 是 yt-dlp 里那条轨的真实代码
    （`zh-Hans` / `en-US` …）。两者分开是因为：`subtitleslangs` 要的是**真实代码**
    （填族名 `zh` 匹配不到 `zh-Hans`），而落库与展示要的是**稳定的族名**。
    """

    language: str
    code: str | None = None
    origin: SubtitleOrigin = SubtitleOrigin.none
    # 为什么是这个结论。**每条分支都要有一句人话**，否则「这个视频怎么没字幕」无从查起。
    reason: str = ""

    @property
    def languages(self) -> list[str]:
        """交给 yt-dlp 的 `subtitleslangs`。**永远有界**（0 或 1 条），绝不可能是 `["all"]`。"""
        return [self.code] if self.code else []

    @property
    def available(self) -> bool:
        return self.code is not None


# 来源的人话。**界面与日志共用这一份**：`bili_upload` 的投稿预填要把「这一条会不会带字幕」
# 直接显示给用户，自己拼一遍就会出现「界面写自动生成、后端写 ASR」这种两套说法。
ORIGIN_LABELS: dict[str, str] = {
    SubtitleOrigin.human.value: "人工",
    SubtitleOrigin.auto.value: "自动生成",
    SubtitleOrigin.none.value: "无字幕",
}


def subtitle_summary(language: str | None, source: str | None) -> str:
    """一条字幕结论的人话，如 `en · 人工`。**没有结论时返回空串**（不是「无字幕」）。

    `source == none` 与「没有结论」必须区分开：前者是跑过一次后的结论
    （这个视频确实没有可用字幕），后者是**还没跑过**（刚建的任务）。
    混成一句会让用户在任务刚提交时就看到「无字幕」。
    """
    if source == SubtitleOrigin.none.value:
        return ORIGIN_LABELS[SubtitleOrigin.none.value]
    if not language and not source:
        return ""
    label = ORIGIN_LABELS.get(source or "", "")
    if language and label:
        return f"{language} · {label}"
    return language or label


def language_family(code: str) -> str | None:
    """把一条字幕轨的代码归到语言族。**不在中英两族里就返回 `None`**（= 忽略）。

    `zh-Hans` / `zh-CN` / `zh-Hant` / `zh-TW` / `zh-Hans-orig` 都归 `zh`；
    `en` / `en-US` / `en-GB` / `en-orig` 都归 `en`；`yue`（粤语）**不算**中文 ——
    yt-dlp 也把它单列，混进来会让「中文视频」的判定漂到粤语内容上。
    """
    text = (code or "").strip().lower()
    if not text:
        return None
    if text.endswith(ORIGINAL_TRACK_SUFFIX):
        text = text[: -len(ORIGINAL_TRACK_SUFFIX)]
    head = text.split("-", 1)[0]
    if head in SUBTITLE_LANGUAGE_FAMILIES:
        return head
    return None


def is_supported_language(code: str) -> bool:
    return language_family(code) is not None


def filter_supported_languages(codes: Iterable[str]) -> list[str]:
    """保序过滤出中英语言。**顺序是语义的一部分**（后面的偏好排序依赖它）。"""
    return [code for code in codes if is_supported_language(code)]


def _is_original_track(code: str) -> bool:
    return (code or "").strip().lower().endswith(ORIGINAL_TRACK_SUFFIX)


def detect_primary_language(
    manual_languages: Iterable[str],
    automatic_languages: Iterable[str],
) -> str | None:
    """判定视频的主语言，返回族名（`en` / `zh`）；判不出来返回 `None`。

    判据按强度排序：

    1. **ASR 的 `-orig` 轨** —— 平台标出的原始语音语言（见模块文档第 2 条）。
       绝大多数带自动字幕的视频都会命中这一条。
    2. **人工字幕**里出现中文 → 判中文；否则出现英文 → 判英文。
       这条只在「完全没有自动字幕」时才轮到（有 ASR 时第 1 条必然先命中），
       中文优先于英文的理由是：人工挂中文字幕的视频几乎只可能面向中文观众。
    """
    automatic = filter_supported_languages(automatic_languages)
    originals = sorted({language_family(code) for code in automatic if _is_original_track(code)} - {None})
    if originals:
        # 多于一族只可能是平台给了多条原始轨（罕见）。取排序后的第一个，
        # 保证**同一份输入永远同一个结论** —— 判定的可复现比「挑得更准」重要。
        return str(originals[0])

    manual = {language_family(code) for code in filter_supported_languages(manual_languages)} - {None}
    if CHINESE in manual:
        return CHINESE
    if ENGLISH in manual:
        return ENGLISH
    return None


# 同一族里有多条轨时的偏好。**越靠前越优先**；不在表里的按代码字典序兜底。
# 简体优先于繁体（受众面），无后缀的基础码优先于地区变体（`en` 比 `en-US` 通用）。
_TRACK_PREFERENCE: dict[str, tuple[str, ...]] = {
    ENGLISH: ("en", "en-us", "en-gb", "en-ca"),
    CHINESE: ("zh-hans", "zh-cn", "zh", "zh-hant", "zh-tw", "zh-hk"),
}


def _track_rank(code: str) -> tuple[int, str]:
    lowered = (code or "").lower()
    family = language_family(code) or ""
    preference = _TRACK_PREFERENCE.get(family, ())
    rank = preference.index(lowered) if lowered in preference else len(preference)
    # 原始轨（`-orig`）排在同名普通轨之后：两者内容相同，普通轨是平台对外那条。
    if _is_original_track(lowered):
        rank += 1
    return rank, lowered


def pick_track(languages: Iterable[str], family: str) -> str | None:
    """在同一族里挑一条轨的**真实代码**；没有就 `None`。

    为什么必须挑一条：`subtitleslangs` 里填多条会把同一语言的好几个变体
    （`en` + `en-orig`）都写下来，于是同一条字幕出现两份文件。
    """
    candidates = [code for code in languages if language_family(code) == family]
    if not candidates:
        return None
    return min(candidates, key=_track_rank)


def resolve_subtitle_plan(
    subtitles: Mapping[str, Any] | None,
    automatic_captions: Mapping[str, Any] | None,
    *,
    fallback_language: str = FALLBACK_PRIMARY_LANGUAGE,
    allow_human: bool = True,
    allow_auto: bool = True,
) -> SubtitlePlan:
    """把「这个视频有哪些字幕轨」变成「这次下哪一条、它是什么来源」。

    `allow_human` / `allow_auto` 对应下载选项里的 `subtitle_source`
    （`human` / `auto` / `both`）；两者都关时恒为 `none`。

    **返回 `origin=none` 不是失败**：它表示「按规则没有可下的字幕」，
    调用方据此记录结论并继续跑视频本体 —— 中断才是错的那一边。
    """
    manual = filter_supported_languages((subtitles or {}).keys())
    automatic = filter_supported_languages((automatic_captions or {}).keys())

    detected = detect_primary_language(manual, automatic)
    language = detected or (fallback_language if fallback_language in SUBTITLE_LANGUAGE_FAMILIES else FALLBACK_PRIMARY_LANGUAGE)

    if detected is None:
        origin_note = "判定不出主语言，按兜底语言处理"
    else:
        origin_note = f"视频主语言判为 {detected}"

    if not allow_human and not allow_auto:
        return SubtitlePlan(language=language, reason=f"{origin_note}；下载选项关闭了全部字幕来源")

    human_code = pick_track(manual, language) if allow_human else None
    if human_code:
        return SubtitlePlan(
            language=language,
            code=human_code,
            origin=SubtitleOrigin.human,
            reason=f"{origin_note}；命中人工字幕轨 {human_code}",
        )

    auto_code = pick_track(automatic, language) if allow_auto else None
    if auto_code:
        return SubtitlePlan(
            language=language,
            code=auto_code,
            origin=SubtitleOrigin.auto,
            reason=f"{origin_note}；人工字幕轨缺失，降级到自动字幕轨 {auto_code}",
        )

    skipped = []
    if not allow_human:
        skipped.append("人工字幕未开启")
    if not allow_auto:
        skipped.append("自动字幕未开启")
    detail = "；".join(skipped) or f"该视频没有 {language} 的人工字幕也没有自动字幕"
    return SubtitlePlan(language=language, reason=f"{origin_note}；{detail}（跳过字幕下载，不中断下载流程）")
