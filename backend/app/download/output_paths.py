"""条目产物在磁盘上的**位置**：候选链、变体展开、成品定位。

本模块是「这个条目的文件可能在哪」的**唯一计算处**。两个调用方各自只做筛选，不各自拼链：

- API 层（`job_artifacts.py`）问「**已存在**的成品在哪」→ `existing_output_file` / `existing_item_folder`，
  用于播放与打开文件夹；
- 编排层（`job_manager.py`）问「**可能**涉及哪些文件」→ `item_artifact_candidates`，用于删除。

在这之前，这两处各拼一套候选链（同一条链在两处各算一次），是本仓库「同一概念两处计算」的
又一例。收敛后本模块是落点，记录见 `ai/refactor/004-artifact-paths.md`。
**不要再在调用方里拼第三条链。**

本模块只负责「在哪」：除 `is_file()` / `iterdir()` 这类只读探测外不碰 IO，
不做白名单校验、不做删除（那是 `job_manager` 的安全删除）。
"""

from collections.abc import Iterable
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from .subtitle_plan import is_supported_language, pick_track


SIDECAR_SUFFIXES = [".description", ".info.json", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".vtt"]
# yt-dlp 写出的字幕文件后缀。**只认文本字幕**：`.json` 是元数据，`.ass` 本项目不产出
# （`subtitle_format` 的值域是 best/srt/vtt），认了反而会把 info.json 之类的邻居捞进来。
SUBTITLE_SUFFIXES = (".srt", ".vtt")
MERGED_OUTPUT_SUFFIXES = [".mp4", ".mkv", ".webm"]
MEDIA_SUFFIXES = [".mp4", ".mkv", ".webm", ".m4v", ".mov"]
PARTIAL_SUFFIXES = [".part", ".ytdl", ".tmp", ".temp"]
FORMAT_SUFFIX_PATTERN = re.compile(r"^(?P<stem>.+)\.f\d+(?P<suffix>\.[^.]+)$")


def resolve_existing_output_path(output_path: Path, base_dir: Path | None = None) -> Path | None:
    for path in _path_variants(output_path, base_dir):
        for candidate in merged_output_path_candidates(path):
            if candidate.is_file():
                return candidate
        if path.is_file():
            return path
    return None


def discover_existing_output_path(source_url: str, job_download_dir: Path | None) -> Path | None:
    for path in discover_output_file_candidates(source_url, job_download_dir):
        if _is_preferred_media_path(path):
            return path
    return None


def discover_output_file_candidates(source_url: str, job_download_dir: Path | None) -> list[Path]:
    if job_download_dir is None:
        return []
    directory = job_download_dir.expanduser()
    if not directory.is_dir():
        return []
    video_id = source_video_id(source_url)
    if not video_id:
        return []
    token = f"[{video_id}]"
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file() and token in path.name
    ]
    return sorted(candidates, key=lambda path: (not _is_preferred_media_path(path), path.name.lower()))


def item_artifact_candidates(
    output_path: str | Path | None,
    source_url: str,
    base_dir: Path | None = None,
) -> list[Path]:
    """一个条目在磁盘上**可能关联的全部路径**：库记录的 `output_path` 在前，其后是按视频 id 的磁盘发现。

    这是「条目候选链」的唯一计算处 —— API 层从里面挑「已存在的成品」，编排层拿全部去删。
    只枚举、不判断存在性：要不要用某个候选，由调用方按自己的语义决定（删除要覆盖全部候选，
    展示只要第一个）。
    """
    paths: list[Path] = []
    if output_path:
        paths.append(Path(output_path))
    paths.extend(discover_output_file_candidates(source_url, base_dir))
    return _dedupe(paths)


def existing_output_file(
    output_path: str | Path | None,
    source_url: str,
    base_dir: Path | None = None,
) -> Path | None:
    """第一个**已存在**的成品视频；找不到返回 None（由调用方决定是 409 还是别的）。

    顺序：库记录路径（含合并 / 后缀变体）优先；仅当它一个都不存在时，才退回按视频 id
    发现的第一**个成品**（要求是媒体文件且不是 `.part` 之类的临时产物）。
    """
    if output_path:
        resolved = resolve_existing_output_path(Path(output_path), base_dir)
        if resolved is not None:
            return resolved
    return discover_existing_output_path(source_url, base_dir)


def existing_item_folder(
    output_path: str | Path | None,
    source_url: str,
    base_dir: Path | None = None,
) -> Path | None:
    """条目产物所在目录；产物还没出现时退回按视频 id 发现的第一个候选所在目录。找不到返回 None。

    与 [`existing_output_file`](#) 的区别是**不要求成品存在**：调用方在产物缺失时会退到下载根目录
    （打开文件夹比报「不存在」更有用）。也正因如此，它退到的是「第一个候选」而不是「第一个成品」。
    """
    resolved = resolve_existing_output_path(Path(output_path), base_dir) if output_path else None
    if resolved is None:
        discovered = discover_output_file_candidates(source_url, base_dir)
        resolved = discovered[0] if discovered else None
    return resolved.parent if resolved is not None else None


def subtitle_candidates(video_path: Path) -> list[tuple[Path, str]]:
    """视频旁边的字幕文件，返回 `(路径, 语言代码)`。

    命名是 yt-dlp 定的：`<视频文件名去掉后缀>.<语言代码>.<后缀>`，例如
    `Title [abc].en.vtt`。这里按「同名前缀 + 字幕后缀」反推出语言代码 ——
    **不枚举语言**，因为语言代码没有有界集合（`zh-Hans` / `en-US` / `en-orig` …）。

    用 `iterdir()` 而不是 `glob`：标题里常见 `[` `]`（本项目自己的命名模板就带它），
    `glob` 会把它们当字符类，于是带方括号的标题一个候选都匹配不到。
    """
    directory = video_path.parent
    if not directory.is_dir():
        return []
    prefix = f"{video_path.stem}."
    found: list[tuple[Path, str]] = []
    for entry in sorted(directory.iterdir(), key=lambda path: path.name.lower()):
        if not entry.is_file() or not entry.name.startswith(prefix):
            continue
        if entry.suffix.lower() not in SUBTITLE_SUFFIXES:
            continue
        code = entry.name[len(prefix) : -len(entry.suffix)]
        # 空代码 = 文件就叫 `X..vtt`，不是「某语言的字幕」。
        if code:
            found.append((entry, code))
    return found


def subtitle_language_from_path(subtitle_path: Path) -> str | None:
    """从字幕文件名里取回语言代码。**`subtitle_candidates` 的逆运算。**

    命名是 `<视频名>.<语言代码>.<后缀>`，语言代码永远是**最后一个点分段** ——
    即使视频标题里带点（`Ep 3.1 [abc].en.vtt`）也成立。
    """
    stem = Path(subtitle_path).stem
    if "." not in stem:
        return None
    code = stem.rsplit(".", 1)[-1]
    return code or None


def find_subtitle_file(video_path: Path, family: str | None = None) -> tuple[Path, str] | None:
    """挑一条**中英**字幕，返回 `(路径, 语言代码)`；没有返回 `None`。

    `family` 给了就只在该族里挑（挑法与下载计划同一套偏好，见
    `subtitle_plan.pick_track`）—— 否则「视频被判成中文却挑到一条英文轨」会一路带到
    B站 上传，语言标识就错了。没给 `family` 时按文件名的字典序取第一条，
    保证同一份目录永远同一个结论。

    **非中英语种一律跳过**：本项目只处理英文与中文字幕。
    """
    supported = [
        (path, code) for path, code in subtitle_candidates(video_path) if is_supported_language(code)
    ]
    if not supported:
        return None
    if family:
        picked = pick_track([code for _path, code in supported], family)
        if picked:
            return next((path, code) for path, code in supported if code == picked)
    return supported[0]


def existing_subtitle_file(
    subtitle_path: str | Path | None,
    video_path: Path | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    """条目**已下载到磁盘**的字幕文件；没有返回 `None`。

    「字幕在哪」与成品同一条思路（也正是本模块存在的理由：只有一个计算处）：
    库记录的 `subtitle_path` 优先（只验存在 —— 记录可能指向已被删掉的文件），
    否则看**这段视频旁边**有没有同名字幕（命名规则归 `subtitle_candidates`）。

    视频路径由调用方给（它已经算过一遍 `existing_output_file`）—— 这里**不再自己发现一遍**：
    读模型每一次轮询都要为每个条目问这个问题，多扫一遍目录是白花的钱。

    第二条兜底不是多余的：`subtitle_path` 是 2026-10-18 才加的列，此前下载的任务
    那一列是空的，而字幕文件好端端躺在视频旁边。只认记录的话，那些条目的按钮会是灰的。
    """
    if subtitle_path:
        for candidate in _path_variants(Path(subtitle_path), base_dir):
            if candidate.is_file():
                return candidate
    if video_path is None:
        return None
    found = find_subtitle_file(video_path)
    return found[0] if found else None


def source_video_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        value = parsed.path.strip("/").split("/", 1)[0]
        return value or None
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id
    path_parts = [part for part in parsed.path.split("/") if part]
    for marker in ("shorts", "embed", "live"):
        if marker in path_parts:
            index = path_parts.index(marker)
            if index + 1 < len(path_parts):
                return path_parts[index + 1]
    return None


def merged_output_path_candidates(output_path: Path) -> list[Path]:
    match = FORMAT_SUFFIX_PATTERN.match(output_path.name)
    if not match:
        return []

    base = output_path.with_name(match.group("stem"))
    original_suffix = match.group("suffix").lower()
    suffixes = [".mp4", original_suffix, *MERGED_OUTPUT_SUFFIXES]
    return _dedupe(base.with_suffix(suffix) for suffix in suffixes)


def output_file_candidates(output_path: Path, base_dir: Path | None = None) -> list[Path]:
    base_paths: list[Path] = []
    for path in _path_variants(output_path, base_dir):
        base_paths.extend([path, *merged_output_path_candidates(path)])
    candidates: list[Path] = []
    for base_path in base_paths:
        candidates.extend([base_path, *(base_path.with_suffix(suffix) for suffix in SIDECAR_SUFFIXES)])
    return _dedupe(candidates)


def _is_preferred_media_path(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_SUFFIXES and not _is_partial_path(path)


def _is_partial_path(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in PARTIAL_SUFFIXES)


def _path_variants(output_path: Path, base_dir: Path | None) -> list[Path]:
    path = output_path.expanduser()
    variants: list[Path] = []
    if base_dir is not None and not path.is_absolute():
        variants.append(base_dir.expanduser() / path)
    variants.append(path)
    return _dedupe(variants)


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    """保序去重。**顺序是语义的一部分**（候选链「谁先被选中」由顺序决定），所以不能用 `set`。"""
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped
