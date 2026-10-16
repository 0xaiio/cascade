"""`bili_auth`：登录态文件的四种形态、凭据摘要、扫码会话。

三条最要紧的判据，这里各有专门用例：

1. **凭据不外泄**：`cookie_summary()` 的输出里**不许出现任何值**（连前缀都不给）。
   掩码前缀仍然是凭据的一部分，截图或日志一贴就漏（requirements FR-B12）。
2. **四种文件形态都要认**：`biliup` 的 `LoginInfo`、扁平 dict、`[{name,value}]`、Netscape `cookies.txt`。
   少认一种，用户手里的导出文件就会「看起来导入成功、实则一项都没有」。
3. **回调 URL 里的值要解码**：`SESSDATA` 本身含逗号（`%2C`），不解码会得到一个永远登录不上的串。
"""

import json
from pathlib import Path

import pytest

from app.bili_auth import (
    TV_APPKEY,
    TV_AUTH_CODE_URL,
    TV_CONFIRM_URL,
    TV_POLL_NOT_CONFIRMED,
    TV_POLL_URL,
    POLL_MESSAGE,
    POLL_STATE_BY_CODE,
    REQUIRED_COOKIES,
    BiliLoginSession,
    TvToken,
    app_sign,
    build_login_info,
    cookie_header,
    cookie_summary,
    delete_cookie_file,
    exchange_tv_token,
    is_cookie_tracked_by_git,
    load_cookie_jar,
    missing_required_cookies,
    read_login_token,
    write_cookie_file,
)
from app.bili_http import HttpError, HttpResult

SECRET = "9f2a%2Csuper-secret-value"


class FakeTransport:
    """按 URL 分派响应。`set_cookie` 用来验证「凭据只出现在 Set-Cookie 里」那条路径。"""

    def __init__(self, *, generate=None, poll=None, set_cookie=()):
        self.generate_payload = generate if generate is not None else {
            "code": 0,
            "data": {"url": "https://www.bilibili.com/h5/login?qrcode_key=KEY", "qrcode_key": "KEY"},
        }
        self.poll_payload = poll if poll is not None else {"code": 0, "data": {"code": 86101}}
        self.set_cookie = set_cookie
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, *, params=None, headers=None, form=None, json_body=None, timeout=20.0):
        self.calls.append((method, url, {"params": params, "headers": headers or {}}))
        payload = self.generate_payload if url.endswith("/qrcode/generate") else self.poll_payload
        return HttpResult(200, json.dumps(payload), self.set_cookie)


# ---------------------------------------------------------------- 文件形态
def load_cookie_jar_from(tmp_path: Path, payload) -> dict[str, str]:
    """走真实的读盘路径（而不是直接调私有函数）：把内容写成文件再读回来。"""
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_cookie_jar(path)


def test_flatten_accepts_biliup_login_info(tmp_path):
    payload = {"cookie_info": {"cookies": [{"name": "SESSDATA", "value": SECRET}, {"name": "bili_jct", "value": "j"}]}}
    assert load_cookie_jar_from(tmp_path, payload) == {"SESSDATA": SECRET, "bili_jct": "j"}


def test_flatten_accepts_a_flat_dict(tmp_path):
    assert load_cookie_jar_from(tmp_path, {"SESSDATA": SECRET, "bili_jct": "j"}) == {"SESSDATA": SECRET, "bili_jct": "j"}


def test_flatten_accepts_a_list_of_name_value_objects(tmp_path):
    payload = [{"name": "SESSDATA", "value": SECRET}, {"name": "bili_jct", "value": "j"}]
    assert load_cookie_jar_from(tmp_path, payload) == {"SESSDATA": SECRET, "bili_jct": "j"}


def test_the_two_entry_list_call_sites_agree(tmp_path):
    """同一个「条目列表」形状在两种登录态文件里各有一处调用点（`LoginInfo` 内层 / 顶层数组）。

    两处判定必须**逐字一致**：有一处放宽，就会出现「同一个文件按扩展名走这条路读不出来、
    走那条路读得出来」—— 而这种不一致只会在用户的某一个文件上复现，极难查。
    """
    entries = [{"name": "SESSDATA", "value": SECRET}, {"name": "bili_jct", "value": "j"}]
    nested = load_cookie_jar_from(tmp_path, {"cookie_info": {"cookies": entries}})
    flat = load_cookie_jar_from(tmp_path, entries)
    assert nested == flat == {"SESSDATA": SECRET, "bili_jct": "j"}


def test_entries_missing_a_name_or_a_value_are_skipped(tmp_path):
    """缺 `name` 或 `value` 的条目静默跳过 —— 不能因为平台多发了一个坏条目就丢光全部 cookie。"""
    payload = [
        {"name": "SESSDATA", "value": SECRET},
        {"name": "no_value"},
        {"value": "no_name"},
        "not-an-object",
        None,
        3,
    ]
    assert load_cookie_jar_from(tmp_path, payload) == {"SESSDATA": SECRET}


def test_an_empty_inner_cookie_list_falls_through_to_the_flat_dict(tmp_path):
    """`cookie_info.cookies` 为空时**继续走扁平那条**：这种文件仍可能是一份扁平的 cookie 字典。

    若把它当成「内层读过了」而直接返回空表，用户会看到「登录态文件读出来了，但是空的」，
    而文件里其实明明白白写着 SESSDATA。
    """
    payload = {"cookie_info": {"cookies": []}, "SESSDATA": SECRET}
    assert load_cookie_jar_from(tmp_path, payload) == {"SESSDATA": SECRET}


def test_flatten_drops_nested_non_scalar_values_from_a_flat_dict(tmp_path):
    """扁平 dict 里混进嵌套结构（如 `{"token_info": {...}}`）时不能把整个对象塞进 Cookie 头。"""
    assert load_cookie_jar_from(tmp_path, {"SESSDATA": SECRET, "token_info": {"a": 1}}) == {"SESSDATA": SECRET}


def test_flatten_returns_empty_for_an_unusable_payload(tmp_path):
    assert load_cookie_jar_from(tmp_path, "just a string") == {}
    assert load_cookie_jar_from(tmp_path, None) == {}


def test_load_cookie_jar_returns_empty_for_a_missing_file(tmp_path):
    """体检需要一个「没有」的正常结果，而不是异常。"""
    assert load_cookie_jar(tmp_path / "nope.json") == {}


def test_load_cookie_jar_returns_empty_for_an_empty_file(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text("   \n", encoding="utf-8")
    assert load_cookie_jar(path) == {}


def test_load_cookie_jar_parses_netscape_cookies_txt(tmp_path):
    """Netscape 格式：7 列 Tab 分隔，第 6/7 列是名字与值。"""
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1999999999\tSESSDATA\t{}\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1999999999\tbili_jct\tj\n".format(SECRET),
        encoding="utf-8",
    )
    assert load_cookie_jar(path) == {"SESSDATA": SECRET, "bili_jct": "j"}


def test_load_cookie_jar_ignores_lines_with_too_few_columns(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("not\ta\tcookie\n.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tv\n", encoding="utf-8")
    assert load_cookie_jar(path) == {"SESSDATA": "v"}


# ---------------------------------------------------------------- 落盘与摘要
def test_write_then_load_round_trips_the_jar(tmp_path):
    path = tmp_path / "nested" / "cookies.json"
    write_cookie_file(path, {"SESSDATA": SECRET, "bili_jct": "j"}, mid=42)
    assert load_cookie_jar(path) == {"SESSDATA": SECRET, "bili_jct": "j"}


def test_build_login_info_omits_empty_values():
    info = build_login_info({"SESSDATA": SECRET, "bili_jct": ""}, mid=42)
    names = [item["name"] for item in info["cookie_info"]["cookies"]]
    assert names == ["SESSDATA"]


def test_build_login_info_carries_the_upload_token():
    """令牌必须写进去，且 `platform` 只能是 BiliTV。

    这两条都不是「顺手填」：缺令牌时 biliup 会在发出任何投稿请求之前退出（空 access_token 打
    `oauth2/info` 得到 `-400 请求错误`）；`platform` 错了则令牌到期后 biliup 的 `renew` 会
    原样返回、静默不续期，一个月后突然开始投稿失败。
    """
    token = TvToken(access_token="at", refresh_token="rt", expires_in=15552000, mid=7)
    info = build_login_info({"SESSDATA": SECRET}, mid=42, token=token)
    assert info["token_info"] == {
        "access_token": "at",
        "expires_in": 15552000,
        "mid": 7,
        "refresh_token": "rt",
    }
    assert info["platform"] == "BiliTV"
    assert ".bilibili.com" in info["cookie_info"]["domains"]
    assert info["sso"] == []


def test_build_login_info_without_a_token_produces_a_file_biliup_will_reject():
    """没有令牌时写出的就是那个「看着正常、其实投不了稿」的文件 —— 这件事要显式钉住，
    因为界面上的三项体检曾经对它一路放绿。"""
    info = build_login_info({"SESSDATA": SECRET}, mid=42)
    assert info["token_info"]["access_token"] == ""
    assert "platform" not in info


def test_read_login_token_only_reports_presence(tmp_path):
    path = tmp_path / "cookies.json"
    assert read_login_token(path).has_access_token is False  # 文件不存在
    path.write_text("not json", encoding="utf-8")
    assert read_login_token(path).has_access_token is False
    path.write_text("{}", encoding="utf-8")
    assert read_login_token(path).has_access_token is False
    write_cookie_file(path, {"SESSDATA": SECRET}, 42)
    assert read_login_token(path).has_access_token is False
    write_cookie_file(
        path,
        {"SESSDATA": SECRET},
        42,
        TvToken(access_token="ACCESS-LEAK", refresh_token="REFRESH-LEAK", expires_in=1, mid=7),
    )
    state = read_login_token(path)
    assert state.has_access_token and state.has_refresh_token
    assert state.mid == 7 and state.platform == "BiliTV"
    # 摘要里**不能出现值**：这个对象会被体检直接投影给界面。
    # （注意断言要拿具体的值比，别拿 `"at"` 这种两字母 —— 它会撞上 `platform` 里的 `at`。）
    assert "ACCESS-LEAK" not in repr(state) and "REFRESH-LEAK" not in repr(state)


def test_delete_cookie_file_reports_whether_it_did_anything(tmp_path):
    path = tmp_path / "cookies.json"
    assert delete_cookie_file(path) is False
    write_cookie_file(path, {"SESSDATA": SECRET})
    assert delete_cookie_file(path) is True
    assert not path.exists()


def test_cookie_header_skips_empty_values():
    assert cookie_header({"a": "1", "b": "", "c": "3"}) == "a=1; c=3"


def test_missing_required_cookies_returns_field_names_only():
    assert missing_required_cookies({"SESSDATA": SECRET}) == [name for name in REQUIRED_COOKIES if name != "SESSDATA"]


@pytest.mark.parametrize("secret", [SECRET, "deadbeefcafe", "42073777"])
def test_cookie_summary_never_leaks_a_value(secret):
    """**凭据摘要里不许出现任何值**，连前缀都不给。"""
    jar = {"SESSDATA": "9f2a%2Csuper-secret-value", "bili_jct": "deadbeefcafe", "DedeUserID": "42073777"}
    summary = cookie_summary(jar)
    rendered = json.dumps(summary, ensure_ascii=False)
    assert secret not in rendered
    # 值的前 4 个字符也不许出现（掩码前缀同样是凭据的一部分）。
    assert secret[:4] not in rendered


def test_cookie_summary_is_exactly_the_documented_shape():
    """摘要只承诺这 5 项。多一个字段就意味着多一处可能漏值的口子。"""
    summary = cookie_summary({"SESSDATA": SECRET, "bili_jct": "j"})
    assert set(summary) == {"present", "count", "names", "missing_required", "missing_recommended"}


def test_cookie_summary_of_an_empty_jar_is_a_normal_result():
    summary = cookie_summary({})
    assert summary["present"] is False
    assert summary["count"] == 0
    assert summary["missing_required"] == list(REQUIRED_COOKIES)


def test_cookie_summary_lists_names_sorted_for_a_stable_ui():
    assert cookie_summary({"b": "1", "a": "2"})["names"] == ["a", "b"]


# ---------------------------------------------------------------- git 跟踪
def test_is_cookie_tracked_by_git_is_false_for_a_missing_file(tmp_path):
    assert is_cookie_tracked_by_git(tmp_path / "nope.json", tmp_path) is False


def test_is_cookie_tracked_by_git_is_false_outside_a_repository(tmp_path):
    """判定失败一律返回 False —— 体检不该因为环境缺工具而报假警。"""
    path = tmp_path / "cookies.json"
    path.write_text("{}", encoding="utf-8")
    assert is_cookie_tracked_by_git(path, tmp_path) is False


# ---------------------------------------------------------------- 扫码
def test_generate_returns_key_url_and_inline_svg():
    """二维码是**内联 SVG 文本**，不是位图 —— 前端直接塞进 DOM，不牵入图像依赖。

    顺带钉住一个实测事实：`SvgPathImage` 把尺寸写在**毫米**里（`width="29.6mm"`），
    所以在不同 DPI 下渲染出来大小并不一致。前端的 `.bili-qr-image svg` 必须显式覆盖
    宽高，否则同一个二维码在别人的机器上会忽大忽小。
    """
    pytest.importorskip("qrcode")
    challenge = BiliLoginSession(FakeTransport()).generate()
    assert challenge.qrcode_key == "KEY"
    assert challenge.url.endswith("qrcode_key=KEY")
    assert challenge.svg and "<svg" in challenge.svg
    assert 'width="29.6mm"' in challenge.svg, "尺寸单位变了：前端那条 width/height 覆盖的前提是毫米"
    assert challenge.note is None


def test_generate_degrades_when_the_qr_component_is_missing(monkeypatch):
    """缺 `qrcode` 时给一句「改用导入 cookies.json」，而不是让整个设置面板不可用。"""
    import app.bili_auth as module

    monkeypatch.setattr(module, "render_qr_svg", lambda *_a, **_k: None)
    challenge = BiliLoginSession(FakeTransport()).generate()
    assert challenge.svg is None
    assert "导入 cookies.json" in (challenge.note or "")


def test_generate_raises_a_readable_error_when_the_platform_refuses():
    transport = FakeTransport(generate={"code": -412, "message": "请求被拦截"})
    with pytest.raises(HttpError, match="没能生成登录二维码"):
        BiliLoginSession(transport).generate()


def test_generate_sends_the_passport_referer():
    transport = FakeTransport()
    BiliLoginSession(transport).generate()
    headers = transport.calls[0][2]["headers"]
    assert headers["Referer"].startswith("https://passport.bilibili.com")


@pytest.mark.parametrize(
    ("code", "state"),
    [(86101, "waiting"), (86090, "scanned"), (86038, "expired")],
)
def test_poll_maps_platform_codes_to_our_states(code, state):
    result = BiliLoginSession(FakeTransport(poll={"code": 0, "data": {"code": code}})).poll("KEY")
    assert result.state == state
    assert result.message == POLL_MESSAGE[state]
    assert result.jar is None


def test_poll_treats_an_unknown_code_as_unknown_and_asks_for_a_new_qrcode():
    result = BiliLoginSession(FakeTransport(poll={"code": 0, "data": {"code": 12345}})).poll("KEY")
    assert result.state == "unknown"
    assert result.message == POLL_MESSAGE["unknown"]


def test_poll_treats_a_missing_code_as_unknown():
    result = BiliLoginSession(FakeTransport(poll={"code": 0, "data": {}})).poll("KEY")
    assert result.state == "unknown"


def test_poll_confirms_and_decodes_credentials_from_the_callback_url():
    """`SESSDATA` 本身含逗号（以 `%2C` 编码）—— 不解码就会存进一个永远登录不上的串。"""
    callback = (
        "https://passport.biligame.com/crossDomain"
        f"?DedeUserID=42&SESSDATA={SECRET}&bili_jct=j"
    )
    result = BiliLoginSession(FakeTransport(poll={"code": 0, "data": {"code": 0, "url": callback}})).poll("KEY")
    assert result.state == "confirmed"
    assert result.jar is not None
    assert result.jar["SESSDATA"] == "9f2a,super-secret-value"
    assert result.jar["bili_jct"] == "j"
    assert result.jar["DedeUserID"] == "42"


def test_poll_harvests_credentials_from_set_cookie_when_the_callback_has_none():
    transport = FakeTransport(
        poll={"code": 0, "data": {"code": 0}},
        set_cookie=(
            f"SESSDATA={SECRET}; Path=/; Domain=.bilibili.com",
            "bili_jct=j; Path=/",
            "DedeUserID=42; Path=/",
        ),
    )
    result = BiliLoginSession(transport).poll("KEY")
    assert result.state == "confirmed"
    assert result.jar is not None
    assert set(result.jar) >= {"SESSDATA", "bili_jct", "DedeUserID"}


def test_poll_ignores_deleted_cookie_values():
    transport = FakeTransport(
        poll={"code": 0, "data": {"code": 0}},
        set_cookie=("SESSDATA=deleted; Path=/", "bili_jct=j; Path=/", "DedeUserID=42; Path=/", "buvid3=deleted"),
    )
    result = BiliLoginSession(transport).poll("KEY")
    assert result.state == "failed"
    assert "SESSDATA" in result.message


def test_poll_reports_incomplete_credentials_instead_of_pretending_to_succeed():
    """拿到 0 但缺 SESSDATA 时必须说「重新扫码」，而不是让后续投稿以「你没权限」失败。"""
    transport = FakeTransport(poll={"code": 0, "data": {"code": 0, "url": "https://x/?bili_jct=j"}})
    result = BiliLoginSession(transport).poll("KEY")
    assert result.state == "failed"
    assert "SESSDATA" in result.message
    assert result.jar is None


def test_poll_keeps_earlier_credentials_when_a_later_round_only_returns_one():
    """生成二维码那一步就可能已经拿到 SESSDATA；轮询只补回缺的那项即可。"""
    session = BiliLoginSession(FakeTransport(set_cookie=(f"SESSDATA={SECRET}; Path=/",)))
    session.generate()
    transport = FakeTransport(poll={"code": 0, "data": {"code": 0, "url": "https://x/?bili_jct=j&DedeUserID=42"}})
    session.transport = transport
    result = session.poll("KEY")
    assert result.state == "confirmed"
    assert result.jar is not None
    assert result.jar["SESSDATA"] == SECRET


def test_poll_sends_the_accumulated_cookie_back_on_the_next_round():
    """passport 的轮询要把第一阶段拿到的 cookie 带回去，否则拿不到确认结果。"""
    session = BiliLoginSession(FakeTransport(set_cookie=(f"SESSDATA={SECRET}; Path=/",)))
    session.generate()
    transport = FakeTransport()
    session.transport = transport
    session.poll("KEY")
    assert f"SESSDATA={SECRET}" in transport.calls[0][2]["headers"]["Cookie"]


def test_every_poll_state_has_a_message():
    """`POLL_STATE_BY_CODE` 折出来的每一档都要有话说，否则界面上会出现空白状态。

    `failed` 不在表里是有意的：那条文案要**点名缺了哪一项**（见 `poll`），
    所以只能当场拼出来，塞进常量表反而会退化成一句没用的「登录失败」。
    """
    for state in (*POLL_STATE_BY_CODE.values(), "unknown"):
        assert POLL_MESSAGE.get(state, "").strip(), state


# ---------------------------------------------------------------- 投稿令牌交换
class TvTransport:
    """只认识令牌交换那三个端点。每个端点都能被改成失败，用来走完分支。"""

    def __init__(
        self,
        *,
        auth_code="tv-auth-code",
        auth_payload=None,
        confirm_payload=None,
        poll_codes=(0,),
        token=None,
        error=None,
    ) -> None:
        self.auth_code = auth_code
        self.auth_payload = auth_payload
        self.confirm_payload = (
            confirm_payload if confirm_payload is not None else {"code": 0, "message": "OK", "data": {"gourl": ""}}
        )
        self.poll_codes = list(poll_codes)
        self.token = token or {
            "access_token": "at-value",
            "refresh_token": "rt-value",
            "expires_in": 15552000,
            "mid": 42,
        }
        self.error = error
        self.polls = 0
        self.calls: list[dict] = []

    def request(self, method, url, *, params=None, headers=None, form=None, json_body=None, timeout=20.0):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "form": form or {}})
        if self.error is not None:
            raise self.error
        if url == TV_CONFIRM_URL:
            return HttpResult(200, json.dumps(self.confirm_payload))
        if url == TV_POLL_URL:
            index = min(self.polls, len(self.poll_codes) - 1)
            code = self.poll_codes[index]
            self.polls += 1
            if code != 0:
                return HttpResult(200, json.dumps({"code": code, "message": "未确认"}))
            return HttpResult(
                200,
                json.dumps(
                    {
                        "code": 0,
                        "data": {
                            "cookie_info": {"cookies": [{"name": "SESSDATA", "value": "s"}], "domains": []},
                            "sso": [],
                            "token_info": self.token,
                        },
                    }
                ),
            )
        payload = self.auth_payload
        if payload is None:
            payload = {"code": 0, "message": "OK", "data": {"auth_code": self.auth_code, "url": "u"}}
        return HttpResult(200, json.dumps(payload))

    def form_for(self, url: str) -> dict:
        return next(call["form"] for call in self.calls if call["url"] == url)

    def headers_for(self, url: str) -> dict:
        return next(call["headers"] for call in self.calls if call["url"] == url)


def test_app_sign_matches_the_frozen_biliup_value():
    """签名算法必须逐字节等于 biliup 的。

    签名不对时 B站 回的是**同一个** `-400 请求错误` —— 「我算错了」会被伪装成「你的凭据坏了」，
    排查方向直接跑偏。所以这里钉一个算好的字面值，而不是在测试里把实现再抄一遍。
    """
    frozen = {"appkey": TV_APPKEY, "local_id": "0", "ts": 1700000000}
    assert app_sign(frozen) == "ebb086c9f52ae7393619a89bdc320e45"


def test_app_sign_sorts_keys_alphabetically():
    assert app_sign({"b": "2", "a": "1"}) == app_sign({"a": "1", "b": "2"})


def test_exchange_tv_token_returns_a_real_token():
    transport = TvTransport()
    token = exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)
    assert token.access_token == "at-value"
    assert token.refresh_token == "rt-value"
    assert token.expires_in == 15552000
    assert token.mid == 42


def test_exchange_tv_token_confirms_with_the_users_own_cookies():
    """确认这一步是用用户已有的 cookies 替他点的 —— 少了这个 Cookie 头，B站 会说没权限。"""
    transport = TvTransport()
    exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)
    headers = transport.headers_for(TV_CONFIRM_URL)
    assert headers["Cookie"] == "SESSDATA=s; bili_jct=j"
    assert transport.form_for(TV_CONFIRM_URL)["csrf"] == "j"


def test_exchange_tv_token_drops_the_junk_picked_up_from_the_callback_url():
    """扫码时从回调 URL 上顺手捡来的 `gourl` / `sid` **不是 cookie**（那是查询参数的名字）。
    一起发过去只是给对方一个「这个客户端行为很奇怪」的信号。"""
    transport = TvTransport()
    jar = {"SESSDATA": "s", "bili_jct": "j", "gourl": "https://x", "sid": "abc", "first_domain": ".bilibili.com"}
    exchange_tv_token(transport, jar, sleeper=lambda _s: None)
    assert transport.headers_for(TV_CONFIRM_URL)["Cookie"] == "SESSDATA=s; bili_jct=j"


def test_exchange_tv_token_signs_the_appkey_rounds_but_not_the_confirmation():
    """两组参数风格**相反**，这一点很容易写错：

    - 申请二维码与轮询走 appkey 签名（`sign` 在表单里）；
    - h5 确认走的是**用户自己的 csrf**（`csrf` 在表单里、`Cookie` 在头上），**没有 sign**。
    """
    transport = TvTransport()
    exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)
    signed = {call["url"] for call in transport.calls if call["form"].get("sign")}
    assert TV_AUTH_CODE_URL in signed and TV_POLL_URL in signed
    assert TV_CONFIRM_URL not in signed


def test_exchange_tv_token_retries_while_the_platform_says_not_confirmed():
    """自动确认刚发出去时可能赶上「还没确认」这一拍 —— 这一拍不该被判成失败。"""
    slept: list[float] = []
    transport = TvTransport(poll_codes=(TV_POLL_NOT_CONFIRMED, TV_POLL_NOT_CONFIRMED, 0))
    token = exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=slept.append)
    assert token.access_token == "at-value"
    assert transport.polls == 3
    assert len(slept) == 2


def test_exchange_tv_token_gives_up_after_the_attempt_limit():
    transport = TvTransport(poll_codes=(TV_POLL_NOT_CONFIRMED,))
    with pytest.raises(HttpError, match="还没被确认"):
        exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)


def test_exchange_tv_token_fails_fast_on_an_unexpected_poll_code():
    """其它码是确定性拒绝，重试只是白等 —— 必须立刻把码报出来。"""
    transport = TvTransport(poll_codes=(-400,))
    with pytest.raises(HttpError, match="code=-400"):
        exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)


def test_exchange_tv_token_reports_a_refused_confirmation():
    transport = TvTransport(confirm_payload={"code": -400, "message": "请求错误", "data": None})
    with pytest.raises(HttpError, match="拒绝了用当前登录换投稿令牌"):
        exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)


def test_exchange_tv_token_reports_a_missing_auth_code():
    transport = TvTransport(auth_payload={"code": 0, "message": "OK", "data": {}})
    with pytest.raises(HttpError, match="没能签发投稿用的二维码"):
        exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)


@pytest.mark.parametrize("jar", [{}, {"SESSDATA": "s"}, {"bili_jct": "j"}])
def test_exchange_tv_token_needs_both_web_cookies(jar):
    with pytest.raises(HttpError, match="SESSDATA 或 bili_jct"):
        exchange_tv_token(TvTransport(), jar, sleeper=lambda _s: None)


def test_exchange_tv_token_reports_a_token_less_response():
    transport = TvTransport(token={"access_token": "", "refresh_token": "", "expires_in": 0, "mid": 1})
    with pytest.raises(HttpError, match="没有 access_token"):
        exchange_tv_token(transport, {"SESSDATA": "s", "bili_jct": "j"}, sleeper=lambda _s: None)
