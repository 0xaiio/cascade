"""B站 上传的**读模型投影**：把 `bili_upload` / `bili_season` 两张表投影成界面要的形状。

与 [`job_read_model.py`](job_read_model.py) 同层同性质（L2 编排，不写库、不改状态），
拆成两个模块而不是塞在一起，是因为**它们服务于两个不同的界面对象**：
任务读模型驱动「下载」那一行的进度与错误，本模块驱动「B站 图标」的状态。

这里承载本功能唯一一条「多条记录 → 一档状态」的聚合规则（design §5.3）。放在后端有两个理由：

1. 任务行与条目行必须显示**同一个**结论。前端各算一遍，就会出现
   「任务行的 B站 图标说已上传、展开后条目行说失败」这种自相矛盾的界面；
2. 这条规则有真实的分支（在途 / 全成功 / 全失败 / 有成功有失败 / 全待核对），
   纯函数才好被参数化测试逐档覆盖。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlmodel import Session, select

from .bili_meta import copyright_label, partition_label
from .bili_policy import (
    BADGE_FAILED,
    BADGE_NONE,
    BADGE_PARTIAL,
    BADGE_UNVERIFIED,
    BADGE_UPLOADED,
    BADGE_UPLOADING,
    BADGE_TEXT,
    BADGE_TONE,
    season_status_text,
    upload_status_text,
)
from .job_read_model import elapsed_seconds
from .models import BiliSeason, BiliSeasonStatus, BiliUpload, BiliUploadStatus, JobItem
from .schemas import (
    BiliJobUploadsRead,
    BiliJobUploadsSummaryRead,
    BiliSeasonRead,
    BiliUploadRead,
)

VIDEO_URL_TEMPLATE = "https://www.bilibili.com/video/{bvid}"

# 「还在跑」的稿件状态。含 `submitted`：那是「命令跑完、正在对账」的窗口，对外仍是进行中。
IN_FLIGHT_STATUSES = {
    BiliUploadStatus.queued.value,
    BiliUploadStatus.uploading.value,
    BiliUploadStatus.submitted.value,
}
ACCEPTED_STATUSES = {BiliUploadStatus.published.value, BiliUploadStatus.reviewing.value}
REJECTED_STATUSES = {BiliUploadStatus.rejected.value, BiliUploadStatus.failed.value}


def bilibili_video_url(bvid: str | None) -> str | None:
    return VIDEO_URL_TEMPLATE.format(bvid=bvid) if bvid else None


def derive_badge(uploads: Sequence[BiliUpload], season: BiliSeason | None) -> str:
    """把一组稿件 + 合集折成界面上的那一档状态。

    判定**按顺序短路**，顺序本身是有意义的：只要还有一条在跑，结论就是「上传中」——
    一个任务 8 条成功 2 条在跑时说「已上传」会让用户关掉页面。
    """
    statuses = [item.status for item in uploads]
    grouping = season is not None and season.status == BiliSeasonStatus.grouping.value

    if not statuses and season is None:
        return BADGE_NONE
    if grouping or any(status in IN_FLIGHT_STATUSES for status in statuses):
        return BADGE_UPLOADING

    accepted = [status for status in statuses if status in ACCEPTED_STATUSES]
    # **合集归集失败算一次失败**：视频传上去了、合集没建成，界面必须体现出来，
    # 否则用户会以为一切都好了，直到自己去 B站 发现没有合集。
    failed = [status for status in statuses if status in REJECTED_STATUSES]
    season_failed = season is not None and season.status == BiliSeasonStatus.failed.value

    if (failed or season_failed) and accepted:
        return BADGE_PARTIAL
    if failed or season_failed:
        return BADGE_FAILED
    if accepted:
        return BADGE_UPLOADED
    if statuses:
        # 走到这里只可能是「全是 unverified」—— 如实说待核对，不说成功。
        return BADGE_UNVERIFIED
    if season is not None and season.status == BiliSeasonStatus.grouped.value:
        return BADGE_UPLOADED
    return BADGE_UNVERIFIED


def read_upload(upload: BiliUpload, *, index: int = 1) -> BiliUploadRead:
    badge = derive_badge([upload], None)
    return BiliUploadRead(
        id=upload.id,
        job_id=upload.job_id,
        item_id=upload.item_id,
        index=index,
        title=upload.title,
        desc=upload.desc,
        tags=upload.tags,
        tid=upload.tid,
        tid_label=partition_label(upload.tid),
        copyright=upload.copyright or "",
        copyright_label=copyright_label(upload.copyright),
        source=upload.source or "",
        status=upload.status,
        status_text=upload_status_text(upload.status),
        badge=badge,
        badge_text=BADGE_TEXT[badge],
        badge_tone=BADGE_TONE[badge],
        progress=upload.progress,
        bvid=upload.bvid,
        aid=upload.aid,
        video_url=bilibili_video_url(upload.bvid),
        cover_path=upload.cover_path,
        verified_state=upload.verified_state,
        verified_note=upload.verified_note,
        error=upload.error,
        advice=upload.advice,
        attempt=upload.attempt,
        created_at=upload.created_at,
        updated_at=upload.updated_at,
        started_at=upload.started_at,
        finished_at=upload.finished_at,
        elapsed_seconds=elapsed_seconds(upload.started_at, upload.finished_at),
    )


def read_season(season: BiliSeason) -> BiliSeasonRead:
    try:
        upload_ids = [str(item) for item in json.loads(season.upload_ids_json or "[]")]
    except ValueError:
        upload_ids = []
    return BiliSeasonRead(
        id=season.id,
        job_id=season.job_id,
        title=season.title,
        desc=season.desc,
        season_id=season.season_id,
        status=season.status,
        status_text=season_status_text(season.status),
        upload_ids=upload_ids,
        error=season.error,
        advice=season.advice,
    )


def list_uploads(session: Session, job_id: str) -> list[BiliUpload]:
    """读一个任务的稿件记录，**按下载条目顺序** —— 合集加入合集的顺序就是它。"""
    return list(session.exec(select(BiliUpload).where(BiliUpload.job_id == job_id)).all())


def list_seasons(session: Session, job_id: str) -> list[BiliSeason]:
    return list(session.exec(select(BiliSeason).where(BiliSeason.job_id == job_id)).all())


def item_index_map(session: Session, job_id: str) -> dict[str, int]:
    """条目 id → 序号。序号只有 `JobItem` 有这个知识，所以从那里读，不在 B站 记录里存第二份。"""
    items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
    return {item.id: item.index for item in items}


def read_job_uploads(session: Session, job_id: str) -> BiliJobUploadsRead:
    uploads = list_uploads(session, job_id)
    seasons = list_seasons(session, job_id)
    indexes = item_index_map(session, job_id)

    def sort_key(upload: BiliUpload) -> tuple[int, str]:
        if upload.item_id and upload.item_id in indexes:
            return (indexes[upload.item_id], upload.id)
        return (10**6, upload.id)

    uploads.sort(key=sort_key)
    # 一个任务最多一个合集记录：再投一次是「重试」而新建。
    season = seasons[-1] if seasons else None
    badge = derive_badge(uploads, season)
    return BiliJobUploadsRead(
        job_id=job_id,
        badge=badge,
        badge_text=BADGE_TEXT[badge],
        badge_tone=BADGE_TONE[badge],
        uploads=[
            read_upload(upload, index=indexes.get(upload.item_id or "", 1))
            for upload in uploads
        ],
        season=read_season(season) if season is not None else None,
    )


def read_all_job_uploads(session: Session) -> BiliJobUploadsSummaryRead:
    """所有有 B站 记录的任务的徽标。**只回有记录的**：没有记录 = 未上传，前端不必知道这件事。"""
    job_ids = {
        row.job_id for row in session.exec(select(BiliUpload)).all()
    } | {row.job_id for row in session.exec(select(BiliSeason)).all()}
    return BiliJobUploadsSummaryRead(
        jobs=[read_job_uploads(session, job_id) for job_id in sorted(job_ids)]
    )
