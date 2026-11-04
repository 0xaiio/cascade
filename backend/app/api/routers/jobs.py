"""任务资源：创建、列表、批量操作、单任务生命周期。

任务（`Job`/`JobItem`）是应用的主资源，这一组路由只做三件事：校验存在性、转调 `JobManager`、
把结果投影成读模型。**任务状态机不在这一层** —— 暂停/重启/删除的真实语义见 `job_manager.py`。
"""

from fastapi import APIRouter, Body, HTTPException, Response
from sqlmodel import select

from .. import api_support
from ..api_context import ContextDep, SessionDep
from ...download.job_manager import new_id
from ...download.disk_budget import assess_for_job
from ...contracts.models import Job, JobItem
from ...contracts.schemas import (
    CreateJobRequest,
    DeleteJobItemsRequest,
    DeleteJobItemsResponse,
    JobBatchActionRequest,
    JobBatchActionResponse,
    JobRead,
    RestartJobRequest,
)
router = APIRouter()


def _refuse_if_bili_busy(ctx: ContextDep, job_id: str) -> None:
    """任务还有在途投稿时拒绝删除（409）。

    理由见 `BiliManager.active_upload_message`：投稿跑在另一个 worker 上，
    删掉记录之后它还会把结论写回来 —— 写不进就是一次静默丢失。
    """
    reason = ctx.bili.active_upload_message(job_id)
    if reason:
        raise HTTPException(status_code=409, detail=reason)


@router.post("/api/jobs", response_model=JobRead, status_code=201)
async def create_job(request: CreateJobRequest, session: SessionDep, ctx: ContextDep) -> JobRead:
    """建任务：再解析一次（不信任前端传回的元数据）→ 选条目 → 写 Job + N 个 JobItem → 入队。

    解析失败与 `POST /api/analyze` 同语义；一条条目都没选出来是 400 而不是建一个空任务。
    """
    try:
        analysis = api_support.extract_metadata_with_cookies(ctx.service, ctx.settings, request.url, True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entries = api_support.selected_entries(request.url, analysis, request.options.playlist_items)
    if not entries:
        raise HTTPException(status_code=400, detail="No downloadable playlist entries were selected.")

    job_id = new_id()
    download_dir = api_support.job_download_dir(ctx.settings.download_dir, analysis, job_id)
    # 建任务前的阻断：这里的 `request.options` 是**本次实际所选**的清晰度，
    # 与解析结果里那条按默认清晰度算的提示不同。写库之前判，拒绝时不留孤儿任务。
    budget = assess_for_job(analysis, request.options, len(entries), download_dir)
    if budget.blocks:
        raise HTTPException(status_code=409, detail=budget.message)
    job = Job(
        id=job_id,
        url=request.url,
        title=analysis.title,
        options_json=request.options.model_dump_json(),
        total_items=len(entries),
        download_dir=str(download_dir),
    )
    session.add(job)
    session.commit()
    for entry in entries:
        session.add(
            JobItem(
                id=new_id(),
                job_id=job.id,
                source_url=entry.url,
                title=entry.title,
                index=entry.index,
                duration=entry.duration,
            )
        )
    session.commit()
    await ctx.manager.enqueue(job.id)
    return api_support.read_job_or_404(session, job.id)


@router.get("/api/jobs", response_model=list[JobRead])
def list_jobs(session: SessionDep) -> list[JobRead]:
    jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()
    return [api_support.read_job_or_404(session, job.id) for job in jobs]


@router.post("/api/jobs/batch", response_model=JobBatchActionResponse)
async def batch_job_action(request: JobBatchActionRequest, session: SessionDep, ctx: ContextDep) -> JobBatchActionResponse:
    """批量暂停/重启/删除。找不到任何目标任务时 404，而不是「成功 0 条」。"""
    affected: list[str] = []
    targets = [job_id for job_id in request.job_ids if session.get(Job, job_id)]
    if request.action == "delete":
        # **先整批预检再动手**：删到一半才因为某一条有在途投稿被拒，前几条已经没了。
        for job_id in targets:
            _refuse_if_bili_busy(ctx, job_id)

    for job_id in targets:
        if request.action == "pause":
            await ctx.manager.pause(job_id)
        elif request.action == "restart":
            await ctx.manager.restart(job_id)
        else:
            await ctx.manager.delete(job_id, delete_files=request.delete_files)
            # 任务行没了，投稿侧的三张表还留着 —— 不清理的话摘要里会一直有它。
            ctx.bili.forget_job(job_id)
        affected.append(job_id)

    if not affected:
        raise HTTPException(status_code=404, detail="No matching jobs found.")

    if request.action == "delete":
        return JobBatchActionResponse(affected_job_ids=affected, jobs=[])

    session.expire_all()
    return JobBatchActionResponse(
        affected_job_ids=affected,
        jobs=[api_support.read_job_or_404(session, job_id) for job_id in affected],
    )


@router.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: SessionDep) -> JobRead:
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/cancel", response_model=JobRead)
async def cancel_job(job_id: str, session: SessionDep, ctx: ContextDep) -> JobRead:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.cancel(job_id)
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/pause", response_model=JobRead)
async def pause_job(job_id: str, session: SessionDep, ctx: ContextDep) -> JobRead:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.pause(job_id)
    session.expire_all()
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/restart", response_model=JobRead)
async def restart_job(
    job_id: str,
    session: SessionDep,
    ctx: ContextDep,
    request: RestartJobRequest | None = Body(default=None),
) -> JobRead:
    """重启任务；请求体里带 `resolution` 表示换清晰度重下（按需覆盖原选项）。"""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.restart(job_id, resolution=request.resolution if request else None)
    session.expire_all()
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/items/{item_id}/restart", response_model=JobRead)
async def restart_job_item(
    job_id: str,
    item_id: str,
    session: SessionDep,
    ctx: ContextDep,
    request: RestartJobRequest | None = Body(default=None),
) -> JobRead:
    if not await ctx.manager.restart_item(job_id, item_id, resolution=request.resolution if request else None):
        raise HTTPException(status_code=404, detail="Job item not found.")
    session.expire_all()
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/items/delete", response_model=DeleteJobItemsResponse)
async def delete_job_items(
    job_id: str,
    request: DeleteJobItemsRequest,
    session: SessionDep,
    ctx: ContextDep,
) -> DeleteJobItemsResponse:
    """删子视频。删掉最后一条时任务本身也会消失，因此响应里要带回 `job_deleted`。"""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    # 删掉最后一条 = 任务本身也会消失，所以「会不会删掉任务」要先算出来再问在途。
    items = list(session.exec(select(JobItem).where(JobItem.job_id == job_id)).all())
    requested = set(request.item_ids)
    will_delete_job = bool(items) and all(item.id in requested for item in items)
    if will_delete_job:
        _refuse_if_bili_busy(ctx, job_id)

    deleted_item_ids, job_deleted = await ctx.manager.delete_items(
        job_id,
        request.item_ids,
        delete_files=request.delete_files,
    )
    if not deleted_item_ids:
        raise HTTPException(status_code=404, detail="Job item not found.")
    if job_deleted:
        ctx.bili.forget_job(job_id)
        return DeleteJobItemsResponse(deleted_item_ids=deleted_item_ids, job_deleted=True, job=None)
    session.expire_all()
    return DeleteJobItemsResponse(
        deleted_item_ids=deleted_item_ids,
        job_deleted=False,
        job=api_support.read_job_or_404(session, job_id),
    )


@router.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, session: SessionDep, ctx: ContextDep, delete_files: bool = False) -> Response:
    """删任务。`delete_files=true` 才会动磁盘上的产物，默认只删记录。"""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    _refuse_if_bili_busy(ctx, job_id)
    await ctx.manager.delete(job_id, delete_files=delete_files)
    ctx.bili.forget_job(job_id)
    return Response(status_code=204)
