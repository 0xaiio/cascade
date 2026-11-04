"""诊断接口的「配置对照」：库里存的值 vs 这个进程实际在用的值。

对应 [ai/bug-fix/035](../ai/bug-fix/035-diagnostics-shows-the-stored-value.md)：
`032` 的故障形态（库里已是新值、进程仍按旧值跑）没有异常也没有日志，
唯一的现场就是诊断接口 —— 而它此前只回显「库里的值」，等于把人引向「配置是对的」。

这里钉三件事：
1. 两项都回显，且**运行值取自真正持有它的对象**（service / 调度器策略）；
2. 库改了、进程没跟上时，`differs` 必须为真（这就是下一次的第一现场）；
3. **自检只读** —— 调一次诊断不许顺手把库里的值灌回运行态，否则它再也测不出差异。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from app.api.api_support import set_setting
from test_api import make_client


def _items(client) -> dict[str, dict]:
    payload = client.get("/api/diagnostics").json()
    return {item["key"]: item for item in payload["settings_effective"]}


def test_every_audited_key_reports_both_sides(tmp_path: Path) -> None:
    """每一项都要有 `stored` 与 `runtime` 两个字段 —— 只回显一边就等于没做对照。"""
    client = make_client(tmp_path)

    items = _items(client)

    # 覆盖三类持有者：下载服务、AppSettings、自动重试调度器。
    assert {"download_dir", "default_resolution", "bili_auto_retry_max_attempts"} <= set(items)
    for key, item in items.items():
        assert set(item) == {"key", "stored", "runtime", "differs"}, key
        assert item["runtime"] is not None, key
    # 出厂状态：库里没有覆盖值 ⇒ 没有可比对象，不该报差异（否则默认值全会被标成「不一致」）。
    assert all(item["differs"] is False for item in items.values())


def test_a_stored_value_the_process_has_not_loaded_is_flagged(tmp_path: Path) -> None:
    """别的进程（或手工）改了库、本进程还没重新读 ⇒ 必须报出差异，并写清两个值。"""
    client = make_client(tmp_path)
    ctx = client.app.state.api_context
    moved = tmp_path / "another-downloads"

    with Session(ctx.manager.engine) as session:
        set_setting(session, "download_dir", str(moved))

    items = _items(client)

    assert items["download_dir"]["stored"] == str(moved)
    assert items["download_dir"]["runtime"] == str(ctx.settings.download_dir)
    assert items["download_dir"]["differs"] is True
    # 只有这一项不同：把「不一致」报成一片，等于没有信号。
    assert [key for key, item in items.items() if item["differs"]] == ["download_dir"]


def test_the_audit_does_not_reload_the_settings(tmp_path: Path) -> None:
    """自检**只读**：它不能顺手把库里的值灌回运行态。

    灌回去的话，下一次「改了没生效」就再也查不出来了 —— 诊断会把现场抹平再报「一致」。
    """
    client = make_client(tmp_path)
    ctx = client.app.state.api_context
    moved = tmp_path / "never-applied"

    with Session(ctx.manager.engine) as session:
        set_setting(session, "download_dir", str(moved))

    client.get("/api/diagnostics")
    client.get("/api/diagnostics")

    assert ctx.settings.download_dir != moved
    assert _items(client)["download_dir"]["differs"] is True


def test_the_auto_retry_knobs_read_the_scheduler_not_the_table(tmp_path: Path) -> None:
    """自动重试三个旋钮的运行值来自**调度器的策略**（`032` 之后它是每轮现取的）。

    只读 `AppSettings` 的话，测不出「调度器持有旧策略」这一类 —— 而那正是 `032` 的形态。
    """
    client = make_client(tmp_path)
    ctx = client.app.state.api_context

    with Session(ctx.manager.engine) as session:
        set_setting(session, "bili_auto_retry_max_attempts", "9")

    items = _items(client)
    assert items["bili_auto_retry_max_attempts"]["stored"] == "9"
    assert items["bili_auto_retry_max_attempts"]["runtime"] == str(
        ctx.bili.auto_retry.policy.max_attempts
    )
    assert items["bili_auto_retry_max_attempts"]["differs"] is True

    # 走真正的设置接口重新加载之后，两边就该一致了。
    assert client.put("/api/settings", json={"bili_auto_retry_max_attempts": 9}).status_code == 200
    reloaded = _items(client)["bili_auto_retry_max_attempts"]
    assert reloaded["stored"] == reloaded["runtime"] == "9"
    assert reloaded["differs"] is False


def test_boolean_knobs_compare_in_their_stored_form(tmp_path: Path) -> None:
    """布尔开关在库里是 `"1"/"0"` —— 拿 `True` 去比字符串会永远「不一致」。"""
    client = make_client(tmp_path)
    ctx = client.app.state.api_context

    with Session(ctx.manager.engine) as session:
        set_setting(session, "bili_auto_retry_enabled", "0")

    item = _items(client)["bili_auto_retry_enabled"]
    assert item["stored"] == "0"
    assert item["runtime"] == "1"
    assert item["differs"] is True
