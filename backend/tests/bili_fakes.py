"""B站 上传测试用的共享替身。

`bili_upload` 的整条链路（投稿 → 对账 → 归集 → 评论）都被设计成可替换的：
`runner` 跑子进程、`transport` 发 HTTP、`sleeper` 等待。把这三个都换掉之后，
最贵也最难在真实账号上复现的分支（「命令成功但对账查不到」「601 频控」
「合集建好但归集失败」）全部可以在毫秒级跑出来。

**替身遇到不认识的 URL 一律 `AssertionError`**，不返回空响应：那样「某处多打了一个
接口」会立刻暴露，而不是被当成「这个接口返回空」静静吞掉。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.bili_api import DEFAULT_VERIFY_ATTEMPTS
from app.bili_bin import CommandResult, LineSink, Runner
from app.bili_http import HttpError, HttpResult

DEFAULT_ARCHIVE = {
    "aid": 111,
    "bvid": "BV1xx411c7mD",
    "title": "演示标题",
    "state": 0,
    "cover": "https://i0.hdslb.com/bfs/archive/from-platform.jpg",
}

# 一份「该有的都有」的登录态。`DedeUserID` 是 `find_own_comment` 判断「这条评论是不是我发的」
# 的依据；少了它，幂等检查会**静默地永远返回 None**（看起来像「平台上还没有这条评论」）。
JAR = {"SESSDATA": "sess-value", "bili_jct": "jct-value", "DedeUserID": "42"}


def own_reply(rpid: int, message: str, *, mid: int = 42) -> dict[str, Any]:
    """构造一条「我自己发的」评论，形状对齐 `/x/v2/reply/main` 的返回。"""
    return {"rpid": rpid, "mid": mid, "content": {"message": message}}


def section_id_for(season_id: int) -> int:
    """合集 → 它的默认分区 id 的**约定**。

    平台上的分区 id 与合集 id 是两个独立编号（实测 `9343273` → `10455347`），
    任何「看起来差不多」的巧合都不该被拿来当捷径。替身用 `season_id + 1000` 生成一个
    **一定不等于**合集 id 的值，于是「把合集 id 当分区 id 用」在断言里必然露馅。
    """
    return int(season_id) + 1000


def default_section(season_id: int) -> dict[str, Any]:
    """平台给新建合集自动带的那个默认分区（实测 `type=1`、叫「正片」）。"""
    return {"id": section_id_for(season_id), "title": "正片", "type": 1, "order": 1}


class StubTransport:
    """只回同一个固定响应的替身。

    比在 `FakeBiliTransport` 上继续加开关更清楚：这些用例的重点是**响应本身**
    （`code != 0`、不是 JSON、连接失败），而不是「哪个端点被调用了」。
    """

    def __init__(
        self,
        payload: Any = None,
        *,
        text: str | None = None,
        status_code: int = 200,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.text = text
        self.status_code = status_code
        self.error = error
        self.calls: list[dict[str, Any]] = []

    @property
    def urls(self) -> list[str]:
        return [call["url"] for call in self.calls]

    def called(self, suffix: str) -> int:
        return sum(1 for url in self.urls if url.endswith(suffix))

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = 20.0,
    ) -> HttpResult:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "form": form or {},
                "json_body": json_body,
            }
        )
        if self.error is not None:
            raise self.error
        text = self.text if self.text is not None else json.dumps(self.payload, ensure_ascii=False)
        return HttpResult(self.status_code, text)


class _Unset:
    """「没传这个参数」与「显式传 None / 空」必须能区分开。

    早先用 `None` 兼作哨兵，于是 `account=None`（要表达「未登录」）和 `video=None`
    （要表达「查不到这条稿件」）都会被当成「用默认值」—— 那两条用例**根本测不到**
    目标分支，只会绿着过去。
    """

    def __repr__(self) -> str:  # pragma: no cover - 只为排障可读
        return "UNSET"


UNSET = _Unset()


class FakeBiliTransport:
    """认识本功能用到的全部端点。每个端点的行为都能从构造函数里改。"""

    def __init__(
        self,
        *,
        archives: list[dict[str, Any]] | None = UNSET,
        archives_ok_after: int | None = None,
        video: dict[str, Any] | None = UNSET,
        account: dict[str, Any] | None = UNSET,
        season_id: int = 990011,
        season_add_error: Exception | None = None,
        episodes_error: Exception | None = None,
        comment_code: int = 0,
        comment_message: str = "重复评论",
        comment_rpid: int = 123456,
        top_code: int = 0,
        top_message: str = "置顶失败",
        top_error: Exception | None = None,
        replies: list[dict[str, Any]] | None = UNSET,
        reply_pages: dict[int, list[dict[str, Any]]] | None = None,
        archives_error: Exception | None = None,
        qrcode_url: str = "https://www.bilibili.com/h5/login?qrcode=abc",
        qrcode_key: str = "qr-key-1",
        qr_generate_error: Exception | None = None,
        poll_code: int | str = 86101,
        poll_url: str = "",
        poll_error: Exception | None = None,
        tv_access_token: str = "tv-access-token",
        tv_refresh_token: str = "tv-refresh-token",
        tv_poll_codes: list[int] | None = None,
        tv_auth_code_error: Exception | None = None,
        tv_confirm_error: Exception | None = None,
        tv_confirm_code: int = 0,
        tv_confirm_message: str = "ok",
        seasons: list[dict[str, Any]] | None = None,
        season_sections: dict[int, list[dict[str, Any]]] | None = None,
        season_visible_after: int | None = None,
    ) -> None:
        self.archives = [dict(DEFAULT_ARCHIVE)] if archives is UNSET else list(archives or [])
        # 稿件列表有平台侧延迟。`archives_ok_after=N` 让**前 N 次**读取失败、之后成功，
        # 用来验证 `find_archive` 的退避重试真的会再试（而不是「失败一次就放弃」）。
        self.archives_ok_after = archives_ok_after
        self.archive_reads = 0
        self.video = (
            {"cid": 7001, "pic": "https://i0.hdslb.com/bfs/archive/pic.jpg"} if video is UNSET else video
        )
        self.account = {"mid": 42, "uname": "测试账号"} if account is UNSET else account
        self.season_id = season_id
        # 合集列表的**内层**对象（`{"season": {...}}` 的那个 `...`）。默认给一个合集，
        # 测「账号没有合集」时传 `[]`。归一层对缺字段的容忍（见 `normalize_season_option`）
        # 正是要用这些不完整对象来压。
        self.seasons = (
            [{"id": self.season_id, "title": "已有合集"}] if seasons is None else list(seasons)
        )
        # 每个合集下的分区。没显式给就按 `default_section` 造一个 —— 平台新建合集就长这样。
        # 传 `{season_id: []}` 可以构造「合集在、但读不出分区」那种形状。
        self.season_sections = dict(season_sections or {})
        # 新建合集在平台列表里**有延迟**。`season_visible_after=N` 让合集在**前 N 次**读取里
        # 消失，用来验证 `find_season_section` 真的会退避重试、而不是读一次就判死。
        self.season_visible_after = season_visible_after
        self.season_reads = 0
        self.season_add_error = season_add_error
        self.episodes_error = episodes_error
        self.comment_code = comment_code
        self.comment_message = comment_message
        self.comment_rpid = comment_rpid
        self.top_code = top_code
        self.top_message = top_message
        self.top_error = top_error
        self.replies = [] if replies is UNSET else list(replies or [])
        # `/x/v2/reply/main` 的 `mode` 决定读的是「热门」还是「时间序」。两种结果不同时
        # 才能测出「平台判重之后按时间序重找」这一步真的走了另一条路。
        self.reply_pages = reply_pages or {}
        self.archives_error = archives_error
        # 扫码登录。`poll_code` 是 data 里的那个 code（0=已确认 / 86101=等待 / …）。
        self.qrcode_url = qrcode_url
        self.qrcode_key = qrcode_key
        self.qr_generate_error = qr_generate_error
        self.poll_code = poll_code
        self.poll_url = poll_url
        self.poll_error = poll_error
        # 投稿令牌交换（`bili_auth.exchange_tv_token`）。默认一次到位；
        # `tv_poll_codes` 用来构造「前几拍还没确认」的那条分支。
        self.tv_access_token = tv_access_token
        self.tv_refresh_token = tv_refresh_token
        self.tv_poll_codes = list(tv_poll_codes) if tv_poll_codes is not None else [0]
        self.tv_polls = 0
        self.tv_auth_code_error = tv_auth_code_error
        self.tv_confirm_error = tv_confirm_error
        self.tv_confirm_code = tv_confirm_code
        self.tv_confirm_message = tv_confirm_message
        # 每一次调用都记下来：断言「该调的调了、不该调的没调」全靠它。
        self.calls: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.topped: list[dict[str, Any]] = []

    # -- 查询辅助 ----------------------------------------------------------
    @property
    def urls(self) -> list[str]:
        return [call["url"] for call in self.calls]

    def called(self, suffix: str) -> int:
        return sum(1 for url in self.urls if url.endswith(suffix))

    # -- 替身本体 ----------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = 20.0,
    ) -> HttpResult:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "form": form or {},
                "json_body": json_body,
            }
        )
        if url.endswith("/x/passport-login/web/qrcode/generate"):
            if self.qr_generate_error is not None:
                raise self.qr_generate_error
            return self._json({"code": 0, "data": {"url": self.qrcode_url, "qrcode_key": self.qrcode_key}})
        if url.endswith("/x/passport-login/web/qrcode/poll"):
            if self.poll_error is not None:
                raise self.poll_error
            return self._json({"code": 0, "data": {"code": self.poll_code, "url": self.poll_url}})
        if url.endswith("/x/passport-tv-login/qrcode/auth_code"):
            if self.tv_auth_code_error is not None:
                raise self.tv_auth_code_error
            return self._json(
                {
                    "code": 0,
                    "data": {"auth_code": "tv-auth-code", "url": "https://passport.bilibili.com/confirm?x=1"},
                }
            )
        if url.endswith("/x/passport-tv-login/h5/qrcode/confirm"):
            if self.tv_confirm_error is not None:
                raise self.tv_confirm_error
            return self._json(
                {"code": self.tv_confirm_code, "message": self.tv_confirm_message, "data": {"gourl": ""}}
            )
        if url.endswith("/x/passport-tv-login/qrcode/poll"):
            index = min(self.tv_polls, len(self.tv_poll_codes) - 1)
            code = self.tv_poll_codes[index]
            self.tv_polls += 1
            if code != 0:
                return self._json({"code": code, "message": "二维码尚未确认", "data": None})
            return self._json(
                {
                    "code": 0,
                    "data": {
                        "cookie_info": {
                            "cookies": [{"name": "SESSDATA", "value": "sess-value"}],
                            "domains": [".bilibili.com"],
                        },
                        "sso": [],
                        "token_info": {
                            "access_token": self.tv_access_token,
                            "refresh_token": self.tv_refresh_token,
                            "expires_in": 15552000,
                            "mid": 42,
                        },
                    },
                }
            )
        if url.endswith("/x/web-interface/nav"):
            if self.account is None:
                return self._json({"code": 0, "data": {"isLogin": False}})
            return self._json({"code": 0, "data": {"isLogin": True, **self.account}})
        if url.endswith("/x/web/archives"):
            self.archive_reads += 1
            if self.archives_error is not None and (
                self.archives_ok_after is None or self.archive_reads <= self.archives_ok_after
            ):
                raise self.archives_error
            return self._json({"code": 0, "data": {"arc_audits": [{"Archive": item} for item in self.archives]}})
        if url.endswith("/x/web-interface/view"):
            if self.video is None:
                return self._json({"code": -404, "message": "啥都木有"})
            return self._json({"code": 0, "data": self.video})
        if url.endswith("/season/add"):
            if self.season_add_error is not None:
                raise self.season_add_error
            return self._json({"code": 0, "data": self.season_id})
        if url.endswith("/season/section/episodes/add"):
            if self.episodes_error is not None:
                raise self.episodes_error
            return self._json({"code": 0, "data": {}})
        if url.endswith("/x2/creative/web/seasons"):
            self.season_reads += 1
            page = int((params or {}).get("pn") or 1)
            size = int((params or {}).get("ps") or 30)
            rows = []
            for season in self.seasons:
                hidden = (
                    self.season_visible_after is not None
                    and self.season_reads <= self.season_visible_after
                    and int(season.get("id") or 0) == self.season_id
                )
                if hidden:
                    continue
                rows.append({"season": season, "sections": {"sections": self._sections_of(season)}})
            start = (page - 1) * size
            return self._json(
                {
                    "code": 0,
                    "data": {"seasons": rows[start : start + size], "total": len(rows)},
                }
            )
        if url.endswith("/x/v2/reply/main"):
            mode = int((params or {}).get("mode") or 3)
            rows = self.reply_pages.get(mode, self.replies)
            return self._json({"code": 0, "data": {"replies": rows}})
        if url.endswith("/x/v2/reply/add"):
            self.comments.append(dict(form or {}))
            if self.comment_code:
                return self._json({"code": self.comment_code, "message": self.comment_message})
            return self._json({"code": 0, "data": {"rpid": self.comment_rpid}})
        if url.endswith("/x/v2/reply/top"):
            self.topped.append(dict(form or {}))
            if self.top_error is not None:
                raise self.top_error
            if self.top_code:
                return self._json({"code": self.top_code, "message": self.top_message})
            return self._json({"code": 0, "data": {}})
        raise AssertionError(f"替身不认识这个端点：{method} {url}")

    @staticmethod
    def _json(payload: dict[str, Any]) -> HttpResult:
        return HttpResult(200, json.dumps(payload, ensure_ascii=False))

    def _sections_of(self, season: dict[str, Any]) -> list[dict[str, Any]]:
        sid = int(season.get("id") or 0)
        if sid in self.season_sections:
            return list(self.season_sections[sid])
        return [default_section(sid)]


def command_runner(
    *,
    returncode: int = 0,
    stdout: str = "APP接口投稿成功",
    stderr: str = "",
    lines: tuple[str, ...] | None = None,
) -> Runner:
    """总是成功的假 runner。`lines` 会在返回前透给 `on_line`（用来驱动进度与日志尾部）。"""

    def run(argv: list[str], timeout: float, on_line: LineSink | None) -> CommandResult:
        if on_line is not None:
            for line in lines if lines is not None else ("start pre_upload", "Upload completed", stdout):
                on_line(line)
        return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)

    return run


class ScriptedRunner:
    """按调用次序切换结果的假 runner。用来构造「第二条失败」这类批次场景。"""

    def __init__(self, script: list[CommandResult], *, lines: tuple[str, ...] = ("start pre_upload", "Upload completed")) -> None:
        self.script = list(script)
        self.lines = lines
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float, on_line: LineSink | None) -> CommandResult:
        self.calls.append(list(argv))
        result = self.script.pop(0) if self.script else CommandResult(0, "", "")
        if on_line is not None:
            for line in self.lines:
                on_line(line)
        return result


class RecordingSleeper:
    """记下每一次等待。合集条间间隔靠它断言（「最后一条之后不再等」）。"""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def write_video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 32)
    return path


# 与 `bili_api.DEFAULT_VERIFY_ATTEMPTS` 对齐：断言退避次数时用它，别再抄一个 3。
VERIFY_ATTEMPTS = DEFAULT_VERIFY_ATTEMPTS

__all__ = [
    "DEFAULT_ARCHIVE",
    "JAR",
    "UNSET",
    "VERIFY_ATTEMPTS",
    "FakeBiliTransport",
    "HttpError",
    "RecordingSleeper",
    "ScriptedRunner",
    "StubTransport",
    "command_runner",
    "default_section",
    "own_reply",
    "section_id_for",
    "write_video",
]
