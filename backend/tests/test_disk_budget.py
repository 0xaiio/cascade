"""E-01 · 磁盘预算判定的纯函数层。

覆盖三档结论、量级格式化、估值来源与「读不到就只提示」的边界。
这一层不碰真实网络；磁盘量取只在 `free_space_bytes` 一处，用 monkeypatch 注入。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts.schemas import AnalyzeResponse, DownloadOptions, FormatOption, VideoEntry
from app.download import disk_budget
from app.download.disk_budget import (
    DEFAULT_RESERVE_BYTES,
    assess,
    estimate_job_bytes,
    format_bytes,
    free_space_bytes,
)

MIB = 1024 * 1024


def _analysis(filesize: int | None = 400 * MIB, *, playlist: int = 1) -> AnalyzeResponse:
    return AnalyzeResponse(
        url="https://youtu.be/demo",
        title="Demo",
        is_playlist=playlist > 1,
        entries=[VideoEntry(index=i + 1, title=f"E{i + 1}", url=f"https://youtu.be/e{i + 1}") for i in range(playlist)]
        if playlist > 1
        else [],
        formats=[
            FormatOption(format_id="22", label="720p", height=720, filesize=filesize),
            FormatOption(format_id="18", label="360p", height=360, filesize=filesize // 2 if filesize else None),
        ],
    )


# ---------------------------------------------------------------- 量级与量取


def test_format_bytes_uses_binary_units_and_never_invents_a_number() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.00 KiB"
    assert format_bytes(1536 * MIB) == "1.50 GiB"
    assert format_bytes(None) == "未知"
    assert format_bytes(-1) == "未知"


def test_free_space_walks_up_to_an_existing_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """playlist 的同名子目录在建任务时还不存在；不能据此报「读不到」。"""
    seen: list[Path] = []

    def fake_usage(path: Path):
        seen.append(path)
        return type("Usage", (), {"free": 42 * MIB})()

    monkeypatch.setattr(disk_budget.shutil, "disk_usage", fake_usage)
    assert free_space_bytes(tmp_path / "new-playlist" / "deeper") == 42 * MIB
    assert seen == [tmp_path]


def test_free_space_unavailable_is_unknown_not_insufficient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_usage(_path: Path):
        raise OSError("volume is gone")

    monkeypatch.setattr(disk_budget.shutil, "disk_usage", fake_usage)
    assert free_space_bytes(tmp_path) is None


# ---------------------------------------------------------------- 估值来源


def test_estimate_picks_the_format_closest_to_the_requested_height() -> None:
    analysis = _analysis()
    assert estimate_job_bytes(analysis, DownloadOptions(resolution="720p"), 1) == 400 * MIB
    assert estimate_job_bytes(analysis, DownloadOptions(resolution="360p"), 1) == 200 * MIB


def test_estimate_multiplies_by_selected_entry_count_for_a_playlist() -> None:
    analysis = _analysis(playlist=3)
    assert estimate_job_bytes(analysis, DownloadOptions(resolution="720p"), 3) == 3 * 400 * MIB
    assert estimate_job_bytes(analysis, DownloadOptions(resolution="720p"), 1) == 400 * MIB


def test_estimate_respects_an_explicit_format_id() -> None:
    analysis = _analysis()
    assert estimate_job_bytes(analysis, DownloadOptions(format_id="18"), 1) == 200 * MIB
    assert estimate_job_bytes(analysis, DownloadOptions(format_id="missing"), 1) is None


def test_best_resolution_takes_the_largest_known_size() -> None:
    assert estimate_job_bytes(_analysis(), DownloadOptions(resolution="best"), 1) == 400 * MIB


@pytest.mark.parametrize(
    "options",
    [
        DownloadOptions(mode="subtitles_only"),
        DownloadOptions(resolution="720p"),
    ],
)
def test_estimate_is_none_when_the_platform_gave_no_size(options: DownloadOptions) -> None:
    assert estimate_job_bytes(_analysis(filesize=None), options, 1) is None


def test_estimate_is_none_for_an_empty_selection() -> None:
    assert estimate_job_bytes(_analysis(), DownloadOptions(), 0) is None


# ---------------------------------------------------------------- 三档结论与边界


def test_assess_ok_when_free_space_covers_estimate_plus_reserve() -> None:
    budget = assess(100 * MIB, DEFAULT_RESERVE_BYTES + 100 * MIB, reserve_bytes=10 * MIB)
    assert budget.status == "ok"
    assert budget.blocks is False
    assert budget.required_bytes == 110 * MIB
    assert "110.00 MiB" in budget.message


def test_assess_exactly_at_the_boundary_is_ok() -> None:
    assert assess(100, 110, reserve_bytes=10).status == "ok"


def test_assess_one_byte_short_is_insufficient_and_gives_the_deficit() -> None:
    budget = assess(100, 109, reserve_bytes=10)
    assert budget.status == "insufficient"
    assert budget.blocks is True
    assert "1 B" in budget.message
    assert "清理" in budget.message and "下载目录" in budget.message


def test_insufficient_message_states_the_next_step_and_keeps_existing_files() -> None:
    budget = assess(2 * 1024**3, 10 * MIB)
    assert "磁盘空间不足" in budget.message
    assert "已有的文件不会被删除" in budget.message
    assert "2.50 GiB" in budget.message  # 2 GiB 估值 + 512 MiB 余量


@pytest.mark.parametrize(
    ("estimated", "free"),
    [(None, 10 * MIB), (100 * MIB, None), (0, 10 * MIB), (-1, 10 * MIB), (100 * MIB, -1)],
)
def test_unknown_never_blocks(estimated: int | None, free: int | None) -> None:
    budget = assess(estimated, free)
    assert budget.status == "unknown"
    assert budget.blocks is False
    assert "无法预估" in budget.message
    assert "再检查一次" in budget.message


def test_assess_for_job_reads_both_numbers_from_the_real_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk_budget, "free_space_bytes", lambda _path: 200 * MIB)
    budget = disk_budget.assess_for_job(_analysis(), DownloadOptions(resolution="720p"), 1, tmp_path)
    assert budget.status == "insufficient"
    assert budget.estimated_bytes == 400 * MIB
    assert budget.free_bytes == 200 * MIB
