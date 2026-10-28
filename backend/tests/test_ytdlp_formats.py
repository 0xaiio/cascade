"""`ytdlp_formats` 的纯逻辑：选择器、信息字典提取、降级候选。

这个模块此前**没有任何直接用例**（只在 `test_api` / `test_ytdlp_service` 里被间接带到），
而它决定的是「用户要什么画质、实际下什么」—— 认不出取值时的兜底是 `bv*+ba/b`（最佳可用），
即**静默换了画质**。所以每条分支都单独钉一遍，尤其是「认不出」那条。
"""

from __future__ import annotations

import pytest

from app.schemas import DownloadOptions, FormatOption
from app.ytdlp_formats import (
    DEFAULT_MIN_AUTO_FALLBACK_HEIGHT,
    actual_format_from_info_dict,
    duration_from_info_dict,
    filesize_from_info_dict,
    format_selector,
    has_resolution_at_or_above,
    positive_float,
    positive_int,
    requires_ffmpeg,
    resolution_from_info_dict,
    resolution_height,
    short_codec,
    single_file_format_selector,
    suggest_lower_resolution,
)


def options(**kwargs) -> DownloadOptions:
    return DownloadOptions(**kwargs)


def raw_options(**kwargs) -> DownloadOptions:
    """绕过 schema 校验构造 options。

    入口（`schemas._normalize_resolution`）已经会挡住非法取值，所以「纯函数遇到它怎么办」
    只能这样测 —— 那也是**历史数据**走的路（库里存着早于校验的行）。
    """
    return DownloadOptions.model_construct(**kwargs)


# ------------------------------------------------------------------ 选择器


def test_an_explicit_format_id_is_paired_with_the_best_audio() -> None:
    assert format_selector(options(format_id="137")) == "137+ba/137"


def test_best_uses_the_merge_selector() -> None:
    assert format_selector(options(resolution="best")) == "bv*+ba/b"
    assert format_selector(options(resolution="best"), prefer_hls=True) == "b[protocol^=m3u8]/bv*+ba/b"


def test_a_resolution_prefers_avc1_mp4_then_falls_back_through_mp4_to_any_codec() -> None:
    selector = format_selector(options(resolution="1080p"))

    # 顺序本身就是语义：先「avc1 + m4a」，再「mp4 容器」，再「任意编码」，最后才是单文件。
    assert selector.startswith(
        "bv*[height=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/"
        "bv*[height=1080][ext=mp4]+ba[ext=m4a]/"
        "bv*[height=1080]+ba/"
    )
    assert selector.endswith("b[height=1080]")


def test_prefer_hls_puts_the_m3u8_progressive_form_first() -> None:
    selector = format_selector(options(resolution="1080p"), prefer_hls=True)

    assert selector.startswith("b[height=1080][protocol^=m3u8]/")


def test_without_ffmpeg_the_selector_must_stay_single_file() -> None:
    selector = format_selector(options(resolution="720p"), allow_merge=False)

    assert selector == "b[height=720][ext=mp4]/b[height=720]"
    assert "+ba" not in selector


def test_an_uppercase_resolution_is_still_that_height() -> None:
    """`1440P` 曾经只因为一个大写字母就回落成 `bv*+ba/b`（=下最高画质），见 ai/bug-fix/026。"""
    assert format_selector(raw_options(resolution="1440P")) == format_selector(options(resolution="1440p"))
    assert single_file_format_selector(raw_options(resolution="1440P")) == "b[height=1440][ext=mp4]/b[height=1440]"
    # 入口也会把它归一小写，所以「正常构造」进来就已经是 1440p。
    assert options(resolution="1440P").resolution == "1440p"


def test_a_value_that_cannot_be_parsed_falls_back_to_best() -> None:
    """纯函数层的兜底仍是「最佳可用」；挡在入口的是 `schemas._normalize_resolution`。

    这条用例存在的意义是**记录**这个兜底：若哪天有人在入口放开了校验，至少这里能指出
    「落到这一层就等于换了画质」，而不是让沉默继续。
    """
    for value in ("hd", "1080", "1440p60"):
        assert format_selector(raw_options(resolution=value)) == "bv*+ba/b"


def test_single_file_selector_ignores_merge_only_options() -> None:
    assert single_file_format_selector(options(format_id="18")) == "18"
    assert single_file_format_selector(options(resolution="best")) == "best[ext=mp4]/best"


def test_ffmpeg_is_only_required_by_an_explicit_format_id() -> None:
    # 不给 merge 能力时清晰度会退化成单文件选择器，所以只有 format_id 是硬需求。
    assert requires_ffmpeg(options(format_id="137")) is True
    assert requires_ffmpeg(options(resolution="1440p")) is False
    assert requires_ffmpeg(options(resolution="best")) is False


# ------------------------------------------------------- 信息字典（纯映射）


def test_resolution_prefers_the_top_level_fields() -> None:
    assert resolution_from_info_dict({"width": 1920, "height": 1080}) == (1920, 1080)


def test_resolution_of_a_merged_download_takes_the_largest_video_stream() -> None:
    info = {
        "requested_formats": [
            {"width": 640, "height": 360, "vcodec": "avc1"},
            {"width": 1920, "height": 1080, "vcodec": "avc1"},
            {"width": 0, "height": 0, "acodec": "mp4a"},  # 音频流没有分辨率
        ]
    }

    assert resolution_from_info_dict(info) == (1920, 1080)


def test_resolution_ignores_audio_only_streams() -> None:
    info = {"requested_formats": [{"width": 1920, "height": 1080, "vcodec": "none"}, {"acodec": "mp4a"}]}

    assert resolution_from_info_dict(info) is None


def test_resolution_returns_none_for_garbage_shapes() -> None:
    assert resolution_from_info_dict({}) is None
    assert resolution_from_info_dict({"requested_formats": "nope"}) is None
    assert resolution_from_info_dict({"requested_formats": ["nope"]}) is None


def test_actual_format_label_of_a_merged_download() -> None:
    info = {
        "ext": "mp4",
        "requested_formats": [
            {"ext": "mp4", "vcodec": "avc1.640028"},
            {"ext": "m4a", "acodec": "mp4a.40.2"},
        ],
    }

    assert actual_format_from_info_dict(info) == "mp4 · avc1 + mp4a"


def test_actual_format_label_when_only_one_stream_has_a_codec() -> None:
    assert (
        actual_format_from_info_dict({"ext": "webm", "requested_formats": [{"vcodec": "vp9"}]})
        == "webm · vp9"
    )
    # 没有分离流时读顶层字段。
    assert actual_format_from_info_dict({"ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"}) == "mp4 · avc1 + mp4a"
    assert actual_format_from_info_dict({}) is None


def test_filesize_sums_every_stream_of_a_merged_download() -> None:
    info = {
        "requested_formats": [
            {"filesize": 1_000},
            {"filesize_approx": 500},
            {"filesize": None, "filesize_approx": None},
        ]
    }

    assert filesize_from_info_dict(info) == 1500


def test_filesize_falls_back_to_the_approx_value_and_rejects_zero() -> None:
    assert filesize_from_info_dict({"filesize_approx": 900}) == 900
    assert filesize_from_info_dict({"filesize": 0}) is None
    assert filesize_from_info_dict({"requested_formats": []}) is None


def test_duration_treats_zero_and_negative_as_unknown() -> None:
    assert duration_from_info_dict({"duration": 61.5}) == 61.5
    # 0 秒在界面上会显示成 0:00:00，比「未知」更糟 —— 直播条目就是这样回来的。
    assert duration_from_info_dict({"duration": 0}) is None
    assert duration_from_info_dict({"duration": -3}) is None
    assert duration_from_info_dict({}) is None


def test_numeric_helpers_reject_unusable_values() -> None:
    assert positive_int("12") == 12
    assert positive_int("x") is None
    assert positive_float("1.5") == 1.5
    assert positive_float(0) is None


def test_codec_short_name_strips_the_profile() -> None:
    assert short_codec("avc1.640028") == "avc1"
    assert short_codec("mp4a.40.2") == "mp4a"
    assert short_codec("none") is None
    assert short_codec("") is None
    assert short_codec(None) is None


# ------------------------------------------------------------ 清晰度解析


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1080p", 1080),
        ("1080P", 1080),
        (" 720p ", 720),
        ("2160p", 2160),
        ("best", None),
        ("1080", None),
        ("p", None),
        ("1440p60", None),
        ("0p", None),
    ],
)
def test_resolution_height(value: str, expected: int | None) -> None:
    assert resolution_height(value) == expected


# ------------------------------------------------------------ 降级候选


def formats(*heights: int | None) -> list[FormatOption]:
    return [FormatOption(format_id=str(index), label="x", height=height) for index, height in enumerate(heights)]


def test_fallback_picks_the_highest_lower_resolution_above_the_floor() -> None:
    assert suggest_lower_resolution("1440p", formats(2160, 1080, 720, 480)) == "1080p"


def test_fallback_refuses_to_go_below_720_when_the_source_can_do_better() -> None:
    # 1080p 在，但用户要 1080p 以下没有安全档位（480/360 都低于 720 底线）。
    assert suggest_lower_resolution("1080p", formats(1080, 480, 360)) is None


def test_fallback_may_go_below_the_floor_when_the_source_itself_is_below_it() -> None:
    assert suggest_lower_resolution(
        "720p", formats(480, 360), allow_below_min_if_source_below_min=True
    ) == "480p"
    # 同一个视频，但不允许越线时什么都不给 —— 让调用方去报「没有可用的降级」。
    assert suggest_lower_resolution("720p", formats(480, 360)) is None


def test_fallback_returns_none_when_nothing_is_lower() -> None:
    assert suggest_lower_resolution("720p", formats(720, 1080, 2160)) is None
    assert suggest_lower_resolution("best", formats(720)) is None


def test_default_floor_is_720() -> None:
    assert DEFAULT_MIN_AUTO_FALLBACK_HEIGHT == 720


def test_has_resolution_at_or_above_is_inclusive_of_the_floor() -> None:
    assert has_resolution_at_or_above(formats(720)) is True
    assert has_resolution_at_or_above(formats(719)) is False
    assert has_resolution_at_or_above(formats(None, 480)) is False
