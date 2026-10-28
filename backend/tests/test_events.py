"""进程内事件扇出（`EventBroker`）与 SSE 路由的装配。

这个模块此前只有 61% 覆盖，未覆盖的正是它的**全部生命周期**（订阅 / 取消订阅）。
它错了的症状很安静：前端收不到事件就不会重拉 `/api/jobs`，界面停在旧数据上；
或者更糟 —— 取消订阅没清干净，一个关掉的标签页会一直留在集合里。

**异步生成器的注册与 `publish` 之间有竞态**：`anext()` 返回的瞬间只是「任务已排队」，
真正 `self._listeners.add(queue)` 要等它被调度到。所以下面统一用 `wait_until_subscribed()`
先确认注册完成再发消息 —— 否则用例会以「等一条永远不来的消息」的形式挂住，而不是报错。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.events import EventBroker
from app.routers.events import events

TIMEOUT = 2.0


async def wait_until_subscribed(broker: EventBroker, expected: int) -> None:
    for _ in range(200):
        if len(broker._listeners) >= expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"订阅者没有在预期时间内注册（期望 {expected} 个，实际 {len(broker._listeners)}）")


async def open_stream(broker: EventBroker, expected_listeners: int):
    """订阅并等到**真的注册好**，返回一个「取一条消息」的 awaitable。"""
    stream = broker.subscribe()
    task = asyncio.create_task(anext(stream))
    await wait_until_subscribed(broker, expected_listeners)
    return stream, task


async def test_every_subscriber_receives_every_message() -> None:
    broker = EventBroker()
    first, first_task = await open_stream(broker, 1)
    second, second_task = await open_stream(broker, 2)

    await broker.publish({"type": "job_started", "job_id": "job-1"})

    expected = {"type": "job_started", "job_id": "job-1"}
    assert json.loads(await asyncio.wait_for(first_task, TIMEOUT)) == expected
    assert json.loads(await asyncio.wait_for(second_task, TIMEOUT)) == expected

    await first.aclose()
    await second.aclose()


async def test_values_that_json_cannot_encode_are_stringified_instead_of_raising() -> None:
    """事件里带 `datetime` / `Path` 是常事；序列化失败会让**整条推送**丢掉。"""
    broker = EventBroker()
    stream, task = await open_stream(broker, 1)

    await broker.publish({"type": "x", "at": datetime(2026, 5, 15, 10, 0, tzinfo=UTC), "path": Path("a/b.mp4")})
    payload = json.loads(await asyncio.wait_for(task, TIMEOUT))

    assert payload["at"].startswith("2026-05-15 10:00")
    # `Path` 的平台分隔符因机器而异，这里只断言「被转成了字符串且仍是那个文件名」。
    assert payload["path"].endswith("b.mp4")

    await stream.aclose()


async def test_a_closed_stream_is_removed_from_the_listener_set() -> None:
    """关掉标签页 = 断开 SSE = 生成器被关闭。留着它就是一个永远没人读的队列。"""
    broker = EventBroker()
    stream, task = await open_stream(broker, 1)
    await broker.publish({"type": "ping"})
    await asyncio.wait_for(task, TIMEOUT)

    await stream.aclose()

    assert broker._listeners == set()
    # 关掉之后再发也不能炸（遍历的是快照）。
    await broker.publish({"type": "after-close"})


async def test_a_slow_subscriber_does_not_block_the_others() -> None:
    """队列是无界的，`put_nowait` 不会阻塞 —— 否则一个卡住的标签页能拖停所有推送。"""
    broker = EventBroker()
    stalled, _ = await open_stream(broker, 1)
    healthy, healthy_task = await open_stream(broker, 2)

    for index in range(50):
        await broker.publish({"type": "tick", "index": index})

    assert json.loads(await asyncio.wait_for(healthy_task, TIMEOUT))["index"] == 0
    await stalled.aclose()
    await healthy.aclose()


async def test_the_sse_route_frames_each_message_as_a_data_line() -> None:
    """`routers/events.py` 只做一件事：把 payload 包成 `data: ...\\n\\n`。包错了前端解析不出。"""
    broker = EventBroker()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(event_broker=broker)))

    response = await events(request)
    assert response.media_type == "text/event-stream"

    body = response.body_iterator
    task = asyncio.create_task(anext(body))
    await wait_until_subscribed(broker, 1)
    await broker.publish({"type": "item_finished", "item_id": "i-1"})

    chunk = await asyncio.wait_for(task, TIMEOUT)
    assert chunk == 'data: {"type": "item_finished", "item_id": "i-1"}\n\n'

    await body.aclose()
    assert broker._listeners == set()
