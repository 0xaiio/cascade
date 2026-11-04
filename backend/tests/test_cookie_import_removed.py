"""浏览器 cookies 导入入口已移除（[ai/bug-fix/038](../ai/bug-fix/038-browser-cookie-import-is-gone.md)）。

判据不是「旧接口还能不能跑」，而是三件更硬的事：

1. **入口本身不存在了**：路由 404，前端不再有按钮（前端用例在
   `frontend/src/cookies/cookies.test.tsx`）；
2. **失败路径只尝试一次**，消息只指向「导出 cookies.txt 再上传」，
   不再出现「关闭 Edge」「从浏览器导入」这种指向已删除按钮的动作；
3. **源码里不再有 `taskkill` / `msedge.exe`** —— 这条最贵，它是唯一会破坏用户数据的声明。

删除入口的理由是实测出来的，不是偏好：本机 Edge 的 cookie 是 v20 app-bound 加密，
离线程序必然解不开；旧实现失败后会用 `taskkill /IM msedge.exe /F /T` 去「释放」数据库，
关掉的是用户**所有** Edge 窗口（见 [ai/bug-fix/005](../ai/bug-fix/005-edge-cdp-fallback-touches-the-live-profile.md)）。
"""

import ast
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import tokenize
from typing import get_args

from fastapi.testclient import TestClient

from app.contracts.schemas import CookieSource, CookieStatus
from app.main import create_app
from fakes import BotChallengeYtDlpService, DownloadBotChallengeYtDlpService
from test_api import make_client, make_settings, wait_for_job_status


def test_the_browser_import_route_is_gone(tmp_path) -> None:
    """`POST /api/cookies/from-browser` 必须 404：删接口比留一个「永远 400 的接口」诚实。"""
    client = make_client(tmp_path)

    response = client.post("/api/cookies/from-browser", json={"browser": "auto"})

    assert response.status_code == 404


def test_uploading_and_clearing_a_cookie_file_still_works(tmp_path) -> None:
    """删掉的是「替用户去读浏览器」，不是「用户自己给文件」—— 上传/清除必须原样可用。"""
    client = make_client(tmp_path)

    uploaded = client.post(
        "/api/cookies",
        files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tx\n", "text/plain")},
    )

    assert uploaded.status_code == 200
    assert uploaded.json() == {"enabled": True, "filename": "cookies.txt", "source": "file"}

    cleared = client.delete("/api/cookies")

    assert cleared.status_code == 200
    assert cleared.json() == {"enabled": False, "filename": None, "source": "none"}


def test_cookie_status_has_no_browser_only_fields() -> None:
    """契约里不该留「永远为空」的字段：那会让人以为还有一条从浏览器来的路。"""
    assert "browser" not in CookieStatus.model_fields
    assert "imported_count" not in CookieStatus.model_fields
    assert "browser" not in get_args(CookieSource)


def test_analyze_tells_you_how_to_export_instead_of_reading_the_browser(tmp_path) -> None:
    service = BotChallengeYtDlpService()
    client = make_client(tmp_path, service=service)

    response = client.post("/api/analyze", json={"url": "https://youtube.com/playlist?list=abc"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    # 结论：帮不上忙，且说清为什么。
    assert "不会去读浏览器里的 cookies" in detail
    # 下一步：可执行，且指向真实存在的入口。
    assert "Get cookies.txt LOCALLY" in detail
    assert "选择 cookies" in detail
    # 不得再提已经删掉的两个按钮。
    assert "关闭 Edge" not in detail
    assert "从浏览器导入" not in detail
    # 只尝试一次：没有「导入后重试」，也没有偷偷产出一个 cookies 文件。
    assert service.extract_calls == [None]
    assert not (tmp_path / "data" / "cookies.txt").exists()


def test_analyze_with_a_stale_cookie_file_still_attempts_exactly_once(tmp_path) -> None:
    """边界：文件在、但已经过期 —— 仍然只试一次（旧实现会「自动刷新」后再试一次）。"""
    settings = make_settings(tmp_path)
    settings.ensure_directories()
    settings.cookies_path.write_text("STALE_COOKIE_VALUE", encoding="utf-8")
    service = BotChallengeYtDlpService()
    client = TestClient(create_app(settings=settings, ytdlp_service=service))

    response = client.post("/api/analyze", json={"url": "https://youtu.be/G9MxNwUoSt0"})

    assert response.status_code == 400
    assert service.extract_calls == [settings.cookies_path]
    assert "Get cookies.txt LOCALLY" in response.json()["detail"]


def test_two_concurrent_analyses_both_get_the_same_guidance(tmp_path) -> None:
    """并发（界面上两个标签页同时解析）：两次都只试一次、都拿到同一句下一步，且不写任何文件。

    旧实现这里要抢一把 `_cookie_import_lock`，并且会把同一份浏览器 cookie 读两遍；
    现在没有共享状态可抢 —— 这条用例钉的是「没有它也能同时失败得干净」。
    """
    service = BotChallengeYtDlpService()
    settings = make_settings(tmp_path)

    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        def call(_: int) -> tuple[int, str]:
            response = client.post("/api/analyze", json={"url": "https://youtu.be/G9MxNwUoSt0"})
            return response.status_code, response.json()["detail"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, range(2)))

    assert [code for code, _ in results] == [400, 400]
    assert all("Get cookies.txt LOCALLY" in detail for _, detail in results)
    # 每个请求各尝试一次 —— 没有「一次失败触发两条重试」这种事。
    assert len(service.extract_calls) == 2
    assert not (tmp_path / "data" / "cookies.txt").exists()


def test_a_failed_download_says_how_to_export_and_does_not_retry(tmp_path) -> None:
    """下载侧同理：失败消息里只有导出的下一步，且只下载一次。"""
    settings = make_settings(tmp_path)
    settings.ensure_directories()
    settings.cookies_path.write_text("STALE_COOKIE_VALUE", encoding="utf-8")
    service = DownloadBotChallengeYtDlpService()

    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/G9MxNwUoSt0",
                "options": {"mode": "video_subtitles", "resolution": "720p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert item["status"] == "failed"
    assert service.cookie_snapshots == ["STALE_COOKIE_VALUE"]
    assert "Get cookies.txt LOCALLY" in item["error"]
    assert "关闭 Edge" not in item["error"]
    assert "从浏览器导入" not in item["error"]


def test_the_destructive_browser_shutdown_is_gone_from_the_source() -> None:
    """源码级回归：结束进程的命令不许回来，那个模块也不许被重新引入。

    判据刻意收窄到**独立的字符串字面量**（`["taskkill", ...]`）与 import 语句：
    注释和 docstring 里提到这个词是允许的 —— 它们解释的正是「为什么删掉」。
    `msedge.exe` 不在此列：它还合法地出现在「找 Edge 可执行文件给 PO token provider」的表里，
    而光有一个路径字符串杀不掉任何进程。
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    problems: list[str] = []

    for path in sorted(app_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(app_dir)

        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.STRING and token.string.strip("'\"") == "taskkill":
                problems.append(f"{relative}:{token.start[0]} 又出现了结束进程的命令")

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("browser_cookies"):
                problems.append(f"{relative}:{node.lineno} 又导入了 browser_cookies")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("browser_cookies"):
                        problems.append(f"{relative}:{node.lineno} 又导入了 browser_cookies")

    assert not (app_dir / "host" / "browser_cookies.py").exists()
    assert problems == []
