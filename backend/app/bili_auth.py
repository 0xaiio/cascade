"""扫码登录与登录态文件。

**为什么不用 `biliup login`**：它只在进程正常退出时落盘，且必须有交互终端；沙箱与无人值守场景直接拒绝。
这里直连 `passport.bilibili.com` 的二维码生成/轮询接口，用 HTTP 把二维码交给前端去显示 ——
从一个需要 TTY 的流程，变成一次普通的请求/响应。

凭据处理的硬约束（requirements FR-B12）：**本模块对外只暴露「有没有、缺哪项、多长」，绝不暴露值**。
连掩码前缀都不给 —— 掩码前缀仍然是凭据的一部分，截图或日志一贴就漏。
要显示「登录的是哪个账号」，走 `bili_api.fetch_account()` 从平台侧查 `uname`。
"""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bili_http import DEFAULT_TIMEOUT, HttpError, Transport

QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
PASSPORT_REFERER = "https://passport.bilibili.com/"

# 投稿与对账至少要这两项；DedeUserID 用于把评论/合集操作归属到自己，缺了会静默变成「你没权限」。
REQUIRED_COOKIES = ("SESSDATA", "bili_jct")
RECOMMENDED_COOKIES = ("DedeUserID",)

# 轮询状态码 → 我们自己的状态名（前缀匹配即可支持 string 形式的 code）。
POLL_STATE_BY_CODE: dict[int, str] = {
    0: "confirmed",
    86101: "waiting",
    86090: "scanned",
    86038: "expired",
}
POLL_MESSAGE: dict[str, str] = {
    "waiting": "请用 B站 App 扫描二维码",
    "scanned": "已扫描，请在手机上点「确认登录」",
    "confirmed": "登录成功",
    "expired": "二维码已过期，请重新生成",
    "unknown": "登录状态未知，请重新生成二维码再试",
}


@dataclass(frozen=True)
class QrcodeChallenge:
    qrcode_key: str
    url: str
    svg: str | None
    note: str | None = None


@dataclass(frozen=True)
class LoginPollResult:
    state: str
    message: str
    jar: dict[str, str] | None = None


def render_qr_svg(url: str, *, box_size: int = 8, border: int = 2) -> str | None:
    """把登录 URL 画成 SVG。`qrcode` 装不上时返回 `None`，由调用方降级到「导入 cookie 文件」。"""
    try:
        import qrcode
        import qrcode.image.svg
    except Exception:  # noqa: BLE001 - 缺依赖不该让设置面板整体不可用
        return None
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=box_size, border=border)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def _flatten_cookies(payload: Any) -> dict[str, str]:
    """四种登录态文件形态统一成 `name -> value`：

    1. `biliup` 的 `LoginInfo`：`{"cookie_info": {"cookies": [{"name","value"}, …]}}`
    2. 扁平的 `{"name": "value", …}`
    3. `[{"name","value"}, …]`
    4. Netscape `cookies.txt` 文本（由 `load_cookie_jar` 在 JSON 解析失败时处理）
    """
    if isinstance(payload, dict) and isinstance(payload.get("cookie_info"), dict):
        jar: dict[str, str] = {}
        for item in payload["cookie_info"].get("cookies", []) or []:
            if isinstance(item, dict) and "name" in item and "value" in item:
                jar[str(item["name"])] = str(item["value"])
        if jar:
            return jar
    if isinstance(payload, dict):
        return {str(k): str(v) for k, v in payload.items() if isinstance(v, (str, int, float))}
    if isinstance(payload, list):
        jar = {}
        for item in payload:
            if isinstance(item, dict) and "name" in item and "value" in item:
                jar[str(item["name"])] = str(item["value"])
        return jar
    return {}


def _parse_netscape(text: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            jar[parts[5]] = parts[6]
    return jar


def load_cookie_jar(path: str | Path) -> dict[str, str]:
    """读登录态文件。文件不存在返回空字典（**不抛异常**：体检需要一个「没有」的正常结果）。"""
    file = Path(path)
    if not file.is_file():
        return {}
    raw = file.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return {}
    try:
        return _flatten_cookies(json.loads(raw))
    except ValueError:
        return _parse_netscape(raw)


def build_login_info(jar: dict[str, str], mid: int | None, refresh_token: str = "") -> dict[str, Any]:
    """写出 `biliup` 认识的结构。这个 schema 不是猜的 —— 由 `biliup renew` 的实际产物反推。"""
    return {
        "cookie_info": {
            "cookies": [{"name": name, "value": value} for name, value in jar.items() if value],
            "domains": [".bilibili.com", "bilibili.com", "passport.bilibili.com"],
        },
        "sso": [],
        "token_info": {
            "access_token": "",
            "expires_in": 0,
            "mid": mid or 0,
            "refresh_token": refresh_token or "",
        },
    }


def write_cookie_file(path: str | Path, jar: dict[str, str], mid: int | None = None, refresh_token: str = "") -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(build_login_info(jar, mid, refresh_token), ensure_ascii=False, indent=1), encoding="utf-8")


def delete_cookie_file(path: str | Path) -> bool:
    file = Path(path)
    if not file.is_file():
        return False
    file.unlink()
    return True


def cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items() if value)


def missing_required_cookies(jar: dict[str, str]) -> list[str]:
    """哪些必需项不在。返回的是**字段名**，不含值。"""
    return [name for name in REQUIRED_COOKIES if not jar.get(name)]


def cookie_summary(jar: dict[str, str]) -> dict[str, Any]:
    """给界面看的凭据摘要。**只有名字、计数、长度，没有值、也没有值的前缀。**"""
    return {
        "present": bool(jar),
        "count": len(jar),
        "names": sorted(jar),
        "missing_required": missing_required_cookies(jar),
        "missing_recommended": [name for name in RECOMMENDED_COOKIES if not jar.get(name)],
    }


def is_cookie_tracked_by_git(path: str | Path, repo_root: str | Path) -> bool:
    """登录文件是否已被 git 跟踪。跟踪了就等于把账号密码提交进了仓库。

    判定失败（没有 git、路径不在仓库里）一律返回 `False` —— 体检不该因为环境缺工具而报假警。
    """
    file = Path(path)
    if not file.is_file():
        return False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(file)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        return False
    return result.returncode == 0


class BiliLoginSession:
    """一次扫码登录的会话。

    无状态服务端本来不需要「会话」，但 passport 的轮询要把第一阶段拿到的 cookie 带回去；
    同时前端是「生成一次、轮询多次」，所以这份状态必须活在请求之外 —— 由调用方（`BiliManager`）持有。
    """

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.jar: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        headers = {"Referer": PASSPORT_REFERER}
        if self.jar:
            headers["Cookie"] = cookie_header(self.jar)
        return headers

    def _absorb(self, result: Any) -> None:
        for raw in getattr(result, "set_cookie", ()) or ():
            head = raw.split(";", 1)[0]
            if "=" in head:
                name, value = head.split("=", 1)
                if value and value != "deleted":
                    self.jar[name.strip()] = value.strip()

    def generate(self) -> QrcodeChallenge:
        result = self.transport.request("GET", QR_GENERATE_URL, headers=self._headers(), timeout=DEFAULT_TIMEOUT)
        self._absorb(result)
        payload = result.json()
        if payload.get("code") != 0:
            raise HttpError(f"B站 没能生成登录二维码（code={payload.get('code')} message={payload.get('message')}）")
        data = payload.get("data") or {}
        url = data.get("url") or ""
        svg = render_qr_svg(url)
        note = None if svg else "缺少二维码组件，无法显示二维码；请改用「导入 cookies.json」"
        return QrcodeChallenge(qrcode_key=str(data.get("qrcode_key") or ""), url=url, svg=svg, note=note)

    def poll(self, qrcode_key: str) -> LoginPollResult:
        result = self.transport.request(
            "GET", QR_POLL_URL, params={"qrcode_key": qrcode_key}, headers=self._headers(), timeout=DEFAULT_TIMEOUT
        )
        self._absorb(result)
        payload = result.json()
        data = payload.get("data") or {}
        # 轮询接口成功时 code 在 data 里；外层 code 只表示请求本身成没成。
        code = data.get("code")
        state = POLL_STATE_BY_CODE.get(int(code) if isinstance(code, int) else -1, "unknown")
        if state != "confirmed":
            return LoginPollResult(state=state, message=POLL_MESSAGE.get(state, POLL_MESSAGE["unknown"]))
        jar = self._harvest(data)
        missing = missing_required_cookies(jar)
        if missing:
            return LoginPollResult(
                state="failed",
                message=f"登录没有拿到完整凭据（缺 {'、'.join(missing)}），请重新扫码",
            )
        self.jar = {**self.jar, **jar}
        return LoginPollResult(state="confirmed", message=POLL_MESSAGE["confirmed"], jar=dict(self.jar))

    def _harvest(self, data: dict[str, Any]) -> dict[str, str]:
        """凭据来源：轮询拿到的 cookie > 回调 URL 的 query（要解码）。两处都要试，
        因为不同账号/客户端命中哪一处并不一致。"""
        jar: dict[str, str] = {}
        callback = str(data.get("url") or "")
        if "?" in callback:
            from urllib.parse import parse_qs, unquote

            for name, values in parse_qs(callback.split("?", 1)[1], keep_blank_values=True).items():
                if values:
                    jar[name] = unquote(values[0])
        for name in (*REQUIRED_COOKIES, *RECOMMENDED_COOKIES):
            if not jar.get(name) and self.jar.get(name):
                jar[name] = self.jar[name]
        return jar
