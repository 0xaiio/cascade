"""时间的两种形态：**aware**（带时区）与 **naive**（墙上时间）。

为什么需要它：库里存的是 UTC，而 **SQLite 不保存时区** —— 读回来的 `datetime`
一律是 naive 的。把它和 `utc_now()`（aware）放在一起比大小会抛
`TypeError: can't compare offset-naive and offset-aware datetimes`。

本仓库已经因为这件事栽过一次：对外的时间戳少了偏移量，前端按本地时间解析，
本机 UTC+8 上每个时长都凭空多出 8 小时（见 `ai/bug-fix/020`）。

**一个实现，三处用**：`contracts/schemas.py`（序列化成带 `Z` 的 ISO 串）、
`download/job_read_model.py`（算已用时）、`bilibili/bili_auto_retry.py`（调度器排下一次）。
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_aware_utc(value: datetime) -> datetime:
    """把一个时刻收敛成 **aware 的 UTC**。

    - naive ⇒ 它就是 UTC 墙上时间，补上 `UTC`；
    - aware ⇒ 转成 UTC（它可能带着别的时区，比如从平台读回来的本地时间）。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def naive_utc(value: datetime) -> datetime:
    """反向：把一个时刻收敛成 **naive 的 UTC**（与库里读出来的形态一致）。

    写库时用它 —— 库里存的是 naive 的 UTC，写成 aware 的话，
    `sqlmodel` 在不同 SQLite/SQLAlchemy 版本下可能存成带偏移量的字符串，
    读回来就不是同一个形状了。
    """
    return as_aware_utc(value).replace(tzinfo=None)
