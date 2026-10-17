"""单次上传的领域逻辑：把「一条已下载好的视频」变成「B站 上可对账的稿件」。

**分层约束**（design §3）：本模块纯函数式 —— **不写库、不发事件、不读全局配置**。
它只接受一个 `UploadContext`（外部可执行文件、登录态、transport、回调）与一组显式的投稿参数，
返回结构化结果。队列、重试计数、状态落账、SSE 推送全部属于 `bili_manager`（L2）。

这样切分有一个直接的收益：**投稿的每一个分支都能离线单测** ——
`runner` 是假的、`transport` 是假的、`sleeper` 是假的，于是
「biliup 成功但对账查不到」「601 频控」「合集建好但归集失败」这些最贵、
也最难在真实账号上复现的路径，全部可以在测试里跑出来。

**本模块里唯一一处「业务判断」清单**（其它都只是搬运）：

1. `biliup` 返回 0 **不等于**已发布 —— 必须过对账，查不到就停在 `unverified`；
2. 单条失败**不中断**整个合集（继续下一条），否则一条坏视频会拖掉整批；
3. 合集归集只收纳「同时拿到 aid 与 cid」的条目 —— 缺 cid 的条目加进去会被平台拒绝；
4. 已有 `season_id` 时**不再新建**合集，只补加视频（重试不产生第二个合集）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .bili_api import (
    DEFAULT_VERIFY_ATTEMPTS,
    DEFAULT_VERIFY_DELAY_SECONDS,
    add_episodes,
    create_season,
    fetch_video,
    find_archive,
    find_season_section,
    send_and_top_comment,
)
from .bili_bin import (
    DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    CommandResult,
    LineSink,
    Runner,
    extract_cover_frame,
    parse_progress,
    upload,
)
from .bili_http import Transport
from .bili_meta import (
    MetadataTemplate,
    UploadMetadata,
    merge_item_tags,
    render,
    unknown_placeholders,
    validate,
)
from .bili_policy import (
    ACCEPTED_STATUSES,
    MISSING_VIDEO,
    SEASON_CREATE_FAILED,
    SEASON_EPISODES_FAILED,
    SEASON_PENDING_NO_EPISODES,
    SEASON_SECTION_UNRESOLVED,
    SEASON_SUBMITTED_NOTE,
    UNVERIFIED_NOTE,
    UPLOAD_UNVERIFIED,
    classify_failure,
    platform_state_text,
    status_for_platform_state,
)
from .log_safety import sanitize_log_message
from .models import BiliSeasonStatus, BiliUploadStatus

logger = logging.getLogger(__name__)

# 落库的日志尾部行数。全量输出可能有几百行、且含 HTML 进度条转义串，
# 存下来只会把数据库撑大而不增加任何排障信息。
LOG_TAIL_LINES = 40

# 合集逐条投稿之间的默认间隔（秒）。settings 里可调，但**默认值只在这里定义一次**。
DEFAULT_COLLECTION_GAP_SECONDS = 20.0

# 评论去重标记：查自己的历史评论时用它认领「上次发的那一条」。
# 用 ASCII 是为了不与用户自己写的中文评论撞词。
COMMENT_MARKER = "[ytdl-bili-auto]"


# ---------------------------------------------------------------- 输入
@dataclass(frozen=True)
class UploadTarget:
    """一条待投稿的视频。

    `index` 是它在合集里的序号（单视频恒为 1），也是 `{index}` 占位符的来源；
    `title` 是**视频自己的标题**（已去扩展名），不是渲染后的投稿标题。
    """

    index: int
    title: str
    video_path: Path
    source_url: str = ""
    item_id: str | None = None


@dataclass
class UploadContext:
    """一次上传会话的全部外部依赖。**每个字段都可替换，因此整条链路可离线测试。**"""

    biliup_exe: str
    cookie_path: Path
    jar: dict[str, str]
    transport: Transport
    # 抽封面用；为空即「不生成封面」，不是错误 —— 封面是可选项。
    ffmpeg_path: str | None = None
    cover_dir: Path | None = None
    timeout: float = DEFAULT_UPLOAD_TIMEOUT_SECONDS
    verify_attempts: int = DEFAULT_VERIFY_ATTEMPTS
    verify_delay: float = DEFAULT_VERIFY_DELAY_SECONDS
    runner: Runner | None = None
    sleeper: Callable[[float], None] = time.sleep
    # 进度：0~1 的粗粒度估计；只在变大时回调（见 bili_bin.parse_progress）。
    on_progress: Callable[[float], None] | None = None
    # 原始输出行，交给调用方决定要不要落日志。
    on_log: LineSink | None = None


# ---------------------------------------------------------------- 输出
@dataclass
class UploadResult:
    """一条稿件的最终结论。字段与 `models.BiliUpload` 一一对应，由 `bili_manager` 落账。"""

    index: int
    item_id: str | None
    status: str
    title: str = ""
    desc: str = ""
    tags: str = ""
    tid: str = ""
    video_path: str = ""
    cover_path: str | None = None
    cover_url: str | None = None
    aid: int | None = None
    bvid: str | None = None
    cid: int | None = None
    verified_state: str | None = None
    verified_note: str | None = None
    error: str | None = None
    advice: str | None = None
    log_tail: str | None = None
    progress: float = 0.0
    # 置顶评论这一附加动作的结果（人话）。评论失败不影响稿件状态。
    comment: str | None = None

    @property
    def episode_ready(self) -> bool:
        """能不能进合集。**必须同时有 aid 与 cid** —— 只有 aid 时加进合集会被平台拒。"""
        return bool(self.aid) and bool(self.cid)

    @property
    def ok(self) -> bool:
        """平台上确实已经有这条稿件。**判据在 `bili_policy.ACCEPTED_STATUSES`**，
        与徽标派生用的是同一组取值 —— 两处各写一遍字面量时，加一档状态就会只改一处。"""
        return self.status in ACCEPTED_STATUSES


@dataclass
class SeasonOutcome:
    """一次归集的结论。字段与 `models.BiliSeason` 一一对应。"""

    status: str
    season_id: int | None = None
    cover_url: str | None = None
    error: str | None = None
    advice: str | None = None
    log_tail: str | None = None
    note: str | None = None


@dataclass
class CollectionResult:
    uploads: list[UploadResult] = field(default_factory=list)
    season: SeasonOutcome = field(default_factory=lambda: SeasonOutcome(status=BiliSeasonStatus.pending.value))


# ---------------------------------------------------------------- 内部工具
def tail_text(output: str, *, lines: int = LOG_TAIL_LINES) -> str:
    """取输出的最后若干行并做凭据清洗。空输出返回空串，让落库侧统一判空。"""
    rows = [row for row in (output or "").splitlines() if row.strip()]
    return sanitize_log_message("\n".join(rows[-lines:]))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def prepare_cover(context: UploadContext, *, key: str, video_path: Path) -> Path | None:
    """抽一帧当封面。**任何一步不满足都返回 `None`，绝不因此让投稿失败。**"""
    if not context.ffmpeg_path or context.cover_dir is None:
        return None
    if not video_path.is_file():
        return None
    cover = Path(context.cover_dir) / f"{key}.jpg"
    return extract_cover_frame(context.ffmpeg_path, video_path, cover, runner=context.runner)


def _execute(
    context: UploadContext,
    target: UploadTarget,
    meta: UploadMetadata,
    cover_path: Path | None,
) -> tuple[CommandResult, float]:
    """跑一次 `biliup`，同时把进度与原始输出透出去。返回 (结果, 最终进度)。

    回调**总是**装上：进度是落库字段之一，它不该取决于「这一刻有没有人在听」——
    否则同一个投稿在后端观察到 0.6、在直连调用里观察到 0.0，又是一处两处真相。
    """
    box = [0.0]

    def sink(line: str) -> None:
        if context.on_log is not None:
            context.on_log(line)
        value = parse_progress(line, box[0])
        if value > box[0]:
            box[0] = value
            if context.on_progress is not None:
                context.on_progress(value)

    result = upload(
        context.biliup_exe,
        context.cookie_path,
        target.video_path,
        meta,
        cover_path=cover_path,
        timeout=context.timeout,
        on_line=sink,
        runner=context.runner,
    )
    return result, box[0]


def _verify(
    context: UploadContext,
    title: str,
    result: UploadResult,
) -> None:
    """对账：把「命令跑完了」变成「平台上确实有一条稿件，它现在是什么状态」。

    就地写 `result`。查不到就停在 `unverified` —— 这是本功能唯一不能妥协的一条。
    """
    archive = find_archive(
        context.transport,
        context.jar,
        title,
        attempts=context.verify_attempts,
        delay=context.verify_delay,
        sleeper=context.sleeper,
    )
    if not archive:
        result.status = BiliUploadStatus.unverified.value
        result.error = UPLOAD_UNVERIFIED[0]
        result.advice = UPLOAD_UNVERIFIED[1]
        result.verified_note = UNVERIFIED_NOTE
        return

    result.aid = _as_int(archive.get("aid"))
    bvid = archive.get("bvid")
    result.bvid = str(bvid) if bvid else None
    state = archive.get("state")
    result.verified_state = None if state is None else str(state)
    result.verified_note = platform_state_text(result.verified_state)
    result.status = status_for_platform_state(result.verified_state)
    if not result.title:
        result.title = str(archive.get("title") or "")
    # `cover_url` 优先取平台侧的值：本地抽的封面被 B站 转码/裁切过，平台那个才是最终生效的。
    cover = archive.get("cover")
    if cover:
        result.cover_url = str(cover)

    if result.bvid:
        detail = fetch_video(context.transport, context.jar, result.bvid)
        if detail:
            result.cid = _as_int(detail.get("cid"))
            pic = detail.get("pic")
            if pic:
                result.cover_url = str(pic)


def _reject(result: UploadResult, message: tuple[str, str]) -> UploadResult:
    """把一条结论为「失败」的结果补齐并返回。`_reject` 的语义是「不进入对账」。"""
    result.status = BiliUploadStatus.failed.value
    result.error, result.advice = message
    return result


def run_single(
    context: UploadContext,
    target: UploadTarget,
    meta: UploadMetadata,
    *,
    cover_path: Path | None = None,
    make_cover: bool = True,
    cover_key: str | None = None,
    comment: str | None = None,
) -> UploadResult:
    """投稿一条视频，返回它的最终结论。**不做队列、不写库。**

    `meta` 必须由调用方**先校验过**（`bili_meta.validate`）——这里再校验一遍等于两处真相，
    而且到这一步已经来不及让用户改了。

    `cover_key` 是封面文件名的主键。默认按序号命名只适用于单条调用 ——
    多个任务并存时 `bili_01.jpg` 会互相覆盖，落库的 `cover_path` 就会指向别人的画面，
    所以编排层一律传记录 id。
    """
    result = UploadResult(
        index=target.index,
        item_id=target.item_id,
        status=BiliUploadStatus.uploading.value,
        title=meta.title,
        desc=meta.desc,
        tags=meta.tags,
        tid=meta.tid,
        video_path=str(target.video_path),
    )
    video = Path(target.video_path)
    if not video.is_file():
        return _reject(result, MISSING_VIDEO)

    cover = cover_path
    if cover is None and make_cover:
        cover = prepare_cover(
            context,
            key=cover_key or f"bili_{target.index:02d}",
            video_path=video,
        )
    result.cover_path = str(cover) if cover else None

    command, progress = _execute(context, target, meta, cover)
    result.progress = progress
    result.log_tail = tail_text(command.output) or None

    if not command.ok:
        conclusion, advice = classify_failure(command.output)
        logger.warning(
            "bili 投稿未成功：index=%s rc=%s 结论=%s", target.index, command.returncode, conclusion
        )
        # 原始输出必须进日志。此前它只落在库里的 `log_tail`（要经 API 才看得到），而失败建议
        # 却是让用户去翻 app.log —— 于是照做的人什么都找不到。逐行打、带固定前缀，
        # `grep "biliup 输出"` 就能把一次失败的全部上下文捞出来。
        for row in (result.log_tail or "").splitlines():
            logger.warning("biliup 输出 | %s", row)
        return _reject(result, (conclusion, advice))

    result.status = BiliUploadStatus.submitted.value
    _verify(context, meta.title, result)

    # `result.ok` 而不是再抄一遍「published / reviewing」：这条判据（以及上面的
    # `episode_ready`）本来就写在 `UploadResult` 上，散回调用点是同一份判断的第二处。
    if comment and result.episode_ready and result.ok:
        outcome = send_and_top_comment(
            context.transport,
            context.jar,
            oid=int(result.aid),
            message=comment,
            marker=COMMENT_MARKER,
        )
        result.comment = outcome.message
    return result


def reverify(
    context: UploadContext,
    *,
    title: str,
    index: int = 1,
    item_id: str | None = None,
) -> UploadResult:
    """**只重新对账，不重新投稿。**

    「待核对」的重试必须走这条路：那些稿件的投稿命令已经成功过，再跑一次 `biliup` 就是
    真的投第二遍。把它们和失败态一起「重试」是最容易写错、代价也最大的一个分支 ——
    用户点一次「重试」，账号上就多一条重复稿件。
    """
    result = UploadResult(
        index=index,
        item_id=item_id,
        status=BiliUploadStatus.unverified.value,
        title=title,
    )
    _verify(context, title, result)
    return result


def group_into_season(
    context: UploadContext,
    uploads: Sequence[UploadResult],
    *,
    season_title: str,
    season_desc: str,
    existing_season_id: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> SeasonOutcome:
    """把已就绪的稿件归进一个合集。

    **重试不会重复建合集**：`existing_season_id` 有值时跳过 `create_season` 只补加视频 ——
    否则一次「合集建好了但归集超时」会让重试产出第二个同名合集，而用户根本不知道多了一个。

    **两条路都要把合集 id 换成分区 id**：`season/add` 只回合集 id，而归集接口的 `sectionId`
    要的是分区 id。重试走的是「existing」那条路，所以换 id 这一步放在建合集**之外**，
    重试时同样会执行 —— 这正是「重试能自愈」的前提（见 `find_season_section`）。
    """
    ready = [item for item in uploads if item.episode_ready]
    if not ready:
        return SeasonOutcome(
            status=BiliSeasonStatus.pending.value,
            error=SEASON_PENDING_NO_EPISODES[0],
            advice=SEASON_PENDING_NO_EPISODES[1],
        )

    cover_url = next((item.cover_url for item in ready if item.cover_url), "") or ""
    season_id = existing_season_id
    if not season_id:
        try:
            season_id = create_season(
                context.transport,
                context.jar,
                title=season_title,
                desc=season_desc,
                cover_url=cover_url,
            )
        except Exception as exc:  # noqa: BLE001 - 归集失败必须落成结论而不是异常
            logger.warning("bili 创建合集失败：%s", sanitize_log_message(exc))
            return SeasonOutcome(
                status=BiliSeasonStatus.failed.value,
                cover_url=cover_url or None,
                error=SEASON_CREATE_FAILED[0],
                advice=SEASON_CREATE_FAILED[1],
                log_tail=tail_text(str(exc)),
            )

    episodes = [
        {"aid": int(item.aid), "cid": int(item.cid), "title": item.title}
        for item in ready
    ]
    section = find_season_section(
        context.transport, context.jar, season_id=int(season_id), sleeper=sleeper
    )
    if section is None:
        # 读不到分区就**到此为止**。拿合集 id 顶上去只会换来 `-404 啥都木有`，
        # 那条结论对用户没有任何信息量，还让「重试」看起来像没用。
        logger.warning("bili 读不到合集 %s 的分区，放弃归集", season_id)
        return SeasonOutcome(
            status=BiliSeasonStatus.failed.value,
            season_id=int(season_id),
            cover_url=cover_url or None,
            error=SEASON_SECTION_UNRESOLVED[0],
            advice=SEASON_SECTION_UNRESOLVED[1],
            log_tail=tail_text(f"合集 season_id={season_id} 的列表里没有可用的分区（sectionId）"),
        )

    logger.info(
        "bili 归集：合集 %s → 分区 %s「%s」%s 条",
        season_id,
        section["id"],
        section["title"],
        len(episodes),
    )
    try:
        add_episodes(context.transport, context.jar, section_id=int(section["id"]), episodes=episodes)
    except Exception as exc:  # noqa: BLE001 - 同上，且必须把 season_id 带回去以支持「只补加」
        logger.warning("bili 归集稿件失败：season=%s %s", season_id, sanitize_log_message(exc))
        return SeasonOutcome(
            status=BiliSeasonStatus.failed.value,
            season_id=int(season_id),
            cover_url=cover_url or None,
            error=SEASON_EPISODES_FAILED[0],
            advice=SEASON_EPISODES_FAILED[1],
            log_tail=tail_text(str(exc)),
        )

    return SeasonOutcome(
        status=BiliSeasonStatus.grouped.value,
        season_id=int(season_id),
        cover_url=cover_url or None,
        note=SEASON_SUBMITTED_NOTE,
    )


def render_collection(
    template: MetadataTemplate,
    targets: Sequence[UploadTarget],
    *,
    job_title: str,
) -> list[UploadMetadata]:
    """把模板渲染成「每条视频各一组元数据」。

    **单一实现，两处调用**：界面在提交前用它做逐条预览与校验（400 的判据），
    上传时用它生成真正提交的内容。分成两份必然漂移 —— 预览说没问题、提交却超限，
    正是 `bili_meta` 文档里那条「超限要报错、不许静默截断」要防的场景。
    """
    total = len(targets)
    rendered: list[UploadMetadata] = []
    for target in targets:
        meta = render(
            template,
            index=target.index,
            title=target.title,
            count=total,
            job=job_title,
            source=target.source_url,
        )
        # 模板标签 + 该条标题的派生标签：合集里每条内容不同，
        # 只用模板标签会让所有条目挂上一模一样的标签。
        rendered.append(replace(meta, tags=merge_item_tags(template.tags, target.title)))
    return rendered


def validate_collection(
    template: MetadataTemplate,
    targets: Sequence[UploadTarget],
    *,
    job_title: str,
) -> list[str]:
    """逐条校验并**定位到具体条目**（`第 3 条：标题超出 6 个字`）。

    只报「有 N 条不合格」是不够的：用户面对几十条视频，需要知道改哪一条。
    """
    problems: list[str] = []
    for target, meta in zip(targets, render_collection(template, targets, job_title=job_title), strict=True):
        for problem in validate(meta):
            problems.append(f"第 {target.index} 条「{target.title}」：{problem}")
    for token in unknown_placeholders(template.title_template, template.desc_template):
        problems.append(f"模板里的 {{{token}}} 不是可用的占位符，提交时会原样保留这段花括号")
    return problems


def run_collection(
    context: UploadContext,
    *,
    job_title: str,
    template: MetadataTemplate,
    targets: Sequence[UploadTarget],
    season_title: str,
    season_desc: str,
    gap_seconds: float = DEFAULT_COLLECTION_GAP_SECONDS,
    comment: str | None = None,
    on_upload: Callable[[UploadResult], None] | None = None,
) -> CollectionResult:
    """逐个投稿并归集。

    **单条失败不中断整批**：一条视频文件损坏或一次 601 频控，不该让后面几十条一条都发不出去。
    条间 `gap_seconds` 是抗 601 频控的唯一手段，且**最后一条之后不再等待**（那只是白等）。
    """
    result = CollectionResult()
    metadatas = render_collection(template, targets, job_title=job_title)
    total = len(targets)
    for position, (target, meta) in enumerate(zip(targets, metadatas, strict=True)):
        item = run_single(context, target, meta, comment=comment)
        result.uploads.append(item)
        if on_upload is not None:
            on_upload(item)
        if position < total - 1 and gap_seconds > 0:
            context.sleeper(gap_seconds)

    result.season = group_into_season(
        context,
        result.uploads,
        season_title=season_title,
        season_desc=season_desc,
    )
    return result
