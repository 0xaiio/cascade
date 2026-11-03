"""「等平台放行」的自动重试：**判据**（纯函数）与**调度**（有状态）。

要解决的问题写在 [ai/feature/bilibili-upload](../ai/feature/bilibili-upload/01-requirements.md) FR-B38：
B站 的审核要几分钟到几小时，其间稿件既**归不进合集**（平台还没给分P编号），
**也传不了字幕**（同样要 `cid`）。用户只能盯着界面手动点「重试归集」「重传字幕」。

三条设计决定（每条都有代价，都写在下面）：

1. **不另写一套平台交互。** 自动重试只做三件事：判断「该不该再问一次」、调用**现有的**
   `BiliManager.retry_season` / `retry_upload`、把结论写进 `bili_auto_retry` 表。
   平台交互、错误分类、状态判定全部复用手动按钮那套 —— 这是需求里「所有自动操作应与手动
   按钮复用同一套业务逻辑」的落点。两套逻辑一旦分叉，自动重试就会做出与手动不同的判断，
   而用户分不出那两者为什么不一样。

2. **「审核过没过」不由这里判断，而由平台回读给出。** 归集要 `cid`、字幕也要 `cid`，
   平台什么时候给是它说了算。本模块只负责「什么时候再问一次」—— 这条边界让
   「自动重试会不会做错事」这个问题不需要在这里回答。

3. **问平台这件事要串行。** 平台有账号级频控（错误码 601），并发问只会把自己限流。
   调度器一次只处理一个到期目标。

退避：`delay = interval × 2^(attempts-1)`，封顶 `interval × 16`（约 16 分钟）。
指数退避是因为审核时长**不可预期**：固定间隔要么问得太勤，要么等太久。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Awaitable, Callable, Iterable

from sqlmodel import Session, select

from .bili_policy import rate_limited
from ..common.time import as_aware_utc
from ..contracts.models import (
    BiliAutoRetry,
    BiliAutoRetryKind,
    BiliAutoRetryState,
    utc_now,
)

logger = logging.getLogger(__name__)

#: 退避封顶倍数（相对 `interval`）。16 × 60 s ≈ 16 分钟 —— 再长就没意义了：
#: 审核超过十几分钟还没放行，等下一次和现在没有区别。
MAX_BACKOFF_FACTOR = 16


@dataclass(frozen=True)
class AutoRetryPolicy:
    """自动重试的**参数**。全部来自配置，构造后不可变（便于单测）。"""

    #: 一次问平台之间的最小间隔（秒）。同时是退避的基数。
    interval_seconds: int = 60
    #: 最多自动试几次。默认与 `AppSettings.bili_auto_retry_max_attempts` 一致 ——
    #: 两处各写一个数就会在某处对不上（这里只是构造时的兜底，运行时永远由设置注入）。
    max_attempts: int = 3
    #: 总开关。关掉时调度器连扫描都不做。
    enabled: bool = True

    def backoff_seconds(self, attempts: int) -> int:
        """第 `attempts` 次之后要等多久（秒）。

        `attempts` 是**已经问过几次**。指数退避 + 封顶：
        `interval × 2^(attempts-1)`，最多 `interval × 16`。

        **为什么用指数而不是固定间隔**：审核时长不可预期。固定 60 秒意味着审核 10 分钟时
        空问 10 次（还在被审核 ⇒ 每次都白问，还可能被限流）；固定 30 分钟则审核刚过就干等
        半小时。指数退避两头都不吃亏。
        """
        if attempts < 1:
            attempts = 1
        factor = min(2 ** (attempts - 1), MAX_BACKOFF_FACTOR)
        return max(1, self.interval_seconds * factor)

    def exhausted(self, attempts: int) -> bool:
        """试够了没有。**达到上限后转 `exhausted`，不再自动重试**（手动按钮仍在）。"""
        return attempts >= self.max_attempts

    def delay_for(self, attempts: int, *, rate_limited_now: bool = False) -> int:
        """下一次间隔（秒）。`rate_limited_now` 时**再乘 2**。

        平台限流（601）说明「你现在问得太多了」，退避必须比平时更长 ——
        否则自动重试会持续踩在限流线上，把一次临时限流变成账号风险。
        """
        delay = self.backoff_seconds(attempts)
        return delay * 2 if rate_limited_now else delay


@dataclass(frozen=True)
class RetryDecision:
    """一次调度的**结论**：这个目标现在该不该问平台。"""

    #: 该不该问。
    due: bool
    #: 问完之后这一行变成什么状态。
    state: str
    #: 下一次到点的时间（`due=False` 时是 `None`）。
    next_attempt_at: object | None
    #: 给界面看的一句话（失败原因 / 「还在审核」/「成了」）。
    message: str = ""


def decide_next(
    policy: AutoRetryPolicy,
    *,
    attempts: int,
    succeeded: bool,
    message: str = "",
    rate_limited_now: bool = False,
    now=None,
) -> RetryDecision:
    """**纯函数**：问完平台之后，这一行下一步该是什么。

    `succeeded` 是「这次真的成了」—— 归集成功 / 字幕传上去了。别的结论都算「还没成」，
    但「还没成」不等于「失败」：平台还在审核时**不该**消耗重试次数以外的任何东西，
    更不该把它说成失败（那会让用户以为要去查什么）。

    判据三条：
    1. 成了 ⇒ `done`，不再自动重试（但行留着，界面上还能看到「已自动归集」）。
    2. 没成且试够了 ⇒ `exhausted`，交回给手动按钮。
    3. 没成且还有次数 ⇒ `waiting`，按退避安排下一次。
    """
    moment = now or utc_now()
    if succeeded:
        return RetryDecision(due=False, state=BiliAutoRetryState.done.value, next_attempt_at=None, message=message)
    attempts = attempts + 1
    if policy.exhausted(attempts):
        # **试够了这件事必须出现在那句话里**，哪怕已经有了一句失败原因：
        # 用户要知道「机器不再试了，接下来该我手动点」。只留失败原因的话，
        # 界面上看着像是「还在自动重试，只是又失败了一次」。
        stopped = f"自动重试已试 {attempts} 次，先停在这里；可以手动重试。"
        return RetryDecision(
            due=False,
            state=BiliAutoRetryState.exhausted.value,
            next_attempt_at=None,
            message=f"{stopped}（上次：{message}）" if message else stopped,
        )
    delay = policy.delay_for(attempts, rate_limited_now=rate_limited_now)
    return RetryDecision(
        due=False,
        state=BiliAutoRetryState.waiting.value,
        next_attempt_at=moment + timedelta(seconds=delay),
        message=message,
    )


def register(
    session: Session,
    *,
    target_id: str,
    kind: str,
    job_id: str,
    now=None,
) -> BiliAutoRetry:
    """登记一个「等平台放行」的目标。**幂等**，且**不打断已有的退避节奏**。

    三种情形，行为各不相同（这是最容易写错的一处）：

    | 已有状态 | 行为 | 为什么 |
    | --- | --- | --- |
    | 没有这一行 | 新建，`next_attempt_at = 现在` | 第一次登记：立刻排上 |
    | `waiting` / `running` | **什么都不改** | 它正等着下一次问。改 `next_attempt_at` 会把退避清零 —— 而调度器每问一次都会经过这里，于是每一次尝试后都被拉回「立刻再试」，指数退避就没了 |
    | `done` / `exhausted` | 重新武装：状态回 `waiting`、次数归零 | 手动重试成功、或新一轮审核又开始了 —— 该给一次新的机会 |

    幂等很重要：手动重试成功一次之后自动重试也可能在同一时刻登记同一个目标，
    两次登记不能变成两行（那会让调度器问两次平台）。
    """
    moment = now or utc_now()
    row = session.get(BiliAutoRetry, target_id)
    if row is None:
        row = BiliAutoRetry(
            target_id=target_id,
            kind=kind,
            job_id=job_id,
            state=BiliAutoRetryState.waiting.value,
            attempts=0,
            next_attempt_at=moment,
            created_at=moment,
            updated_at=moment,
        )
    elif row.state in {BiliAutoRetryState.done.value, BiliAutoRetryState.exhausted.value}:
        # 重新武装 ⇒ 重新开始一轮等待（次数也归零：上一轮已经用完了它的机会）。
        row.state = BiliAutoRetryState.waiting.value
        row.attempts = 0
        row.next_attempt_at = moment
        row.updated_at = moment
    session.add(row)
    session.commit()
    return row


def due_rows(session: Session, *, now=None) -> list[BiliAutoRetry]:
    """到点的目标。**只取 `waiting`** —— `running` 是正在问，`done`/`exhausted`/`off` 不该再问。

    排序按 `next_attempt_at`，同一时刻到点的按 `target_id` 排 —— 顺序稳定才有可复现的行为。
    """
    moment = now or utc_now()
    rows = session.exec(
        select(BiliAutoRetry).where(BiliAutoRetry.state == BiliAutoRetryState.waiting.value)
    ).all()
    # 库里读出来的是 **naive** 的 UTC，而 `utc_now()` 是 aware 的 ——
    # 直接比较会抛「can't compare offset-naive and offset-aware datetimes」。
    due = [
        row
        for row in rows
        if row.next_attempt_at is None or as_aware_utc(row.next_attempt_at) <= moment
    ]
    due.sort(
        key=lambda row: (
            as_aware_utc(row.next_attempt_at) if row.next_attempt_at else moment,
            row.target_id,
        )
    )
    return due


def apply_decision(
    session: Session,
    row: BiliAutoRetry,
    decision: RetryDecision,
    *,
    now=None,
) -> BiliAutoRetry:
    """把一条结论写回那一行（状态 / 次数 / 下一次时间 / 给界面看的话）。"""
    moment = now or utc_now()
    row.state = decision.state
    row.next_attempt_at = decision.next_attempt_at
    row.last_message = decision.message
    row.updated_at = moment
    session.add(row)
    session.commit()
    return row


def is_rate_limited(text: str) -> bool:
    """这段失败输出里说的是不是「账号侧临时限流」（B站 错误码 601）。

    判据只在**限流**这一件事上：它要求「再等久一点」，而别的失败要求「照原样再试」。
    认错了的后果是反的：把限流当普通失败 ⇒ 退避不够 ⇒ 继续踩限流线。
    """
    return rate_limited(text or "")


__all__ = [
    "AutoRetryPolicy",
    "MAX_BACKOFF_FACTOR",
    "RetryDecision",
    "apply_decision",
    "decide_next",
    "due_rows",
    "is_rate_limited",
    "register",
]


# ================================================================== 调度器
@dataclass
class RetryOutcome:
    """一次自动尝试的**结论**（由被调用的业务逻辑给出的原始信息 + 本层的判断）。"""

    #: 这次真的成了吗（归集成功 / 字幕传上去了）。
    succeeded: bool
    #: 这次「还没成」是因为平台还没放行吗（那就继续等，不是失败）。
    waiting: bool
    #: 给用户看的话（失败原因 / 「还在审核」/「成了」）。
    message: str
    #: 这次是不是撞上了平台限流（撞上了要退避更久）。
    rate_limited: bool = False


class BiliAutoRetryScheduler:
    """到点问一次平台，问完按结论安排下一次。

    **它只做调度**：什么时候问、问完怎么记，全在这里；「问」这件事本身交给
    手动按钮那套业务逻辑（`retry_season` / `retry_upload`）—— 这是需求里
    「所有自动操作应与手动按钮复用同一套业务逻辑」的落点。

    三条可靠性约束：

    1. **串行**：一次只处理一个到期目标。平台有账号级频控，并发问会把自己限流。
    2. **落库**：每一次尝试的结论都写进 `bili_auto_retry`，所以进程重启后
       下一轮扫描能接着做（内存态的进度在重启后就全丢了）。
    3. **上限**：`max_attempts` 到了就转 `exhausted`，不再自动重试 ——
       无限重试在平台一直不放行时会把账号刷成限流状态。
    """

    def __init__(
        self,
        engine,
        policy: AutoRetryPolicy,
        *,
        retry_season: Callable[[str], Awaitable["object"]],
        retry_upload: Callable[[str], Awaitable["object"]],
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self._retry_season = retry_season
        self._retry_upload = retry_upload
        self._sleep = sleeper or asyncio.sleep
        self._on_change = on_change
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- 生命周期 ----------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self._sleep(self.policy.interval_seconds)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 一轮失败不能带走整个调度器
                logger.exception("bili 自动重试这一轮失败了")

    # -- 恢复 --------------------------------------------------------------
    def resume_interrupted(self) -> int:
        """把重启前停在 `running` 的行折回 `waiting`，返回条数。

        `running` 是**必然**会留下的：进程被杀时它正拿着平台接口，
        那一瞬间没有任何代码能收尾。折回 `waiting` 而不是 `exhausted` ——
        这一条本来就没试完，不该因此被判「试够了」。

        **这是「进程重启后不丢任务」的落点**：待办清单在库里，重启后接着走。
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(BiliAutoRetry).where(BiliAutoRetry.state == BiliAutoRetryState.running.value)
            ).all()
            for row in rows:
                row.state = BiliAutoRetryState.waiting.value
                row.next_attempt_at = utc_now()
                row.updated_at = utc_now()
                session.add(row)
            if rows:
                session.commit()
                logger.info("bili 自动重试：把 %s 条中断的等待折回待办", len(rows))
            return len(rows)

    # -- 一轮 --------------------------------------------------------------
    async def tick(self) -> int:
        """处理一轮到点的目标，返回**处理了几条**。

        一次只处理一条：平台有账号级频控，而一轮里问十个目标就是十次请求 ——
        即便每个目标各自都要等一分钟，攒在一起也是一串密集请求。
        """
        if not self.policy.enabled:
            return 0
        with Session(self.engine) as session:
            rows = due_rows(session)
            if not rows:
                return 0
            row = rows[0]
            row.state = BiliAutoRetryState.running.value
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            target_id, kind, attempts = row.target_id, row.kind, row.attempts

        outcome = await self._attempt(kind, target_id)
        decision = decide_next(
            self.policy,
            attempts=attempts,
            succeeded=outcome.succeeded,
            message=outcome.message,
            rate_limited_now=outcome.rate_limited,
        )
        with Session(self.engine) as session:
            row = session.get(BiliAutoRetry, target_id)
            if row is not None:
                row.attempts = attempts + 1
                row.last_action = kind
                row.last_result = "done" if outcome.succeeded else ("waiting" if outcome.waiting else "failed")
                apply_decision(session, row, decision)
        if self._on_change is not None:
            self._on_change()
        return 1

    async def _attempt(self, kind: str, target_id: str) -> RetryOutcome:
        """问一次平台，并把结果翻译成（成了 / 还在等 / 失败）。"""
        try:
            if kind == BiliAutoRetryKind.season.value:
                return await self._attempt_season(target_id)
            if kind == BiliAutoRetryKind.subtitle.value:
                return await self._attempt_subtitle(target_id)
        except Exception as exc:  # noqa: BLE001 - 一次失败不该带走调度器
            logger.warning("bili 自动重试失败：kind=%s target=%s error=%s", kind, target_id, exc)
            return RetryOutcome(
                succeeded=False,
                waiting=False,
                message=f"自动重试时出错：{exc}",
                rate_limited=is_rate_limited(str(exc)),
            )
        return RetryOutcome(succeeded=False, waiting=False, message="自动重试的目标类型不认识")

    async def _attempt_season(self, target_id: str) -> RetryOutcome:
        """自动归集：走的是**手动「重试归集」那条路**（`retry_season`）。

        结论三档：归好了（`grouped`）/ 还没到（`pending`，平台没给分P编号）/ 这次又失败了。
        """
        result = await self._retry_season(target_id)
        season = getattr(result, "season", None)
        status = getattr(season, "status", "") or ""
        error = getattr(season, "error", "") or ""
        message = getattr(result, "message", "") or ""
        if status == "grouped":
            return RetryOutcome(succeeded=True, waiting=False, message="已自动归集")
        if status == "pending":
            # 「还在审核」不是失败：平台还没给编号，再等就是了。
            return RetryOutcome(
                succeeded=False,
                waiting=True,
                message=error or "视频还在 B 站 排队审核，暂时没法加进合集",
                rate_limited=False,
            )
        return RetryOutcome(
            succeeded=False,
            waiting=False,
            message=error or message or "自动归集这次没能完成",
            rate_limited=is_rate_limited(f"{error} {message}"),
        )

    async def _attempt_subtitle(self, target_id: str) -> RetryOutcome:
        """自动重传字幕：走的是**手动「重传字幕」那条路**（`retry_upload`）。

        「还在审核」的判据是**结构性的**：这次尝试之后稿件仍然没有 `cid` ——
        平台还没给分P编号，字幕就挂不上去。不用去匹配那句文案（文案会改，结构不会）。
        """
        result = await self._retry_upload(target_id)
        upload = getattr(result, "upload", None)
        subtitle_state = getattr(upload, "subtitle_state", "") or ""
        subtitle_message = getattr(upload, "subtitle_message", "") or ""
        message = getattr(result, "message", "") or ""
        if subtitle_state == "posted":
            return RetryOutcome(succeeded=True, waiting=False, message="字幕已自动补传")
        with Session(self.engine) as session:
            from ..contracts.models import BiliUpload

            row = session.get(BiliUpload, target_id)
            cid = getattr(row, "cid", None) if row is not None else None
        if not cid:
            # 平台还没给分P编号 ⇒ 还在审核。**不是失败**。
            return RetryOutcome(
                succeeded=False,
                waiting=True,
                message=subtitle_message or "平台还没给出分P编号，字幕暂时传不了",
                rate_limited=False,
            )
        return RetryOutcome(
            succeeded=False,
            waiting=False,
            message=subtitle_message or message or "字幕这次没传上去",
            rate_limited=is_rate_limited(f"{subtitle_message} {message}"),
        )
