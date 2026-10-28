"""实时事件流：`GET /api/events`（SSE）。

独立成模块的理由不是行数，而是**传输方式**：它是一条长连接、只写不读、
不参与请求/响应周期。混进任何一个业务 router 都会让那个模块多出一种生命周期。
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ...infra.events import EventBroker

router = APIRouter()


@router.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """订阅任务事件。前端收到任何一条就重新拉一次 `/api/jobs`，因此这里不必做增量语义。"""
    broker: EventBroker = request.app.state.event_broker

    async def event_stream():
        # `async for` **不会**在提前退出时关闭子生成器（PEP 525 的老坑）：客户端一断，
        # `await subscription.aclose()` 才是让 `EventBroker.subscribe()` 的 `finally`
        # 真正执行的唯一保证。少了这一步，订阅者要等到生成器被 GC 才注销 ——
        # 在此之前它那个无界队列会一直被推送，且每断开一个标签页就多一个。见 ai/bug-fix/027。
        subscription = broker.subscribe()
        try:
            async for message in subscription:
                yield f"data: {message}\n\n"
        finally:
            await subscription.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
