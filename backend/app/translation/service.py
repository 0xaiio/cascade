"""字幕翻译的编排层：DB 索引 + JSON 逐条数据 + 后台分块执行 + SSE 进度。

这一层只做编排：翻译规则在 `align`，HTTP 在 `local`，文件在 `store` / `render`。
它不直接拼 HTTP 参数、不自己写 SRT 文本。

**配置即时生效**：provider 由工厂现场构造，工厂读的是可变的 `AppSettings`；
设置在运行时改完，下一条请求就用新地址，不需要重启（见 ai/bug-fix/032 的教训）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, select

from ..bilibili.bili_subtitle import Cue
from ..contracts.config import AppSettings
from ..contracts.models import JobItem, SubtitleTranslation
from ..contracts.schemas import TranslationCueRead, TranslationExportRead, TranslationRead
from ..download.output_paths import subtitle_language_from_path
from ..infra.events import EventBroker
from .align import AlignedCue, apply_chunk, assemble, make_chunks, parse_subtitle_file
from .provider import (
    TranslationChunk,
    TranslationError,
    TranslationItem,
    TranslationProvider,
    TranslationReply,
)
from .model_server import ModelServerError, ensure_model_server
from .render import export_bilingual_files, export_files
from .store import cues_path, cue_records, load_cues, new_translation_id, save_cues

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[], TranslationProvider]
EnsureServer = Callable[[], str | None]
ACTIVE_STATES = {"queued", "running"}

# 本机 8B 模型实测约 3.8 token/s：32 条一块会超过单请求超时，8 条一块约 1 分钟内完成。
# 分块小也让进度条是真的在动，而不是「卡三分钟突然跳 32 条」。
CHUNK_MAX_CUES = 8
CHUNK_MAX_CHARS = 1200


class TranslationService:
    def __init__(
        self,
        engine,
        settings: AppSettings,
        provider_factory: ProviderFactory,
        broker: EventBroker,
        ensure_server: EnsureServer | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.provider_factory = provider_factory
        self.broker = broker
        # 默认会按需拉起本机模型服务；测试注入 lambda: None 即声明「模型由我接管」，
        # 与 provider 的注入纪律一致，避免单测在你的机器上真起一个 llama-server。
        self.ensure_server: EnsureServer = ensure_server or (
            lambda: ensure_model_server(settings)
        )
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._cancelled: set[str] = set()

    # ------------------------------------------------------------ 读取
    def get(self, translation_id: str) -> TranslationRead:
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            return self._read(row)

    def _read(self, row: SubtitleTranslation) -> TranslationRead:
        data = load_cues(self.settings.data_dir, row.id)
        cues = [
            TranslationCueRead(
                index=int(item.get("index", 0)),
                start=float(item.get("start", 0)),
                end=float(item.get("end", 0)),
                source=str(item.get("source", "")),
                translation=str(item.get("translation", "")),
                edited=bool(item.get("edited", False)),
                state=str(item.get("state", "pending")),
                error=item.get("error"),
            )
            for item in data["cues"]
        ]
        return TranslationRead(
            id=row.id,
            job_id=row.job_id,
            item_id=row.item_id,
            source_path=row.source_path,
            source_language=row.source_language,
            state=row.state,
            total=row.total,
            translated=row.translated,
            failed=row.failed,
            source_sha256=row.source_sha256,
            display_mode=row.display_mode,
            output_path=row.output_path,
            bilingual_path=row.bilingual_path,
            error=row.error,
            cues=cues,
        )

    def find(self, job_id: str, item_id: str | None = None) -> TranslationRead | None:
        """按 `(job_id, item_id)` 找最新一次翻译任务（**包含已完成的**）。

        入口先查后建：重启后再次点击不应该新建任务、更不该再发一次模型请求。
        """
        with Session(self.engine) as session:
            query = select(SubtitleTranslation).where(SubtitleTranslation.job_id == job_id)
            if item_id:
                query = query.where(SubtitleTranslation.item_id == item_id)
            row = session.exec(query.order_by(SubtitleTranslation.created_at.desc())).first()
            return self._read(row) if row is not None else None

    # ------------------------------------------------------------ 创建
    def create(self, job_id: str, item_id: str | None = None) -> TranslationRead:
        with Session(self.engine) as session:
            item = session.get(JobItem, item_id) if item_id else None
            if item is None:
                item = session.exec(
                    select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)
                ).first()
            if item is None or not item.subtitle_path:
                raise HTTPException(status_code=409, detail="这一条没有已下载的字幕，无法翻译")
            path = Path(item.subtitle_path)
            if not path.is_file():
                raise HTTPException(status_code=409, detail="字幕文件已不在磁盘上，无法翻译")

            existing = session.exec(
                select(SubtitleTranslation)
                .where(SubtitleTranslation.job_id == job_id, SubtitleTranslation.item_id == item.id)
                .order_by(SubtitleTranslation.created_at.desc())
            ).first()
            if existing is not None and existing.state in ACTIVE_STATES:
                return self._read(existing)  # 幂等入口：重复点不会起第二次任务

            source = parse_subtitle_file(path)
            if not source:
                raise HTTPException(status_code=409, detail="字幕文件里没有可用条目，无法翻译")
            language = subtitle_language_from_path(path) or item.subtitle_language
            if language and language.startswith("zh"):
                raise HTTPException(status_code=409, detail="这条字幕已经是中文，不需要翻译")

            translation_id = new_translation_id()
            aligned = assemble(source, {})
            data = {
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "cues": cue_records(list(aligned), {index: "pending" for index in range(len(source))}),
            }
            save_cues(self.settings.data_dir, translation_id, data)
            row = SubtitleTranslation(
                id=translation_id,
                job_id=job_id,
                item_id=item.id,
                source_path=str(path),
                source_language=language,
                state="queued",
                total=len(source),
                translated=0,
                failed=0,
                source_sha256=data["source_sha256"],
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._read(row)

    # ------------------------------------------------------------ 编辑与重试
    def set_display_mode(self, translation_id: str, mode: str) -> TranslationRead:
        """切换「仅中文 / 中英双语同屏」。只改呈现，不改译文与时间轴。"""
        if mode not in {"zh", "bilingual"}:
            raise HTTPException(status_code=422, detail="呈现模式只能是 zh 或 bilingual")
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            row.display_mode = mode
            from ..contracts.models import utc_now

            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            return self._read(row)

    def edit(self, translation_id: str, index: int, text: str) -> TranslationRead:
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            data = load_cues(self.settings.data_dir, translation_id)
            record = next((item for item in data["cues"] if int(item.get("index", -1)) == index), None)
            if record is None:
                raise HTTPException(status_code=404, detail="找不到这一条字幕")
            record["translation"] = text
            record["edited"] = True
            record["state"] = "translated"
            record["error"] = None
            save_cues(self.settings.data_dir, translation_id, data)
            self._recount(row, data)
            session.add(row)
            session.commit()
            return self._read(row)

    def _recount(self, row: SubtitleTranslation, data: dict) -> None:
        cues = data["cues"]
        row.translated = sum(1 for item in cues if item.get("state") == "translated")
        row.failed = sum(1 for item in cues if item.get("state") == "failed")

    def cancel(self, translation_id: str) -> TranslationRead:
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            if row.state in ACTIVE_STATES:
                self._cancelled.add(translation_id)
                if row.state == "queued":
                    row.state = "cancelled"
                    from ..contracts.models import utc_now

                    row.updated_at = utc_now()
                    session.add(row)
                    session.commit()
            return self._read(row)

    def retry(self, translation_id: str) -> TranslationRead:
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            if row.state in ACTIVE_STATES:
                raise HTTPException(status_code=409, detail="这次翻译还在进行中")
            data = load_cues(self.settings.data_dir, translation_id)
            pending = False
            for item in data["cues"]:
                if item.get("state") == "failed":
                    item["state"] = "pending"
                    item["error"] = None
                    pending = True
            if not pending:
                raise HTTPException(status_code=409, detail="没有可重试的失败条目")
            save_cues(self.settings.data_dir, translation_id, data)
            row.state = "queued"
            row.error = None
            self._recount(row, data)
            from ..contracts.models import utc_now

            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            return self._read(row)

    # ------------------------------------------------------------ 导出
    def export(self, translation_id: str) -> TranslationExportRead:
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            data = load_cues(self.settings.data_dir, translation_id)
            source = parse_subtitle_file(row.source_path)
            aligned = [
                AlignedCue(
                    index=int(item.get("index", 0)),
                    start=float(item.get("start", 0)),
                    end=float(item.get("end", 0)),
                    source=str(item.get("source", "")),
                    translation=str(item.get("translation", "")),
                    edited=bool(item.get("edited", False)),
                )
                for item in data["cues"]
            ]
            try:
                if row.display_mode == "bilingual":
                    srt_path, bilingual_path, bcc_path = export_bilingual_files(
                        source, aligned, row.source_path
                    )
                else:
                    srt_path, bilingual_path = export_files(source, aligned, row.source_path)
                    bcc_path = None
            except TranslationError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            row.output_path = srt_path
            row.bilingual_path = bilingual_path
            from ..contracts.models import utc_now

            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            return TranslationExportRead(
                srt_path=srt_path,
                bilingual_path=bilingual_path,
                cue_count=len(aligned),
                bcc_path=bcc_path,
            )

    def delete(self, translation_id: str) -> None:
        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="找不到这次字幕翻译")
            if row.state == "running":
                raise HTTPException(status_code=409, detail="这次翻译还在进行中，先取消")
            session.delete(row)
            session.commit()
        path = cues_path(self.settings.data_dir, translation_id)
        if path.is_file():
            path.unlink()

    # ------------------------------------------------------------ 后台执行
    def start(self, translation_id: str, loop: asyncio.AbstractEventLoop) -> None:
        """由路由在后台调用；重复调用同一个 id 不会起第二次。"""
        with self._lock:
            if translation_id in self._running:
                return
            self._running.add(translation_id)
            self._cancelled.discard(translation_id)
        try:
            self._run(translation_id, loop)
        finally:
            with self._lock:
                self._running.discard(translation_id)
                self._cancelled.discard(translation_id)

    def _publish(self, loop: asyncio.AbstractEventLoop, event: str, payload: dict) -> None:
        message = {"type": event, **payload}

        def emit() -> None:
            asyncio.create_task(self.broker.publish(message))

        loop.call_soon_threadsafe(emit)

    def _run(self, translation_id: str, loop: asyncio.AbstractEventLoop) -> None:
        from ..contracts.models import utc_now

        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None or row.state != "queued":
                return
            row.state = "running"
            row.updated_at = utc_now()
            session.add(row)
            session.commit()

        data = load_cues(self.settings.data_dir, translation_id)

        # 服务没起是「这一步没法做」，不是「某一条没译出来」。在发任何请求之前先
        # 确认，失败就一次性给结论，避免 8 条 cue 各背一句 WinError 10061。
        try:
            startup_note = self.ensure_server()
        except Exception as exc:  # noqa: BLE001 - 任何启动失败都要落成用户看得懂的结论
            message = (
                str(exc)
                if isinstance(exc, ModelServerError)
                else f"本机模型服务没能就绪：{exc}。详情见 data/logs/model-server.log"
            )
            logger.warning("字幕翻译：本机模型服务未就绪 id=%s error=%s", translation_id, message)
            for record in data["cues"]:
                if record.get("edited"):
                    continue
                record["state"] = "failed"
                record["error"] = message
            save_cues(self.settings.data_dir, translation_id, data)
            with Session(self.engine) as session:
                row = session.get(SubtitleTranslation, translation_id)
                if row is not None:
                    self._recount(row, data)
                    row.state = "failed"
                    row.error = message
                    row.updated_at = utc_now()
                    session.add(row)
                    session.commit()
                    job_id, item_id = row.job_id, row.item_id
                else:
                    job_id, item_id = "", None
            self._publish(
                loop,
                "subtitle_translation_finished",
                {"id": translation_id, "job_id": job_id, "item_id": item_id, "state": "failed"},
            )
            return
        if startup_note:
            logger.info("字幕翻译：%s id=%s", startup_note, translation_id)

        source = [
            Cue(start=float(item["start"]), end=float(item["end"]), content=str(item["source"]))
            for item in data["cues"]
        ]
        chunks = make_chunks(source, max_cues=CHUNK_MAX_CUES, max_chars=CHUNK_MAX_CHARS)
        provider = self.provider_factory()
        cancelled = False
        row = None
        state, translated, failed = "running", 0, 0

        for chunk in chunks:
            with self._lock:
                cancelled = translation_id in self._cancelled
            if cancelled:
                break
            pending = [
                item
                for item in chunk.items
                if data["cues"][item.index].get("state") in {"pending", "failed"}
            ]
            if not pending:
                continue
            try:
                chunk_items = [TranslationItem(index=item.index, text=item.text) for item in pending]
                context = source[chunk.items[0].index - 1].content if chunk.items[0].index > 0 else ""
                replies = provider.translate(chunk_items, context=context)
                chunk_for_apply = TranslationChunk(
                    index=chunk.index, items=tuple(chunk_items), context=chunk.context
                )
                try:
                    applied = apply_chunk(chunk_for_apply, replies)
                except TranslationError:
                    # 本机 8B 模型偶发「8 条只回 7 条」或某条给空串。若回复里的编号都是
                    # **合法子集**，就只重问缺的那几条；绝不把猜测填进空位、绝不挪动序号。
                    got = {
                        reply.index: reply.text
                        for reply in replies
                        if reply.index in {item.index for item in chunk_items}
                        and reply.text.strip()
                    }
                    missing = [item for item in chunk_items if item.index not in got]
                    if not missing or len(missing) == len(chunk_items):
                        raise
                    more = provider.translate(missing, context=context)
                    got.update(
                        {
                            reply.index: reply.text
                            for reply in more
                            if reply.index in {item.index for item in missing} and reply.text.strip()
                        }
                    )
                    applied = apply_chunk(
                        chunk_for_apply,
                        [TranslationReply(index=index, text=text) for index, text in got.items()],
                    )
            except TranslationError as exc:
                for item in pending:
                    data["cues"][item.index]["state"] = "failed"
                    data["cues"][item.index]["error"] = str(exc)
                logger.warning("字幕翻译分块失败：id=%s chunk=%s error=%s", translation_id, chunk.index, exc)
            else:
                for index, text in applied.items():
                    record = data["cues"][index]
                    if record.get("edited"):
                        continue  # 人工修订永不被机器覆盖
                    record["translation"] = text
                    record["state"] = "translated"
                    record["error"] = None

            save_cues(self.settings.data_dir, translation_id, data)
            with Session(self.engine) as session:
                row = session.get(SubtitleTranslation, translation_id)
                if row is not None:
                    self._recount(row, data)
                    session.add(row)
                    session.commit()
                    state, translated, failed = row.state, row.translated, row.failed
            self._publish(
                loop,
                "subtitle_translation_progress",
                {
                    "id": translation_id,
                    "job_id": row.job_id if row else "",
                    "item_id": row.item_id if row else None,
                    "state": state,
                    "translated": translated,
                    "failed": failed,
                    "total": len(source),
                },
            )

        with Session(self.engine) as session:
            row = session.get(SubtitleTranslation, translation_id)
            if row is None:
                return
            self._recount(row, data)
            if cancelled:
                row.state = "cancelled"
            elif row.failed:
                row.state = "failed"
                row.error = f"{row.failed} 条没译出来，点重试只补这些条目"
            else:
                row.state = "succeeded"
                row.error = None
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            final_state, job_id, item_id = row.state, row.job_id, row.item_id
        self._publish(
            loop,
            "subtitle_translation_finished",
            {"id": translation_id, "job_id": job_id, "item_id": item_id, "state": final_state},
        )
