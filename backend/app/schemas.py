from datetime import UTC, datetime
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, Field, HttpUrl
from pydantic.functional_validators import BeforeValidator

DownloadMode = Literal["video_subtitles", "video_only", "subtitles_only"]
SubtitleSource = Literal["human", "auto", "both"]
SubtitleFormat = Literal["best", "srt", "vtt"]
JobBatchAction = Literal["pause", "restart", "delete"]
CookieSource = Literal["none", "file", "browser"]


def _as_aware_utc(value: object) -> object:
    """SQLite 不存时区，读回来的 `datetime` 一律是 **naive 的 UTC 墙上时间**。

    不补上时区就发出去，前端 `Date.parse` 会按**本地时间**解释它 —— 本机 UTC+8，
    于是每一个「已用 / 已提交」都凭空多出 8 小时（实测 Ep 125：真实 52 秒显示成 8:00:52）。
    这类偏差**不报错、只是数字变大**，所以用例里全是带 `Z` 的假时间戳时它一直藏着。

    判据：**跨接口的时间必须自带偏移量**，让「什么时候」不依赖读它的人猜。
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# 所有对外的时间字段都用它。补成 aware 之后 pydantic 会序列化成 `...Z`，
# 而 `Z` 是 `Date.parse` 唯一能可靠识别为 UTC 的形式。
UtcDateTime = Annotated[datetime, BeforeValidator(_as_aware_utc)]

# 任务跑到终态时怎么提醒。`off` 不提醒；`sound` 提示音；`toast` 右下角浮层；`both` 两者都要。
# 提醒本身在**前端**发生（浏览器里播声音、浮层也是 DOM），后端只负责把这个偏好存下来并读回去
# —— 不然用户换一台机器打开同一个后端，偏好就丢了。
CompletionNotification = Literal["off", "sound", "toast", "both"]
COMPLETION_NOTIFICATION_MODES: tuple[str, ...] = get_args(CompletionNotification)
DEFAULT_COMPLETION_NOTIFICATION: CompletionNotification = "both"


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=1)
    cookies_enabled: bool = True


class FormatOption(BaseModel):
    format_id: str
    label: str
    height: int | None = None
    ext: str | None = None
    fps: float | None = None
    filesize: int | None = None


class SubtitleOption(BaseModel):
    language: str
    name: str | None = None
    formats: list[str] = Field(default_factory=list)


class VideoEntry(BaseModel):
    index: int
    id: str | None = None
    title: str
    url: str
    duration: float | None = None
    thumbnail: str | None = None


class AnalyzeResponse(BaseModel):
    url: str
    title: str
    is_playlist: bool
    duration: float | None = None
    thumbnail: str | None = None
    entries: list[VideoEntry] = Field(default_factory=list)
    formats: list[FormatOption] = Field(default_factory=list)
    subtitles: list[SubtitleOption] = Field(default_factory=list)
    automatic_subtitles: list[SubtitleOption] = Field(default_factory=list)
    ffmpeg: dict[str, bool] = Field(default_factory=dict)


class DownloadOptions(BaseModel):
    mode: DownloadMode = "video_subtitles"
    resolution: str = "1440p"
    format_id: str | None = None
    subtitle_languages: list[str] = Field(default_factory=list)
    subtitle_source: SubtitleSource = "both"
    subtitle_format: SubtitleFormat = "best"
    playlist_items: list[int] | None = None
    write_metadata: bool = False
    write_thumbnail: bool = False
    skip_existing: bool = True
    speed_limit_kbps: int | None = Field(default=None, ge=1)
    retries: int = Field(default=10, ge=0, le=20)
    # 默认**开着**：这是「要不要提醒我这一次」的逐任务开关，提醒**方式**由全局设置决定。
    # 出厂若为 False，用户就必须在每次提交前记得勾一下 —— 那等于把「设置里选了提醒方式」
    # 变成一句空话（选了却不响，正是本目录要消灭的那类名实不符）。
    notify_on_complete: bool = True


class CreateJobRequest(BaseModel):
    url: str = Field(min_length=1)
    options: DownloadOptions = Field(default_factory=DownloadOptions)


class RestartJobRequest(BaseModel):
    resolution: str | None = None


class JobBatchActionRequest(BaseModel):
    action: JobBatchAction
    job_ids: list[str] = Field(min_length=1)
    delete_files: bool = False


class DeleteJobItemsRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    delete_files: bool = False


class ResolutionFallback(BaseModel):
    requested_resolution: str
    fallback_resolution: str
    reason: str | None = None
    restart_resolution: str | None = None
    message: str


class JobItemRead(BaseModel):
    id: str
    job_id: str
    source_url: str
    title: str
    index: int
    status: str
    progress: float
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    # 视频时长（秒）。建任务时从解析结果写入，供任务中心显示「时长」；平台没给时长时为 None。
    duration: float | None = None
    speed: float | None = None
    eta: int | None = None
    output_path: str | None = None
    actual_width: int | None = None
    actual_height: int | None = None
    actual_format: str | None = None
    requested_resolution: str | None = None
    fallback_resolution: str | None = None
    fallback_reason: str | None = None
    resolution_fallback: ResolutionFallback | None = None
    # 下载到的字幕：**语言族**（`en` / `zh`）与来源（`human` 人工 / `auto` 自动生成 /
    # `none` 没有可用字幕）。没有字幕结论时两列都是 `None`（与 `none` 区分：
    # 那是「跑过一次，结论是没有」）。
    subtitle_language: str | None = None
    subtitle_source: str | None = None
    # 已下载到磁盘的**字幕文件**（没有就是 None）。任务中心据此决定「打开字幕」按钮是否可点：
    # 不给这个字段，前端就只能在点下去之后吃一个 409。与 `output_path` 一样是**后端算的**，
    # 前端不拼路径。
    subtitle_path: str | None = None
    error: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    elapsed_seconds: int = 0


class JobRead(BaseModel):
    id: str
    url: str
    title: str
    status: str
    progress: float
    speed: float | None = None
    eta: int | None = None
    total_items: int
    completed_items: int
    failed_items: int
    current_item_title: str | None = None
    error: str | None = None
    download_dir: str | None = None
    actual_resolution: str | None = None
    actual_format: str | None = None
    resolution_fallback: ResolutionFallback | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    elapsed_seconds: int = 0
    # 创建这个任务时勾没勾「完成后通知」。它只存在于 `options_json` 里，读模型不把它抬出来的话，
    # 前端刷新页面后就再也拿不到了（SSE 每次事件只是重拉 `/api/jobs`）—— 于是「这次会话里
    # 提交的任务会提醒、重开页面后同一个任务就不提醒」。
    notify_on_complete: bool = False
    items: list[JobItemRead] = Field(default_factory=list)


class JobBatchActionResponse(BaseModel):
    affected_job_ids: list[str]
    jobs: list[JobRead] = Field(default_factory=list)


class DeleteJobItemsResponse(BaseModel):
    deleted_item_ids: list[str]
    job_deleted: bool
    job: JobRead | None = None


class SettingsRead(BaseModel):
    download_dir: str
    default_concurrency: int
    default_subtitle_languages: list[str]
    default_resolution: str
    default_speed_limit_kbps: int | None
    default_retries: int
    aria2c_connections: int
    # 用户显式设置的代理（None = 自动）。
    proxy: str | None = None
    # 实际生效的代理与来源（setting/system/environment/direct/none），用于在界面上
    # 说明「现在到底走没走代理、走的哪个」——这正是以前最查不出来的东西。
    proxy_source: str = "none"
    proxy_effective: str | None = None
    cookies_enabled: bool
    ffmpeg: dict[str, bool]
    # 任务跑到终态时怎么提醒。默认 `both`；`off` 时前端一个都不发 —— 下载选项里那个逐任务
    # 开关会显示成不可勾并说明原因，否则「勾了不响」又是一次名实不符。
    completion_notification: CompletionNotification = DEFAULT_COMPLETION_NOTIFICATION


class SettingsUpdate(BaseModel):
    download_dir: str | None = None
    default_concurrency: int | None = Field(default=None, ge=1)
    default_subtitle_languages: list[str] | None = None
    default_resolution: str | None = None
    default_speed_limit_kbps: int | None = Field(default=None, ge=1)
    default_retries: int | None = Field(default=None, ge=0, le=20)
    aria2c_connections: int | None = Field(default=None, ge=1, le=4)
    proxy: str | None = None
    completion_notification: CompletionNotification | None = None


class CookieStatus(BaseModel):
    enabled: bool
    filename: str | None = None
    source: CookieSource = "none"
    browser: str | None = None
    imported_count: int | None = None


class BrowserCookieImportRequest(BaseModel):
    browser: str = "auto"
    close_browser_if_locked: bool = False


class ProxyTestRequest(BaseModel):
    # 留空 = 用当前生效的解析结果；填值 = 先试这个地址再决定要不要保存。
    proxy: str | None = None


class ProxyTestRead(BaseModel):
    ok: bool
    source: str
    proxy: str | None = None
    probe_url: str
    http_status: int | None = None
    elapsed_ms: int = 0
    bytes_read: int = 0
    error: str | None = None
    summary: str = ""
    next_steps: list[str] = Field(default_factory=list)


class CookieVerifyRequest(BaseModel):
    # True = 额外把 cookies 发到 youtube.com 看 LOGGED_IN（需要联网）。
    deep: bool = True


class CookieHealthRead(BaseModel):
    present: bool
    path: str
    filename: str | None = None
    size_bytes: int = 0
    format_ok: bool = False
    format_note: str = ""
    cookie_count: int = 0
    domains: dict[str, int] = Field(default_factory=dict)
    youtube_domain_count: int = 0
    auth_cookie_names: list[str] = Field(default_factory=list)
    missing_auth_cookie_names: list[str] = Field(default_factory=list)
    anonymous_only: bool = False
    expired_count: int = 0
    verdict: str = ""
    next_steps: list[str] = Field(default_factory=list)
    logged_in: bool | None = None
    logged_in_detail: str | None = None
    checked_at: float = 0.0


class DiagnosticsRead(BaseModel):
    cookies_enabled: bool
    dependencies: dict[str, bool | int | str | None | list[str]]
    # 日志文件路径：出问题时用户第一件事就是找它。
    log_file: str | None = None
    # 启动时被摘掉的、会打坏 JS 运行时的宿主环境变量。
    sanitized_environment: list[dict[str, str]] = Field(default_factory=list)


# ================================================================ B站 上传
# 「结论 + 下一步」是本功能文案的硬形状：只报结论不给动作的提示视为未完成（requirements 验收口径）。
# 所以这里的每个缺项都不是一个字符串，而是一对 (message, advice)。
class BiliAdvice(BaseModel):
    message: str
    advice: str


class BiliPartitionRead(BaseModel):
    tid: str
    label: str


class BiliAccountRead(BaseModel):
    mid: int | None = None
    name: str = ""


class BiliToolRead(BaseModel):
    """外部可执行文件的探测结论。`problem` 非空即不可用，`advice` 是装/修的动作。"""

    found: bool = False
    path: str | None = None
    version: str | None = None
    problem: str | None = None
    advice: str | None = None


class BiliLoginRead(BaseModel):
    """登录态摘要。**只有名字与计数，没有任何值、也没有值的前缀**（requirements FR-B12）。"""

    present: bool = False
    cookie_count: int = 0
    names: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    missing_recommended: list[str] = Field(default_factory=list)
    # 登录文件被 git 跟踪 = 账号密码已经进了仓库。这比「没登录」严重得多，必须单独一档提示。
    tracked_by_git: bool = False
    account: BiliAccountRead | None = None
    account_checked: bool = False


class BiliStatusRead(BaseModel):
    ready: bool
    biliup: BiliToolRead
    ffmpeg: BiliToolRead
    login: BiliLoginRead
    # 人话缺项清单：每一项都是「结论 + 下一步」。
    blockers: list[BiliAdvice] = Field(default_factory=list)
    partitions: list[BiliPartitionRead] = Field(default_factory=list)
    limits: BiliLimits
    # 两次投稿之间强制留的秒数（抗频控）。界面把它显示出来，用户才知道为什么「一条条慢慢来」。
    gap_seconds: float = 0.0
    uploads_in_flight: int = 0


class BiliQrcodeRead(BaseModel):
    qrcode_key: str
    url: str
    # SVG 二维码文本。`qrcode` 缺失时为 None，此时 note 会给出替代路径。
    svg: str | None = None
    note: str | None = None
    expires_in: int = 180


class BiliLoginPollRead(BaseModel):
    state: str
    message: str
    logged_in: bool = False
    account: BiliAccountRead | None = None


class BiliMetadata(BaseModel):
    """投稿的可编辑字段。合集场景下它同时充当**模板**（标题/简介可含占位符）。"""

    title: str = ""
    desc: str = ""
    tags: str = ""
    tid: str = ""
    # 版权：`1` 自制 / `2` 转载，与 B站 接口的 `copyright` 同值域。
    # 默认值是 `2`（转载）—— 与 `bili_meta.normalize_copyright` 的回退一致；
    # 界面一定会显式回传（表单里那个选择默认就选中转载）。
    copyright: str = "2"
    # 转载来源链接。**合集模板上它恒为空**：合集里每条视频的来源是各自的原始链接，
    # 由服务端在逐条渲染时填（`bili_upload.render_collection`），界面没有共用的来源输入框。
    source: str = ""


class BiliLimits(BaseModel):
    """平台硬限，随响应一起下发给界面。

    **界面不自己写一份数字**：写一份就是两处真相，改一处必漏另一处，
    而漏掉的后果是「界面说没超、提交回来 400」。界面只负责把这里的数字显示成 `12/80`。
    """

    title: int
    desc: int
    tags: int
    tag_count: int
    tag_item: int
    # 报「某个标签太长」时回显多少个字。**它也是平台相关的数字**（由 `TAG_PREVIEW_MAX` 决定），
    # 所以一并下发：界面写死一个（曾经是 12）就会出现「弹窗里截到 12 个字、服务端的 400 里
    # 截到 40 个字」——同一件事的两种说法，而用户无从判断哪个才算数。
    tag_preview: int
    comment: int


class BiliPrefillItem(BaseModel):
    index: int
    item_id: str | None = None
    source_title: str
    # 原视频链接：`{source}` 占位符展开成它。**必须下发**，否则界面预览里的 `{source}`
    # 只能留空或写个「链接」—— 而「预览与真实提交不一致」正是最该避免的一种偏差。
    source_url: str = ""
    title: str
    desc: str
    tags: str
    # 这一条的视频文件是否就绪。合集里任何一条没就绪都会让整批被拒（服务端会给结论），
    # 界面据此在提交前就把它标出来。
    ready: bool = True
    # 这一条**会不会带字幕**的人话说明（如 `en · 人工`、`无字幕`）。
    # 由服务端算而不是让前端把 `subtitle_language` / `subtitle_source` 拼一遍：
    # 拼法（尤其是「没有字幕」怎么说）属于本功能的文案，写两处早晚不一致。
    subtitle: str = ""


class BiliPrefillRead(BaseModel):
    job_id: str
    job_title: str
    kind: Literal["single", "collection"]
    # 默认置顶评论文案（可清空 = 不发评论）。
    comment: str = ""
    # 单视频：一组预填值；合集：模板。
    metadata: BiliMetadata | None = None
    template: BiliMetadata | None = None
    items: list[BiliPrefillItem] = Field(default_factory=list)
    # 逐条预览里超限或用了未知占位符的条目，形如「第 3 条：标题超出 6 个字」。
    problems: list[str] = Field(default_factory=list)
    partitions: list[BiliPartitionRead] = Field(default_factory=list)
    limits: BiliLimits
    make_cover: bool = True
    make_comment: bool = True
    # 合集简介的默认值（单视频恒为空）。界面没有这个输入框，所以由后端派生一句非空的话 ——
    # 平台那边的合集简介是必填项，不能发空串。见 `bili_meta.default_season_desc`。
    season_desc: str = ""
    # 「新建合集」时用户填的合集名（界面预填成任务名，留空也用任务名顶上）。
    # **只在复用了上一次投稿时才可能有值**：`existing` 模式下平台上的合集名改不了，
    # 那时存下来的是空串，前端照旧用 `job_title` 兜底。
    season_title: str = ""
    # 这批预填值**来自上一次投稿**，而不是按视频标题现算的默认值。
    # 界面据此提示一句「已复用上次填的内容」—— 用户需要知道表单为什么是满的，
    # 否则他会以为这是应用自己猜的，改都不敢改。
    reused: bool = False
    # 界面上「归集方式」的初始选中项。**由服务端算，不让前端按 `kind` 推**：
    # 这个默认值按场景而不同（合集 `new` / 单视频 `none`），写成两处早晚会漂移，
    # 而漂移的表现是「某次升级之后投稿悄悄多出一批合集」，不会有任何报错。
    # 兜底取 `none`（不归集）而不是 `new`：漏填时停在「什么都不做」这一侧。
    default_group_mode: str = "none"


class BiliUploadCreate(BaseModel):
    job_id: str = Field(min_length=1)
    item_id: str | None = None
    metadata: BiliMetadata
    make_cover: bool = True
    comment: str = ""
    # 归入方式：`none` 不归集 / `existing` 加进 `season_id` 指定的已有合集 /
    # `new` 用 `season_title` 新建一个。**默认 `none`** —— 改动前单视频从不进任何合集，
    # 把 `new` 当默认会让此后每条单视频投稿都在账号里多出一个只含它自己的合集。
    # 单视频与合集的字面默认值不同是刻意的，理由见 `bili_meta.DEFAULT_SINGLE_GROUP_MODE`。
    group_mode: str = "none"
    # `group_mode="existing"` 时必填。与合集那边同样**不校验它是不是真存在**：
    # 那是归集阶段的网络动作，这里只挡「没给编号」。
    season_id: int | None = None
    # 只有 `group_mode="new"` 用得上；留空时由路由用任务名兜底。
    season_title: str = ""


class BiliSeasonOption(BaseModel):
    """账号下的**一个已有合集**，供界面下拉选择。"""

    season_id: int
    title: str
    # 简介与封面从平台数据里兜底读取，键名有多个变体（见 `bili_meta.normalize_season_option`）。
    # 拿不到就留空 —— 空简介只是界面上少一行字，不该让整个下拉列表查不出来。
    desc: str = ""
    cover_url: str = ""
    # 平台统计的视频条数。**仅供界面显示**（让用户认出这是哪个合集），不参与任何判断 ——
    # 界面上的条数是这个合集**目前**的，与「本次要归入几条」无关。
    total: int = 0


class BiliSeasonListRead(BaseModel):
    seasons: list[BiliSeasonOption] = Field(default_factory=list)
    # 列不出来时的原因（未登录 / 网络不通）。空字符串表示「确实没有合集」。
    # **刻意用 `""` 而不是 `[]` 区分不了这两种情况**：未登录返回空列表，
    # 界面就会显示成「这个账号还没有合集」，用户会以为是自己还没建过。
    error: str = ""
    advice: str = ""


class BiliCollectionCreate(BaseModel):
    job_id: str = Field(min_length=1)
    template: BiliMetadata
    season_title: str = ""
    season_desc: str = ""
    make_cover: bool = True
    comment: str = ""
    # 归入方式：`none` 不归集 / `existing` 加进 `season_id` 指定的已有合集 /
    # `new` 用 `season_title` 新建一个。默认 `new` —— 改动前的行为就是每次新建。
    group_mode: str = "new"
    # `group_mode="existing"` 时必填。**不校验它是不是真存在**：那是归集阶段的网络动作，
    # 放在提交前查一次会多一次往返，且账号可能在提交后被登出。这里只挡「没给编号」。
    season_id: int | None = None


class BiliUploadRead(BaseModel):
    id: str
    job_id: str
    item_id: str | None = None
    index: int = 1
    title: str
    desc: str = ""
    tags: str = ""
    tid: str = ""
    tid_label: str = ""
    # 实际提交出去的版权与来源（提交时的快照）。界面要用它显示「转载 / 自制」，
    # 也是「重试没有把稿件悄悄换成自制」这件事可被核对的那一份证据。
    copyright: str = ""
    copyright_label: str = ""
    source: str = ""
    status: str
    status_text: str = ""
    # 单条记录的徽标：由同一套 `derive_badge` 折算（`[这条记录] + 无合集`），
    # 因此条目行与任务行永远显示同一个结论，前端不必自己把 status 折一遍。
    badge: str = "none"
    badge_text: str = ""
    badge_tone: str = "idle"
    progress: float = 0.0
    bvid: str | None = None
    aid: int | None = None
    video_url: str | None = None
    cover_path: str | None = None
    verified_state: str | None = None
    verified_note: str | None = None
    # CC 字幕这一步的结论（`posted` / `skipped` / `failed`）与平台侧的语言标识。
    # **它与 `status` 是两件事**：字幕没传上去时 `status` 照样是 `published`，
    # 界面必须能同时说清「稿件已发布」和「字幕没传上去」，而不是二选一。
    subtitle_state: str = "skipped"
    subtitle_lan: str = ""
    subtitle_message: str = ""
    error: str | None = None
    advice: str | None = None
    attempt: int = 1
    created_at: UtcDateTime
    updated_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    elapsed_seconds: int = 0


class BiliSeasonRead(BaseModel):
    id: str
    job_id: str
    title: str = ""
    desc: str = ""
    season_id: int | None = None
    status: str
    status_text: str = ""
    upload_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    advice: str | None = None


class BiliJobUploadsRead(BaseModel):
    """任务级读模型：界面上的**一个** B站 图标 = 这里的 `badge`。

    为什么徽标由后端算：它是「多条记录 → 一档状态」的规则（§5.3），
    前端各算一遍必然出现「任务行说已上传、条目行说失败」这种自相矛盾的界面。
    """

    job_id: str
    badge: str
    badge_text: str
    badge_tone: str
    uploads: list[BiliUploadRead] = Field(default_factory=list)
    season: BiliSeasonRead | None = None


class BiliJobUploadsSummaryRead(BaseModel):
    """**所有有 B站 记录的任务**的徽标汇总。

    为什么要有它：任务中心的每一行都有一个 B站 图标，逐个任务去问就是「N 个任务 = N 个请求」，
    而 SSE 每来一条事件都会重拉一次。一次请求把全部徽标拿回来，是唯一不做无谓放大的做法。
    没有记录的任务不出现在这里 —— 前端按「缺省 = 未上传」渲染即可。
    """

    jobs: list[BiliJobUploadsRead] = Field(default_factory=list)


class BiliRetryRead(BaseModel):
    # reupload（重新投稿）/ reverify（只重新核对，不重投）/ none（已经是成功态，什么都没做）
    action: Literal["reupload", "reverify", "none"]
    message: str
    upload: BiliUploadRead | None = None


class BiliSeasonRetryRead(BaseModel):
    """一次「重试归集」的结果。**`action` 是「这一次到底做了什么」，不是合集状态。**

    `waiting` 表示**连归集请求都没发**（前提还不满足，例如视频还在审核、平台没给分P编号）。
    界面必须能把它和「发了请求但又失败」分开：前者该说「等审核」，后者该说「又没成」。
    """

    # grouped（这次归集成了）/ failed（发了请求又失败）/ waiting（没发起，前提还不满足）
    action: Literal["grouped", "failed", "waiting"]
    message: str
    season: BiliSeasonRead
