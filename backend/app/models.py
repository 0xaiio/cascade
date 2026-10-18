from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class DownloadMode(StrEnum):
    video_subtitles = "video_subtitles"
    video_only = "video_only"
    subtitles_only = "subtitles_only"


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    url: str
    title: str
    status: str = Field(default=JobStatus.queued.value, index=True)
    options_json: str
    progress: float = 0.0
    speed: Optional[float] = None
    eta: Optional[int] = None
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    current_item_title: Optional[str] = None
    error: Optional[str] = None
    download_dir: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class JobItem(SQLModel, table=True):
    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    source_url: str
    title: str
    index: int = 1
    # 视频时长（秒）。**建任务时就从解析结果写进去**（`POST /api/jobs` 的条目已经带时长），
    # 因此任务一进队列就能显示时长，不必等预检或下载开始 —— 时长是视频的固有属性，
    # 不像大小/分辨率那样要等选出格式才知道。
    duration: Optional[float] = None
    status: str = Field(default=JobStatus.queued.value, index=True)
    progress: float = 0.0
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    speed: Optional[float] = None
    eta: Optional[int] = None
    output_path: Optional[str] = None
    actual_width: Optional[int] = None
    actual_height: Optional[int] = None
    actual_format: Optional[str] = None
    options_json: Optional[str] = None
    requested_resolution: Optional[str] = None
    fallback_resolution: Optional[str] = None
    fallback_reason: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class JobEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    item_id: Optional[str] = Field(default=None, index=True)
    event_type: str
    payload_json: str
    created_at: datetime = Field(default_factory=utc_now)


class BiliUploadStatus(StrEnum):
    """稿件级上传状态。**每一档都有独立的语义，不允许合并**：

    - `submitted` 只代表「biliup 命令跑完了」，B站 从不让上传接口承诺稿件已存在；
    - `unverified` 是刻意留出的一档 —— 对账查不到时必须停在这里，而不是折进 `published`
      （arXiv 那条实测教训：投稿命令返回 0 不等于已发布）。
    """

    queued = "queued"
    uploading = "uploading"
    submitted = "submitted"
    published = "published"
    reviewing = "reviewing"
    rejected = "rejected"
    unverified = "unverified"
    failed = "failed"


class BiliSeasonStatus(StrEnum):
    pending = "pending"
    grouping = "grouping"
    grouped = "grouped"
    failed = "failed"
    # 用户在投稿表单里选了「不归集」。**必须是终态**：`_maybe_group` 只自动归集
    # `pending`，所以这个状态的作用就是让那批稿件投完就停下，不留一个永远不结束的徽标。
    skipped = "skipped"


class BiliUpload(SQLModel, table=True):
    """一次投稿 = 一行。字段里同时保存**实际提交的元数据快照**（用于对账与复现）与平台返回的标识。"""

    __tablename__ = "bili_upload"

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    item_id: Optional[str] = Field(default=None, index=True)
    title: str = ""
    desc: str = ""
    tags: str = ""
    tid: str = ""
    # 版权（`1` 自制 / `2` 转载）与转载来源链接。**必须落库**：
    # 重投时 `_load_request` 靠这两列重建要提交的元数据，不存就会出现「重试一次，
    # 稿件从转载变成了自制」这种没人发现得了的偏差。
    # 这里的字面量是**产品默认值**（默认转载，理由见 `bili_meta`），
    # 而 `db._ensure_columns` 给**历史行**补的默认值是 `1` —— 那是改动之前的实际行为，两者不一样。
    copyright: str = "2"
    source: str = ""
    video_path: str = ""
    cover_path: Optional[str] = None
    cover_url: Optional[str] = None
    # 用户在弹窗里勾的两件事，必须落库：worker 是在**另一次请求之后**才跑这条稿件的，
    # 到那时请求对象早没了 —— 只存在内存里的选项会静默丢失（表现为「勾了封面却没封面」）。
    make_cover: bool = True
    comment: str = ""
    status: str = Field(default=BiliUploadStatus.queued.value, index=True)
    progress: float = 0.0
    bvid: Optional[str] = None
    aid: Optional[int] = None
    cid: Optional[int] = None
    verified_state: Optional[str] = None
    verified_note: Optional[str] = None
    error: Optional[str] = None
    advice: Optional[str] = None
    log_tail: Optional[str] = None
    attempt: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class BiliSeason(SQLModel, table=True):
    """一次合集归集 = 一行。

    **为什么与稿件分表**：稿件的成败与合集的成败是两个独立生命周期 —— 稿件可以全部成功而归集失败
    （requirements FR-B9）。塞进同一行会让「合集图标状态」与「单条视频状态」互相污染。
    """

    __tablename__ = "bili_season"

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    title: str = ""
    desc: str = ""
    cover_path: Optional[str] = None
    cover_url: Optional[str] = None
    season_id: Optional[int] = None
    status: str = Field(default=BiliSeasonStatus.pending.value, index=True)
    # 参与归集的稿件记录 id，**保序** —— 既是重试的输入，也是界面展示的顺序来源。
    upload_ids_json: str = "[]"
    error: Optional[str] = None
    advice: Optional[str] = None
    log_tail: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
