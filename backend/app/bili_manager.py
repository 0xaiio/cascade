"""B站 上传的队列与状态编排（L2）。

职责边界（design §3）：**队列、串行与间隔、状态写库与收敛、事件发布、失败落账、重启后状态可读**。
不直接调 `biliup`（经 `bili_upload`），不解析 B站 的内部结构（经 `bili_api`），不算界面状态（经 `bili_read_model`）。

四条设计决定，都是「不这么做就会出一个静默的错」：

1. **单 worker，且投稿之间强制留间隔。** `bili_upload_gap_seconds` 是唯一抗账号级频控（错误码 601）的手段，
   而频控是**账号级**的 —— 所以间隔在两个任务之间同样要留。这里用「距上次投稿结束的时间」
   现算该等多久（而不是每条都盲等一个固定值），既不违反间隔，也不会让第一个投稿白等 20 秒。
2. **进程内的活跃集合只在内存**（`_deleted` 那类），**状态一律以数据库为准**：重启后
   `queued`/`uploading` 的记录会被 `resume_interrupted()` 折成可重试的失败，
   而不是永远停在「上传中」——这正是 job_manager 里那条「静默卡死最终要变成一条可读原因」的同一件事。
3. **重试分两种**：`failed`/`rejected` 重新投稿；`unverified` **只重新对账**（见 `bili_upload.reverify`）。
   已成功的记录不动（幂等）。把三者混成一条「重试」路径，最轻的后果是重复投稿。
4. **行是 upsert 的**：`(job_id, item_id)` 只有一条记录。重投就是重投，不是新开一条 ——
   否则同一个任务会攒出多份记录，徽标与合集归集都要面对「哪一条才算」这个问题。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .bili_api import fetch_account
from .bili_auth import (
    BiliLoginSession,
    TvToken,
    cookie_summary,
    delete_cookie_file,
    exchange_tv_token,
    is_cookie_tracked_by_git,
    load_cookie_jar,
    missing_required_cookies,
    read_login_token,
    write_cookie_file,
)
from .bili_bin import (
    Runner,
    biliup_version,
    detect_broken_stub,
    find_biliup,
    repair_broken_stub,
)
from .bili_http import Transport, default_transport
from .bili_meta import (
    MetadataTemplate,
    UploadMetadata,
    copyright_label,
    default_collection_template,
    default_comment,
    default_season_desc,
    default_single,
    effective_source,
    limits as bili_limits,
    normalize_copyright,
    partition_options,
    validate as validate_metadata,
    validate_comment,
)
from .bili_policy import (
    COOKIE_TRACKED_BY_GIT,
    MISSING_BILIUP,
    MISSING_COOKIE,
    MISSING_COOKIE_FIELDS,
    MISSING_FFMPEG,
    MISSING_UPLOAD_TOKEN,
    MISSING_VIDEO,
    NOT_LOGGED_IN,
)
from .bili_read_model import (
    IN_FLIGHT_STATUSES,
    list_seasons,
    read_job_uploads,
    read_season,
    read_upload,
)
from .bili_upload import (
    SeasonOutcome,
    UploadContext,
    UploadResult,
    UploadTarget,
    group_into_season,
    render_collection,
    reverify,
    run_single,
    validate_collection,
)
from .config import REPO_ROOT, AppSettings
from .events import EventBroker
from .log_safety import sanitize_log_message
from .models import (
    BiliSeason,
    BiliSeasonStatus,
    BiliUpload,
    BiliUploadStatus,
    Job,
    JobItem,
    utc_now,
)
from .output_paths import existing_output_file
from .schemas import (
    BiliAccountRead,
    BiliAdvice,
    BiliJobUploadsRead,
    BiliLoginRead,
    BiliMetadata,
    BiliPrefillItem,
    BiliPrefillRead,
    BiliSeasonRead,
    BiliSeasonRetryRead,
    BiliStatusRead,
    BiliToolRead,
    BiliUploadRead,
    BiliRetryRead,
)
from .ytdlp_service import YtDlpService

logger = logging.getLogger(__name__)

# 上传收尾阶段崩溃时写给用户看的一句话。与 job_manager 的 CRASHED_ITEM_ERROR 同口径：
# 不猜是哪一步崩的，只保证「不会永远停在正在上传」。
CRASHED_UPLOAD_ERROR = "内部错误：上传在收尾阶段异常退出，详情见日志。"
CRASHED_UPLOAD_ADVICE = "点「重试」重新上传这一条；若反复出现，请把 data/logs/app.log 里的报错发出来"

# 单条上传的重试上限（次数）。超过之后不再自动累加 attempt，但仍允许手动重试 ——
# 硬上限的意义是让「反复失败」在界面上看得出来，而不是阻止用户再试。
MAX_ATTEMPTS = 99


class BiliPreconditionError(RuntimeError):
    """前置条件不满足。L0 把它映射成 409 —— 请求本身没问题，是「现在还不能做」。

    `code` 是前端判据（沿用 `cookieLock.ts` 的做法：判 `detail.code`，不判文案）。
    """

    def __init__(self, message: str, advice: str, *, code: str = "not_ready") -> None:
        super().__init__(message)
        self.message = message
        self.advice = advice
        self.code = code

    def to_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "advice": self.advice}


def _new_id() -> str:
    return uuid4().hex


def _advice(pair: tuple[str, str]) -> BiliAdvice:
    return BiliAdvice(message=pair[0], advice=pair[1])


def _log_biliup_line(line: str) -> None:
    """biliup 的一行原始输出 → 日志。

    过一遍脱敏再落：工具会打印什么不在我们控制之内，而这条通道最终会进用户会分享的日志文件。
    空行与进度条的转义串（每帧都在变）不落，否则日志会被刷成噪声。
    """
    text = sanitize_log_message(line)
    if text.strip():
        logger.debug("biliup 运行 | %s", text)


# ================================================================ 健康检查
def _tool(exe: str | None, *, version: str | None = None) -> BiliToolRead:
    return BiliToolRead(found=bool(exe), path=exe, version=version)


def _biliup_tool(settings: AppSettings, runner: Runner | None) -> BiliToolRead:
    exe = find_biliup(settings.biliup_path)
    tool = _tool(exe, version=biliup_version(exe, runner) if exe else None)
    if tool.found:
        return tool
    stub, real = detect_broken_stub(settings.biliup_path)
    if stub and real:
        # 这一档必须与「没装」分开：用户会去重新 winget install，而问题其实是一个 0 字节文件。
        tool.problem = "PATH 上的 biliup.exe 是一个 0 字节空文件，执行时会报「不是此 OS 平台的有效应用」"
        tool.advice = "点「修复」即可：会用 WinGet 包目录里的真实程序覆盖它"
    else:
        tool.problem, tool.advice = MISSING_BILIUP
    return tool


def _login_read(settings: AppSettings, jar: dict[str, str]) -> BiliLoginRead:
    summary = cookie_summary(jar)
    return BiliLoginRead(
        present=summary["present"],
        cookie_count=summary["count"],
        names=summary["names"],
        missing_required=summary["missing_required"],
        missing_recommended=summary["missing_recommended"],
        tracked_by_git=is_cookie_tracked_by_git(settings.bili_cookie_path, REPO_ROOT),
    )


def build_prefill(session: Session, job: Job, item_id: str | None = None) -> BiliPrefillRead:
    """预填表单：单视频给一组值，合集给「模板 + 逐条预览」。

    逐条预览是**必需**的，不是锦上添花：模板渲染后可能超限，而超限必须在提交前告诉用户
    是**第几条**超了（design §7）。这里算出来的 problems 就是提交校验会给出的同一份。
    """
    items = list(
        session.exec(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.index)).all()
    )
    base_dir = Path(job.download_dir) if job.download_dir else None
    is_collection = len(items) > 1 or (item_id is None and len(items) != 1)

    if not is_collection and items:
        item = next((row for row in items if row.id == item_id), items[0])
        source_title = item.title
        meta = default_single(job_title=job.title, item_title=source_title, source_url=item.source_url)
        comment = default_comment(source_url=item.source_url)
        return BiliPrefillRead(
            job_id=job.id,
            job_title=job.title,
            kind="single",
            comment=comment,
            metadata=_metadata_model(meta),
            items=[_prefill_item(item, meta, base_dir)],
            problems=validate_metadata(meta) + validate_comment(comment),
            partitions=partition_options(),
            limits=bili_limits(),
        )

    template = default_collection_template(job.title)
    targets = [
        UploadTarget(
            index=item.index,
            title=item.title,
            video_path=_item_path(item, base_dir) or Path(""),
            source_url=item.source_url,
            item_id=item.id,
        )
        for item in items
    ]
    rendered = render_collection(template, targets, job_title=job.title) if targets else []
    comment = default_comment()
    problems = validate_collection(template, targets, job_title=job.title) if targets else []
    problems += validate_comment(comment)
    return BiliPrefillRead(
        job_id=job.id,
        job_title=job.title,
        kind="collection",
        comment=comment,
        template=_metadata_model(template),
        items=[
            _prefill_item(item, rendered[position], base_dir)
            for position, item in enumerate(items)
            if position < len(rendered)
        ],
        problems=problems,
        partitions=partition_options(),
        limits=bili_limits(),
        season_desc=default_season_desc(job.title, len(items)),
    )


def _metadata_model(value: UploadMetadata | MetadataTemplate) -> BiliMetadata:
    """`UploadMetadata` 与 `MetadataTemplate` 都投影成同一个线上模型 ——
    单视频的「一组值」与合集的「模板」在界面上是**同一个表单**，只是标题/简介的语义不同。"""
    if isinstance(value, UploadMetadata):
        return BiliMetadata(
            title=value.title,
            desc=value.desc,
            tags=value.tags,
            tid=value.tid,
            copyright=normalize_copyright(value.copyright),
            source=effective_source(value.copyright, value.source),
        )
    return BiliMetadata(
        title=value.title_template,
        desc=value.desc_template,
        tags=value.tags,
        tid=value.tid,
        copyright=normalize_copyright(value.copyright),
        # 模板上没有共用来源（每条视频各用各的原始链接）。**留空而不是回填第一条**：
        # 回填一个看着像「所有条目都用它」的值，正是这条设计要避免的误导。
        source="",
    )


def _item_path(item: JobItem, base_dir: Path | None) -> Path | None:
    return existing_output_file(item.output_path, item.source_url, base_dir)


def _prefill_item(item: JobItem, meta: UploadMetadata, base_dir: Path | None) -> BiliPrefillItem:
    return BiliPrefillItem(
        index=item.index,
        item_id=item.id,
        source_title=item.title,
        source_url=item.source_url,
        title=meta.title,
        desc=meta.desc,
        tags=meta.tags,
        ready=_item_path(item, base_dir) is not None,
    )


# ================================================================ 管理器
class BiliManager:
    def __init__(
        self,
        engine: Engine,
        settings: AppSettings,
        service: YtDlpService,
        broker: EventBroker,
        *,
        transport: Transport | None = None,
        runner: Runner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.service = service
        self.broker = broker
        # 三个可替换接缝：测试注入假 transport / 假 runner / 假 sleeper 即可完全离线。
        self.transport: Transport = transport or default_transport()
        self.runner = runner
        self.sleeper = sleeper
        self._queue: asyncio.Queue[str | None] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._login: BiliLoginSession | None = None
        self._last_upload_finished_at: float | None = None
        self._progress_lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        if self._queue is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._queue = None

    async def resume_interrupted(self) -> int:
        """把重启前留在 `queued`/`uploading` 的记录折成可重试的失败，并返回条数。

        `uploading` 是**必然**会留下的：进程被杀时它正跑在 `biliup` 里，那一瞬间没有任何代码能收尾。
        折成 `failed` 而不是重新入队，是因为我们无法知道那次投稿在平台上到底成没成 ——
        擅自重投可能产生重复稿件，而这正是本功能最该避免的错（requirements 非目标）。
        """
        with Session(self.engine) as session:
            rows = session.exec(
                select(BiliUpload).where(BiliUpload.status.in_(sorted(IN_FLIGHT_STATUSES)))
            ).all()
            # job_id 必须在会话内取出：`commit()` 会让对象进入过期状态，
            # 出了 `with` 再读非主键属性会抛 DetachedInstanceError（而不是拿到 None）。
            touched = [(row.id, row.job_id) for row in rows]
            for row in rows:
                row.status = BiliUploadStatus.failed.value
                row.error = "这次上传被中断了（应用重启或进程退出）"
                row.advice = "点「重试」重新上传这一条；也可以先去 B站 创作中心确认它有没有被投上去"
                row.updated_at = utc_now()
                row.finished_at = row.updated_at
                session.add(row)
            if rows:
                session.commit()
                logger.info("bili 上传：把 %s 条中断记录折成可重试的失败", len(rows))
        for upload_id, job_id in touched:
            await self._publish(
                {"type": "bili_upload_finished", "job_id": job_id, "upload_id": upload_id}
            )
        return len(touched)

    # ------------------------------------------------------------ 登录态
    def current_jar(self) -> dict[str, str]:
        return load_cookie_jar(self.settings.bili_cookie_path)

    def login_session(self) -> BiliLoginSession:
        if self._login is None:
            self._login = BiliLoginSession(self.transport)
        return self._login

    def save_login(self, jar: dict[str, str], mid: int | None = None) -> str | None:
        """落盘登录态，并**顺手把投稿令牌换回来**。返回值是「没换成时的原因」，成功即 `None`。

        只写 cookies 是**不完整**的登录：`biliup` 每次启动都先拿 `token_info.access_token` 去
        `oauth2/info` 校验一次，没有令牌它连一个请求都不会发出去，而报错还是那个泛用的
        `-400 请求错误`（实测过，见 ai/bug-fix 对应记录）。所以「登录」的完成判据是
        **拿到令牌**，不是「文件里出现了 SESSDATA」。
        """
        token: TvToken | None = None
        warning: str | None = None
        try:
            token = exchange_tv_token(self.transport, jar, sleeper=self.sleeper)
        except Exception as exc:  # noqa: BLE001 - 换不到令牌也要把 cookies 留住：读接口还需要它
            warning = sanitize_log_message(exc)
            logger.warning("bili 换取投稿令牌失败：%s", warning)
        write_cookie_file(self.settings.bili_cookie_path, jar, mid, token)
        self._login = None
        return warning

    def ensure_upload_token(self) -> str | None:
        """给一份**已经落盘**的登录文件补上投稿令牌（「导入已有 cookies.json」走这里）。

        已经有令牌就原样返回 —— 不重复换：换一次会作废上一次的令牌，等于把同时在用的另一个
        客户端顶掉线。读不出内容时也不动那个文件（一张我们读不懂的文件更不该被我们清空）。
        """
        state = read_login_token(self.settings.bili_cookie_path)
        if state.has_access_token:
            return None
        jar = self.current_jar()
        if not jar:
            return None
        return self.save_login(jar, state.mid)

    def reset_login_session(self) -> None:
        """丢掉进行中的扫码会话。

        导入一份 cookies 文件之后必须调它：那次扫码会话的 `jar` 里可能已经攒了半套旧凭据，
        继续轮询会把「旧账号的 cookie + 新账号的扫码结果」混在一起落盘。
        """
        self._login = None

    def clear_login(self) -> bool:
        self._login = None
        return delete_cookie_file(self.settings.bili_cookie_path)

    def account(self) -> BiliAccountRead | None:
        jar = self.current_jar()
        if not jar or missing_required_cookies(jar):
            return None
        payload = fetch_account(self.transport, jar)
        if not payload:
            return None
        return BiliAccountRead(mid=payload.get("mid"), name=str(payload.get("uname") or ""))

    # ------------------------------------------------------------ 体检
    async def health(self, *, probe_account: bool = True) -> BiliStatusRead:
        return await asyncio.to_thread(self._health_sync, probe_account)

    def _health_sync(self, probe_account: bool) -> BiliStatusRead:
        tool = _biliup_tool(self.settings, self.runner)
        ffmpeg_path = self._ffmpeg_path()
        ffmpeg = _tool(ffmpeg_path)
        if not ffmpeg.found:
            ffmpeg.problem, ffmpeg.advice = MISSING_FFMPEG

        jar = self.current_jar()
        login = _login_read(self.settings, jar)
        if probe_account and login.present and not login.missing_required:
            login.account_checked = True
            login.account = self.account()

        blockers: list[BiliAdvice] = []
        if not tool.found:
            blockers.append(_advice(MISSING_BILIUP))
        if not login.present:
            blockers.append(_advice(MISSING_COOKIE))
        elif login.missing_required:
            blockers.append(_advice(MISSING_COOKIE_FIELDS))
        if login.tracked_by_git:
            blockers.append(_advice(COOKIE_TRACKED_BY_GIT))
        if login.present and not login.missing_required and not read_login_token(
            self.settings.bili_cookie_path
        ).has_access_token:
            # 凭据齐、账号也认得，但**没有投稿令牌**。这一档过去是「静默通过」的：
            # 面板三项全绿，一投稿就必失败，而失败原因（-400）在本地读不出来。
            blockers.append(_advice(MISSING_UPLOAD_TOKEN))
        if login.account_checked and login.account is None:
            # 凭据齐全但平台说没登录 —— 这是「过期」，与「没有凭据」是两回事。
            blockers.append(_advice(NOT_LOGGED_IN))

        with Session(self.engine) as session:
            in_flight = len(
                session.exec(
                    select(BiliUpload).where(BiliUpload.status.in_(sorted(IN_FLIGHT_STATUSES)))
                ).all()
            )
        return BiliStatusRead(
            ready=not blockers,
            biliup=tool,
            ffmpeg=ffmpeg,
            login=login,
            blockers=blockers,
            partitions=partition_options(),
            limits=bili_limits(),
            gap_seconds=self._gap_seconds(),
            uploads_in_flight=in_flight,
        )

    def repair_tool(self) -> str:
        """修复 WinGet Links 下的 0 字节空壳。返回人话结果（无论成没成）。"""
        return repair_broken_stub(self.settings.biliup_path) or "没有发现需要修复的 biliup"

    def _ffmpeg_path(self) -> str | None:
        # 探测只有一处（`ytdlp_service`），这里读它 —— 再写一份会出现「诊断面板说可用、抽封面却失败」。
        probe = getattr(self.service, "ffmpeg_executable", None)
        try:
            return probe() if callable(probe) else None
        except Exception:  # noqa: BLE001 - 探不到就当没有，封面是可选项
            return None

    def _gap_seconds(self) -> float:
        return max(0.0, float(self.settings.bili_upload_gap_seconds or 0.0))

    # ------------------------------------------------------------ 提交
    async def submit_single(
        self,
        *,
        job_id: str,
        item_id: str | None,
        meta: UploadMetadata,
        video_path: Path,
        make_cover: bool,
        comment: str,
    ) -> BiliUploadRead:
        self._require_ready()
        if not Path(video_path).is_file():
            raise BiliPreconditionError(MISSING_VIDEO[0], MISSING_VIDEO[1], code="missing_video")
        with Session(self.engine) as session:
            self._guard_idle(session, job_id)
            row = self._upsert_upload(
                session,
                job_id=job_id,
                item_id=item_id,
                meta=meta,
                video_path=Path(video_path),
                make_cover=make_cover,
                comment=comment,
            )
            session.commit()
            read = read_upload(row, index=self._index_for(session, job_id, item_id))
        await self._enqueue(row.id)
        return read

    async def submit_collection(
        self,
        *,
        job_id: str,
        template: MetadataTemplate,
        targets: Sequence[UploadTarget],
        paths: Sequence[Path | None],
        season_title: str,
        season_desc: str,
        make_cover: bool,
        comment: str,
    ) -> BiliJobUploadsRead:
        self._require_ready()
        missing = [
            target.title
            for target, path in zip(targets, paths, strict=True)
            if path is None or not Path(path).is_file()
        ]
        if missing:
            raise BiliPreconditionError(
                f"有 {len(missing)} 条视频还没下载好，不能上传：" + "、".join(missing[:3]),
                "先等这些下载任务跑完，或对它们单独重试下载",
                code="missing_video",
            )
        with Session(self.engine) as session:
            self._guard_idle(session, job_id)
            upload_ids: list[str] = []
            for target, meta, path in zip(
                targets,
                render_collection(template, targets, job_title=season_title or ""),
                paths,
                strict=True,
            ):
                row = self._upsert_upload(
                    session,
                    job_id=job_id,
                    item_id=target.item_id,
                    meta=meta,
                    video_path=Path(path) if path else Path(""),
                    make_cover=make_cover,
                    comment=comment,
                )
                upload_ids.append(row.id)
            season = self._upsert_season(
                session,
                job_id=job_id,
                title=season_title,
                desc=season_desc,
                upload_ids=upload_ids,
            )
            session.commit()
            read = read_job_uploads(session, job_id)
        for upload_id in upload_ids:
            await self._enqueue(upload_id)
        await self._publish(
            {"type": "bili_season_queued", "job_id": job_id, "season_id": season.id, "count": len(upload_ids)}
        )
        return read

    def _guard_idle(self, session: Session, job_id: str) -> None:
        """同一任务里还有在跑的记录时拒绝新提交。两个 worker 抢同一个任务只会互相踩状态。"""
        rows = session.exec(select(BiliUpload).where(BiliUpload.job_id == job_id)).all()
        busy = [row for row in rows if row.status in IN_FLIGHT_STATUSES]
        if busy:
            raise BiliPreconditionError(
                f"这个任务有 {len(busy)} 条视频正在上传，请等它们结束",
                "上传是串行的（避免被 B站 当作批量刷量限流），稍等片刻再提交",
                code="in_flight",
            )

    def _upsert_upload(
        self,
        session: Session,
        *,
        job_id: str,
        item_id: str | None,
        meta: UploadMetadata,
        video_path: Path,
        make_cover: bool,
        comment: str,
    ) -> BiliUpload:
        row = self._find_upload(session, job_id, item_id)
        if row is None:
            row = BiliUpload(id=_new_id(), job_id=job_id, item_id=item_id)
        else:
            row.attempt += 1
            # 平台标识与上一次的对账结论全部作废：这是**一次新的投稿**，
            # 留着旧 bvid 会让界面显示一个已经不指向本次投稿的链接。
            row.bvid = None
            row.aid = None
            row.cid = None
            row.verified_state = None
            row.verified_note = None
            row.cover_url = None
        row.title = meta.title
        row.desc = meta.desc
        row.tags = meta.tags
        row.tid = meta.tid
        # 版权与来源一起落库：重投时 `_load_request` 用它们重建要提交的元数据，
        # 而 worker 是在**另一次请求之后**才跑的 —— 只活在请求对象里的选项会静默丢失。
        row.copyright = normalize_copyright(meta.copyright)
        row.source = effective_source(meta.copyright, meta.source)
        row.video_path = str(video_path)
        row.cover_path = None
        row.make_cover = make_cover
        row.comment = comment or ""
        row.status = BiliUploadStatus.queued.value
        row.progress = 0.0
        row.error = None
        row.advice = None
        row.log_tail = None
        row.started_at = None
        row.finished_at = None
        row.updated_at = utc_now()
        session.add(row)
        return row

    def _upsert_season(
        self,
        session: Session,
        *,
        job_id: str,
        title: str,
        desc: str,
        upload_ids: list[str],
    ) -> BiliSeason:
        season = next(iter(list_seasons(session, job_id)), None)
        if season is None:
            season = BiliSeason(id=_new_id(), job_id=job_id)
        # `season_id` **不清空**：重投时归集要回到同一个合集，重新建一个会留下两个同名合集。
        season.title = title or season.title
        season.desc = desc or season.desc
        season.status = BiliSeasonStatus.pending.value
        season.upload_ids_json = json.dumps(upload_ids)
        season.error = None
        season.advice = None
        season.log_tail = None
        season.started_at = None
        season.finished_at = None
        season.updated_at = utc_now()
        session.add(season)
        return season

    def _find_upload(self, session: Session, job_id: str, item_id: str | None) -> BiliUpload | None:
        rows = session.exec(select(BiliUpload).where(BiliUpload.job_id == job_id)).all()
        for row in rows:
            if (row.item_id or None) == (item_id or None):
                return row
        return None

    def _index_for(self, session: Session, job_id: str, item_id: str | None) -> int:
        if not item_id:
            return 1
        item = session.get(JobItem, item_id)
        return item.index if item is not None else 1

    def _require_ready(self) -> None:
        if not find_biliup(self.settings.biliup_path):
            raise BiliPreconditionError(*MISSING_BILIUP, code="missing_biliup")
        jar = self.current_jar()
        if not jar:
            raise BiliPreconditionError(*MISSING_COOKIE, code="missing_cookie")
        missing = missing_required_cookies(jar)
        if missing:
            raise BiliPreconditionError(
                f"{MISSING_COOKIE_FIELDS[0]}（缺 {'、'.join(missing)}）",
                MISSING_COOKIE_FIELDS[1],
                code="missing_cookie_fields",
            )
        self._require_upload_token()

    def _require_upload_token(self) -> None:
        """令牌是**硬前置**，必须在提交前拦下来。

        没有它时 biliup 会照常启动、照常打印，然后立刻退出，报一个泛用的 `-400 请求错误` ——
        用户看到的是「投稿失败，而且没能识别出具体原因」，而真实原因（缺令牌）在本地没法从
        那个错误码里读出来。拦在提交前，至少能把话说清楚。
        """
        if not read_login_token(self.settings.bili_cookie_path).has_access_token:
            raise BiliPreconditionError(*MISSING_UPLOAD_TOKEN, code="missing_upload_token")

    async def _enqueue(self, upload_id: str) -> None:
        await self.start()
        assert self._queue is not None
        await self._queue.put(upload_id)

    # ------------------------------------------------------------ 重试
    async def retry_upload(self, upload_id: str) -> BiliRetryRead:
        """重试一条稿件。**三种终态走三条不同的路**（本模块开头的第 3 条决定）。

        `unverified` 尤其不能当成失败处理：它的投稿命令是成功过的，重投等于真的投第二遍。
        """
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            if row is None:
                raise KeyError(upload_id)
            status = row.status
            read = read_upload(row, index=self._index_for(session, row.job_id, row.item_id))
            title, item_id = row.title, row.item_id
            index = read.index
            if status in IN_FLIGHT_STATUSES:
                return BiliRetryRead(action="none", message="这一条正在上传中，请稍候", upload=read)
            if status in {BiliUploadStatus.published.value, BiliUploadStatus.reviewing.value}:
                return BiliRetryRead(
                    action="none",
                    message="这一条已经上传成功，没有重复投稿（避免在 B 站留下重复稿件）",
                    upload=read,
                )

        if status == BiliUploadStatus.unverified.value:
            updated = await asyncio.to_thread(self._reverify_work, upload_id, title, index, item_id)
            return BiliRetryRead(action="reverify", message="已重新核对一次", upload=updated)

        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            assert row is not None
            row.status = BiliUploadStatus.queued.value
            row.attempt = min(row.attempt + 1, MAX_ATTEMPTS)
            row.progress = 0.0
            row.error = None
            row.advice = None
            row.started_at = None
            row.finished_at = None
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            read = read_upload(row, index=index)
        await self._enqueue(upload_id)
        return BiliRetryRead(action="reupload", message="已重新排队上传", upload=read)

    async def retry_season(self, season_record_id: str) -> BiliSeasonRetryRead:
        with Session(self.engine) as session:
            season = session.get(BiliSeason, season_record_id)
            if season is None:
                raise KeyError(season_record_id)
            job_id = season.job_id
        read = await asyncio.to_thread(self._group_work, job_id, season_record_id)
        return BiliSeasonRetryRead(
            message="已重新尝试归集" if read.status != BiliSeasonStatus.failed.value else "归集还是没有成功",
            season=read,
        )

    def _reverify_work(self, upload_id: str, title: str, index: int, item_id: str | None) -> BiliUploadRead:
        context = self._context()
        result = reverify(context, title=title, index=index, item_id=item_id)
        self._store_result(upload_id, result)
        self._publish_threadsafe(
            {"type": "bili_upload_finished", "job_id": self._job_of(upload_id), "upload_id": upload_id}
        )
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            assert row is not None
            return read_upload(row, index=index)

    def _job_of(self, upload_id: str) -> str | None:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            return row.job_id if row is not None else None

    # ------------------------------------------------------------ worker
    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            upload_id = await self._queue.get()
            try:
                if upload_id is None:
                    return
                await asyncio.to_thread(self._run_upload_work, upload_id)
            except Exception:  # noqa: BLE001 - 单条收尾崩溃不能带走 worker（否则队列少一个消费口）
                logger.exception("bili upload worker crashed: upload_id=%s", upload_id)
                self._mark_failed_after_crash(upload_id)
            finally:
                self._queue.task_done()

    def _mark_failed_after_crash(self, upload_id: str) -> None:
        try:
            with Session(self.engine) as session:
                row = session.get(BiliUpload, upload_id)
                if row is None or row.status not in IN_FLIGHT_STATUSES:
                    return
                row.status = BiliUploadStatus.failed.value
                row.error = CRASHED_UPLOAD_ERROR
                row.advice = CRASHED_UPLOAD_ADVICE
                row.finished_at = utc_now()
                row.updated_at = row.finished_at
                session.add(row)
                session.commit()
                job_id = row.job_id
            self._publish_threadsafe(
                {"type": "bili_upload_finished", "job_id": job_id, "upload_id": upload_id}
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark a crashed bili upload as failed: upload_id=%s", upload_id)

    def _run_upload_work(self, upload_id: str) -> None:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            if row is None or row.status != BiliUploadStatus.queued.value:
                return
            row.status = BiliUploadStatus.uploading.value
            row.started_at = utc_now()
            row.updated_at = row.started_at
            session.add(row)
            session.commit()
            job_id = row.job_id
        self._publish_threadsafe({"type": "bili_upload_started", "job_id": job_id, "upload_id": upload_id})

        try:
            context = self._context(
                on_progress=lambda value: self._store_progress(upload_id, value),
            )
            self._respect_gap()
            target, meta, make_cover, comment = self._load_request(upload_id)
            result = run_single(
                context,
                target,
                meta,
                make_cover=make_cover,
                cover_key=upload_id,
                comment=comment or None,
            )
        except BiliPreconditionError as exc:
            # 排队之后前置条件又坏了（例如用户在中途清了登录态）。落成可读失败，不重排队。
            result = UploadResult(
                index=1,
                item_id=None,
                status=BiliUploadStatus.failed.value,
                error=exc.message,
                advice=exc.advice,
            )
        finally:
            self._last_upload_finished_at = time.monotonic()

        self._store_result(upload_id, result)
        self._publish_threadsafe(
            {"type": "bili_upload_finished", "job_id": job_id, "upload_id": upload_id, "status": result.status}
        )
        self._maybe_group(upload_id)

    def _load_request(self, upload_id: str) -> tuple[UploadTarget, UploadMetadata, bool, str]:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            assert row is not None
            index = self._index_for(session, row.job_id, row.item_id)
            target = UploadTarget(
                index=index,
                title=row.title,
                video_path=Path(row.video_path),
                item_id=row.item_id,
            )
            meta = UploadMetadata(
                title=row.title,
                desc=row.desc,
                tags=row.tags,
                tid=row.tid,
                copyright=normalize_copyright(row.copyright),
                source=effective_source(row.copyright, row.source),
            )
            return target, meta, bool(row.make_cover), row.comment or ""

    def _context(self, *, on_progress: Callable[[float], None] | None = None) -> UploadContext:
        """装配一次上传的外部依赖。前置条件在这里**再查一遍** —— 排队期间状态可能已经变了。"""
        exe = find_biliup(self.settings.biliup_path)
        if not exe:
            raise BiliPreconditionError(*MISSING_BILIUP, code="missing_biliup")
        jar = self.current_jar()
        if not jar:
            raise BiliPreconditionError(*MISSING_COOKIE, code="missing_cookie")
        missing = missing_required_cookies(jar)
        if missing:
            raise BiliPreconditionError(
                f"{MISSING_COOKIE_FIELDS[0]}（缺 {'、'.join(missing)}）",
                MISSING_COOKIE_FIELDS[1],
                code="missing_cookie_fields",
            )
        self._require_upload_token()
        return UploadContext(
            biliup_exe=exe,
            cookie_path=self.settings.bili_cookie_path,
            jar=jar,
            transport=self.transport,
            ffmpeg_path=self._ffmpeg_path(),
            cover_dir=self.settings.bili_cover_dir,
            runner=self.runner,
            sleeper=self.sleeper,
            on_progress=on_progress,
            # 把 biliup 的每一行透到日志（DEBUG）。失败时的那几行会由 `bili_upload` 用 WARNING
            # 再打一遍带前缀的副本 —— 那条路径不依赖日志级别，用户照失败建议去搜就一定搜得到。
            on_log=_log_biliup_line,
        )

    def _respect_gap(self) -> None:
        """距上次投稿结束不足间隔时补足。放在**上传之前**，所以排队的第一条不会被白等。"""
        gap = self._gap_seconds()
        if gap <= 0 or self._last_upload_finished_at is None:
            return
        remaining = gap - (time.monotonic() - self._last_upload_finished_at)
        if remaining > 0:
            logger.info("bili 上传：距上次投稿不足 %.0f 秒，等待 %.0f 秒（抗账号级频控）", gap, remaining)
            self.sleeper(remaining)

    # ------------------------------------------------------------ 落账
    def _store_progress(self, upload_id: str, value: float) -> None:
        with self._progress_lock:
            try:
                with Session(self.engine) as session:
                    row = session.get(BiliUpload, upload_id)
                    if row is None or row.status != BiliUploadStatus.uploading.value:
                        return
                    row.progress = max(row.progress, value)
                    row.updated_at = utc_now()
                    session.add(row)
                    session.commit()
            except Exception:  # noqa: BLE001 - 进度写不进去不该中断正在跑的投稿
                logger.exception("bili 上传：写进度失败 upload_id=%s", upload_id)

    def _store_result(self, upload_id: str, result: UploadResult) -> None:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            if row is None:
                return
            row.status = result.status
            row.progress = max(row.progress, result.progress)
            row.bvid = result.bvid
            row.aid = result.aid
            row.cid = result.cid
            row.cover_path = result.cover_path or row.cover_path
            row.cover_url = result.cover_url or row.cover_url
            row.verified_state = result.verified_state
            row.verified_note = result.verified_note
            row.error = result.error
            row.advice = result.advice
            row.log_tail = result.log_tail
            row.finished_at = utc_now()
            row.updated_at = row.finished_at
            session.add(row)
            session.commit()

    # ------------------------------------------------------------ 归集
    def _maybe_group(self, upload_id: str) -> None:
        """本批次最后一条稿件落地后自动归集。

        **只在 `pending` 时自动归集**：`failed` 是给用户看的结论，自动重试会让它在
        界面上反复闪动，而重试入口是显式的（`retry_season`）。
        """
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            if row is None:
                return
            season = self._season_of(session, row)
            if season is None or season.status != BiliSeasonStatus.pending.value:
                return
            job_id, season_id = row.job_id, season.id
        self._publish_threadsafe({"type": "bili_season_started", "job_id": job_id, "season_id": season_id})
        self._group_work(job_id, season_id)

    def _season_of(self, session: Session, upload: BiliUpload) -> BiliSeason | None:
        for season in list_seasons(session, upload.job_id):
            try:
                ids = [str(item) for item in json.loads(season.upload_ids_json or "[]")]
            except ValueError:
                continue
            if upload.id in ids:
                return season
        return None

    def _group_work(self, job_id: str, season_record_id: str) -> BiliSeasonRead:
        with Session(self.engine) as session:
            season = session.get(BiliSeason, season_record_id)
            if season is None:
                raise KeyError(season_record_id)
            try:
                ids = [str(item) for item in json.loads(season.upload_ids_json or "[]")]
            except ValueError:
                ids = []
            rows = [session.get(BiliUpload, item) for item in ids]
            rows = [row for row in rows if row is not None]
            if any(row.status in IN_FLIGHT_STATUSES for row in rows):
                # 还有在跑的条目：结论会是「pending」，写库反而是降级，直接不改。
                return read_season(season)
            # 结果列表在会话内构造：`commit()` 之后对象会过期，跨出 `with` 再读属性会抛异常。
            results = [
                UploadResult(
                    index=1,
                    item_id=row.item_id,
                    status=row.status,
                    title=row.title,
                    aid=row.aid,
                    cid=row.cid,
                    cover_url=row.cover_url,
                )
                for row in rows
            ]
            existing_season_id = season.season_id
            title, desc = season.title, season.desc
            season.status = BiliSeasonStatus.grouping.value
            season.started_at = season.started_at or utc_now()
            season.updated_at = utc_now()
            session.add(season)
            session.commit()

        try:
            outcome = group_into_season(
                self._context(),
                results,
                season_title=title,
                season_desc=desc,
                existing_season_id=existing_season_id,
            )
        except BiliPreconditionError as exc:
            outcome = _season_outcome_from_error(exc)

        with Session(self.engine) as session:
            season = session.get(BiliSeason, season_record_id)
            assert season is not None
            season.status = outcome.status
            season.season_id = outcome.season_id or season.season_id
            season.cover_url = outcome.cover_url or season.cover_url
            season.error = outcome.error
            season.advice = outcome.advice
            season.log_tail = outcome.log_tail
            if outcome.status != BiliSeasonStatus.grouping.value:
                season.finished_at = utc_now()
            season.updated_at = utc_now()
            session.add(season)
            session.commit()
            read = read_season(season)
        self._publish_threadsafe({"type": "bili_season_finished", "job_id": job_id, "season_id": season_record_id})
        return read

    # ------------------------------------------------------------ 查询
    def job_uploads(self, session: Session, job_id: str) -> BiliJobUploadsRead:
        return read_job_uploads(session, job_id)

    # ------------------------------------------------------------ 事件
    async def _publish(self, payload: dict) -> None:
        await self.broker.publish(payload)

    def _publish_threadsafe(self, payload: dict) -> None:
        """从工作线程发布。`loop.call_soon_threadsafe` 是**唯一**能把调度交回事件循环的方式；
        直接 await 会报「no running event loop」（这是 job_manager 已经踩过的一条）。"""
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.broker.publish(payload)))
        except RuntimeError:
            # 事件循环已关闭（进程正在退出）：丢弃事件，不能因此让 worker 抛异常。
            logger.debug("bili 事件发布被跳过：事件循环已关闭")


def _season_outcome_from_error(exc: BiliPreconditionError) -> SeasonOutcome:
    """归集时前置条件又坏了（例如中途清了登录态）：落成一条可读失败，而不是把异常抛出去。"""
    return SeasonOutcome(
        status=BiliSeasonStatus.failed.value,
        error=exc.message,
        advice=exc.advice,
    )
