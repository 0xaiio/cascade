"""`subtitle_plan`：只处理中英、按主语言选目标、人工优先、没有就跳过。

这四条规则在真实账号上都很贵（要找到对应的视频才能复现），所以判定全部落在纯函数里，
在这里钉死。每条用例都对着**一条具体的输入**，而不是「跑一遍没报错」。
"""

from __future__ import annotations

import pytest

from app.subtitle_plan import (
    CHINESE,
    ENGLISH,
    SubtitleOrigin,
    SubtitlePlan,
    detect_primary_language,
    filter_supported_languages,
    is_supported_language,
    language_family,
    pick_track,
    resolve_subtitle_plan,
    subtitle_summary,
)


# ---------------------------------------------------------------- 语言族
@pytest.mark.parametrize(
    "code,expected",
    [
        ("en", ENGLISH),
        ("en-US", ENGLISH),
        ("en-GB", ENGLISH),
        ("en-orig", ENGLISH),
        ("en-US-orig", ENGLISH),
        ("zh", CHINESE),
        ("zh-Hans", CHINESE),
        ("zh-Hant", CHINESE),
        ("zh-CN", CHINESE),
        ("zh-TW", CHINESE),
        ("zh-Hans-orig", CHINESE),
        ("ZH-HANS", CHINESE),  # 大小写不敏感：平台返回的形状并不统一
    ],
)
def test_chinese_and_english_codes_map_to_their_family(code: str, expected: str) -> None:
    assert language_family(code) == expected


@pytest.mark.parametrize("code", ["ja", "ko", "fr", "de", "yue", "es-419", "", "und", "a-en"])
def test_languages_outside_chinese_and_english_are_ignored(code: str) -> None:
    """只处理英文和中文字幕 —— 这一条的判据就是 `language_family` 返回 `None`。"""
    assert language_family(code) is None
    assert is_supported_language(code) is False


def test_yue_is_not_chinese() -> None:
    """粤语单列：yt-dlp 也把它与 `zh-*` 分开，混进来会让「中文视频」的判定漂走。"""
    assert language_family("yue") is None


def test_filter_supported_languages_keeps_order_and_drops_the_rest() -> None:
    assert filter_supported_languages(["ja", "zh-Hans", "ko", "en", "fr"]) == ["zh-Hans", "en"]


# ---------------------------------------------------------------- 主语言判定
def test_original_asr_track_declares_the_spoken_language() -> None:
    """`zh-Hans-orig` 存在 == 这个视频说的是中文。

    依据是 yt-dlp 自己的标注：它只给**原始语音语言**那条 ASR 轨加 `-orig`
    （`extractor/youtube/_video.py`：Add an "-orig" label to the original language）。
    """
    assert detect_primary_language(["en"], ["zh-Hans", "zh-Hans-orig", "en"]) == CHINESE
    assert detect_primary_language([], ["en", "en-orig", "zh-Hans"]) == ENGLISH


def test_original_track_wins_over_manual_subtitles() -> None:
    """视频说的是英文（有 `en-orig`），但人工挂了中文字幕 → 主语言仍是英文。

    这条最容易被写反：按「人工字幕里有什么」去判，就会把英文视频判成中文，
    于是去下一条机器翻译的中文字幕而不是原生的英文字幕。
    """
    assert detect_primary_language(["zh-Hans", "en"], ["en-orig", "en", "zh-Hans"]) == ENGLISH


def test_manual_subtitles_are_the_fallback_when_there_is_no_asr_at_all() -> None:
    assert detect_primary_language(["en"], []) == ENGLISH
    assert detect_primary_language(["zh-Hans"], []) == CHINESE


def test_chinese_wins_when_only_manual_tracks_exist_and_both_families_are_present() -> None:
    """没有 ASR 可用时，人工字幕里出现中文就判中文 —— 面向中文观众的概率更高。"""
    assert detect_primary_language(["en", "zh-Hans"], []) == CHINESE


def test_primary_language_is_none_when_nothing_is_known() -> None:
    assert detect_primary_language([], []) is None
    assert detect_primary_language(["ja", "ko"], ["fr"]) is None


def test_detection_is_reproducible_for_the_same_input() -> None:
    tracks = ["en-orig", "zh-Hant-orig", "en", "zh-Hant"]
    assert detect_primary_language([], tracks) == detect_primary_language([], tracks)


# ---------------------------------------------------------------- 选轨
def test_pick_track_prefers_the_plain_code_over_the_original_duplicate() -> None:
    """`en` 与 `en-orig` 内容相同，**只挑一条** —— 否则同一条字幕会在磁盘上出现两份。"""
    assert pick_track(["en-orig", "en"], ENGLISH) == "en"


def test_pick_track_prefers_simplified_chinese() -> None:
    assert pick_track(["zh-Hant", "zh-Hans"], CHINESE) == "zh-Hans"
    assert pick_track(["zh-TW", "zh-CN"], CHINESE) == "zh-CN"


def test_pick_track_returns_none_for_another_family() -> None:
    assert pick_track(["en", "en-US"], CHINESE) is None


# ---------------------------------------------------------------- 下载计划
def test_human_subtitle_wins_over_asr_for_the_same_language() -> None:
    """需求第 3 条：人工优先，不存在才降级到 ASR。

    实现上这一条是「免费的」—— yt-dlp 的 `process_subtitles` 先把人工轨放进候选，
    自动轨只补没有被占掉的语言。这里钉的是**我们算出来的结论**与它一致。
    """
    plan = resolve_subtitle_plan({"en": [{}]}, {"en": [{}], "en-orig": [{}]})
    assert plan.origin is SubtitleOrigin.human
    assert plan.code == "en"
    assert plan.languages == ["en"]


def test_missing_human_subtitle_falls_back_to_asr() -> None:
    """人工只有中文、ASR 有英文 → 主语言是英文（`en-orig`），于是降级下 ASR 英文。

    注意**不能**用 `{"en": []}` 表示「没有人工英文」：字幕字典里一个语言键对应一条
    真实存在的轨，空列表这种形状在平台数据里并不出现。
    """
    plan = resolve_subtitle_plan({"zh-Hans": [{}]}, {"en": [{}], "en-orig": [{}]})
    assert plan.language == ENGLISH
    assert plan.origin is SubtitleOrigin.auto
    assert plan.code == "en"


def test_plan_is_none_instead_of_failing_when_nothing_is_available() -> None:
    """两者都没有 → `none`。**它不是失败**：调用方据此继续下载视频本体。"""
    plan = resolve_subtitle_plan({"ja": [{}]}, {"ja": [{}]})
    assert plan.origin is SubtitleOrigin.none
    assert plan.code is None
    # 请求族名本身（而不是空列表）：空 `subtitleslangs` 会被 yt-dlp 当没给，
    # 转而按它自己的默认挑英文 —— 那就把「跳过」悄悄变成了「下英文」。
    assert plan.languages == []


def test_plan_never_asks_for_all_languages() -> None:
    plan = resolve_subtitle_plan({"en": [{}], "zh-Hans": [{}]}, {"en": [{}]})
    assert "all" not in plan.languages


def test_source_option_can_close_each_origin() -> None:
    tracks = ({"en": [{}]}, {"en": [{}]})
    assert resolve_subtitle_plan(*tracks, allow_auto=False).origin is SubtitleOrigin.human
    assert resolve_subtitle_plan({"ja": [{}]}, {"en": [{}]}, allow_human=False).origin is SubtitleOrigin.auto


def test_closing_both_origins_yields_none() -> None:
    plan = resolve_subtitle_plan({"en": [{}]}, {"en": [{}]}, allow_human=False, allow_auto=False)
    assert plan.origin is SubtitleOrigin.none


def test_plan_reason_is_never_empty() -> None:
    """每条分支都要有一句人话 —— 「这个视频怎么没字幕」否则无从查起。"""
    for plan in (
        resolve_subtitle_plan({"en": [{}]}, {"en": [{}]}),
        resolve_subtitle_plan({}, {"en": [{}]}),
        resolve_subtitle_plan({}, {}),
        resolve_subtitle_plan({"ja": [{}]}, {"ja": [{}]}),
    ):
        assert plan.reason


def test_plan_falls_back_to_english_when_the_language_is_unknown() -> None:
    plan = resolve_subtitle_plan({}, {})
    assert plan.language == ENGLISH
    assert plan.origin is SubtitleOrigin.none


def test_plan_for_a_chinese_video_targets_chinese() -> None:
    plan = resolve_subtitle_plan({"zh-Hans": [{}]}, {"zh-Hans-orig": [{}], "en": [{}]})
    assert plan.language == CHINESE
    assert plan.code == "zh-Hans"
    assert plan.origin is SubtitleOrigin.human


def test_non_chinese_tracks_never_become_the_target() -> None:
    """需求第 1 条：忽略其它语种。它们既不能当主语言，也不能当下载目标。"""
    plan = resolve_subtitle_plan({"ja": [{}], "ko": [{}]}, {"ja": [{}]})
    assert plan.code is None
    assert plan.language == ENGLISH


# ---------------------------------------------------------------- 展示文案
def test_summary_distinguishes_no_subtitle_from_no_conclusion() -> None:
    """`none` 是跑过一次的结论；两个都空是「还没跑」。混成一句会让任务刚提交就显示「无字幕」。 """
    assert subtitle_summary(None, SubtitleOrigin.none.value) == "无字幕"
    assert subtitle_summary(None, None) == ""


def test_summary_joins_language_and_origin() -> None:
    assert subtitle_summary("en", SubtitleOrigin.human.value) == "en · 人工"
    assert subtitle_summary("zh", SubtitleOrigin.auto.value) == "zh · 自动生成"


def test_plan_defaults_are_explicit() -> None:
    plan = SubtitlePlan(language=ENGLISH)
    assert plan.available is False
    assert plan.languages == []
