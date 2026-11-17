"""字幕翻译的 HTTP 入口。路由只做取参、调编排、定状态码；
翻译规则在 `translation/align`，请求在 `translation/local`，文件在 `translation/store`。

后台执行用 `asyncio.to_thread`：模型请求是阻塞 IO，放在事件循环里会把整个 API 卡住。
SSE 进度由服务通过 EventBroker 扇出，前端收到事件后只重拉这一条任务。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status

from ..api_context import ContextDep
from ...contracts.schemas import (
    TranslationCreateRequest,
    TranslationEditRequest,
    TranslationExportRead,
    TranslationProbeRead,
    TranslationRead,
)
from ...translation.local import LocalTranslationProvider, probe_local_provider
from ...translation.provider import TranslationError

router = APIRouter()

# 保住后台任务的引用；`create_task` 的返回值被 GC 掉会把任务一起取消。
_BACKGROUND: set[asyncio.Task] = set()


def _launch(ctx: ContextDep, translation_id: str, loop: asyncio.AbstractEventLoop) -> None:
    task = asyncio.create_task(asyncio.to_thread(ctx.translations.start, translation_id, loop))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


@router.post("/api/translation/probe", response_model=TranslationProbeRead)
def probe_translation(ctx: ContextDep) -> TranslationProbeRead:
    """用一条最短请求实测本机模型是否可用。不可用时是正常结果（`ok: false`），不是 4xx。"""
    settings = ctx.settings
    provider = LocalTranslationProvider(
        base_url=settings.translation_base_url,
        model=settings.translation_model,
        timeout=settings.translation_timeout_seconds,
    )
    try:
        model = probe_local_provider(provider)
    except TranslationError as exc:
        return TranslationProbeRead(ok=False, message=str(exc), model=getattr(provider, "model", ""))
    return TranslationProbeRead(ok=True, message="本机模型可用", model=model)


@router.post("/api/translation/jobs", response_model=TranslationRead, status_code=status.HTTP_202_ACCEPTED)
async def create_translation(request: TranslationCreateRequest, ctx: ContextDep) -> TranslationRead:
    created = ctx.translations.create(request.job_id, request.item_id)
    if created.state == "queued":
        _launch(ctx, created.id, asyncio.get_running_loop())
    return created


@router.get("/api/translation/jobs/{translation_id}", response_model=TranslationRead)
def get_translation(translation_id: str, ctx: ContextDep) -> TranslationRead:
    return ctx.translations.get(translation_id)


@router.patch("/api/translation/jobs/{translation_id}/cues/{index}", response_model=TranslationRead)
def edit_translation_cue(
    translation_id: str, index: int, request: TranslationEditRequest, ctx: ContextDep
) -> TranslationRead:
    return ctx.translations.edit(translation_id, index, request.text)


@router.post(
    "/api/translation/jobs/{translation_id}/retry",
    response_model=TranslationRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_translation(translation_id: str, ctx: ContextDep) -> TranslationRead:
    retried = ctx.translations.retry(translation_id)
    _launch(ctx, translation_id, asyncio.get_running_loop())
    return retried


@router.post("/api/translation/jobs/{translation_id}/cancel", response_model=TranslationRead)
def cancel_translation(translation_id: str, ctx: ContextDep) -> TranslationRead:
    return ctx.translations.cancel(translation_id)


@router.post("/api/translation/jobs/{translation_id}/export", response_model=TranslationExportRead)
def export_translation(translation_id: str, ctx: ContextDep) -> TranslationExportRead:
    return ctx.translations.export(translation_id)


@router.delete("/api/translation/jobs/{translation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_translation(translation_id: str, ctx: ContextDep) -> Response:
    ctx.translations.delete(translation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
