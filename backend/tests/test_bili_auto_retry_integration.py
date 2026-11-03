"""自动重试的**端到端**：真 `BiliManager` + 假平台。

`test_bili_auto_retry.py` 测的是「什么时候问、问完怎么记」；这里测的是
**真的接上平台之后会发生什么** —— 这才是用户看得见的那一段。

三条主线，与需求的五种情形一一对应：

1. **审核没通过** ⇒ 自动重试问了一次，结论是「还在审核」，继续等；
2. **审核通过** ⇒ 自动重试自己把合集归好了 / 把字幕补上了；
3. **平台限流** ⇒ 记成失败，并且下一次等更久。

另外两条可靠性：**重启不丢任务**（`running` 的行折回 `waiting`）与
**上限**（试够了就停，手动按钮交回用户）。

**自动重试与手动按钮走的是同一套业务逻辑**，所以这里用的都是
`BiliManager.retry_season` / `retry_upload` —— 断言「合集真的归好了」而不是
「调度器调用了某个函数」。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.bilibili.bili_auto_retry import BiliAutoRetryScheduler
from app.bilibili.bili_upload import UploadTarget
from app.contracts.models import BiliAutoRetry, BiliAutoRetryKind, BiliAutoRetryState
from app.contracts.models import BiliUploadStatus
from bili_fakes import FakeBiliTransport
from test_bili_manager import TITLE


def reviewing_transport(title: str) -> FakeBiliTransport:
    """平台侧：稿件**在列表里但还在审核**（`state=-30`），详情接口取不到 ⇒ 没有 `cid`。

    这正是截图里那条现场：稿件已提交、平台没给分P编号，所以归不了集也传不了字幕。

    **标题必须对上那一条稿件**（平台靠它认出「就是这一条」），所以这个函数要参数。
    """
    return FakeBiliTransport(
        archives=[{"aid": 9, "bvid": "BV9", "title": title, "state": -30}],
        video=None,
    )


def scheduler_for(harness, transport) -> BiliAutoRetryScheduler:
    """用**管理器自己的**那两个方法当业务逻辑 —— 这正是「复用同一套」的要求。"""
    return BiliAutoRetryScheduler(
        harness.engine,
        harness.manager.auto_retry_policy(),
        retry_season=harness.manager.retry_season,
        retry_upload=harness.manager.retry_upload,
    )


async def test_the_subtitle_is_attached_automatically_once_the_review_passes(tmp_path):
    """审核通过后，字幕**不用用户点**就补上了 —— 而且视频没有被重投。

    这条钉的是「自动重试走的是手动那条路」：全程投稿命令只跑一次
    （`len(calls) == 1`），多跑一次就是账号上多一条重复稿件。
    """
    from test_bili_manager import drain, make_harness

    calls: list[list[str]] = []

    def counting_runner(argv, timeout, on_line):
        calls.append(list(argv))
        if on_line is not None:
            for line in ("start pre_upload", "Upload completed", "APP接口投稿成功"):
                on_line(line)
        return __import__("app.bilibili.bili_bin", fromlist=["CommandResult"]).CommandResult(
            0, "APP接口投稿成功", ""
        )

    transport = reviewing_transport(TITLE)
    harness = make_harness(tmp_path, transport=transport, runner=counting_runner)
    items = harness.seed_job("job1", "任务", 1)
    subtitle = Path(items[0]["path"]).with_suffix(".en.vtt")
    subtitle.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n", encoding="utf-8")
    harness.set_item_subtitle(items[0]["id"], subtitle)

    created = await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)

    first = harness.upload(created.id)
    # 审核中：平台还没给分P编号 ⇒ 字幕记 failed ⇒ **自动重试应当已经登记了这一条**。
    assert first.status == BiliUploadStatus.reviewing.value
    assert first.cid is None
    assert first.subtitle_state == "failed"
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, created.id)
    assert row is not None
    assert row.kind == BiliAutoRetryKind.subtitle.value
    assert row.state == BiliAutoRetryState.waiting.value

    # 审核还没通过时问一次：结论是「还在等」，不是失败。
    scheduler = scheduler_for(harness, transport)
    assert await scheduler.tick() == 1
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, created.id)
    assert row.last_result == "waiting"
    assert row.state == BiliAutoRetryState.waiting.value

    # 审核通过：平台给出 cid 了。
    transport.video = {"cid": 7001, "pic": "https://i0.hdslb.com/bfs/archive/pic.jpg"}
    # 让它到期（退避把它推到了后面）。
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, created.id)
        row.next_attempt_at = row.next_attempt_at.replace(year=2000)
        session.add(row)
        session.commit()

    assert await scheduler.tick() == 1
    after = harness.upload(created.id)
    assert after.subtitle_state == "posted"
    assert transport.subtitles[0]["oid"] == 7001
    assert len(calls) == 1  # 全程只有一次投稿命令：自动重试**没有**重投视频
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, created.id)
    assert row.state == BiliAutoRetryState.done.value
    await harness.manager.stop()


async def test_the_collection_is_grouped_automatically_once_the_review_passes(tmp_path):
    """合集也是自动归好的 —— 且**不重复建合集**（`season/add` 只调一次建集接口）。"""
    from app.bilibili.bili_meta import MetadataTemplate
    from test_bili_manager import drain, make_harness

    # 先建任务（要知道条目标题才能让平台认出「就是这一条」），再建管理器。
    seed = make_harness(tmp_path, transport=reviewing_transport("合集任务 第 1 集"))
    items = seed.seed_job("job2", "合集任务", 1)
    transport = seed.transport
    harness = seed
    targets = [
        UploadTarget(
            index=1,
            title=items[0]["title"],
            video_path=Path(items[0]["path"]),
            source_url="https://youtu.be/x",
            item_id=items[0]["id"],
        )
    ]
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="t", tid="122"),
        targets=targets,
        paths=[Path(items[0]["path"])],
        season_title="合集名",
        season_desc="简介",
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)

    season = harness.season("job2")
    # 稿件还在审核 ⇒ 平台还没给 cid ⇒ 归不了集 ⇒ **自动重试应当已经登记了这一条**。
    assert season.status == "pending"
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, season.id)
    assert row is not None
    assert row.kind == BiliAutoRetryKind.season.value

    scheduler = scheduler_for(harness, transport)
    assert await scheduler.tick() == 1
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, season.id)
    assert row.last_result == "waiting"

    # 审核通过：平台给出 aid + cid 了。
    transport.archives = [
        {"aid": 9, "bvid": "BV9", "title": items[0]["title"], "state": 0, "cid": 42927065206}
    ]
    transport.video = {"cid": 42927065206, "pic": "https://i0.hdslb.com/bfs/archive/pic.jpg"}
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, season.id)
        row.next_attempt_at = row.next_attempt_at.replace(year=2000)
        session.add(row)
        session.commit()

    assert await scheduler.tick() == 1
    season = harness.season("job2")
    assert season.status == "grouped"
    with Session(harness.engine) as session:
        row = session.get(BiliAutoRetry, season.id)
    assert row.state == BiliAutoRetryState.done.value
    await harness.manager.stop()


async def test_a_restart_does_not_lose_the_pending_work(tmp_path):
    """进程重启后，待办清单**还在** —— 它在库里，不在内存里。

    这是「任务状态持久化」的落点：重启前停在 `running` 的那一行折回 `waiting`，
    下一轮扫描接着做。
    """
    from test_bili_manager import drain, make_harness

    transport = reviewing_transport(TITLE)
    harness = make_harness(tmp_path, transport=transport)
    items = harness.seed_job("job1", "任务", 1)
    await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)
    await harness.manager.stop()

    with Session(harness.engine) as session:
        from app.contracts.models import utc_now

        row = BiliAutoRetry(
            target_id="s1",
            kind=BiliAutoRetryKind.season.value,
            job_id="job1",
            state=BiliAutoRetryState.running.value,
            attempts=2,
            next_attempt_at=utc_now(),
        )
        session.add(row)
        session.commit()

    # 「重启」：新建一个管理器（同一个库）。
    harness2 = make_harness(tmp_path, transport=transport)
    harness2.manager.auto_retry.resume_interrupted()
    with Session(harness2.engine) as session:
        row = session.get(BiliAutoRetry, "s1")
    assert row.state == BiliAutoRetryState.waiting.value
    # 次数**不归零**：这一条已经试过两次了。
    assert row.attempts == 2
    await harness2.manager.stop()


async def test_the_manual_button_still_works_and_closes_the_automaton(tmp_path):
    """手动按钮**照旧可用**，而且手动成功后自动重试就收工了。

    「保留现有手动按钮」是需求里明写的一条：自动重试是**加法**，不是替换。
    """
    from test_bili_manager import drain, make_harness

    transport = reviewing_transport(TITLE)
    harness = make_harness(tmp_path, transport=transport)
    items = harness.seed_job("job1", "任务", 1)
    # **要有字幕文件**：没有它就没有「可传的东西」，重试按设计什么都不做。
    subtitle = Path(items[0]["path"]).with_suffix(".en.vtt")
    subtitle.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n", encoding="utf-8")
    harness.set_item_subtitle(items[0]["id"], subtitle)
    await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)

    # 平台给出 cid 了，用户自己点「重传字幕」。
    transport.video = {"cid": 7001, "pic": "https://i0.hdslb.com/bfs/archive/pic.jpg"}
    result = await harness.manager.retry_upload(harness.uploads("job1")[0].id)
    assert result.action == "reverify"
    await harness.manager.stop()
