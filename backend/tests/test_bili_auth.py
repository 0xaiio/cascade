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
    POLL_MESSAGE,
    POLL_STATE_BY_CODE,
    REQUIRED_COOKIES,
    BiliLoginSession,
    build_login_info,
    cookie_header,
    cookie_summary,
    delete_cookie_file,
    is_cookie_tracked_by_git,
    load_cookie_jar,
    missing_required_cookies,
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


def test_build_login_info_shape_is_the_one_biliup_reads():
    info = build_login_info({"SESSDATA": SECRET}, mid=42, refresh_token="rt")
    assert info["token_info"]["mid"] == 42
    assert info["token_info"]["refresh_token"] == "rt"
    assert ".bilibili.com" in info["cookie_info"]["domains"]
    assert info["sso"] == []


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
