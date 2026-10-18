from collections.abc import Generator
import logging
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import AppSettings

logger = logging.getLogger(__name__)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.fetchall()
        cursor.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        logger.warning("SQLite WAL pragma skipped because the database is locked")
    finally:
        cursor.close()


def create_app_engine(settings: AppSettings) -> Engine:
    settings.ensure_directories()
    sqlite_url = f"sqlite:///{settings.database_path.as_posix()}"
    engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    """追加式补列。**只登记「表已经存在之后才加上的列」** —— 新表由 `create_all` 建全，
    在这里再抄一遍等于把列定义变成两处真相。

    `bili_upload.create /*` 这两列属于前一种：`bili_upload` 表本身是随 B站 上传功能一起建的，
    但「是否生成封面」与「置顶评论文案」是在它建出来之后才补上的。

    `bili_upload.copyright` 的 DDL 默认值是 **`'1'`（自制）**，不是产品默认值 —— 理由：
    这一列补上时，库里已有的那些行**当时确实是按 `--copyright 1` 发出去的**（那时它写死在
    `bili_bin` 里）。默认值对齐历史行为，这些旧记录才不会在界面上突然变成「转载」；
    改动之后新建的行一律由 `BiliManager._upsert_upload` 显式写入，用不到这个默认值。
    """
    additions = {
        "job": {
            "speed": "FLOAT",
            "eta": "INTEGER",
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
            "download_dir": "TEXT",
        },
        "jobitem": {
            # 视频时长（秒）。补列时库里已有的行拿不到时长（那时没记），留 NULL 即可：
            # 界面显示「时长 未知」，而**重启这些旧任务**会在预检时补上（见
            # `JobManager._apply_download_preparation`）。
            "duration": "FLOAT",
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
            "actual_width": "INTEGER",
            "actual_height": "INTEGER",
            "actual_format": "TEXT",
            "options_json": "TEXT",
            "requested_resolution": "TEXT",
            "fallback_resolution": "TEXT",
            "fallback_reason": "TEXT",
            # 字幕下载结论。补列时库里已有的行拿不到（那时没记），留 NULL 即可：
            # 界面显示「无字幕」，而**重启这些旧任务**会在预检时补上。
            "subtitle_language": "TEXT",
            "subtitle_source": "TEXT",
            "subtitle_path": "TEXT",
        },
        "bili_upload": {
            "make_cover": "BOOLEAN DEFAULT 1",
            "comment": "TEXT DEFAULT ''",
            "copyright": "TEXT DEFAULT '1'",
            "source": "TEXT DEFAULT ''",
            # CC 字幕的附加结论。补列时库里已有的行按 `skipped` 处理 ——
            # 那是改动之前的实际行为（那时根本没有字幕这一步），
            # 标成 `failed` 会让一批老稿件凭空多出一个失败结论。
            "subtitle_state": "TEXT DEFAULT 'skipped'",
            "subtitle_lan": "TEXT DEFAULT ''",
            "subtitle_message": "TEXT DEFAULT ''",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in additions.items():
            existing = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table_name})"))}
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def checkpoint_wal(engine: Engine, mode: str = "TRUNCATE") -> None:
    """Fold the WAL back into the main database file.

    Without an explicit checkpoint a killed process leaves a large `-wal`
    sidecar behind, and readers have to replay it on every query.
    """
    try:
        with engine.begin() as connection:
            connection.execute(text(f"PRAGMA wal_checkpoint({mode})"))
    except Exception as exc:  # noqa: BLE001 - maintenance only, never fatal
        logger.warning("SQLite WAL checkpoint skipped: %s", exc)


def session_dependency(engine: Engine):
    def get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    return get_session
