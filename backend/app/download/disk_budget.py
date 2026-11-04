"""下载前的磁盘预算：**估算 → 量可用空间 → 给三档结论**。

三档是刻意的，不能合并成布尔值：

| status | 成因 | 处置 |
| --- | --- | --- |
| `ok` | 估值与可用空间都读到了，且够 | 不打扰用户 |
| `insufficient` | 两者都读到了，且不够 | **阻断**：建任务回 409，条目判失败 |
| `unknown` | 任一侧读不到 | **只给提示，绝不阻断** |

为什么 playlist 常常是 `unknown`：解析走 `extract_flat="in_playlist"`，flat 条目既没有
`formats` 也没有 `filesize`。按「平均时长 × 猜一个码率」补一个数字，会把提示伪装成预估，
与本项目「界面里的数字必须能回答一个问题」的纪律相悖。因此 bulk 场景由**逐条开始前**
的第二次校验兜住：那时 `prepare_download` 已经给出这一条的真实 `filesize`。

调用方可以在离线测试里直接注入 `free_bytes` / `estimated_bytes`；本模块只在
`free_space_bytes()` 这一处碰磁盘，且路径不存在时向上找最近的存在的祖先
（playlist 的同名子目录在建任务时还没被创建）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..contracts.schemas import AnalyzeResponse, DownloadOptions, FormatOption
from .ytdlp_formats import resolution_height

# 安全余量：合并后的文件、缩略图、字幕与元数据都要占地方，且文件系统在接近满时
# 写放大会明显变差。512 MiB 对本项目「批量下载」的量级是可解释的定值，不是猜的阈值。
DEFAULT_RESERVE_BYTES = 512 * 1024 * 1024

DiskBudgetStatus = Literal["ok", "insufficient", "unknown"]


@dataclass(frozen=True)
class DiskBudget:
    status: DiskBudgetStatus
    estimated_bytes: int | None
    free_bytes: int | None
    required_bytes: int | None
    message: str

    @property
    def blocks(self) -> bool:
        """只有「算得出来且确实不够」才阻断。"""
        return self.status == "insufficient"


class InsufficientDiskSpace(RuntimeError):
    """逐条开始前的空间校验失败。带数字的文案会原样写进 `JobItem.error`。"""


def format_bytes(value: int | None) -> str:
    """人能核对的量级：二进制单位、两位小数。None 不编数字。"""
    if value is None:
        return "未知"
    if value < 0:
        return "未知"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.0f} {units[index]}" if index == 0 else f"{size:.2f} {units[index]}"


def free_space_bytes(path: Path) -> int | None:
    """目标分区的可用字节。**向上找存在的祖先** —— 新建的下载子目录还不存在。

    任何 OSError（盘被拔掉、权限、网络盘语义不同）都返回 None：读不到可用空间
    是一个「不知道」，不是「空间不足」，不能据此阻断用户。
    """
    candidate = Path(path)
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    try:
        return int(shutil.disk_usage(candidate).free)
    except (OSError, ValueError):
        return None


def _closest_format_bytes(formats: list[FormatOption], options: DownloadOptions) -> int | None:
    """从解析给的格式清单里挑一份**带大小**的，作为单条视频的估值依据。"""
    if options.format_id:
        chosen = next((fmt for fmt in formats if fmt.format_id == options.format_id), None)
        return chosen.filesize if chosen and chosen.filesize and chosen.filesize > 0 else None

    target = resolution_height(options.resolution)
    sized = [fmt for fmt in formats if fmt.filesize and fmt.filesize > 0]
    if not sized:
        return None
    if target is None:
        # `best` 没有明确高度：取清单里最大的一份，是保守的一侧。
        return max(fmt.filesize for fmt in sized if fmt.filesize is not None)
    with_height = [fmt for fmt in sized if fmt.height]
    if not with_height:
        return max(fmt.filesize for fmt in sized if fmt.filesize is not None)
    chosen = min(with_height, key=lambda fmt: (abs((fmt.height or 0) - target), -(fmt.filesize or 0)))
    return chosen.filesize


def estimate_job_bytes(analysis: AnalyzeResponse, options: DownloadOptions, entry_count: int) -> int | None:
    """建任务阶段的总量估值。**读不到就返回 None**，不猜、不补、不用时长折算码率。

    `subtitles_only` 没有视频流，估值天然不存在；`skip_existing` 也不在这里打折扣 ——
    哪些条目会被跳过要等逐条开始时才知道，提前减掉会把阻断变成漏判。
    """
    if options.mode == "subtitles_only" or entry_count <= 0:
        return None
    per_entry = _closest_format_bytes(analysis.formats, options)
    if per_entry is None:
        return None
    return per_entry * entry_count


def assess(
    estimated_bytes: int | None,
    free_bytes: int | None,
    *,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    subject: str = "本次下载",
) -> DiskBudget:
    """把两个数字变成一档结论与一句能照做的话。**唯一的判定入口**。"""
    if estimated_bytes is None or estimated_bytes <= 0 or free_bytes is None or free_bytes < 0:
        available = f"当前可用 {format_bytes(free_bytes)}；" if free_bytes is not None else ""
        return DiskBudget(
            status="unknown",
            estimated_bytes=estimated_bytes,
            free_bytes=free_bytes,
            required_bytes=None,
            message=(
                f"暂时无法预估{subject}需要的空间（平台没给文件大小）。{available}"
                "请先确认下载目录所在分区的余量；开始下载前会按每条视频的真实大小再检查一次。"
            ),
        )

    required = estimated_bytes + max(reserve_bytes, 0)
    if free_bytes < required:
        return DiskBudget(
            status="insufficient",
            estimated_bytes=estimated_bytes,
            free_bytes=free_bytes,
            required_bytes=required,
            message=(
                f"磁盘空间不足：{subject}预计需要 {format_bytes(required)}"
                f"（含 {format_bytes(max(reserve_bytes, 0))} 余量），"
                f"当前可用 {format_bytes(free_bytes)}，还差 {format_bytes(required - free_bytes)}。"
                "请清理该分区，或在设置里换一个下载目录后重试；已有的文件不会被删除。"
            ),
        )
    return DiskBudget(
        status="ok",
        estimated_bytes=estimated_bytes,
        free_bytes=free_bytes,
        required_bytes=required,
        message=(
            f"{subject}预计需要 {format_bytes(required)}（含余量），"
            f"当前可用 {format_bytes(free_bytes)}。"
        ),
    )


def assess_for_job(
    analysis: AnalyzeResponse,
    options: DownloadOptions,
    entry_count: int,
    download_dir: Path,
    *,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> DiskBudget:
    return assess(
        estimate_job_bytes(analysis, options, entry_count),
        free_space_bytes(download_dir),
        reserve_bytes=reserve_bytes,
    )


def assess_for_item(
    filesize: int | None,
    download_dir: Path,
    *,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> DiskBudget:
    """逐条开始前：用 `prepare_download` 给的真实大小再核一次。"""
    return assess(filesize, free_space_bytes(download_dir), reserve_bytes=reserve_bytes, subject="本条视频")
