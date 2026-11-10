"""A-04：解析阶段就标出「本地已有」，让重复下载前知道会重下几条。

三个判据：

1. 目录规则与建任务时**同一套**（合集落同名子目录、单视频落根目录），否则提示的地方
   与真正发生下载的地方不是同一个目录，标了也没用；
2. 判据与 `skip_existing` 一致：只认媒体后缀、排除 `.part` 这类中间产物；
3. 一次目录枚举解决全部条目 —— 200 条的合集不能变成 200 次 `iterdir()`。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.contracts.config import AppSettings
from app.download.output_paths import discover_existing_media_by_id
from app.main import create_app

from fakes import FakeYtDlpService


def make_client(tmp_path: Path) -> TestClient:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    return TestClient(create_app(settings=settings, ytdlp_service=FakeYtDlpService()))


# ---------------------------------------------------------------- 纯发现

def test_batch_discovery_only_counts_real_media(tmp_path: Path) -> None:
    (tmp_path / "One [one].mp4").write_text("x", encoding="utf-8")
    (tmp_path / "Two [two].webm").write_text("x", encoding="utf-8")
    (tmp_path / "Three [three].mp4.part").write_text("x", encoding="utf-8")
    (tmp_path / "Four [four].jpg").write_text("x", encoding="utf-8")
    (tmp_path / "background.jpg").write_text("x", encoding="utf-8")

    found = discover_existing_media_by_id(["one", "two", "three", "four", "five"], tmp_path)

    assert set(found) == {"one", "two"}
    assert found["one"].name == "One [one].mp4"


def test_batch_discovery_prefers_the_stable_name_when_several_exist(tmp_path: Path) -> None:
    (tmp_path / "B [abc].mp4").write_text("x", encoding="utf-8")
    (tmp_path / "A [abc].mkv").write_text("x", encoding="utf-8")

    found = discover_existing_media_by_id(["abc"], tmp_path)
    assert found["abc"].name == "A [abc].mkv"


def test_batch_discovery_tolerates_missing_directory_and_empty_ids(tmp_path: Path) -> None:
    assert discover_existing_media_by_id(["one"], tmp_path / "nope") == {}
    assert discover_existing_media_by_id([], tmp_path) == {}
    assert discover_existing_media_by_id(["", "  "], tmp_path) == {}


# ---------------------------------------------------------------- 接口

def test_playlist_entries_are_marked_when_their_file_exists(tmp_path: Path) -> None:
    """合集落「同名子目录」——与建任务时 `job_download_dir` 的规则一致。"""
    client = make_client(tmp_path)
    folder = tmp_path / "downloads" / "Batch"
    folder.mkdir()
    (folder / "Two [two].mp4").write_text("x", encoding="utf-8")

    payload = client.post(
        "/api/analyze", json={"url": "https://youtube.com/playlist?list=abc"}
    ).json()

    assert payload["is_playlist"] is True
    assert [entry["local"] for entry in payload["entries"]] == [False, True]


def test_playlist_without_the_folder_marks_nothing(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    payload = client.post(
        "/api/analyze", json={"url": "https://youtube.com/playlist?list=abc"}
    ).json()
    assert [entry["local"] for entry in payload["entries"]] == [False, False]


def test_single_video_is_marked_from_the_root_directory(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    (tmp_path / "downloads" / "Video [abc123].mp4").write_text("x", encoding="utf-8")

    payload = client.post("/api/analyze", json={"url": "https://youtu.be/abc123"}).json()

    assert payload["is_playlist"] is False
    assert payload["local"] is True


def test_single_video_without_the_file_is_not_marked(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    payload = client.post("/api/analyze", json={"url": "https://youtu.be/abc123"}).json()
    assert payload["local"] is False


def test_half_downloaded_files_do_not_count_as_existing(tmp_path: Path) -> None:
    """`.part` 是没下完的中间产物；把它标成「已有」会让人以为不用再下。"""
    client = make_client(tmp_path)
    (tmp_path / "downloads" / "Video [abc123].mp4.part").write_text("x", encoding="utf-8")

    payload = client.post("/api/analyze", json={"url": "https://youtu.be/abc123"}).json()
    assert payload["local"] is False
