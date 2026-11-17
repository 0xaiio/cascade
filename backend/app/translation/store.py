"""翻译任务的文件侧存储：逐条译文 JSON 的读写与原子替换。

只有逐条数据放文件，任务索引/进度放 DB。这样「编辑一条译文」是一次小文件重写，
而「列表里看进度」是一次轻量 SQL，两边谁都不用装另一边的东西。

原子替换很重要：翻译是后台线程跑的，界面可能正好在刷新；半截 JSON 会让用户
丢失全部已译条目。先写同目录临时文件、`flush` + `os.replace` 才有「要么旧、要么新」。
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .align import AlignedCue


def new_translation_id() -> str:
    return uuid.uuid4().hex


def cues_path(data_dir: str | Path, translation_id: str) -> Path:
    return Path(data_dir) / "translations" / f"{translation_id}.json"


def load_cues(data_dir: str | Path, translation_id: str) -> dict[str, Any]:
    path = cues_path(data_dir, translation_id)
    if not path.is_file():
        return {"source_sha256": "", "cues": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cues"), list):
        raise ValueError(f"译文文件损坏：{path.name}")
    return data


def save_cues(data_dir: str | Path, translation_id: str, data: dict[str, Any]) -> None:
    path = cues_path(data_dir, translation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{translation_id}-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def cue_records(aligned: list[AlignedCue], states: dict[int, str] | None = None) -> list[dict[str, Any]]:
    states = states or {}
    return [
        {
            "index": cue.index,
            "start": cue.start,
            "end": cue.end,
            "source": cue.source,
            "translation": cue.translation,
            "edited": cue.edited,
            "state": states.get(cue.index, "translated" if cue.translation else "pending"),
            "error": None,
        }
        for cue in aligned
    ]
