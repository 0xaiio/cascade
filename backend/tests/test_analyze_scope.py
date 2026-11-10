"""A-01：非 YouTube 链接要**明确拒绝**，而不是交给 yt-dlp 换回一句莫名报错。

三条判据：
1. 拒绝发生在**网络请求之前**（用计数替身钉住「一次请求都没发」）；
2. 文案说清「只支持 YouTube」+「站点是什么」+「没有发请求」；
3. 合法的 YouTube 形态（短链、移动端、音乐、shorts、合集、nocookie）**一个都不能误伤**。

第 3 条与第 1 条同样重要：一个过窄的正则会把手里的 YouTube 链接也挡在门外，而那种故障
看起来像「工具坏了」，比原来的报错更难查。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts.config import AppSettings
from app.download.ytdlp_service import (
    YtDlpService,
    ensure_youtube_url,
    is_youtube_extractor,
    is_youtube_url,
    unsupported_site_message,
)
from app.main import create_app

from fakes import HappyPathExtractCountingYtDlpService


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def make_client(tmp_path: Path, service=None) -> TestClient:
    return TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service or HappyPathExtractCountingYtDlpService()))


# ---------------------------------------------------------------- 纯判定

@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtube.com/watch?v=abc123",
        "https://m.youtube.com/watch?v=abc123",
        "https://music.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/live/abc123",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube-nocookie.com/embed/abc123",
        "http://youtube.com/watch?v=abc123",
        "https://WWW.YouTube.COM/watch?v=abc123",
    ],
)
def test_accepts_every_youtube_shape(url: str) -> None:
    assert is_youtube_url(url) is True
    ensure_youtube_url(url)  # 不抛就是通过


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://vimeo.com/123456",
        "https://example.com/watch?v=abc",
        "https://notyoutube.com/watch?v=abc",
        # 后缀匹配必须按「主机名的点」切，`evil-youtube.com` 不是 YouTube：
        "https://evil-youtube.com/watch?v=abc",
        "https://youtube.com.evil.example/watch?v=abc",
        # 协议必须在白名单里：`file:` 不能借主机名混进来
        "ftp://youtube.com/watch?v=abc",
        "file:///C:/youtube.com.html",
        # 没有主机名 / 不是 URL
        "youtu.be/abc123",
        "随便一段文字",
        "",
    ],
)
def test_rejects_everything_else(url: str) -> None:
    assert is_youtube_url(url) is False
    with pytest.raises(ValueError) as caught:
        ensure_youtube_url(url)
    assert "只支持 YouTube" in str(caught.value)


def test_message_names_the_site_and_says_no_request_was_sent() -> None:
    message = unsupported_site_message("https://www.bilibili.com/video/BV1xx411c7mD")
    assert "bilibili.com" in message
    assert "没有向它发起请求" in message
    assert "youtube.com / youtu.be" in message


def test_message_never_echoes_raw_user_input() -> None:
    # 主机名里可能带尖括号之类的东西；它会进界面文案，不能原样回显。
    message = unsupported_site_message("https://exa<mple.com/watch?v=1")
    assert "<" not in message
    assert "exa" in message


# ---------------------------------------------------------------- 入口：不发请求

def test_analyze_rejects_non_youtube_without_calling_the_service(tmp_path: Path) -> None:
    service = HappyPathExtractCountingYtDlpService()
    client = make_client(tmp_path, service)

    response = client.post("/api/analyze", json={"url": "https://www.bilibili.com/video/BV1xx411c7mD"})

    assert response.status_code == 400
    assert "只支持 YouTube" in response.json()["detail"]
    assert "bilibili.com" in response.json()["detail"]
    # 关键：不是「先试了再报错」，而是一次请求都没发。
    assert service.extract_urls == []


def test_creating_a_job_from_a_non_youtube_url_is_refused_the_same_way(tmp_path: Path) -> None:
    service = HappyPathExtractCountingYtDlpService()
    client = make_client(tmp_path, service)

    response = client.post(
        "/api/jobs",
        json={
            "url": "https://vimeo.com/123456",
            "options": {"mode": "video_subtitles", "resolution": "720p"},
        },
    )

    assert response.status_code == 400
    assert "只支持 YouTube" in response.json()["detail"]
    assert service.extract_urls == []
    # 也没有留下半截任务记录。
    assert client.get("/api/jobs").json() == []


def test_youtube_urls_still_reach_the_service(tmp_path: Path) -> None:
    service = HappyPathExtractCountingYtDlpService()
    client = make_client(tmp_path, service)

    response = client.post("/api/analyze", json={"url": "https://youtu.be/abc123"})

    assert response.status_code == 200
    assert service.extract_urls == ["https://youtu.be/abc123"]


# ---------------------------------------------------------------- 服务层：extractor 说了算

def test_extractor_key_is_authoritative_when_the_host_lies(tmp_path: Path, monkeypatch) -> None:
    """主机名可以通过短链 / 跳转 / 代理骗过去；yt-dlp 拿到页面后的 `extractor_key` 不会。"""

    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x", "title": "Not YouTube", "extractor_key": "BiliBili", "webpage_url": url}

    monkeypatch.setattr("app.download.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)

    with pytest.raises(ValueError) as caught:
        YtDlpService(download_dir=tmp_path).extract_metadata("https://youtube.com/watch?v=x")

    assert "只支持 YouTube" in str(caught.value)


def test_missing_extractor_key_does_not_block_a_valid_youtube_url(tmp_path: Path, monkeypatch) -> None:
    """老版本 yt-dlp 或替身可能不写这个键 —— 那时不在这里二次拒绝（入口的 host 判定已放行）。"""

    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x", "title": "No key", "webpage_url": url}

    monkeypatch.setattr("app.download.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)

    result = YtDlpService(download_dir=tmp_path).extract_metadata("https://youtu.be/x")
    assert result.title == "No key"


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"extractor_key": "Youtube"}, True),
        ({"extractor_key": "YoutubeTab"}, True),
        ({"extractor_key": "youtube:tab"}, True),
        ({"extractor_key": "Generic"}, False),
        ({"extractor_key": "Twitter"}, False),
        # 没有这个键就不能在服务层二次拒绝：入口的 host 判定已经放行过。
        ({}, True),
    ],
)
def test_extractor_key_decides_only_when_present(info: dict, expected: bool) -> None:
    assert is_youtube_extractor(info) is expected
