"""B站 会员态 HTTP 客户端：稿件对账、封面来源、合集归集、置顶评论。

**为什么是「自研」**：`biliup-rs 0.2.4` 不支持 `season_id`（见
[01-requirements.md F4](../ai/feature/bilibili-upload/01-requirements.md)），而合集归集只需要三个调用，
自研的成本远低于替换整个上传工具链。全部端点与参数名都取自 bilibili-API-collect 的实测记录，
不是猜的（尤其 `episodes` / `sectionId` 是 camelCase —— 文档表格里那对下划线命名是笔误）。

**不实现「上传封面」**：稿件封面由 `biliup --cover` 直接带上；合集封面复用第一条稿件的
平台封面 URL（`/x/web-interface/view` 的 `data.pic`）。于是少写一个 multipart 实现，
也少一个会随平台变更的端点。

所有方法都经注入的 `Transport`，因此可以完全离线测试（见 design §8）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .bili_http import MEMBER_REFERER, WEB_REFERER, HttpError, Transport
from .bili_auth import cookie_header

ACCOUNT_URL = "https://api.bilibili.com/x/web-interface/nav"
VIDEO_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
ARCHIVES_URL = "https://member.bilibili.com/x/web/archives"
SEASON_ADD_URL = "https://member.bilibili.com/x2/creative/web/season/add"
SEASON_EPISODES_ADD_URL = "https://member.bilibili.com/x2/creative/web/season/section/episodes/add"
SEASON_LIST_URL = "https://member.bilibili.com/x2/creative/web/seasons"
REPLY_ADD_URL = "https://api.bilibili.com/x/v2/reply/add"
REPLY_TOP_URL = "https://api.bilibili.com/x/v2/reply/top"
REPLY_MAIN_URL = "https://api.bilibili.com/x/v2/reply/main"

MEMBER_ORIGIN = "https://member.bilibili.com"
# 稿件列表的延迟是平台侧固有的，不是网络问题；退避重试是唯一手段。
DEFAULT_VERIFY_ATTEMPTS = 3
DEFAULT_VERIFY_DELAY_SECONDS = 5.0
# 对账时用标题前 20 字匹配：B站 会对标题做首尾空白处理，也会截断过长标题。
TITLE_MATCH_PREFIX = 20
COMMENT_DUPLICATE_CODE = 12051


class BiliApiError(RuntimeError):
    """业务失败（HTTP 200 但 `code != 0`）。`code` / `message` 原样保留，方便对账与排障。"""

    def __init__(self, code: Any, message: str, *, where: str) -> None:
        super().__init__(f"{where}失败（code={code} message={message}）")
        self.code = code
        self.message = message
        self.where = where


@dataclass(frozen=True)
class CommentResult:
    state: str  # posted / already / failed
    rpid: int | None
    message: str


def _member_headers(jar: dict[str, str], *, json_body: bool = False) -> dict[str, str]:
    headers = {
        "Cookie": cookie_header(jar),
        "Referer": MEMBER_REFERER,
        "Origin": MEMBER_ORIGIN,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _api_headers(jar: dict[str, str]) -> dict[str, str]:
    return {"Cookie": cookie_header(jar), "Referer": WEB_REFERER}


def _unwrap(payload: dict[str, Any], *, where: str) -> Any:
    if payload.get("code") != 0:
        raise BiliApiError(payload.get("code"), str(payload.get("message") or ""), where=where)
    return payload.get("data")


# ---------------------------------------------------------------- 账号与稿件
def fetch_account(transport: Transport, jar: dict[str, str]) -> dict[str, Any] | None:
    """查当前登录账号。**这是「登录的是谁」的唯一来源** —— 我们不从 cookie 里反推。"""
    result = transport.request("GET", ACCOUNT_URL, headers=_api_headers(jar))
    try:
        payload = result.json()
    except HttpError:
        return None
    if payload.get("code") != 0:
        return None
    data = payload.get("data") or {}
    if not data.get("isLogin"):
        return None
    return {"mid": data.get("mid"), "uname": data.get("uname") or ""}


def list_recent_archives(transport: Transport, jar: dict[str, str], *, page_size: int = 20) -> list[dict[str, Any]]:
    """读回近期稿件。`arc_audits[].Archive` 才是稿件本体。"""
    result = transport.request(
        "GET",
        ARCHIVES_URL,
        params={"pn": 1, "ps": page_size},
        headers=_member_headers(jar),
    )
    data = _unwrap(result.json(), where="读取稿件列表")
    rows = (data or {}).get("arc_audits") or []
    return [row.get("Archive") or {} for row in rows if isinstance(row, dict)]


def find_archive(
    transport: Transport,
    jar: dict[str, str],
    title: str,
    *,
    attempts: int = DEFAULT_VERIFY_ATTEMPTS,
    delay: float = DEFAULT_VERIFY_DELAY_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """按标题前缀在近期稿件里找。**找不到就返回 `None`，绝不用「命令没报错」冒充找到。**

    平台侧列表有延迟，所以这里带退避重试；`attempts=1` 时可作一次性查询。

    **两侧都要 `strip()`。** 上面说了 B站 会对标题做首尾空白处理，那就必须真的把空白去掉再比：
    用户在手填表单里多打一个尾随空格（`"演示标题 "`）时，只归一不比较的写法会让对账永远查不到，
    于是一次成功投稿被标成「待核对」。两边都归一之后，平台**保留**空白也能对上。
    """
    prefix = (title or "").strip()[:TITLE_MATCH_PREFIX]
    if not prefix:
        return None
    for attempt in range(max(1, attempts)):
        try:
            for archive in list_recent_archives(transport, jar):
                if str(archive.get("title") or "").strip().startswith(prefix):
                    return archive
        except (BiliApiError, HttpError):
            # 一次查询失败不该让整个对账放弃；下一轮再试。最后一轮仍失败就是「未核实」。
            pass
        if attempt < attempts - 1 and delay > 0:
            sleeper(delay)
    return None


def fetch_video(transport: Transport, jar: dict[str, str], bvid: str) -> dict[str, Any] | None:
    """按 BV 号取稿件详情。一次调用同时给出 `cid`（归集必需）与 `pic`（合集封面）。"""
    if not bvid:
        return None
    try:
        result = transport.request("GET", VIDEO_VIEW_URL, params={"bvid": bvid}, headers=_api_headers(jar))
        data = _unwrap(result.json(), where="读取稿件详情")
    except (BiliApiError, HttpError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 合集
def create_season(transport: Transport, jar: dict[str, str], *, title: str, desc: str, cover_url: str) -> int:
    """创建合集。返回 season_id。

    **B站 对合集有人工审核**：这里返回的是「已提交」，不是「已通过」。
    """
    csrf = jar.get("bili_jct") or ""
    if not csrf:
        raise BiliApiError(None, "登录信息里没有 bili_jct，无法创建合集", where="创建合集")
    result = transport.request(
        "POST",
        SEASON_ADD_URL,
        form={
            "title": title,
            "desc": desc,
            "cover": cover_url,
            "season_price": 0,
            "csrf": csrf,
        },
        headers=_member_headers(jar),
    )
    data = _unwrap(result.json(), where="创建合集")
    season_id = data if isinstance(data, int) else (data or {}).get("id") if isinstance(data, dict) else None
    if not season_id:
        raise BiliApiError(None, f"合集建好了但没有拿到编号（data={data!r}）", where="创建合集")
    return int(season_id)


def add_episodes(
    transport: Transport,
    jar: dict[str, str],
    *,
    season_id: int,
    episodes: list[dict[str, Any]],
) -> None:
    """把稿件加进合集。

    参数名是 **`sectionId` / `episodes`（camelCase）**，并且 csrf 走 **query** 而不是正文 ——
    与「创建合集」的形态相反。这一点由社区 issue #1436 实证（文档表格里的下划线命名会 404）。
    """
    csrf = jar.get("bili_jct") or ""
    if not csrf:
        raise BiliApiError(None, "登录信息里没有 bili_jct，无法归集", where="归集稿件")
    body = {
        "sectionId": int(season_id),
        "episodes": [
            {"aid": int(item["aid"]), "cid": int(item["cid"]), "title": item["title"], "charging_pay": 0}
            for item in episodes
        ],
    }
    result = transport.request(
        "POST",
        SEASON_EPISODES_ADD_URL,
        params={"csrf": csrf},
        json_body=body,
        headers=_member_headers(jar, json_body=True),
    )
    _unwrap(result.json(), where="归集稿件")


def list_seasons(transport: Transport, jar: dict[str, str], *, page_size: int = 30) -> list[dict[str, Any]]:
    """列出已有合集，用于把 season_id 反查成人可读的标题。"""
    try:
        result = transport.request(
            "GET",
            SEASON_LIST_URL,
            params={"pn": 1, "ps": page_size},
            headers=_member_headers(jar),
        )
        data = _unwrap(result.json(), where="读取合集列表")
    except (BiliApiError, HttpError):
        return []
    seasons = (data or {}).get("seasons") or []
    out: list[dict[str, Any]] = []
    for row in seasons:
        if isinstance(row, dict) and isinstance(row.get("season"), dict):
            out.append(row["season"])
    return out


# ---------------------------------------------------------------- 置顶评论
def find_own_comment(transport: Transport, jar: dict[str, str], *, oid: int, marker: str, mode: int) -> int | None:
    mid = jar.get("DedeUserID") or ""
    if not mid.isdigit():
        return None
    try:
        result = transport.request(
            "GET",
            REPLY_MAIN_URL,
            params={"oid": oid, "type": 1, "mode": mode},
            headers=_api_headers(jar),
        )
        data = _unwrap(result.json(), where="读取评论列表")
    except (BiliApiError, HttpError):
        return None
    for comment in (data or {}).get("replies") or []:
        if not isinstance(comment, dict):
            continue
        if str(comment.get("mid")) != mid:
            continue
        message = str(((comment.get("content") or {}).get("message")) or "")
        if marker and marker in message:
            return comment.get("rpid")
    return None


def send_and_top_comment(
    transport: Transport,
    jar: dict[str, str],
    *,
    oid: int,
    message: str,
    marker: str,
) -> CommentResult:
    """发一条评论并置顶，**幂等**：先查自己的评论里有没有同一条，有就直接补置顶。

    `/x/v2/reply/send` 已下线（恒 404），这里用 `/x/v2/reply/add`（2026-10-06 实测）。
    评论失败**不影响稿件与合集** —— 调用方只把它当成一条附加结果。
    """
    csrf = jar.get("bili_jct") or ""
    if not csrf:
        return CommentResult(state="failed", rpid=None, message="登录信息里没有 bili_jct，无法发评论")
    rpid = find_own_comment(transport, jar, oid=oid, marker=marker, mode=3)
    if rpid is None:
        try:
            result = transport.request(
                "POST",
                REPLY_ADD_URL,
                form={"oid": oid, "type": 1, "message": message, "plat": 1, "csrf": csrf, "csrf_token": csrf},
                headers=_api_headers(jar),
            )
            payload = result.json()
        except (BiliApiError, HttpError) as exc:
            return CommentResult(state="failed", rpid=None, message=f"评论没有发出去：{exc}")
        if payload.get("code") == COMMENT_DUPLICATE_CODE:
            # 平台侧的判重先于本地判重（热门列表里可能还没有这条），按时间序再找一次。
            rpid = find_own_comment(transport, jar, oid=oid, marker=marker, mode=2)
            if rpid is None:
                return CommentResult(state="already", rpid=None, message="评论已存在，如需置顶请到评论区手动操作")
        elif payload.get("code") != 0:
            return CommentResult(
                state="failed",
                rpid=None,
                message=f"评论没有发出去（code={payload.get('code')} {payload.get('message')}）",
            )
        else:
            rpid = (payload.get("data") or {}).get("rpid")
    try:
        result = transport.request(
            "POST",
            REPLY_TOP_URL,
            form={"oid": oid, "rpid": rpid, "type": 1, "action": 1, "csrf": csrf, "csrf_token": csrf},
            headers=_api_headers(jar),
        )
        payload = result.json()
    except (BiliApiError, HttpError) as exc:
        return CommentResult(state="failed", rpid=rpid, message=f"评论发出去了，但置顶失败：{exc}")
    if payload.get("code") != 0:
        return CommentResult(
            state="failed",
            rpid=rpid,
            message=f"评论发出去了，但置顶失败（code={payload.get('code')} {payload.get('message')}）",
        )
    return CommentResult(state="posted", rpid=rpid, message="评论已发送并置顶")
