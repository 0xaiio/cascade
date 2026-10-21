"""任务产物的本机操作：播放、打开所在文件夹、打开字幕（任务级与条目级各两个）。

这些路由是**唯一的「应用去碰本机文件」入口**：路径全部由后端从数据库记录推导，
不接受前端传路径（安全审计里有这一条）。产物定位在 `job_artifacts.py`，本文件只定状态码。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Response

from .. import api_support
from ..api_context import ApiContext, ContextDep, SessionDep
from ..job_artifacts import job_folder, open_local_path, open_video_path, output_file, output_folder, subtitle_file

router = APIRouter()

# 打开字幕的两种方式，**都不是我们自己起进程**：
#   chooser = 弹系统的「打开方式」窗口，让用户当场挑（并可以让系统记住）—— 界面只有这条路；
#   default = 交给系统关联（用户在系统里选过一次，之后一直用它），留给脚本/接口调用方。
# 两个取值就是系统那边的两种 verb，`mode` 只是把选择权暴露给接口。
SubtitleOpenMode = Literal["default", "chooser"]


def _subtitle_opener(ctx: ApiContext, mode: SubtitleOpenMode) -> Callable[[Path], None]:
    return ctx.open_with_chooser if mode == "chooser" else ctx.open_local_path


@router.post("/api/jobs/{job_id}/play", status_code=204)
async def play_job_video(job_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    """播放单视频任务的成品。合集任务没有「唯一产物」→ 409（`single_job_item`）。"""
    job = api_support.require_job(session, job_id)
    item = api_support.single_job_item(session, job)
    await open_video_path(ctx.open_video_path, output_file(item, job.download_dir), item.actual_format)
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/open-folder", status_code=204)
async def open_job_video_folder(job_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    job = api_support.require_job(session, job_id)
    await open_local_path(ctx.open_local_path, job_folder(session, job))
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/open-subtitle", status_code=204)
async def open_job_subtitle(
    job_id: str,
    session: SessionDep,
    ctx: ContextDep,
    mode: SubtitleOpenMode = "chooser",
) -> Response:
    """用本机的程序打开单视频任务下载到的字幕文件。

    缺省就是 `chooser`：任务中心那颗按钮只有「当场挑一个程序」一个动作，接口缺省必须
    和它一致，否则「不带参数的调用」与「界面上的点击」会是两件不同的事。
    两种模式都**不在后端解析「用什么程序」**，见 `system_open.open_path_with_chooser`。
    """
    job = api_support.require_job(session, job_id)
    item = api_support.single_job_item(session, job)
    await open_local_path(_subtitle_opener(ctx, mode), subtitle_file(item, job.download_dir))
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/items/{item_id}/play", status_code=204)
async def play_job_item_video(job_id: str, item_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    job = api_support.require_job(session, job_id)
    item = api_support.require_job_item(session, job_id, item_id)
    await open_video_path(ctx.open_video_path, output_file(item, job.download_dir), item.actual_format)
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/items/{item_id}/open-folder", status_code=204)
async def open_job_item_video_folder(job_id: str, item_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    job = api_support.require_job(session, job_id)
    item = api_support.require_job_item(session, job_id, item_id)
    await open_local_path(ctx.open_local_path, output_folder(item, job.download_dir))
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/items/{item_id}/open-subtitle", status_code=204)
async def open_job_item_subtitle(
    job_id: str,
    item_id: str,
    session: SessionDep,
    ctx: ContextDep,
    mode: SubtitleOpenMode = "chooser",
) -> Response:
    job = api_support.require_job(session, job_id)
    item = api_support.require_job_item(session, job_id, item_id)
    await open_local_path(_subtitle_opener(ctx, mode), subtitle_file(item, job.download_dir))
    return Response(status_code=204)
