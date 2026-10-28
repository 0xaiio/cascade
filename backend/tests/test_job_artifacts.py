"""任务产物的定位与本机打开（`job_artifacts`）。

未覆盖的 14 行集中在**失败分支**：409 的各种「产物还没就绪」，以及 `open_*` 把
「播放器不可用」与「其它错误」分成 409 / 400。这些分支恰恰是用户会遇到的
（产物还没下完就点了按钮），而它们的文案决定了用户下一步该做什么。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.contracts.config import AppSettings
from app.infra.db import create_app_engine, init_db
from app.download.job_artifacts import (
    item_folder,
    job_folder,
    open_local_path,
    open_video_path,
    output_file,
    subtitle_file,
)
from app.contracts.models import Job, JobItem
from app.host.system_open import LocalOpenError


def make_item(**kwargs) -> JobItem:
    payload = {
        "id": "item-1",
        "job_id": "job-1",
        "source_url": "https://youtu.be/abc",
        "title": "Item",
        "index": 1,
    }
    payload.update(kwargs)
    return JobItem(**payload)


def detail(exc: HTTPException) -> str:
    return str(exc.detail)


def test_output_file_returns_the_recorded_file(tmp_path: Path) -> None:
    video = tmp_path / "Video [abc].mp4"
    video.write_text("x", encoding="utf-8")

    assert output_file(make_item(output_path=str(video))) == video


def test_output_file_is_409_when_nothing_has_been_downloaded(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as excinfo:
        output_file(make_item(), tmp_path)

    assert excinfo.value.status_code == 409
    assert detail(excinfo.value) == "视频文件尚不可用。"
    # 409 而不是 404：条目在库里，只是产物还没就绪 —— 这两种情况用户该做的事不同。
    assert excinfo.value.status_code != 404


def test_a_directory_recorded_as_the_output_counts_as_not_downloaded(tmp_path: Path) -> None:
    """记录路径被一个**目录**占住（重命名/解压残留）：候选链只认文件，于是等于「还没有成品」。"""
    occupied = tmp_path / "Video [abc].mp4"
    occupied.mkdir()

    with pytest.raises(HTTPException) as excinfo:
        output_file(make_item(output_path=str(occupied)))

    assert excinfo.value.status_code == 409
    assert detail(excinfo.value) == "视频文件尚不可用。"


def test_output_file_is_409_when_the_file_disappears_after_it_was_resolved(
    tmp_path: Path, monkeypatch
) -> None:
    """TOCTOU 窗口：候选链解析到之后、真正打开之前文件被删（用户在资源管理器里删了它）。

    这条分支是**竞态兜底**，正常路径走不到 —— 所以这里直接把解析结果替身化，
    而不是指望在两步之间插进去。
    """
    gone = tmp_path / "Video [abc].mp4"
    monkeypatch.setattr("app.download.job_artifacts.existing_output_file", lambda *args, **kwargs: gone)

    with pytest.raises(HTTPException) as excinfo:
        output_file(make_item())

    assert excinfo.value.status_code == 409
    assert detail(excinfo.value) == "视频文件不存在。"


def test_item_folder_falls_back_to_the_download_dir(tmp_path: Path) -> None:
    """产物还没出现时退到下载目录 —— 打开文件夹比报「不存在」有用。"""
    assert item_folder(make_item(), tmp_path) == tmp_path


def test_item_folder_is_409_when_even_the_download_dir_is_gone(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as excinfo:
        item_folder(make_item(), tmp_path / "missing")

    assert excinfo.value.status_code == 409
    assert detail(excinfo.value) == "视频文件夹不存在。"


def test_subtitle_file_prefers_the_recorded_path(tmp_path: Path) -> None:
    subtitle = tmp_path / "Video [abc].en.vtt"
    subtitle.write_text("WEBVTT", encoding="utf-8")

    assert subtitle_file(make_item(subtitle_path=str(subtitle))) == subtitle


def test_subtitle_file_finds_the_sidecar_next_to_the_video(tmp_path: Path) -> None:
    """`subtitle_path` 是后加的列：更早的任务只留下视频旁边的字幕文件。"""
    video = tmp_path / "Video [abc].mp4"
    video.write_text("x", encoding="utf-8")
    sidecar = tmp_path / "Video [abc].en.vtt"
    sidecar.write_text("WEBVTT", encoding="utf-8")

    found = subtitle_file(make_item(output_path=str(video), subtitle_path=None))

    assert found == sidecar


def test_subtitle_file_is_409_when_this_item_has_no_subtitle(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as excinfo:
        subtitle_file(make_item(), tmp_path)

    assert excinfo.value.status_code == 409
    assert detail(excinfo.value) == "字幕文件尚不可用。"


def open_session(tmp_path: Path) -> Session:
    engine = create_app_engine(
        AppSettings(
            data_dir=tmp_path / "data",
            download_dir=tmp_path / "downloads",
            database_path=tmp_path / "data" / "app.sqlite3",
            default_concurrency=1,
        )
    )
    init_db(engine)
    return Session(engine)


def seed_job(session: Session, download_dir: str | None, items: int) -> Job:
    job = Job(id="job-1", url="https://youtu.be/abc", title="Job", options_json="{}", download_dir=download_dir)
    session.add(job)
    for index in range(items):
        session.add(
            JobItem(
                id=f"item-{index}",
                job_id="job-1",
                source_url="https://youtu.be/abc",
                title=f"Item {index}",
                index=index + 1,
            )
        )
    session.commit()
    return job


def test_job_folder_of_a_single_video_is_the_video_folder(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        job = seed_job(session, str(tmp_path), items=1)

        assert job_folder(session, job) == tmp_path


def test_job_folder_of_a_playlist_requires_its_own_directory(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        job = seed_job(session, None, items=2)

        with pytest.raises(HTTPException) as excinfo:
            job_folder(session, job)

    assert detail(excinfo.value) == "合集文件夹尚不可用。"


def test_job_folder_reports_a_directory_that_vanished(tmp_path: Path) -> None:
    with open_session(tmp_path) as session:
        job = seed_job(session, str(tmp_path / "gone"), items=2)

        with pytest.raises(HTTPException) as excinfo:
            job_folder(session, job)

    assert detail(excinfo.value) == "合集文件夹不存在。"


async def test_open_local_path_runs_the_opener_and_wraps_other_errors(tmp_path: Path) -> None:
    opened: list[Path] = []
    await open_local_path(opened.append, tmp_path)
    assert opened == [tmp_path]

    def boom(_path: Path) -> None:
        raise OSError("没有权限")

    with pytest.raises(HTTPException) as excinfo:
        await open_local_path(boom, tmp_path)
    assert excinfo.value.status_code == 400
    assert detail(excinfo.value).startswith("无法打开本地路径：")
    assert "没有权限" in detail(excinfo.value)


async def test_open_local_path_lets_http_exceptions_through_untouched(tmp_path: Path) -> None:
    """调用方已经翻译好的状态码不能被这层改成 400。"""

    def rejects(_path: Path) -> None:
        raise HTTPException(status_code=409, detail="已经有一个窗口在等你选择")

    with pytest.raises(HTTPException) as excinfo:
        await open_local_path(rejects, tmp_path)

    assert excinfo.value.status_code == 409
    assert detail(excinfo.value) == "已经有一个窗口在等你选择"


async def test_a_missing_player_is_409_but_a_broken_launch_is_400(tmp_path: Path) -> None:
    """两种失败要分开：环境问题（409，换播放器/重试可解）与真正的错误（400）。"""
    calls: list[tuple[Path, str | None]] = []

    def good(path: Path, actual_format: str | None) -> None:
        calls.append((path, actual_format))

    await open_video_path(good, tmp_path, "mp4 · avc1 + mp4a")
    assert calls == [(tmp_path, "mp4 · avc1 + mp4a")]

    def no_player(_path: Path, _fmt: str | None) -> None:
        raise LocalOpenError("找不到可确认能解码当前视频的播放器")

    with pytest.raises(HTTPException) as excinfo:
        await open_video_path(no_player, tmp_path, None)
    assert excinfo.value.status_code == 409
    assert "找不到可确认能解码" in detail(excinfo.value)

    def broken(_path: Path, _fmt: str | None) -> None:
        raise RuntimeError("句柄无效")

    with pytest.raises(HTTPException) as excinfo:
        await open_video_path(broken, tmp_path, None)
    assert excinfo.value.status_code == 400
    assert detail(excinfo.value).startswith("无法打开本地视频：")
