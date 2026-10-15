"""扫码登录、投稿令牌交换与登录态文件。

**为什么不用 `biliup login`**：它只在进程正常退出时落盘，且必须有交互终端；沙箱与无人值守场景直接拒绝。
这里直连 `passport.bilibili.com` 的二维码生成/轮询接口，用 HTTP 把二维码交给前端去显示 ——
从一个需要 TTY 的流程，变成一次普通的请求/响应。

**登录必须一路做到拿到投稿令牌为止**（`exchange_tv_token`）。只拿 web cookies 是不够的：
`biliup` 每次启动都会先拿 `token_info.access_token` 去 `oauth2/info` 校验一次，空串会被 B站 判成
`-400 请求错误`，于是命令在发出**任何一个投稿请求之前**就退出 —— 界面上表现为「投稿失败，
而且没能识别出具体原因」，而真实原因在本地日志里也找不到（那次连 biliup 的输出都没落盘）。
这条路不是猜的：与 `biliup` 内建的「网页Cookie登录」逐字一致（`credential.rs::login_by_web_cookies`）。

凭据处理的硬约束（requirements FR-B12）：**本模块对外只暴露「有没有、缺哪项、多长」，绝不暴露值**。
连掩码前缀都不给 —— 掩码前缀仍然是凭据的一部分，截图或日志一贴就漏。
要显示「登录的是哪个账号」，走 `bili_api.fetch_account()` 从平台侧查 `uname`。
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bili_http import DEFAULT_TIMEOUT, HttpError, Transport

QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
PASSPORT_REFERER = "https://passport.bilibili.com/"

# ---------------------------------------------------------------- 投稿令牌（TV 端 OAuth）
# appkey/appsec 是 B站 客户端的公开常量（`biliup-rs` 源码里硬编码的那两个）。放在这里是因为
# 签名必须与 biliup 逐字节一致 —— 签名不合法与凭据不合法在响应里长得一样，都是 `-400 请求错误`。
TV_APPKEY = "4409e2ce8ffd12b8"
TV_APPSEC = "59b43e04ad6965f34319062b478f83dd"
TV_PLATFORM = "BiliTV"
TV_LOCAL_ID = "0"
# h5 自动确认用的类型，取自 biliup 的 web_confirm_qrcode。
TV_SCANNING_TYPE = 3
TV_AUTH_CODE_URL = "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code"
TV_CONFIRM_URL = "https://passport.bilibili.com/x/passport-tv-login/h5/qrcode/confirm"
TV_POLL_URL = "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll"
# 自动确认刚发出去时可能赶上「码还没被确认」这一拍，要重试几次而不是直接判失败。
TV_POLL_NOT_CONFIRMED = 86039
TV_POLL_ATTEMPTS = 5
TV_POLL_DELAY = 1.0

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


@dataclass(frozen=True)
class TvToken:
    """投稿要用的 OAuth 令牌。**只在内存与登录文件之间流动，不进日志、不进 API 响应。**"""

    access_token: str
    refresh_token: str
    expires_in: int
    mid: int | None = None


@dataclass(frozen=True)
class LoginTokenState:
    """登录文件里 token 结构的**摘要**。与 `cookie_summary` 同一条口径：只有有没有、有多大。"""

    has_access_token: bool = False
    has_refresh_token: bool = False
    expires_in: int = 0
    mid: int | None = None
    platform: str | None = None


def app_sign(fields: Mapping[str, Any]) -> str:
    """`md5(urlencoded + appsec)`，**键按字典序**。

    字典序不是随便挑的：biliup 用 `serde_json` 的默认 Map（BTreeMap）序列化参数，所以它算出来的
    串就是排序后的。签名与它不一致时 B站 回的是同一个 `-400 请求错误` —— 会把「我算错了签名」
    伪装成「你的凭据坏了」，两者用同样的文案误导人。
    """
    payload = urllib.parse.urlencode(sorted(fields.items()))
    return hashlib.md5(f"{payload}{TV_APPSEC}".encode("utf-8")).hexdigest()


def _tv_json(result: Any) -> dict[str, Any]:
    payload = result.json()
    if not isinstance(payload, dict):
        raise HttpError(f"B站 的登录接口没有返回预期结构（HTTP {result.status_code}）")
    return payload


def _tv_token(data: Mapping[str, Any]) -> TvToken:
    info = data.get("token_info") or {}
    access = str(info.get("access_token") or "") if isinstance(info, Mapping) else ""
    if not access:
        raise HttpError("B站 返回的登录信息里没有 access_token")
    mid = info.get("mid") if isinstance(info, Mapping) else None
    return TvToken(
        access_token=access,
        refresh_token=str(info.get("refresh_token") or ""),
        expires_in=int(info.get("expires_in") or 0),
        mid=int(mid) if isinstance(mid, int) else None,
    )


def exchange_tv_token(
    transport: Transport,
    jar: dict[str, str],
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> TvToken:
    """用已有的 web cookies 去换一份 TV 端 OAuth 令牌。

    三步与 biliup 内建的「网页Cookie登录」一致：申请一张 TV 二维码 → 用 cookies 替用户点「确认」
    → 轮询换回 `token_info`。**这是唯一能拿到 `access_token` 的自动化路径**（账号密码/短信都要求
    交互终端），而 biliup 没有它就不肯开工。

    Cookie 头**只带 SESSDATA 与 bili_jct**：扫码过程中还从回调 URL 上捡了 `gourl`、`sid` 这类
    并不是 cookie 的查询参数，一起发过去等于给对方一个「这个客户端行为很奇怪」的信号。
    """
    sess_data = str(jar.get("SESSDATA") or "")
    bili_jct = str(jar.get("bili_jct") or "")
    if not sess_data or not bili_jct:
        raise HttpError("登录信息里缺 SESSDATA 或 bili_jct，没法换取投稿令牌")
    cookie = f"SESSDATA={sess_data}; bili_jct={bili_jct}"

    auth_fields: dict[str, Any] = {"appkey": TV_APPKEY, "local_id": TV_LOCAL_ID, "ts": int(time.time())}
    auth_fields["sign"] = app_sign(auth_fields)
    issued = _tv_json(
        transport.request(
            "POST", TV_AUTH_CODE_URL, form=auth_fields, headers={"Referer": PASSPORT_REFERER}, timeout=DEFAULT_TIMEOUT
        )
    )
    auth_code = str((issued.get("data") or {}).get("auth_code") or "")
    if not auth_code:
        raise HttpError(
            f"B站 没能签发投稿用的二维码（code={issued.get('code')} message={issued.get('message')}）"
        )

    confirmed = _tv_json(
        transport.request(
            "POST",
            TV_CONFIRM_URL,
            form={"auth_code": auth_code, "csrf": bili_jct, "scanning_type": TV_SCANNING_TYPE},
            headers={"Referer": PASSPORT_REFERER, "Cookie": cookie},
            timeout=DEFAULT_TIMEOUT,
        )
    )
    if confirmed.get("code") != 0:
        raise HttpError(
            f"B站 拒绝了用当前登录换投稿令牌（code={confirmed.get('code')} message={confirmed.get('message')}）"
        )

    for attempt in range(TV_POLL_ATTEMPTS):
        poll_fields: dict[str, Any] = {
            "appkey": TV_APPKEY,
            "auth_code": auth_code,
            "local_id": TV_LOCAL_ID,
            "ts": int(time.time()),
        }
        poll_fields["sign"] = app_sign(poll_fields)
        polled = _tv_json(
            transport.request(
                "POST",
                TV_POLL_URL,
                form=poll_fields,
                headers={"Referer": PASSPORT_REFERER, "Cookie": cookie},
                timeout=DEFAULT_TIMEOUT,
            )
        )
        code = polled.get("code")
        if code == 0:
            return _tv_token(polled.get("data") or {})
        if code != TV_POLL_NOT_CONFIRMED:
            raise HttpError(f"B站 取投稿令牌时返回了 code={code} message={polled.get('message')}")
        if attempt < TV_POLL_ATTEMPTS - 1:
            sleeper(TV_POLL_DELAY)
    raise HttpError("B站 一直说这张二维码还没被确认，没能取到投稿令牌")


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


def _jar_from_pairs(items: Any) -> dict[str, str]:
    """`[{"name", "value"}, …]` → `{name: value}`。认不出的条目静默跳过。

    「条目列表」这一形状在四种登录态文件里出现了两次（`LoginInfo` 内层与顶层数组），
    抽出来是因为两处的判定条件必须**逐字一致** —— 有一处放宽，就会出现
    「同一个文件按扩展名走这条路读不出来、走那条路读得出来」。
    """
    jar: dict[str, str] = {}
    for item in items or []:
        if isinstance(item, dict) and "name" in item and "value" in item:
            jar[str(item["name"])] = str(item["value"])
    return jar


def _flatten_cookies(payload: Any) -> dict[str, str]:
    """四种登录态文件形态统一成 `name -> value`：

    1. `biliup` 的 `LoginInfo`：`{"cookie_info": {"cookies": [{"name","value"}, …]}}`
    2. 扁平的 `{"name": "value", …}`
    3. `[{"name","value"}, …]`
    4. Netscape `cookies.txt` 文本（由 `load_cookie_jar` 在 JSON 解析失败时处理）
    """
    if isinstance(payload, dict):
        cookie_info = payload.get("cookie_info")
        if isinstance(cookie_info, dict):
            jar = _jar_from_pairs(cookie_info.get("cookies"))
            # 内层读不出东西时**继续往下走扁平那条**：一个带空 `cookie_info` 的文件
            # 仍然可能是一份扁平的 cookie 字典。
            if jar:
                return jar
        return {str(k): str(v) for k, v in payload.items() if isinstance(v, (str, int, float))}
    if isinstance(payload, list):
        return _jar_from_pairs(payload)
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


def build_login_info(
    jar: dict[str, str],
    mid: int | None = None,
    token: TvToken | None = None,
) -> dict[str, Any]:
    """写出 `biliup` 认识的结构。

    `token_info.access_token` **不是可选项**：biliup 每次启动都先拿它去 `oauth2/info` 校验一次，
    空串会让整个命令在发出任何请求之前退出。所以调用方必须把 `exchange_tv_token` 的结果交进来 ——
    默认值「没有令牌」只用于展示与测试，它写出来的文件是投稿不通的。
    """
    info: dict[str, Any] = {
        "cookie_info": {
            "cookies": [{"name": name, "value": value} for name, value in jar.items() if value],
            "domains": [".bilibili.com", "bilibili.com", "passport.bilibili.com"],
        },
        "sso": [],
        "token_info": {
            "access_token": token.access_token if token else "",
            "expires_in": token.expires_in if token else 0,
            "mid": (token.mid or mid or 0) if token else (mid or 0),
            "refresh_token": token.refresh_token if token else "",
        },
    }
    if token:
        # biliup 靠 `platform` 挑续期用的 appkey；不写它，令牌到期后 `renew` 会原样返回、静默不续。
        info["platform"] = TV_PLATFORM
    return info


def write_cookie_file(
    path: str | Path,
    jar: dict[str, str],
    mid: int | None = None,
    token: TvToken | None = None,
) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(build_login_info(jar, mid, token), ensure_ascii=False, indent=1), encoding="utf-8"
    )


def read_login_token(path: str | Path) -> LoginTokenState:
    """读登录文件里的 token 概况。**只回答有没有与多大，不回值**（FR-B12）。

    读不出来（文件不存在、不是 JSON、结构不对）一律当「没有」：体检需要一个正常结果而不是异常，
    而「拿不到令牌」在投稿里的后果是硬失败，不该被静默放过。
    """
    file = Path(path)
    if not file.is_file():
        return LoginTokenState()
    try:
        payload = json.loads(file.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return LoginTokenState()
    if not isinstance(payload, dict) or not isinstance(payload.get("token_info"), dict):
        return LoginTokenState()
    info = payload["token_info"]
    mid = info.get("mid")
    platform = payload.get("platform")
    return LoginTokenState(
        has_access_token=bool(str(info.get("access_token") or "")),
        has_refresh_token=bool(str(info.get("refresh_token") or "")),
        expires_in=int(info.get("expires_in") or 0),
        mid=int(mid) if isinstance(mid, int) else None,
        platform=str(platform) if platform else None,
    )


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
            # `urllib.parse` 在模块顶部已经导入，这里不再就地 import 一次 —— 两种写法并存时，
            # 读的人会以为就地导入有什么不得不这么做的理由。
            query = urllib.parse.parse_qs(callback.split("?", 1)[1], keep_blank_values=True)
            for name, values in query.items():
                if values:
                    jar[name] = urllib.parse.unquote(values[0])
        for name in (*REQUIRED_COOKIES, *RECOMMENDED_COOKIES):
            if not jar.get(name) and self.jar.get(name):
                jar[name] = self.jar[name]
        return jar
