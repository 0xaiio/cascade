"""B站 请求的 HTTP 接缝。

单独成模块的理由是**它要被两个模块共用**（`bili_auth` 的扫码登录、`bili_api` 的会员态接口），
而且它是**唯一需要被替换掉的东西** —— 自动测试注入一个假 transport 就能完全离线跑完
投稿、对账与归集的全部分支（见 [02-design.md §8](../ai/feature/bilibili-upload/02-design.md)）。

两个刻意的决定：

1. **不用传递依赖里的 `requests`。** `requests` 在本机是可导入的，但它不在 `pyproject.toml`
   的 `dependencies` 里（是 yt-dlp 带进来的）—— 靠传递依赖写代码，某天上游改依赖就会断。
2. **B站 请求强制直连，不走用户配置的代理。** 本仓库的代理是给 YouTube 用的；拿它去请求 B站
   轻则绕远、重则被拦成 502 且报错与真实病因无关（arXiv 项目踩过这条，代码里直接写死了 `NO_PROXY`）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_TIMEOUT = 20.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
WEB_REFERER = "https://www.bilibili.com/"
MEMBER_REFERER = "https://member.bilibili.com/platform/upload-manager/article"


class HttpError(RuntimeError):
    """网络层失败（连不上、超时、非 2xx）。**业务错误码不走这里** —— 那些是 HTTP 200 + `code != 0`。"""


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    text: str
    # 扫码登录需要它：凭据可能只出现在 Set-Cookie 里，而不在响应正文的回调 URL 上。
    set_cookie: tuple[str, ...] = ()

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as exc:
            raise HttpError(f"接口没有返回 JSON（HTTP {self.status_code}）：{self.text[:200]}") from exc


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResult: ...


class UrllibTransport:
    """标准库实现。**显式清空 ProxyHandler**，见模块文档第 2 条。"""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResult:
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}{'&' if '?' in url else '?'}{query}"
        body: bytes | None = None
        final_headers = {"User-Agent": USER_AGENT, "Referer": WEB_REFERER}
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            final_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            final_headers["Content-Type"] = "application/json"
        final_headers.update(headers or {})

        request = urllib.request.Request(url, data=body, headers=final_headers, method=method.upper())
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResult(
                    status_code=response.status,
                    text=response.read().decode("utf-8", "replace"),
                    set_cookie=tuple(response.headers.get_all("Set-Cookie") or ()),
                )
        except urllib.error.HTTPError as exc:
            # 4xx/5xx 也把正文带回来：B站 有时把业务错误放在非 200 的响应里，丢掉正文就没法排障。
            return HttpResult(
                status_code=exc.code,
                text=exc.read().decode("utf-8", "replace"),
                set_cookie=tuple(exc.headers.get_all("Set-Cookie") or ()) if exc.headers else (),
            )
        except Exception as exc:  # noqa: BLE001 - 统一成一种网络失败
            raise HttpError(f"请求 B站 失败：{exc}") from exc


def default_transport() -> Transport:
    return UrllibTransport()
