"""自动重试的**判据**（纯函数）与调度循环。

覆盖需求 FR-B38 点名的五种情形：审核未通过、审核通过、接口限流、轮询超时、重试成功与失败。

这里测的是**决策**，不是平台交互：平台交互由 `bili_upload` / `bili_manager` 那套
（手动按钮用的就是它）负责，本文件只回答「什么时候再问一次」「问完怎么记」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from app.bilibili.bili_auto_retry import (
    AutoRetryPolicy,
    BiliAutoRetryScheduler,
    apply_decision,
    decide_next,
    due_rows,
    is_rate_limited,
    register,
)
from app.common.time import as_aware_utc
from app.contracts.models import BiliAutoRetry, BiliAutoRetryKind, BiliAutoRetryState, utc_now


@pytest.fixture
def engine(tmp_path):
    from app.contracts.config import AppSettings
    from app.infra.db import create_app_engine, init_db

    settings = AppSettings()
    settings.data_dir = tmp_path
    settings.database_path = tmp_path / "auto_retry.sqlite3"
    engine = create_app_engine(settings)
    init_db(engine)
    return engine


# ================================================================ 退避
def test_backoff_grows_exponentially_and_stops_at_the_cap():
    policy = AutoRetryPolicy(interval_seconds=60)
    assert policy.backoff_seconds(1) == 60
    assert policy.backoff_seconds(2) == 120
    assert policy.backoff_seconds(3) == 240
    # 封顶：16 × 间隔。再往后等下去与等 16 分钟没有区别。
    assert policy.backoff_seconds(10) == 960
    assert policy.backoff_seconds(50) == 960


def test_a_rate_limited_attempt_waits_twice_as_long():
    """限流要**等更久**，不是照原样再试。

    认错方向的后果是反的：把限流当普通失败 ⇒ 退避不够 ⇒ 继续踩限流线，
    把一次临时限流升级成账号风险。
    """
    policy = AutoRetryPolicy(interval_seconds=60)
    assert policy.delay_for(1) == 60
    assert policy.delay_for(1, rate_limited_now=True) == 120


def test_rate_limiting_is_recognised_by_code_not_by_a_bare_number():
    assert is_rate_limited('{"code":-799,"message":"请求过于频繁"}') is True
    assert is_rate_limited("too many requests") is True
    # 稿件 id 或标题里出现 601 不算限流。
    assert is_rate_limited("稿件 601 号上传失败") is False


# ================================================================ 决策
def test_a_success_ends_the_automaton():
    decision = decide_next(AutoRetryPolicy(), attempts=1, succeeded=True, message="已自动归集")
    assert decision.state == BiliAutoRetryState.done.value
    assert decision.next_attempt_at is None
    assert decision.message == "已自动归集"


def test_still_under_review_schedules_the_next_try():
    decision = decide_next(AutoRetryPolicy(), attempts=0, succeeded=False, message="还在审核")
    assert decision.state == BiliAutoRetryState.waiting.value
    assert decision.next_attempt_at is not None
    # 第一次等一个间隔。
    assert decision.next_attempt_at - utc_now() <= timedelta(seconds=60)


def test_the_attempt_cap_stops_the_automaton():
    policy = AutoRetryPolicy(max_attempts=3)
    decision = decide_next(policy, attempts=2, succeeded=False, message="还是不行")
    assert decision.state == BiliAutoRetryState.exhausted.value
    assert decision.next_attempt_at is None
    # 停下来的理由要说出来（用户要知道自己得手动接手了）。
    assert "手动" in decision.message


def test_a_rate_limited_failure_schedules_a_longer_wait():
    policy = AutoRetryPolicy(interval_seconds=60)
    decision = decide_next(policy, attempts=0, succeeded=False, rate_limited_now=True)
    assert decision.state == BiliAutoRetryState.waiting.value
    assert decision.next_attempt_at - utc_now() > timedelta(seconds=100)


# ================================================================ 登记
def test_registering_the_same_target_twice_keeps_one_row(engine):
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        rows = session.exec(__import__("sqlmodel").select(BiliAutoRetry)).all()
    assert len(rows) == 1


def test_registering_again_does_not_reset_the_backoff(engine):
    """**这是最容易写错的一处**：调度器每问一次都会经过「登记」。

    如果登记顺手把 `next_attempt_at` 拨回现在，退避就被清零了 ——
    每一次尝试后都被拉回「立刻再试」，指数退避等于没有。
    """
    with Session(engine) as session:
        row = register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        later = utc_now() + timedelta(seconds=600)
        row.next_attempt_at = later
        row.attempts = 3
        session.add(row)
        session.commit()

        again = register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        # 库里读出来的是 **naive** 的 UTC（SQLite 不存时区），比较时要先收敛形态。
        assert as_aware_utc(again.next_attempt_at) == as_aware_utc(later)
        assert again.attempts == 3


def test_a_finished_target_is_armed_again_with_a_fresh_budget(engine):
    with Session(engine) as session:
        row = register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        row.state = BiliAutoRetryState.done.value
        row.attempts = 12
        session.add(row)
        session.commit()

        again = register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        assert again.state == BiliAutoRetryState.waiting.value
        assert again.attempts == 0


# ================================================================ 到点
def test_only_waiting_rows_that_are_due_are_picked_up(engine):
    with Session(engine) as session:
        due = register(session, target_id="due", kind=BiliAutoRetryKind.season.value, job_id="job1")
        later = register(session, target_id="later", kind=BiliAutoRetryKind.season.value, job_id="job1")
        later.next_attempt_at = utc_now() + timedelta(seconds=600)
        done = register(session, target_id="done", kind=BiliAutoRetryKind.season.value, job_id="job1")
        done.state = BiliAutoRetryState.done.value
        for row in (due, later, done):
            session.add(row)
        session.commit()

        picked = due_rows(session)
    assert [row.target_id for row in picked] == ["due"]


# ================================================================ 调度循环
class _Outcome:
    """假装业务逻辑给回来的回执。"""

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _Season:
    def __init__(self, status: str, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.advice = None


class _Upload:
    def __init__(self, subtitle_state: str, subtitle_message: str = "") -> None:
        self.subtitle_state = subtitle_state
        self.subtitle_message = subtitle_message


async def test_a_review_not_passed_keeps_waiting_and_says_so(engine):
    """审核没通过 ⇒ 继续等，**且不能算失败**。

    算失败会让界面说「自动重试失败」，用户以为要去查什么；
    而平台的审核不是我们能修的东西。
    """
    calls: list[str] = []

    async def retry_season(target_id: str):
        calls.append(target_id)
        return _Outcome(season=_Season("pending", "视频还在 B 站 排队审核，暂时没法加进合集"), message="还没到")

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")

    assert await scheduler.tick() == 1
    assert calls == ["s1"]
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.state == BiliAutoRetryState.waiting.value
    assert row.last_result == "waiting"
    assert row.attempts == 1
    assert row.next_attempt_at is not None
    assert "排队审核" in row.last_message


async def test_a_review_passed_ends_the_automaton(engine):
    calls: list[str] = []

    async def retry_season(target_id: str):
        calls.append(target_id)
        return _Outcome(season=_Season("grouped"), message="已归集")

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")

    assert await scheduler.tick() == 1
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.state == BiliAutoRetryState.done.value
    assert row.next_attempt_at is None
    assert row.last_result == "done"


async def test_the_attempt_cap_ends_the_automaton_and_leaves_the_button_to_the_user(engine):
    async def retry_season(target_id: str):
        return _Outcome(season=_Season("failed", "合集建好了，但视频没能加进去"), message="又失败了")

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60, max_attempts=2),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")

    await scheduler.tick()
    # 第二次之前要让那一行**到期**（退避把它推到了 60 秒之后）——
    # 不到点就问，等于绕过了退避，那不是「试够了」，是「还没到点」。
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
        session.add(row)
        session.commit()

    await scheduler.tick()
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.state == BiliAutoRetryState.exhausted.value
    assert row.next_attempt_at is None
    assert row.attempts == 2

    # 试够了之后**不再问平台**。
    assert await scheduler.tick() == 0


async def test_a_subtitle_that_still_has_no_cid_is_waiting_not_failed(engine):
    """平台还没给分P编号 ⇒ 字幕传不上去是**正常状态**，不是失败。

    判据是**结构性的**（稿件仍然没有 `cid`），不靠匹配那句会变的文案。
    """
    from app.contracts.models import BiliUpload

    with Session(engine) as session:
        session.add(
            BiliUpload(
                id="u1",
                job_id="job1",
                title="一条稿件",
                status="reviewing",
                subtitle_state="failed",
            )
        )
        session.commit()
        register(session, target_id="u1", kind=BiliAutoRetryKind.subtitle.value, job_id="job1")

    async def retry_upload(target_id: str):
        return _Outcome(upload=_Upload("failed", "稿件已提交（reviewing），但平台还没给出分P编号"))

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60),
        retry_season=lambda target_id: _Outcome(season=_Season("grouped")),
        retry_upload=retry_upload,
    )
    await scheduler.tick()
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "u1")
    assert row.last_result == "waiting"
    assert row.state == BiliAutoRetryState.waiting.value


async def test_a_rate_limited_failure_is_recorded_as_failed_not_waiting(engine):
    async def retry_season(target_id: str):
        return _Outcome(
            season=_Season("failed", "B站 说这个视频请求过于频繁"),
            message="请求过于频繁",
        )

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")

    await scheduler.tick()
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.last_result == "failed"
    # 限流 ⇒ 下一次等更久（比一个间隔长）。
    assert as_aware_utc(row.next_attempt_at) > utc_now() + timedelta(seconds=100)


async def test_one_tick_handles_one_target_only(engine):
    """一轮只处理一个：平台有账号级频控，攒着一起问就是密集请求。"""
    calls: list[str] = []

    async def retry_season(target_id: str):
        calls.append(target_id)
        return _Outcome(season=_Season("pending", "还在审核"))

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        register(session, target_id="s2", kind=BiliAutoRetryKind.season.value, job_id="job2")

    assert await scheduler.tick() == 1
    assert len(calls) == 1


async def test_the_switch_off_stops_everything(engine):
    calls: list[str] = []

    async def retry_season(target_id: str):
        calls.append(target_id)
        return _Outcome(season=_Season("pending"))

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(enabled=False),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")

    assert await scheduler.tick() == 0
    assert calls == []


def test_a_crashed_attempt_leaves_the_target_waiting_and_not_exhausted(engine):
    """进程被杀时它正拿着平台接口 —— 那一瞬间没有任何代码能收尾。

    折回 `waiting`（而不是 `exhausted`）：这一条本来就没试完，
    不该因此被判「试够了、放弃」。
    """
    with Session(engine) as session:
        row = register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")
        row.state = BiliAutoRetryState.running.value
        session.add(row)
        session.commit()

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(),
        retry_season=lambda target_id: _Outcome(season=_Season("pending")),
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    assert scheduler.resume_interrupted() == 1
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.state == BiliAutoRetryState.waiting.value
    assert row.next_attempt_at is not None


async def test_a_crash_inside_the_business_call_is_recorded_not_raised(engine):
    """业务逻辑抛异常 ⇒ 记成失败并安排下一次，**不能让调度器崩掉**。

    崩掉的话整个自动重试就停了，而用户看到的只是「它不动了」。
    """

    async def retry_season(target_id: str):
        raise RuntimeError("网络断了")

    scheduler = BiliAutoRetryScheduler(
        engine,
        AutoRetryPolicy(interval_seconds=60),
        retry_season=retry_season,
        retry_upload=lambda target_id: _Outcome(upload=_Upload("failed")),
    )
    with Session(engine) as session:
        register(session, target_id="s1", kind=BiliAutoRetryKind.season.value, job_id="job1")

    assert await scheduler.tick() == 1
    with Session(engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.state == BiliAutoRetryState.waiting.value
    assert "网络断了" in row.last_message
