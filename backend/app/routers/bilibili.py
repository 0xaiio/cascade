"""B站 上传相关路由。

L0 入口层：**取参 → 调辅助 → 定状态码**（design §3）。这里的每一段判断都必须是
「请求本身合不合法」或「现在能不能做」，而不是领域规则 —— 领域规则在 `bili_upload`，
状态收敛在 `bili_manager`，界面状态在 `bili_read_model`。

状态码口径（与既有 `cookie_import_status_code` 一致）：
**409 = 前置条件不满足**（没装工具、没登录、视频还没下载好、正在上传中），
**400 = 请求本身不对**（元数据超限、只有一个视频却走合集流程），**404 = 记录不存在**。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlmodel import Session, select

from .. import api_support
from ..api_context import ContextDep, SessionDep
from ..bili_manager import BiliManager, BiliPreconditionError, build_prefill
from ..bili_meta import (
    DEFAULT_SINGLE_GROUP_MODE,
    MetadataTemplate,
    UploadMetadata,
    normalize_group_mode,
    validate as validate_metadata,
    validate_comment,
    validate_group_choice,
)
from ..bili_read_model import read_all_job_uploads
from ..bili_upload import UploadTarget, validate_collection
from ..job_artifacts import output_file
from ..models import JobItem
from ..output_paths import existing_output_file
from ..schemas import (
    BiliCollectionCreate,
    BiliJobUploadsRead,
    BiliJobUploadsSummaryRead,
    BiliLoginPollRead,
    BiliMetadata,
    BiliPrefillRead,
    BiliQrcodeRead,
    BiliRetryRead,
    BiliSeasonListRead,
    BiliSeasonRetryRead,
    BiliStatusRead,
    BiliUploadCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _manager(ctx: ContextDep) -> BiliManager:
    return ctx.bili


def _not_ready(exc: BiliPreconditionError) -> HTTPException:
    return HTTPException(status_code=409, detail=exc.to_detail())


@router.get("/api/bilibili/status", response_model=BiliStatusRead)
async def bili_status(ctx: ContextDep, probe_account: bool = Query(default=True)) -> BiliStatusRead:
    """环境体检：投稿工具 / ffmpeg / 登录态 / 缺项清单。

    `probe_account=false` 只做离线体检（不请求 B站）—— 没有网络的环境里也能看到「工具装没装、文件在不在」。
    """
    return await _manager(ctx).health(probe_account=probe_account)


@router.post("/api/bilibili/tools/repair", response_model=BiliStatusRead)
async def bili_repair_tool(ctx: ContextDep) -> BiliStatusRead:
    """修复 WinGet `Links` 目录下的 0 字节 `biliup.exe` 空壳，然后重新体检。

    单独一个入口的理由见 `bili_bin`：那种空壳会让 `shutil.which` 找得到、`CreateProcess` 却拒绝执行，
    报错（「不是此 OS 平台的有效应用」）与真实病因毫无关系，用户按字面去重装也修不好。
    """
    message = await asyncio.to_thread(_manager(ctx).repair_tool)
    logger.info("bili 工具修复：%s", message)
    return await _manager(ctx).health(probe_account=False)


@router.post("/api/bilibili/login/qrcode", response_model=BiliQrcodeRead)
async def bili_login_qrcode(ctx: ContextDep) -> BiliQrcodeRead:
    """生成登录二维码。返回的是 **SVG 文本**（内联到页面里），不是位图。"""
    session = _manager(ctx).login_session()
    try:
        challenge = await asyncio.to_thread(session.generate)
    except Exception as exc:  # noqa: BLE001 - 网络失败要变成可读结论，不是 500
        raise HTTPException(status_code=502, detail=f"没能从 B站 取到登录二维码：{exc}") from exc
    return BiliQrcodeRead(
        qrcode_key=challenge.qrcode_key,
        url=challenge.url,
        svg=challenge.svg,
        note=challenge.note,
    )


@router.get("/api/bilibili/login/poll", response_model=BiliLoginPollRead)
async def bili_login_poll(ctx: ContextDep, qrcode_key: str = Query(min_length=1)) -> BiliLoginPollRead:
    """轮询扫码状态。**只有 `confirmed` 才会落盘登录态**，其余状态原样回报给界面。"""
    manager = _manager(ctx)
    session = manager.login_session()
    try:
        result = await asyncio.to_thread(session.poll, qrcode_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"查询扫码状态失败：{exc}") from exc
    if result.state != "confirmed" or not result.jar:
        return BiliLoginPollRead(state=result.state, message=result.message)

    mid = int(result.jar["DedeUserID"]) if str(result.jar.get("DedeUserID", "")).isdigit() else None
    warning = await asyncio.to_thread(manager.save_login, result.jar, mid)
    account = await asyncio.to_thread(manager.account)
    return BiliLoginPollRead(
        state="confirmed",
        # 换不到令牌要说出来：那次登录对「读稿件状态」够用，对「投稿」不够用，
        # 而两者在界面上只差一个词，事后用户根本分不清哪一步没成。
        message=result.message if not warning else f"{result.message}，但没能取到投稿要用的登录令牌：{warning}",
        logged_in=account is not None,
        account=account,
    )


@router.post("/api/bilibili/cookies", response_model=BiliStatusRead)
async def bili_import_cookies(ctx: ContextDep, file: UploadFile = File(...)) -> BiliStatusRead:
    """导入一份已有的登录文件。

    **这里不校验格式**（与 `/api/cookies` 同一条口径）：先落盘，再由返回的体检结论说清楚
    「读出来了什么、缺哪项」—— 上传时拒收会让用户卡在「传不上去、也验不了」。
    """
    content = await file.read()
    manager = _manager(ctx)
    path = ctx.settings.bili_cookie_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    # 换了登录态，之前那次扫码会话就作废了。
    manager.reset_login_session()
    # 顺手补齐投稿令牌。只导入一份 cookies 的文件在 biliup 眼里是**不能投稿**的 —— 它缺的正是
    # 那次「用 cookies 换 TV 令牌」的交换。放在这里而不是让用户去按某个按钮：导入一份能用的
    # 登录文件本来就该是一次动作。文件已经有令牌、或者读不出内容时它什么都不做。
    await asyncio.to_thread(manager.ensure_upload_token)
    return await manager.health()


@router.delete("/api/bilibili/cookies", response_model=BiliStatusRead)
async def bili_clear_cookies(ctx: ContextDep) -> BiliStatusRead:
    """清除登录态。**幂等**：本来就没有也返回当前体检结论，而不是 404。"""
    manager = _manager(ctx)
    await asyncio.to_thread(manager.clear_login)
    return await manager.health(probe_account=False)


@router.get("/api/bilibili/prefill", response_model=BiliPrefillRead)
def bili_prefill(session: SessionDep, ctx: ContextDep, job_id: str, item_id: str | None = None) -> BiliPrefillRead:
    """预填表单：单视频给一组值，合集给「模板 + 逐条预览」。

    返回里带 `problems`：**提交前**就把超限定位到具体某一条（design §7）。
    """
    job = api_support.require_job(session, job_id)
    return build_prefill(session, job, item_id)


@router.get("/api/bilibili/uploads/summary", response_model=BiliJobUploadsSummaryRead)
def bili_all_job_uploads(session: SessionDep) -> BiliJobUploadsSummaryRead:
    """**所有有 B站 记录的任务**的徽标汇总。

    任务中心的每一行都有 B站 图标，而 SSE 每来一条事件前端就会重拉一次；
    逐个任务去问就是「N 个任务 = N 个请求」。没有记录的任务不在这里 —— 默认就是未上传。
    """
    return read_all_job_uploads(session)


@router.get("/api/bilibili/uploads", response_model=BiliJobUploadsRead)
def bili_job_uploads(session: SessionDep, ctx: ContextDep, job_id: str) -> BiliJobUploadsRead:
    """按任务读回上传状态（任务级徽标 + 条目级明细 + 合集记录）。"""
    api_support.require_job(session, job_id)
    return _manager(ctx).job_uploads(session, job_id)


@router.post("/api/bilibili/uploads", response_model=BiliJobUploadsRead)
async def bili_create_upload(
    payload: BiliUploadCreate, session: SessionDep, ctx: ContextDep
) -> BiliJobUploadsRead:
    """启动单视频上传。返回**整个任务**的上传状态（界面只有一个图标，必须一次拿到全貌）。

    单视频也带「归集方式」（默认**不归集**，与改动前的行为一致）：
    投稿时就能决定这条视频要不要进某个合集，不必先绕去下载一整个 playlist。
    """
    job = api_support.require_job(session, payload.job_id)
    item = (
        api_support.require_job_item(session, job.id, payload.item_id)
        if payload.item_id
        else api_support.single_job_item(session, job)
    )
    meta = _meta_from(payload.metadata)
    # 归集决策先于元数据校验报出来，理由与合集那边一致：用户更可能是「刚改了归集方式
    # 但没选具体合集」，而不是「标题超了 3 个字」。**用原始输入校验，不用归一后的值** ——
    # 先修再校验等于把校验废掉（非法值已被修成合法值，校验永远看不到它）。
    group_problems = validate_group_choice(
        group_mode=payload.group_mode,
        season_id=payload.season_id,
        season_title=payload.season_title,
        count=1,
    )
    if group_problems:
        raise HTTPException(status_code=400, detail=group_problems[0])
    _reject_problems(validate_metadata(meta) + validate_comment(payload.comment))
    mode = normalize_group_mode(payload.group_mode, fallback=DEFAULT_SINGLE_GROUP_MODE)
    video_path = output_file(item, job.download_dir)
    try:
        await _manager(ctx).submit_single(
            job_id=job.id,
            item_id=item.id,
            meta=meta,
            video_path=video_path,
            make_cover=payload.make_cover,
            comment=payload.comment,
            group_mode=mode,
            season_id=payload.season_id,
            # 与合集那边同一个兜底口径：留空用任务名。单视频的任务名就是视频标题。
            season_title=(payload.season_title or job.title).strip(),
        )
    except BiliPreconditionError as exc:
        raise _not_ready(exc) from exc
    return _manager(ctx).job_uploads(session, job.id)


@router.get("/api/bilibili/seasons", response_model=BiliSeasonListRead)
async def bili_list_seasons(ctx: ContextDep) -> BiliSeasonListRead:
    """账号下的已有合集，供投稿界面下拉选择。

    **未登录也返回 200**（只是列表为空并带上原因）：这是界面上一个可选功能的输入，
    不是一次必须成功的动作。用 401/409 逼前端处理异常分支，只会让「想归集但没登录」
    这种最常见的情况显示成一句报错，而不是引导去登录。
    """
    return await _manager(ctx).account_seasons()


@router.post("/api/bilibili/collections", response_model=BiliJobUploadsRead)
async def bili_create_collection(
    payload: BiliCollectionCreate, session: SessionDep, ctx: ContextDep
) -> BiliJobUploadsRead:
    """启动合集上传：按模板逐条投稿，全部跑完后自动归集。"""
    job = api_support.require_job(session, payload.job_id)
    items = _job_items(session, job.id)
    if len(items) < 2:
        raise HTTPException(
            status_code=400,
            detail="这个任务只有 1 个视频，请用单视频上传（合集至少需要 2 个视频）",
        )
    targets, paths = _targets(items, job.download_dir)
    template = MetadataTemplate(
        title_template=payload.template.title,
        desc_template=payload.template.desc,
        tags=payload.template.tags,
        tid=payload.template.tid,
        # 合集模板上没有共用来源：逐条渲染时各取该条视频自己的原始链接
        # （`render_collection` 传 `target.source_url`），所以这里刻意不接 `payload.template.source`。
        copyright=payload.template.copyright,
    )
    problems = validate_collection(template, targets, job_title=job.title)
    # 归集决策先于合集本身的校验报出来：用户可能刚把「新建」改成「加入已有」
    # 却忘了选具体是哪个，这时候先报「没选合集」比先报第 3 条标题超限有用。
    #
    # **校验用原始输入而不是 `normalize_group_mode` 的结果**：先把非法值修成 `new`，
    # 下面的校验就永远看不到它，一个手滑写坏的 `group_mode` 会被静默当成「新建合集」。
    # 归一只用于**通过校验之后**真正执行的那一步。
    group_problems = validate_group_choice(
        group_mode=payload.group_mode,
        season_id=payload.season_id,
        season_title=payload.season_title,
        count=len(targets),
    )
    if group_problems:
        raise HTTPException(status_code=400, detail=group_problems[0])
    mode = normalize_group_mode(payload.group_mode)
    _reject_problems(problems + validate_comment(payload.comment))
    season_title = (payload.season_title or job.title).strip()
    try:
        await _manager(ctx).submit_collection(
            job_id=job.id,
            template=template,
            targets=targets,
            paths=paths,
            season_title=season_title,
            season_desc=payload.season_desc,
            make_cover=payload.make_cover,
            comment=payload.comment,
            group_mode=mode,
            season_id=payload.season_id,
        )
    except BiliPreconditionError as exc:
        raise _not_ready(exc) from exc
    return _manager(ctx).job_uploads(session, job.id)


@router.post("/api/bilibili/uploads/{upload_id}/retry", response_model=BiliRetryRead)
async def bili_retry_upload(upload_id: str, ctx: ContextDep) -> BiliRetryRead:
    """重试一条稿件。**三种终态三种动作**（重新投稿 / 只重新核对 / 什么都不做），见 `bili_manager`。"""
    try:
        return await _manager(ctx).retry_upload(upload_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到这条上传记录") from exc


@router.post("/api/bilibili/collections/{season_record_id}/group", response_model=BiliSeasonRetryRead)
async def bili_retry_season(season_record_id: str, ctx: ContextDep) -> BiliSeasonRetryRead:
    """重试合集归集。**不会重复建合集**：已有 `season_id` 时只补加视频。"""
    try:
        return await _manager(ctx).retry_season(season_record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到这个合集记录") from exc


# ---------------------------------------------------------------- 内部工具
def _meta_from(payload: BiliMetadata) -> UploadMetadata:
    return UploadMetadata(
        title=payload.title,
        desc=payload.desc,
        tags=payload.tags,
        tid=payload.tid,
        copyright=payload.copyright,
        source=payload.source,
    )


def _reject_problems(problems: list[str]) -> None:
    if problems:
        raise HTTPException(status_code=400, detail="；".join(problems))


def _job_items(session: Session, job_id: str) -> list[JobItem]:
    return list(
        session.exec(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)).all()
    )


def _targets(items: list[JobItem], download_dir: str | None) -> tuple[list[UploadTarget], list[Path | None]]:
    """把条目映射成上传目标 + 它们的成品路径。

    **路径可能为 `None`**（还没下载好）。刻意不当场抛 409：合集里有几十条时，
    先把「哪几条没准备好」一次说清，比让用户一条条试出来有用得多（`submit_collection` 会给结论）。
    """
    base_dir = Path(download_dir) if download_dir else None
    targets: list[UploadTarget] = []
    paths: list[Path | None] = []
    for item in items:
        path = existing_output_file(item.output_path, item.source_url, base_dir)
        targets.append(
            UploadTarget(
                index=item.index,
                title=item.title,
                video_path=path or Path(""),
                source_url=item.source_url,
                item_id=item.id,
            )
        )
        paths.append(path)
    return targets, paths
