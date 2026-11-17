"""字幕翻译的 HTTP 入口。路由只做取参、调编排、定状态码；
翻译规则在 `translation/align`，请求在 `translation/local`，文件在 `translation/store`。

后台执行用 `asyncio.to_thread`：模型请求是阻塞 IO，放在事件循环里会把整个 API 卡住。
SSE 进度由服务通过 EventBroker 扇出，前端收到事件后只重拉这一条任务。
"""

from __future__ import annotations

import asyncio

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import Session, select

from ..api_context import ContextDep
from ...bilibili.bili_manager import BiliPreconditionError
from ...contracts.models import BiliUpload, BiliUploadStatus
from ...contracts.schemas import BiliUploadRead
from ...contracts.schemas import (
    TranslationCreateRequest,
    TranslationEditRequest,
    TranslationExportRead,
    TranslationModeRequest,
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


@router.patch("/api/translation/jobs/{translation_id}/mode", response_model=TranslationRead)
def set_translation_mode(
    translation_id: str, request: TranslationModeRequest, ctx: ContextDep
) -> TranslationRead:
    """切换呈现模式；不重译、不改时间轴。"""
    return ctx.translations.set_display_mode(translation_id, request.mode)


@router.post("/api/translation/jobs/{translation_id}/export", response_model=TranslationExportRead)
def export_translation(translation_id: str, ctx: ContextDep) -> TranslationExportRead:
    return ctx.translations.export(translation_id)


@router.post(
    "/api/translation/jobs/{translation_id}/upload-bili",
    response_model=BiliUploadRead,
)
def upload_translated_subtitle(translation_id: str, ctx: ContextDep) -> BiliUploadRead:
    """把这条译文作为 `zh` CC 轨补挂到**已经投稿成功**的稿件上。

    **绝不重投视频**：服务端只走「只补字幕」那条路，稿件状态与 `aid` / `bvid` 不变。
    """
    job = ctx.translations.get(translation_id)
    if job.state != "succeeded" or not job.output_path:
        raise HTTPException(status_code=409, detail="这条字幕还没翻译导出完成，不能上传")
    path = Path(job.output_path)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="译文字幕文件已不在磁盘上")

    engine = ctx.translations.engine
    with Session(engine) as session:
        accepted = {BiliUploadStatus.published.value, BiliUploadStatus.reviewing.value}
        query = select(BiliUpload).where(BiliUpload.status.in_(accepted))
        query = query.where(BiliUpload.job_id == job.job_id)
        if job.item_id:
            query = query.where(BiliUpload.item_id == job.item_id)
        upload = session.exec(query.order_by(BiliUpload.created_at.desc())).first()
        if upload is None:
            raise HTTPException(status_code=409, detail="找不到已投稿成功的对应稿件")
        upload_id = upload.id
    try:
        result = ctx.bili.attach_translation(upload_id, path)
    except BiliPreconditionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if job.display_mode == "bilingual" and result.translated_subtitle_state == "posted":
        # 明确告诉用户这是英上中下的同屏双语，且同一 lan 是覆盖而不是新增轨。
        return result.model_copy(
            update={
                "translated_subtitle_message": (
                    f"中英双语（英上中下，两行共用同一时间轴）：{result.translated_subtitle_message}；"
                    "同一语言轨再传是覆盖，不会多一条轨"
                )
            }
        )
    return result


@router.delete("/api/translation/jobs/{translation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_translation(translation_id: str, ctx: ContextDep) -> Response:
    ctx.translations.delete(translation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
