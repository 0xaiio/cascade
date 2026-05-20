from collections.abc import Callable
from contextlib import suppress
from copy import copy
from dataclasses import dataclass
from http.cookiejar import Cookie
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Any
import urllib.request

from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser


AUTO_BROWSER_COOKIE_CANDIDATES = ["edge", "chrome", "firefox", "brave", "chromium", "vivaldi", "opera"]
YOUTUBE_COOKIE_DOMAIN_SUFFIXES = ("youtube.com", "google.com")


@dataclass(frozen=True)
class BrowserCookieImportResult:
    browser: str
    imported_count: int
    filename: str


class BrowserCookieImportError(RuntimeError):
    def __init__(self, code: str, browser: str | None, message: str, raw_detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.browser = browser
        self.message = message
        self.raw_detail = raw_detail

    @classmethod
    def browser_locked(cls, browser: str, raw_detail: str | None = None) -> "BrowserCookieImportError":
        if browser == "edge":
            message = "Edge 正在运行，cookies 数据库被锁定。请关闭 Edge 后重试，或确认由应用关闭 Edge 并重新导入。"
        else:
            message = f"{browser} 正在运行，cookies 数据库被锁定。请关闭浏览器后重试。"
        return cls("browser_locked", browser, message, raw_detail)

    def to_detail(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "browser": self.browser,
            "message": self.message,
            "raw_detail": self.raw_detail,
        }


class BrowserCookieImporter:
    def __init__(
        self,
        candidates: list[str] | None = None,
        extract_browser_cookie_jar: Callable[[str], YoutubeDLCookieJar] | None = None,
        close_browser_for_cookie_import: Callable[[str], None] | None = None,
        extract_edge_cookies_via_cdp: Callable[[], YoutubeDLCookieJar] | None = None,
    ) -> None:
        self.candidates = candidates or AUTO_BROWSER_COOKIE_CANDIDATES
        self.extract_browser_cookie_jar = extract_browser_cookie_jar or self._extract_browser_cookie_jar
        self.close_browser_for_cookie_import = close_browser_for_cookie_import or self._close_browser_for_cookie_import
        self.extract_edge_cookies_via_cdp = extract_edge_cookies_via_cdp or self._extract_edge_cookies_via_cdp

    def import_browser_cookies(
        self,
        browser: str,
        target_path: Path,
        close_browser_if_locked: bool = False,
    ) -> BrowserCookieImportResult:
        candidates = self.candidates if browser == "auto" else [browser]
        errors: list[str] = []
        locked_error: BrowserCookieImportError | None = None

        for candidate in candidates:
            try:
                imported = self._extract_browser_cookie_jar_with_fallback(candidate, close_browser_if_locked)
            except BrowserCookieImportError as exc:
                if exc.code == "browser_locked":
                    locked_error = exc
                errors.append(f"{candidate}: {exc.raw_detail or exc.message}")
                continue
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue

            filtered = YoutubeDLCookieJar(target_path)
            imported_count = 0
            for cookie in imported:
                if self._is_youtube_related_cookie(cookie.domain):
                    filtered.set_cookie(copy(cookie))
                    imported_count += 1

            if imported_count == 0:
                errors.append(f"{candidate}: no YouTube or Google cookies found")
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            filtered.save(target_path, ignore_discard=True, ignore_expires=True)
            return BrowserCookieImportResult(
                browser=candidate,
                imported_count=imported_count,
                filename=target_path.name,
            )

        if locked_error:
            raise locked_error
        detail = "; ".join(errors) if errors else "no supported browser candidates were available"
        raise RuntimeError(f"Could not import YouTube cookies from browser: {detail}")

    def _extract_browser_cookie_jar(self, browser: str) -> YoutubeDLCookieJar:
        return extract_cookies_from_browser(browser)

    def _extract_browser_cookie_jar_with_fallback(
        self,
        browser: str,
        close_browser_if_locked: bool,
    ) -> YoutubeDLCookieJar:
        try:
            return self.extract_browser_cookie_jar(browser)
        except Exception as exc:
            if self._is_browser_cookie_database_locked(browser, exc):
                if not (close_browser_if_locked and browser == "edge"):
                    raise BrowserCookieImportError.browser_locked(browser, str(exc)) from exc
                self.close_browser_for_cookie_import(browser)
                try:
                    return self.extract_browser_cookie_jar(browser)
                except Exception as retry_exc:
                    if self._is_browser_cookie_database_locked(browser, retry_exc):
                        raise BrowserCookieImportError.browser_locked(browser, str(retry_exc)) from retry_exc
                    if self._is_edge_dpapi_decrypt_error(browser, retry_exc):
                        return self.extract_edge_cookies_via_cdp()
                    raise
            if self._is_edge_dpapi_decrypt_error(browser, exc):
                return self.extract_edge_cookies_via_cdp()
            raise

    def _close_browser_for_cookie_import(self, browser: str) -> None:
        if browser != "edge":
            return
        if os.name != "nt":
            raise BrowserCookieImportError.browser_locked(browser, "Automatic Edge closing is only supported on Windows.")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/IM", "msedge.exe", "/F", "/T"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        time.sleep(1.0)

    def _extract_edge_cookies_via_cdp(self) -> YoutubeDLCookieJar:
        edge = self._edge_executable()
        if not edge:
            raise RuntimeError("Microsoft Edge executable was not found.")
        port = self._free_tcp_port()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [
                edge,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={self._edge_user_data_dir()}",
                "--profile-directory=Default",
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--disable-default-apps",
                "https://www.youtube.com/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            websocket_url = self._wait_for_cdp_websocket_url(port)
            cookies = self._read_cdp_cookies(websocket_url)
        finally:
            self._terminate_edge_process(process)

        jar = YoutubeDLCookieJar()
        for value in cookies:
            cookie = self._cdp_cookie(value)
            if cookie:
                jar.set_cookie(cookie)
        return jar

    def _terminate_edge_process(self, process: Any) -> None:
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with suppress(Exception):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/F", "/T"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=creationflags,
                )
            return
        with suppress(Exception):
            process.terminate()
            process.wait(timeout=5)
        with suppress(Exception):
            process.kill()

    def _edge_executable(self) -> str | None:
        candidates = [
            shutil.which("msedge"),
            str(Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _edge_user_data_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"

    def _free_tcp_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_for_cdp_websocket_url(self, port: int) -> str:
        url = f"http://127.0.0.1:{port}/json/list"
        deadline = time.time() + 15
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, list):
                    raise RuntimeError("Edge DevTools returned an invalid target list.")
                pages = [target for target in payload if isinstance(target, dict) and target.get("type") == "page"]
                target = next((page for page in pages if "youtube.com" in str(page.get("url", ""))), None)
                target = target or (pages[0] if pages else None)
                websocket_url = target.get("webSocketDebuggerUrl") if target else None
                if websocket_url:
                    return str(websocket_url)
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        raise RuntimeError(f"Timed out waiting for Edge DevTools endpoint: {last_error}")

    def _read_cdp_cookies(self, websocket_url: str) -> list[dict[str, Any]]:
        from websockets.sync.client import connect

        with connect(websocket_url, open_timeout=5, close_timeout=2, max_size=None) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Network.getCookies",
                        "params": {
                            "urls": [
                                "https://www.youtube.com/",
                                "https://youtube.com/",
                                "https://accounts.google.com/",
                                "https://www.google.com/",
                            ]
                        },
                    }
                )
            )
            deadline = time.time() + 10
            while time.time() < deadline:
                message = json.loads(websocket.recv(timeout=10))
                if message.get("id") != 1:
                    continue
                result = message.get("result") or {}
                cookies = result.get("cookies") or []
                if not isinstance(cookies, list):
                    raise RuntimeError("Edge DevTools returned an invalid cookies payload.")
                return [cookie for cookie in cookies if isinstance(cookie, dict)]
        raise RuntimeError("Timed out reading cookies from Edge DevTools.")

    def _cdp_cookie(self, value: dict[str, Any]) -> Cookie | None:
        name = value.get("name")
        cookie_value = value.get("value")
        domain = value.get("domain")
        if not name or cookie_value is None or not domain:
            return None
        expires = value.get("expires")
        parsed_expires = int(expires) if isinstance(expires, (int, float)) and expires > 0 else None
        path = str(value.get("path") or "/")
        return Cookie(
            version=0,
            name=str(name),
            value=str(cookie_value),
            port=None,
            port_specified=False,
            domain=str(domain),
            domain_specified=True,
            domain_initial_dot=str(domain).startswith("."),
            path=path,
            path_specified=True,
            secure=bool(value.get("secure")),
            expires=parsed_expires,
            discard=parsed_expires is None,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None} if value.get("httpOnly") else {},
            rfc2109=False,
        )

    def _is_browser_cookie_database_locked(self, browser: str, exc: Exception) -> bool:
        return browser == "edge" and "could not copy chrome cookie database" in str(exc).lower()

    def _is_edge_dpapi_decrypt_error(self, browser: str, exc: Exception) -> bool:
        return browser == "edge" and "failed to decrypt with dpapi" in str(exc).lower()

    def _is_youtube_related_cookie(self, domain: str) -> bool:
        normalized = domain.lower().lstrip(".")
        return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in YOUTUBE_COOKIE_DOMAIN_SUFFIXES)

