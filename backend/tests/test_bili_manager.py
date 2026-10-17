"""`bili_manager`：队列、状态写库、重试语义、重启收敛。

这一层把前面两层的纯逻辑接上了**数据库与事件循环**，所以用例的形态也变了：
需要一个真的 engine（同一份数据要被多次 `Session` 读到）与一个真的 `asyncio` 队列。
外部三个接缝（`transport` / `runner` / `sleeper`）仍然全是假的，因此不联网、不投稿。

重点钉三类**写错了只会静默出错**的地方：

1. **重试的三条路**：`published`/`reviewing` 不动、`unverified` 只重新对账、`failed` 才重投。
   把它们合成一条路径，最轻的后果是同一个视频在 B站 上出现两次。
2. **重启收敛**：`queued`/`uploading` 必须折成可重试的失败。折成「继续上传」会让一条
   可能已经投上去的视频再投一次；不折则永远停在「上传中」。
3. **行是 upsert 的**：`(job_id, item_id)` 只有一条记录，重投时旧的 bvid/aid **必须作废**，
   否则界面会显示一个已经不指向本次投稿的链接。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.bili_bin import CommandResult, is_pe
from app.bili_auth import TvToken, read_login_token, write_cookie_file
from app.bili_http import HttpError
from app.bili_manager import (
    CRASHED_UPLOAD_ERROR,
    BiliManager,
    BiliPreconditionError,
    build_prefill,
)
from app.bili_meta import MetadataTemplate, UploadMetadata, default_collection_template
from app.bili_policy import SEASON_CREATE_FAILED
from app.bili_read_model import read_job_uploads
from app.bili_upload import UploadResult, UploadTarget
from app.config import AppSettings
from app.events import EventBroker
from app.models import (
    BiliSeason,
    BiliSeasonStatus,
    BiliUpload,
    BiliUploadStatus,
    Job,
    JobItem,
)
from bili_fakes import FakeBiliTransport, RecordingSleeper, command_runner, section_id_for

TITLE = "演示标题"
TEST_MID = 42
# 一份「登录是完整的」令牌。**默认带上它**：只写 cookies 的文件在 biliup 眼里是不能投稿的
# （它启动时先拿这个令牌去校验），所以「没有令牌」是一条要专门构造出来的分支，不是常态。
TV_TOKEN = TvToken(
    access_token="tv-access-token",
    refresh_token="tv-refresh-token",
    expires_in=15552000,
    mid=TEST_MID,
)


# ---------------------------------------------------------------- 夹具
class Harness:
    """一个能跑的最小工作台：真 engine + 假 transport/runner/sleeper。"""

    def __init__(self, tmp_path: Path, *, transport=None, runner=None, sleeper=None) -> None:
        self.tmp_path = tmp_path
        self.data = tmp_path / "data"
        self.downloads = tmp_path / "downloads"
        self.data.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.exe = tmp_path / "biliup.exe"
        self.exe.write_bytes(b"MZ" + b"\x00" * 64)
        assert is_pe(self.exe)

        self.settings = AppSettings(
            data_dir=self.data,
            download_dir=self.downloads,
            database_path=self.data / "app.sqlite3",
            biliup_path=str(self.exe),
            bili_upload_gap_seconds=0.0,
        )
        self.engine = create_engine(f"sqlite:///{(self.data / 'app.sqlite3').as_posix()}")
        SQLModel.metadata.create_all(self.engine)

        self.transport = transport if transport is not None else FakeBiliTransport()
        self.runner = runner if runner is not None else command_runner()
        self.sleeper = sleeper if sleeper is not None else RecordingSleeper()
        self.broker = EventBroker()
        self.manager = BiliManager(
            self.engine,
            self.settings,
            service=object(),
            broker=self.broker,
            transport=self.transport,
            runner=self.runner,
            sleeper=self.sleeper,
        )
        self.write_login()

    def write_login(self, jar: dict[str, str] | None = None, *, token: TvToken | None = TV_TOKEN) -> None:
        values = jar if jar is not None else {"SESSDATA": "sess", "bili_jct": "jct", "DedeUserID": "42"}
        write_cookie_file(self.settings.bili_cookie_path, values, TEST_MID, token)

    # -- 数据 --------------------------------------------------------------
    def seed_job(self, job_id: str, title: str, count: int) -> list[dict]:
        """建一个任务与它的条目。返回**纯数据**：ORM 对象跨会话会 detached。"""
        rows: list[dict] = []
        with Session(self.engine) as session:
            session.add(Job(id=job_id, url=f"https://youtu.be/{job_id}", title=title, options_json="{}"))
            for index in range(1, count + 1):
                path = self.downloads / f"{job_id}_{index}.mp4"
                path.write_bytes(b"\x00" * 32)
                item_id = f"{job_id}-i{index}"
                session.add(
                    JobItem(
                        id=item_id,
                        job_id=job_id,
                        source_url=f"https://youtu.be/{job_id}{index}",
                        title=f"{title} 第 {index} 集",
                        index=index,
                        status="succeeded",
                        output_path=str(path),
                    )
                )
                rows.append({"id": item_id, "index": index, "title": f"{title} 第 {index} 集", "path": str(path)})
            session.commit()
        return rows

    def upload(self, upload_id: str) -> BiliUpload:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            assert row is not None, upload_id
            session.expunge(row)
            return row

    def season(self, job_id: str) -> BiliSeason:
        with Session(self.engine) as session:
            row = session.exec(select(BiliSeason).where(BiliSeason.job_id == job_id)).one()
            session.expunge(row)
            return row

    def uploads(self, job_id: str) -> list[BiliUpload]:
        with Session(self.engine) as session:
            rows = list(session.exec(select(BiliUpload).where(BiliUpload.job_id == job_id)).all())
            for row in rows:
                session.expunge(row)
            return rows

    def set_status(self, upload_id: str, status: str, **fields) -> None:
        with Session(self.engine) as session:
            row = session.get(BiliUpload, upload_id)
            assert row is not None
            row.status = status
            for name, value in fields.items():
                setattr(row, name, value)
            session.add(row)
            session.commit()

    def meta(self, title: str = TITLE) -> UploadMetadata:
        return UploadMetadata(title=title, desc="简介", tags="标签", tid="122")


async def drain(manager: BiliManager) -> None:
    """等队列跑空。worker 是单线程消费，`join()` 返回即全部处理完。"""
    assert manager._queue is not None
    await manager._queue.join()


def make_harness(tmp_path: Path, **kwargs) -> Harness:
    return Harness(tmp_path, **kwargs)


# ================================================================ 单条提交
async def test_submit_single_runs_to_published_and_sends_the_comment(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 555, "bvid": "BV1a", "title": "单视频任务 第 1 集", "state": 0}]))
    items = harness.seed_job("job1", "单视频任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta("单视频任务 第 1 集"),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="置顶评论",
    )
    assert created.status == BiliUploadStatus.queued.value
    await drain(harness.manager)

    row = harness.upload(created.id)
    assert row.status == BiliUploadStatus.published.value
    assert (row.aid, row.cid) == (555, 7001)
    assert row.verified_note == "已发布，可以正常观看"
    assert row.progress == pytest.approx(0.95)
    assert harness.transport.called("/x/v2/reply/add") == 1
    await harness.manager.stop()


async def test_submit_single_writes_a_row_before_the_worker_picks_it_up(tmp_path):
    """`submit_single` 返回的是**已落库的 queued 行** —— 界面立刻能显示「排队中」，
    而不是等 worker 起来才知道有一次提交发生过。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    assert created.status == "queued"
    assert harness.upload(created.id).status == "queued"
    await drain(harness.manager)
    await harness.manager.stop()


async def test_resubmitting_the_same_item_updates_the_same_row(tmp_path):
    """`(job_id, item_id)` 只有一条记录。重投就是重投，不是新开一条 ——
    否则同一个任务会攒出多份记录，徽标与归集都要面对「哪一条才算」。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    first = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    second = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    assert second.id == first.id
    assert len(harness.uploads("job1")) == 1
    assert harness.upload(first.id).attempt == 2
    await harness.manager.stop()


async def test_resubmitting_discards_the_previous_platform_identity(tmp_path):
    """旧的 bvid/aid **必须作废**：留着它，界面会显示一个已经不指向本次投稿的链接。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    assert harness.upload(created.id).bvid == "BV1"
    await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    row = harness.upload(created.id)
    assert (row.bvid, row.aid, row.cid) == (None, None, None)
    assert row.verified_state is None and row.verified_note is None
    await drain(harness.manager)
    await harness.manager.stop()


# ---------------------------------------------------------------- 前置条件
async def test_submit_single_refuses_when_biliup_is_missing(tmp_path):
    harness = make_harness(tmp_path)
    items = harness.seed_job("job1", "任务", 1)
    harness.settings.biliup_path = str(tmp_path / "nope.exe")
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_single(
            job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
        )
    assert excinfo.value.code == "missing_biliup"
    # 每一条前置条件都必须给出「下一步」，只报结论的提示算未完成。
    assert "winget install" in excinfo.value.advice
    assert excinfo.value.to_detail()["code"] == "missing_biliup"


async def test_submit_single_refuses_when_not_logged_in(tmp_path):
    harness = make_harness(tmp_path)
    items = harness.seed_job("job1", "任务", 1)
    harness.settings.bili_cookie_path.unlink()
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_single(
            job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
        )
    assert excinfo.value.code == "missing_cookie"


async def test_submit_single_refuses_when_the_cookie_lacks_required_fields(tmp_path):
    """只导入了一半凭据时要**点名缺哪一项**，否则用户会反复重试一个不会好的东西。"""
    harness = make_harness(tmp_path)
    items = harness.seed_job("job1", "任务", 1)
    harness.write_login({"SESSDATA": "sess"})
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_single(
            job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
        )
    assert excinfo.value.code == "missing_cookie_fields"
    assert "bili_jct" in excinfo.value.message


async def test_submit_single_refuses_when_the_video_is_not_downloaded(tmp_path):
    harness = make_harness(tmp_path)
    harness.seed_job("job1", "任务", 1)
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_single(
            job_id="job1", item_id="job1-i1", meta=harness.meta(), video_path=tmp_path / "missing.mp4", make_cover=False, comment=""
        )
    assert excinfo.value.code == "missing_video"


async def test_submit_single_refuses_while_the_same_job_is_still_uploading(tmp_path):
    """上传是串行的。两个 worker 抢同一个任务只会互相踩状态。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 2)
    first = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_single(
            job_id="job1", item_id=items[1]["id"], meta=harness.meta(), video_path=Path(items[1]["path"]), make_cover=False, comment=""
        )
    assert excinfo.value.code == "in_flight"
    assert "串行" in excinfo.value.advice
    await drain(harness.manager)
    assert harness.upload(first.id).status == BiliUploadStatus.published.value
    await harness.manager.stop()


async def test_a_finished_upload_releases_the_job_for_the_next_one(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 2)
    for item in items:
        await harness.manager.submit_single(
            job_id="job1", item_id=item["id"], meta=harness.meta(), video_path=Path(item["path"]), make_cover=False, comment=""
        )
        await drain(harness.manager)
    assert len(harness.uploads("job1")) == 2
    await harness.manager.stop()


# ================================================================ 合集提交
async def test_submit_collection_creates_one_row_per_item_plus_a_season(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[
        {"aid": 600 + i, "bvid": f"BV{i}xx", "title": f"合集任务 第 {i} 集", "state": -30} for i in (1, 2, 3)
    ]))
    items = harness.seed_job("job2", "合集任务", 3)
    targets = [
        UploadTarget(index=i, title=f"合集任务 第 {i} 集", video_path=Path(items[i - 1]["path"]), item_id=items[i - 1]["id"])
        for i in (1, 2, 3)
    ]
    view = await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{job} 第 {index}/{count} 集", tags="合集标签", tid="122"),
        targets=targets,
        paths=[Path(item["path"]) for item in items],
        season_title="合集任务",
        season_desc="合集说明",
        make_cover=False,
        comment="",
    )
    assert len(view.uploads) == 3
    assert view.season is not None and view.season.status == BiliSeasonStatus.pending.value
    await drain(harness.manager)

    rows = harness.uploads("job2")
    assert [row.status for row in rows] == ["reviewing"] * 3
    assert [row.title for row in rows] == ["合集任务 第 1 集", "合集任务 第 2 集", "合集任务 第 3 集"]
    season = harness.season("job2")
    assert season.status == BiliSeasonStatus.grouped.value
    episodes = harness.transport.calls[-1]["json_body"]["episodes"]
    assert episodes and len(episodes) == 3
    await harness.manager.stop()


async def test_submit_collection_records_the_item_order_in_the_season(tmp_path):
    """合集加入的顺序应当就是下载条目的顺序 —— 序号只有 `JobItem` 有，不能存第二份。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[]))
    items = harness.seed_job("job2", "合集任务", 2)
    targets = [
        UploadTarget(index=i, title=f"合集任务 第 {i} 集", video_path=Path(items[i - 1]["path"]), item_id=items[i - 1]["id"])
        for i in (1, 2)
    ]
    await harness.manager.submit_collection(
        job_id="job2",
        template=default_collection_template("合集任务"),
        targets=targets,
        paths=[Path(item["path"]) for item in items],
        season_title="合集任务",
        season_desc="说明",
        make_cover=False,
        comment="",
    )
    stored = json.loads(harness.season("job2").upload_ids_json)
    assert len(stored) == 2
    assert [row.item_id for row in harness.uploads("job2")] == ["job2-i1", "job2-i2"]
    await drain(harness.manager)
    await harness.manager.stop()


async def test_submit_collection_lists_the_videos_that_are_not_ready(tmp_path):
    harness = make_harness(tmp_path)
    items = harness.seed_job("job2", "合集任务", 2)
    targets = [
        UploadTarget(index=i, title=f"合集任务 第 {i} 集", video_path=Path(items[i - 1]["path"]), item_id=items[i - 1]["id"])
        for i in (1, 2)
    ]
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_collection(
            job_id="job2",
            template=default_collection_template("合集任务"),
            targets=targets,
            paths=[Path(items[0]["path"]), None],
            season_title="合集任务",
            season_desc="说明",
            make_cover=False,
            comment="",
        )
    assert excinfo.value.code == "missing_video"
    assert "第 2 集" in excinfo.value.message


async def test_submit_collection_keeps_the_previous_season_id_on_a_retry(tmp_path):
    """重投时归集要回到**同一个**合集；重新建一个会留下两个同名合集，而用户不知道多了哪个。"""
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    for _ in range(2):
        await harness.manager.submit_collection(
            job_id="job2",
            template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
            targets=[target],
            paths=[Path(items[0]["path"])],
            season_title="合集任务",
            season_desc="说明",
            make_cover=False,
            comment="",
        )
        await drain(harness.manager)
    season = harness.season("job2")
    assert season.status == BiliSeasonStatus.grouped.value
    assert season.season_id == 990011
    # 建合集只发生了一次；第二次是「回到同一个合集补加」。
    assert harness.transport.called("/season/add") == 1
    assert harness.transport.called("/season/section/episodes/add") == 2
    await harness.manager.stop()


# ================================================================ 归集方式
async def test_join_an_existing_collection_never_creates_a_new_one(tmp_path):
    """「加入已有合集」的全部实现就是**预先把平台编号写进归集记录**：
    `_group_work` 读它当 `existing_season_id`，于是跳过 `create_season` 只补加视频。
    复用既有的「重试不重复建合集」机制，而不是另开一条分支。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(
            archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}],
            seasons=[{"id": 4242, "title": "选中的合集"}],
        ),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
        targets=[target],
        paths=[Path(items[0]["path"])],
        season_title="这个标题不该被用上",
        season_desc="",
        make_cover=False,
        comment="",
        group_mode="existing",
        season_id=4242,
    )
    await drain(harness.manager)

    season = harness.season("job2")
    assert season.status == BiliSeasonStatus.grouped.value
    assert season.season_id == 4242
    # 关键断言：**一个都没有建**。建了就是两个同名合集，而用户只看见自己选的那个。
    assert harness.transport.called("/season/add") == 0
    assert harness.transport.called("/season/section/episodes/add") == 1
    await harness.manager.stop()


async def test_creating_a_collection_still_leaves_season_id_empty_so_the_grouping_builds_it(tmp_path):
    """与上一条互为反向断言。

    这里的判据是 **`season_id` 在提交后必须仍是 `None`**：它一旦被预置成任何值
    （哪怕是对的 0），`_group_work` 就会跳过建合集，对着不存在的编号加视频。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
        targets=[target],
        paths=[Path(items[0]["path"])],
        season_title="合集任务",
        season_desc="说明",
        make_cover=False,
        comment="",
        group_mode="new",
    )
    await drain(harness.manager)

    assert harness.season("job2").season_id == 990011
    assert harness.transport.called("/season/add") == 1
    await harness.manager.stop()


async def test_switching_the_target_collection_overrides_the_previous_one(tmp_path):
    """用户从「加入 A」改成「加入 B」时，必须加到 B。

    `_upsert_season` 的「`season_id` 不清空」规则是为了**重试**不重复建合集；
    照搬到「换了目标合集」上，就会静默地继续往 A 里加 —— 用户选了什么完全不起作用。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(
            archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}],
            # 两个候选合集都得在列表里，否则归集前换分区 id 那一步就断了。
            seasons=[{"id": 111, "title": "A"}, {"id": 222, "title": "B"}],
        ),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])

    async def submit(season_id: int) -> None:
        await harness.manager.submit_collection(
            job_id="job2",
            template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
            targets=[target],
            paths=[Path(items[0]["path"])],
            season_title="合集任务",
            season_desc="说明",
            make_cover=False,
            comment="",
            group_mode="existing",
            season_id=season_id,
        )
        await drain(harness.manager)

    await submit(111)
    await submit(222)

    assert harness.season("job2").season_id == 222
    # 加进去的必须是 222 那个：两次归集的 sectionId 都得记下来。
    # `sectionId` 走 **json_body**（见 `bili_api.add_episodes`：csrf 在 query，参数在正文），
    # 而它装的是**分区 id**（由合集 id 换出来的），所以比对的是 `section_id_for(合集)`。
    sections = [
        call["json_body"]["sectionId"]
        for call in harness.transport.calls
        if call["url"].endswith("/season/section/episodes/add")
    ]
    assert sections == [section_id_for(111), section_id_for(222)]
    await harness.manager.stop()


async def test_choosing_no_collection_leaves_nothing_pending(tmp_path):
    """「不归集」必须落成终态。

    留着 `pending` 的归集记录有两个后果，都很糟：`_maybe_group` 看到 `pending` 就自动归集，
    于是「不归集」根本不管用；界面上还会挂一个永远不会结束的「正在归集」。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
        targets=[target],
        paths=[Path(items[0]["path"])],
        season_title="合集任务",
        season_desc="说明",
        make_cover=False,
        comment="",
        group_mode="none",
    )
    await drain(harness.manager)

    season = harness.season("job2")
    assert season.status == BiliSeasonStatus.skipped.value
    assert harness.transport.called("/season/add") == 0
    assert harness.transport.called("/season/section/episodes/add") == 0
    # 稿件本身照投 —— 「不归集」说的是合集，不是「不投稿」。
    assert [row.status for row in harness.uploads("job2")] == [BiliUploadStatus.published.value]
    await harness.manager.stop()


async def test_switching_to_no_collection_stops_a_previous_pending_grouping(tmp_path):
    """同一任务重投时从「新建」改成「不归集」，上一条归集记录不能留在 `pending`。

    这是「不归集」最容易漏的一处：新建了记录又忘了把它停掉，稿件投完后 `_maybe_group`
    照样会去建合集 —— 用户明明选了不归集。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])

    async def submit(mode: str) -> None:
        await harness.manager.submit_collection(
            job_id="job2",
            template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
            targets=[target],
            paths=[Path(items[0]["path"])],
            season_title="合集任务",
            season_desc="说明",
            make_cover=False,
            comment="",
            group_mode=mode,
        )
        await drain(harness.manager)

    await submit("new")
    created_before = harness.transport.called("/season/add")
    await submit("none")

    assert harness.season("job2").status == BiliSeasonStatus.skipped.value
    assert harness.transport.called("/season/add") == created_before
    await harness.manager.stop()


# ================================================================ 重试的三条路
async def test_retry_on_a_published_upload_does_nothing(tmp_path):
    """幂等：已经成功的稿件点重试**不能**再投一次。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    comments_before = harness.transport.called("/x/v2/reply/add")
    again = await harness.manager.retry_upload(created.id)
    assert again.action == "none"
    assert "没有重复投稿" in again.message
    assert harness.upload(created.id).status == BiliUploadStatus.published.value
    assert harness.transport.called("/x/v2/reply/add") == comments_before
    await harness.manager.stop()


async def test_retry_on_an_in_flight_upload_asks_the_user_to_wait(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    result = await harness.manager.retry_upload(created.id)
    assert result.action == "none"
    assert "正在上传中" in result.message
    await drain(harness.manager)
    await harness.manager.stop()


async def test_retry_on_an_unverified_upload_only_rechecks(tmp_path):
    """**本模块最贵的一条。** `unverified` 的投稿命令成功过，重投等于真的投第二遍。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    assert harness.upload(created.id).status == BiliUploadStatus.unverified.value

    # 平台上这时出现了这条稿件，并且投稿命令**没有**再被跑一次。
    harness.transport.archives.insert(0, {"aid": 700, "bvid": "BV700", "title": TITLE, "state": 0})
    result = await harness.manager.retry_upload(created.id)
    assert result.action == "reverify"
    assert result.upload.aid == 700
    assert harness.upload(created.id).status == BiliUploadStatus.published.value
    await harness.manager.stop()


async def test_retry_on_a_failed_upload_requeues_the_command(tmp_path):
    harness = make_harness(tmp_path, runner=command_runner(returncode=1, stdout="no space left", lines=("no space left",)))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    assert harness.upload(created.id).status == BiliUploadStatus.failed.value

    harness.manager.runner = command_runner()
    result = await harness.manager.retry_upload(created.id)
    assert result.action == "reupload"
    assert result.upload.status == BiliUploadStatus.queued.value
    await drain(harness.manager)
    row = harness.upload(created.id)
    assert row.status == BiliUploadStatus.published.value
    # 首次提交时 attempt 落 1，重投再 +1。
    assert row.attempt == 2
    # 第一次是**命令失败**（`_reject` 路径不查对账），所以整场只发生了一次对账。
    assert harness.transport.called("/x/web/archives") == 1
    await harness.manager.stop()


async def test_retry_of_an_unknown_upload_raises_key_error(tmp_path):
    harness = make_harness(tmp_path)
    with pytest.raises(KeyError):
        await harness.manager.retry_upload("nope")


async def test_retry_season_reports_a_generic_success_message(tmp_path):
    """成功那条走的是「已重新尝试归集」—— 它不承诺结果，因为归集本身还要 B站 审核。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
        targets=[target],
        paths=[Path(items[0]["path"])],
        season_title="合集任务",
        season_desc="说明",
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)
    again = await harness.manager.retry_season(harness.season("job2").id)
    assert again.season.status == BiliSeasonStatus.grouped.value
    assert again.message == "已重新尝试归集"
    await harness.manager.stop()


async def test_retry_season_says_so_when_it_failed_again(tmp_path):
    """失败那条必须与成功那条**措辞不同**，否则用户不知道要不要再点一次。"""
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(
            archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}],
            season_add_error=HttpError("连接被重置"),
        ),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
        targets=[target],
        paths=[Path(items[0]["path"])],
        season_title="合集任务",
        season_desc="说明",
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)
    season = harness.season("job2")
    assert season.status == BiliSeasonStatus.failed.value
    assert season.error == SEASON_CREATE_FAILED[0]

    again = await harness.manager.retry_season(season.id)
    assert again.season.status == BiliSeasonStatus.failed.value
    assert again.message == "归集还是没有成功"
    # 视频是成功的，只有合集没建成 —— 界面必须体现出来，不能一律说「失败」。
    assert harness.upload(harness.uploads("job2")[0].id).status == BiliUploadStatus.published.value
    await harness.manager.stop()


async def test_retry_season_of_an_unknown_record_raises_key_error(tmp_path):
    harness = make_harness(tmp_path)
    with pytest.raises(KeyError):
        await harness.manager.retry_season("nope")


async def test_retry_season_on_a_grouped_season_stays_grouped(tmp_path):
    """已经归集成功后再点「重试归集」，只会补加、不会重开一个合集。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    await harness.manager.submit_collection(
        job_id="job2",
        template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
        targets=[target],
        paths=[Path(items[0]["path"])],
        season_title="合集任务",
        season_desc="说明",
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)
    assert harness.season("job2").status == BiliSeasonStatus.grouped.value
    again = await harness.manager.retry_season(harness.season("job2").id)
    assert again.season.status == BiliSeasonStatus.grouped.value
    assert harness.transport.called("/season/add") == 1
    await harness.manager.stop()


# ================================================================ 重启收敛
async def test_resume_interrupted_folds_in_flight_rows_into_a_retryable_failure(tmp_path):
    """`uploading` 是**必然**会留下的：进程被杀时它正跑在 `biliup` 里，没有任何代码能收尾。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    harness.set_status(created.id, BiliUploadStatus.uploading.value)

    assert await harness.manager.resume_interrupted() == 1
    row = harness.upload(created.id)
    assert row.status == BiliUploadStatus.failed.value
    assert "被中断" in row.error
    assert "重试" in row.advice
    assert row.finished_at is not None
    await harness.manager.stop()


async def test_resume_interrupted_also_covers_rows_still_queued(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[]))
    items = harness.seed_job("job1", "任务", 1)
    with Session(harness.engine) as session:
        session.add(BiliUpload(id="u-queued", job_id="job1", item_id=items[0]["id"], status=BiliUploadStatus.queued.value, title=TITLE))
        session.commit()
    assert await harness.manager.resume_interrupted() == 1
    assert harness.upload("u-queued").status == BiliUploadStatus.failed.value


async def test_resume_interrupted_leaves_finished_rows_alone(tmp_path):
    """它只该碰「在途」的那些。把已完成的记录也折成失败等于把用户的成果抹掉。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    assert await harness.manager.resume_interrupted() == 0
    assert harness.upload(created.id).status == BiliUploadStatus.published.value
    await harness.manager.stop()


async def test_resume_interrupted_is_idempotent(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    harness.set_status(created.id, BiliUploadStatus.uploading.value)
    assert await harness.manager.resume_interrupted() == 1
    assert await harness.manager.resume_interrupted() == 0
    await harness.manager.stop()


# ---------------------------------------------------------------- worker 崩溃
async def test_a_crashing_runner_becomes_a_readable_failure(tmp_path):
    """「静默卡死」必须在界面上变成一条可读原因 —— 否则用户会一直等一个已经死掉的东西。"""

    def boom(argv, timeout, on_line):
        raise RuntimeError("biliup 炸了")

    harness = make_harness(tmp_path, runner=boom)
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    row = harness.upload(created.id)
    assert row.status == BiliUploadStatus.failed.value
    assert "内部错误" in row.error
    await harness.manager.stop()


async def test_a_crashing_runner_does_not_take_the_worker_down(tmp_path):
    """worker 少一个消费口，队列里剩下的条目就永远不会被处理。"""
    calls = {"n": 0}

    def flaky(argv, timeout, on_line):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("第一次炸了")
        return command_runner()(argv, timeout, on_line)

    harness = make_harness(tmp_path, runner=flaky, transport=FakeBiliTransport(archives=[{"aid": 2, "bvid": "BV2", "title": TITLE, "state": 0}]))
    items = harness.seed_job("job1", "任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    assert harness.upload(created.id).status == BiliUploadStatus.failed.value
    # 队列还活着：再提交一条仍然能被处理。
    second = await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    assert harness.upload(second.id).status == BiliUploadStatus.published.value
    await harness.manager.stop()


# ================================================================ 体检
async def test_health_reports_ready_with_everything_in_place(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport(account={"mid": 42, "uname": "测试账号"}))
    status = await harness.manager.health()
    assert status.ready is True
    assert status.biliup.found is True
    assert status.biliup.path == str(harness.exe)
    assert status.login.present is True
    assert status.login.account is not None and status.login.account.name == "测试账号"
    assert len(status.partitions) > 5
    assert status.limits.title == 80
    assert status.gap_seconds == 0.0
    await harness.manager.stop()


async def test_health_reports_missing_biliup_as_a_blocker(tmp_path):
    harness = make_harness(tmp_path)
    harness.settings.biliup_path = str(tmp_path / "nope.exe")
    status = await harness.manager.health()
    assert status.ready is False
    assert any(blocker.message == "没找到投稿工具 biliup" for blocker in status.blockers)
    assert status.biliup.problem and status.biliup.advice


async def test_health_separates_a_zero_byte_stub_from_a_missing_tool(tmp_path, monkeypatch):
    """这一档必须与「没装」分开：用户会去重新 winget install，而问题其实是一个 0 字节文件。

    `detect_broken_stub` 判据收得很窄 —— **必须**是 WinGet Links 下的那个具体路径。
    所以这里要把那个路径指到临时目录里：本机正好装着 biliup，指向真实路径的话
    `is_pe(link)` 为真，会直接短路掉整条分支。
    """
    harness = make_harness(tmp_path)
    link = tmp_path / "links" / "biliup.exe"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.write_bytes(b"")
    real = tmp_path / "packages" / "ForgQi.biliup-rs_win-x64" / "biliup.exe"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"MZ" + b"\x00" * 64)
    monkeypatch.setattr("app.bili_bin.WINGET_LINK_PATH", link)
    monkeypatch.setattr("app.bili_bin.WINGET_PACKAGE_GLOB", str(tmp_path / "packages"))
    harness.settings.biliup_path = str(link)

    status = await harness.manager.health()
    assert status.biliup.found is False
    assert "0 字节" in (status.biliup.problem or "")
    assert "修复" in (status.biliup.advice or "")


async def test_repair_tool_overwrites_the_stub_and_says_so(tmp_path, monkeypatch):
    harness = make_harness(tmp_path)
    link = tmp_path / "links" / "biliup.exe"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.write_bytes(b"")
    real = tmp_path / "packages" / "ForgQi.biliup-rs_win-x64" / "biliup.exe"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"MZ" + b"\x00" * 64)
    monkeypatch.setattr("app.bili_bin.WINGET_LINK_PATH", link)
    monkeypatch.setattr("app.bili_bin.WINGET_PACKAGE_GLOB", str(tmp_path / "packages"))
    harness.settings.biliup_path = str(link)

    message = harness.manager.repair_tool()
    assert "覆盖修复" in message
    assert is_pe(link) is True


async def test_health_reports_a_missing_cookie_as_a_blocker(tmp_path):
    harness = make_harness(tmp_path)
    harness.settings.bili_cookie_path.unlink()
    status = await harness.manager.health()
    assert status.ready is False
    assert status.login.present is False
    assert any(blocker.message == "还没有登录 B站" for blocker in status.blockers)
    await harness.manager.stop()


async def test_health_flags_expired_credentials_only_after_probing(tmp_path):
    """凭据齐全但平台说没登录，是「过期」，与「没有凭据」是两回事。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport(account=None))
    status = await harness.manager.health()
    assert status.login.present is True
    assert status.login.account_checked is True
    assert status.login.account is None
    assert any(blocker.message == "B站 登录已失效" for blocker in status.blockers)
    await harness.manager.stop()


async def test_health_can_skip_the_account_probe(tmp_path):
    """体检面板不必每次都打一次网络请求。"""
    harness = make_harness(tmp_path, transport=FakeBiliTransport())
    status = await harness.manager.health(probe_account=False)
    assert status.login.account_checked is False
    assert harness.transport.called("/x/web-interface/nav") == 0
    await harness.manager.stop()


async def test_health_counts_in_flight_uploads(tmp_path):
    harness = make_harness(tmp_path)
    with Session(harness.engine) as session:
        session.add(BiliUpload(id="u1", job_id="job1", status=BiliUploadStatus.uploading.value, title=TITLE))
        session.commit()
    status = await harness.manager.health()
    assert status.uploads_in_flight == 1


async def test_health_works_without_an_ffmpeg_probe(tmp_path):
    """`service` 没有 `ffmpeg_executable` 时不该抛 —— 封面是可选项，体检仍要出结果。"""
    harness = make_harness(tmp_path)
    status = await harness.manager.health()
    assert status.ffmpeg.found is False
    assert status.ffmpeg.problem == "没找到 ffmpeg，无法自动生成封面"


async def test_repair_tool_says_so_when_there_is_nothing_to_repair(tmp_path):
    harness = make_harness(tmp_path)
    assert harness.manager.repair_tool() == "没有发现需要修复的 biliup"


# ================================================================ 登录态
async def test_account_returns_none_without_a_usable_jar(tmp_path):
    harness = make_harness(tmp_path, transport=FakeBiliTransport())
    assert harness.manager.account() is not None
    harness.write_login({"SESSDATA": "sess"})
    assert harness.manager.account() is None
    harness.settings.bili_cookie_path.unlink()
    assert harness.manager.account() is None
    await harness.manager.stop()


async def test_clear_login_removes_the_file_and_reports_whether_it_did(tmp_path):
    harness = make_harness(tmp_path)
    assert harness.manager.clear_login() is True
    assert not harness.settings.bili_cookie_path.exists()
    assert harness.manager.clear_login() is False
    await harness.manager.stop()


async def test_save_login_writes_a_jar_that_can_be_read_back(tmp_path):
    """登录的完成判据是**拿到令牌**，不只是「文件里有了 cookies」。

    只写 cookies 的文件 biliup 会在开工前拒收（空 access_token 打 `oauth2/info` 得到
    `-400 请求错误`），所以这里连令牌一起断言。
    """
    harness = make_harness(tmp_path)
    warning = harness.manager.save_login({"SESSDATA": "new", "bili_jct": "new-jct"}, 42)
    assert warning is None
    jar = harness.manager.current_jar()
    assert jar["SESSDATA"] == "new" and jar["bili_jct"] == "new-jct"
    assert read_login_token(harness.settings.bili_cookie_path).has_access_token is True
    await harness.manager.stop()


async def test_save_login_keeps_the_cookies_when_the_token_exchange_fails(tmp_path):
    """换不到令牌也要把 cookies 留住：读稿件状态、查合集都只用 cookies。

    返回的 warning 会被登录接口拼进提示 —— 「登录成功」这四个字在投稿不通的时候是假消息。
    """
    transport = FakeBiliTransport(tv_confirm_code=-400, tv_confirm_message="请求错误")
    harness = make_harness(tmp_path, transport=transport)
    warning = harness.manager.save_login({"SESSDATA": "s", "bili_jct": "j"}, 42)
    assert warning and "请求错误" in warning
    assert harness.manager.current_jar()["SESSDATA"] == "s"
    assert read_login_token(harness.settings.bili_cookie_path).has_access_token is False
    await harness.manager.stop()


async def test_ensure_upload_token_does_not_re_exchange_when_one_is_present(tmp_path):
    """已经有令牌就不许再换：换一次会作废上一次的，等于把同时在用的另一个客户端顶掉线。"""
    harness = make_harness(tmp_path)
    assert harness.manager.ensure_upload_token() is None
    assert harness.transport.called("/x/passport-tv-login/qrcode/auth_code") == 0
    await harness.manager.stop()


async def test_ensure_upload_token_fills_in_a_cookie_only_file(tmp_path):
    """「导入一份 cookies.json」之后必须补上令牌 —— 那份文件在 biliup 眼里本来是不能投稿的。"""
    harness = make_harness(tmp_path)
    harness.write_login(token=None)
    assert read_login_token(harness.settings.bili_cookie_path).has_access_token is False
    assert harness.manager.ensure_upload_token() is None
    assert read_login_token(harness.settings.bili_cookie_path).has_access_token is True
    assert harness.transport.called("/x/passport-tv-login/qrcode/auth_code") == 1
    # 再调一次不该再换。
    assert harness.manager.ensure_upload_token() is None
    assert harness.transport.called("/x/passport-tv-login/qrcode/auth_code") == 1
    await harness.manager.stop()


async def test_ensure_upload_token_leaves_an_unreadable_file_alone(tmp_path):
    """读不出内容就不动它。一张我们读不懂的文件也不该被我们清空。"""
    harness = make_harness(tmp_path)
    harness.settings.bili_cookie_path.write_text("这不是 JSON", encoding="utf-8")
    assert harness.manager.ensure_upload_token() is None
    assert harness.settings.bili_cookie_path.read_text(encoding="utf-8") == "这不是 JSON"
    await harness.manager.stop()


async def test_submit_single_refuses_without_an_upload_token(tmp_path):
    """没有令牌时 biliup 会在发出任何投稿请求之前退出，报的还是泛用的 `-400 请求错误`

    —— 那正是「投稿失败但查不出原因」的来源。拦在提交前，至少能把话说清楚。
    """
    harness = make_harness(tmp_path)
    items = harness.seed_job("job1", "任务", 1)
    harness.write_login(token=None)
    with pytest.raises(BiliPreconditionError) as excinfo:
        await harness.manager.submit_single(
            job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
        )
    assert excinfo.value.code == "missing_upload_token"
    assert "重新登录" in excinfo.value.advice
    await harness.manager.stop()


async def test_health_reports_a_missing_upload_token_as_a_blocker(tmp_path):
    """这一档过去是**静默通过**的：面板三项全绿，一投稿必失败。

    漏掉它的代价不是「少一条提示」，而是用户拿着一个全绿的面板去查一个查不出来的错。
    """
    harness = make_harness(tmp_path)
    harness.write_login(token=None)
    status = await harness.manager.health()
    assert status.ready is False
    assert any("登录令牌" in blocker.message for blocker in status.blockers)
    # 凭据本身是齐的 —— 缺的是令牌，不要误导用户去重新导入 cookies。
    assert status.login.present is True and not status.login.missing_required
    await harness.manager.stop()


async def test_importing_a_jar_drops_the_in_progress_qr_session(tmp_path):
    """导入之后必须丢掉扫码会话：它的 jar 里可能攒了半套旧凭据，
    继续轮询会把「旧账号的 cookie + 新账号的扫码结果」混在一起落盘。"""
    harness = make_harness(tmp_path)
    before = harness.manager.login_session()
    harness.manager.reset_login_session()
    assert harness.manager.login_session() is not before
    await harness.manager.stop()


# ================================================================ 预填
async def test_build_prefill_gives_a_single_set_of_values_for_a_one_video_job(tmp_path):
    harness = make_harness(tmp_path)
    harness.seed_job("job1", "单视频任务", 1)
    with Session(harness.engine) as session:
        job = session.get(Job, "job1")
        prefill = build_prefill(session, job, "job1-i1")
    assert prefill.kind == "single"
    assert prefill.metadata is not None
    assert prefill.metadata.title == "单视频任务 第 1 集"
    assert "原视频：https://youtu.be/job11" in prefill.metadata.desc
    assert prefill.season_desc == ""
    assert prefill.problems == []
    assert prefill.items[0].ready is True


async def test_build_prefill_gives_a_template_plus_a_per_item_preview_for_a_collection(tmp_path):
    harness = make_harness(tmp_path)
    harness.seed_job("job2", "合集任务", 3)
    with Session(harness.engine) as session:
        job = session.get(Job, "job2")
        prefill = build_prefill(session, job)
    assert prefill.kind == "collection"
    assert prefill.template is not None and prefill.template.title == "{title}"
    assert len(prefill.items) == 3
    assert prefill.items[0].desc == "合集任务 · 第 01/3 集\n原视频：https://youtu.be/job21"
    assert prefill.problems == []


async def test_build_prefill_derives_a_non_empty_season_desc(tmp_path):
    """合集简介是 B站 的必填项之一，界面上没有这个字段 —— 必须由后端派生一句。"""
    harness = make_harness(tmp_path)
    harness.seed_job("job2", "合集任务", 2)
    with Session(harness.engine) as session:
        job = session.get(Job, "job2")
        prefill = build_prefill(session, job)
    assert prefill.season_desc
    assert "合集任务" in prefill.season_desc and "2" in prefill.season_desc


async def test_build_prefill_marks_items_whose_file_is_gone(tmp_path):
    harness = make_harness(tmp_path)
    items = harness.seed_job("job2", "合集任务", 2)
    Path(items[1]["path"]).unlink()
    with Session(harness.engine) as session:
        job = session.get(Job, "job2")
        prefill = build_prefill(session, job)
    assert [item.ready for item in prefill.items] == [True, False]


async def test_build_prefill_reports_problems_it_can_already_see(tmp_path):
    """预填自带的 problems 必须与提交时的校验**是同一份**：预览说没问题、提交却超限，
    正是「超限要报错、不许静默截断」要防的场景。

    用合集而不是单视频来构造：单视频走的是 `default_single`，它在生成侧就把标题截到 80 字，
    于是永远验不出「标题超限」—— 覆盖这条要挑一条真的会超限的路径。
    """
    harness = make_harness(tmp_path)
    long_title = "超" * 90
    with Session(harness.engine) as session:
        session.add(Job(id="job3", url="https://youtu.be/job3", title="任务", options_json="{}"))
        for index, title in enumerate(("正常", long_title), start=1):
            session.add(
                JobItem(
                    id=f"job3-i{index}",
                    job_id="job3",
                    source_url=f"https://youtu.be/job3{index}",
                    title=title,
                    index=index,
                    status="succeeded",
                    output_path=None,
                )
            )
        session.commit()
        prefill = build_prefill(session, session.get(Job, "job3"))
    assert prefill.kind == "collection"
    assert len(prefill.problems) == 1
    assert prefill.problems[0].startswith(f"第 2 条「{long_title}」：标题超出 10 个字")


async def test_the_default_templates_never_carry_a_placeholder_problem(tmp_path):
    """默认值自带问题会让每个用户第一步就撞上 400 —— 所以默认模板必须干净。"""
    harness = make_harness(tmp_path)
    harness.seed_job("job2", "合集任务", 2)
    with Session(harness.engine) as session:
        prefill = build_prefill(session, session.get(Job, "job2"))
    assert not [problem for problem in prefill.problems if "占位符" in problem]
    assert not prefill.problems


async def test_build_prefill_offers_the_partition_list_and_limits(tmp_path):
    harness = make_harness(tmp_path)
    harness.seed_job("job1", "任务", 1)
    with Session(harness.engine) as session:
        prefill = build_prefill(session, session.get(Job, "job1"), "job1-i1")
    assert prefill.partitions and prefill.partitions[0].label.startswith("知识")
    assert prefill.limits.tag_item == 20


# ================================================================ 事件与生命周期
async def test_start_is_idempotent_and_stop_can_be_repeated(tmp_path):
    harness = make_harness(tmp_path)
    await harness.manager.start()
    first_queue = harness.manager._queue
    await harness.manager.start()
    assert harness.manager._queue is first_queue
    await harness.manager.stop()
    await harness.manager.stop()
    await harness.manager.start()
    await harness.manager.stop()


async def test_events_are_published_for_start_and_finish(tmp_path):
    """SSE 是界面刷新的唯一来源；少了 finished 事件，条目会一直停在「上传中」。"""
    seen: list[str] = []

    async def capture(payload):
        seen.append(payload["type"])

    harness = make_harness(tmp_path, transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]))
    harness.broker.publish = capture  # type: ignore[method-assign]
    items = harness.seed_job("job1", "任务", 1)
    await harness.manager.submit_single(
        job_id="job1", item_id=items[0]["id"], meta=harness.meta(), video_path=Path(items[0]["path"]), make_cover=False, comment=""
    )
    await drain(harness.manager)
    # 事件是从工作线程投递回事件循环的，给它一拍。
    await asyncio.sleep(0.05)
    assert "bili_upload_started" in seen
    assert "bili_upload_finished" in seen
    await harness.manager.stop()


async def test_publishing_from_a_thread_without_a_loop_is_a_noop(tmp_path):
    """进程退出时事件循环可能已经关了。丢事件可以，让 worker 抛异常不行。"""
    harness = make_harness(tmp_path)
    harness.manager._loop = None
    harness.manager._publish_threadsafe({"type": "irrelevant"})
    await harness.manager.stop()


async def test_the_upload_gap_never_goes_negative(tmp_path):
    harness = make_harness(tmp_path)
    harness.settings.bili_upload_gap_seconds = -5.0
    assert harness.manager._gap_seconds() == 0.0
    await harness.manager.stop()


async def test_the_first_upload_does_not_wait_for_the_gap(tmp_path):
    """间隔是「距上次投稿结束」现算的，所以队列里的第一条不该白等。"""
    harness = make_harness(tmp_path, sleeper=RecordingSleeper())
    harness.settings.bili_upload_gap_seconds = 20.0
    harness.manager._last_upload_finished_at = None
    harness.manager._respect_gap()
    assert harness.sleeper.waits == []
    await harness.manager.stop()


async def test_the_gap_is_respected_between_two_uploads(tmp_path):
    harness = make_harness(tmp_path, sleeper=RecordingSleeper())
    harness.settings.bili_upload_gap_seconds = 20.0
    # 间隔按「距上次投稿结束」现算 —— 时间基准是 `monotonic()`，不能拿 0 当「刚刚结束」。
    harness.manager._last_upload_finished_at = time.monotonic()
    harness.manager._respect_gap()
    assert harness.sleeper.waits and 0.0 < harness.sleeper.waits[0] <= 20.0
    await harness.manager.stop()


async def test_no_gap_is_waited_once_enough_time_has_passed(tmp_path):
    """等了足够久之后再投稿不该再等一遍 —— 否则队列里每条都白等 20 秒。"""
    harness = make_harness(tmp_path, sleeper=RecordingSleeper())
    harness.settings.bili_upload_gap_seconds = 20.0
    harness.manager._last_upload_finished_at = time.monotonic() - 100.0
    harness.manager._respect_gap()
    assert harness.sleeper.waits == []
    await harness.manager.stop()


async def test_a_zero_gap_never_sleeps(tmp_path):
    harness = make_harness(tmp_path, sleeper=RecordingSleeper())
    harness.settings.bili_upload_gap_seconds = 0.0
    harness.manager._last_upload_finished_at = time.monotonic()
    harness.manager._respect_gap()
    assert harness.sleeper.waits == []
    await harness.manager.stop()


async def test_manager_itself_refuses_to_join_without_an_id(tmp_path):
    """`submit_collection` 是公开方法，路由那道 400 不是唯一防线。

    漏掉编号时若无条件采纳 `existing`，归集记录会带着 `None` 的 `season_id` 落进
    「建集」分支 —— 用户明明选了「加入已有」，却凭空多出一个合集。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job2", "合集任务", 1)
    target = UploadTarget(index=1, title=TITLE, video_path=Path(items[0]["path"]), item_id=items[0]["id"])
    with pytest.raises(BiliPreconditionError) as caught:
        await harness.manager.submit_collection(
            job_id="job2",
            template=MetadataTemplate(title_template="{title}", desc_template="{title}", tags="标签", tid="122"),
            targets=[target],
            paths=[Path(items[0]["path"])],
            season_title="合集任务",
            season_desc="",
            make_cover=False,
            comment="",
            group_mode="existing",
            season_id=None,
        )
    assert caught.value.code == "missing_season_id"
    # 拒绝时不该留下任何痕迹：没建记录，也没投递。
    assert harness.transport.called("/season/add") == 0
    await harness.manager.stop()


# ================================================================ 单视频的归集方式
#
# 这一组钉的是「单视频与合集口径**不同**的那几处」。共用同一套归集机制，
# 所以最容易在「顺手复用合集那边」时被抹平 —— 而两处的默认值恰好是相反的。


async def test_single_submit_joins_an_existing_collection(tmp_path):
    """单视频投稿也能直接加进一个已有合集。

    `submit_single` 以前**连归集记录都不建**，所以 `_maybe_group` 永远找不到它 ——
    这个能力在此之前只有先下载一整个 playlist 才够得着。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(
            archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}],
            # 用户选的那个合集必须在列表里 —— 归集前要拿它换分区 id。
            seasons=[{"id": 4242, "title": "选中的合集"}],
        ),
    )
    items = harness.seed_job("job1", "单视频任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
        group_mode="existing",
        season_id=4242,
    )
    await drain(harness.manager)

    assert harness.upload(created.id).status == BiliUploadStatus.published.value
    season = harness.season("job1")
    assert season.season_id == 4242
    assert season.status == BiliSeasonStatus.grouped.value
    # 一个都不建：建了就是账号里多出一个用户没要的合集。
    assert harness.transport.called("/season/add") == 0
    assert harness.transport.called("/season/section/episodes/add") == 1
    await harness.manager.stop()


async def test_single_submit_creates_a_collection_when_asked(tmp_path):
    """反向：选了「新建合集」时，单视频也会真的建一个并把它放进去。"""
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job1", "单视频任务", 1)
    await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
        group_mode="new",
        season_title="我自己的合集",
    )
    await drain(harness.manager)

    season = harness.season("job1")
    # 标题用的是**用户填的那个**，不是任务名 —— `season_title` 漏在中间某一层的话，
    # 建的合集名会悄悄变成任务名，而两者看起来都「像是对的」。
    assert season.title == "我自己的合集"
    assert season.season_id == 990011
    assert season.status == BiliSeasonStatus.grouped.value
    assert harness.transport.called("/season/add") == 1
    await harness.manager.stop()


async def test_single_submit_without_grouping_leaves_no_season_record(tmp_path):
    """默认（不归集）**一条归集记录都不该落**。

    单视频的默认恰好是「不归集」，所以「也建一条 `skipped` 记录」在这里的含义是
    「此后每个单视频任务都多出一行『合集：未归集』」—— 默认路径上多出来的东西必须是零。
    （合集那边反过来：它有归集记录，留一条 `skipped` 是在说明它的去向。）
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job1", "单视频任务", 1)
    created = await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
    )
    await drain(harness.manager)

    # 稿件照投 —— 「不归集」说的是合集，不是「不投稿」。
    assert harness.upload(created.id).status == BiliUploadStatus.published.value
    with Session(harness.engine) as session:
        assert list(session.exec(select(BiliSeason).where(BiliSeason.job_id == "job1")).all()) == []
    assert harness.transport.called("/season/add") == 0
    assert harness.transport.called("/season/section/episodes/add") == 0
    await harness.manager.stop()


async def test_single_submit_to_no_grouping_stops_a_previous_pending_season(tmp_path):
    """已有归集记录时，「不归集」必须把它**停掉**而不是放着不管。

    放着不管的后果是 `_maybe_group` 只认 `pending`、照旧自动归集 ——
    用户明明选了「不归集」，视频投完还是被归了集，而界面上看不出任何异常。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job1", "单视频任务", 1)
    with Session(harness.engine) as session:
        session.add(BiliSeason(id="season-pending", job_id="job1", title="上一轮建的合集"))
        session.commit()

    await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
        group_mode="none",
    )
    await drain(harness.manager)

    season = harness.season("job1")
    assert season.status == BiliSeasonStatus.skipped.value
    # 上一轮的标题**留着**：它是那次投稿的审计痕迹，改掉会让人以为记录被覆盖了。
    assert season.title == "上一轮建的合集"
    assert harness.transport.called("/season/add") == 0
    assert harness.transport.called("/season/section/episodes/add") == 0
    await harness.manager.stop()


async def test_single_submit_refuses_existing_without_an_id(tmp_path):
    """`submit_single` 是公开方法，路由那道 400 不是唯一防线。"""
    harness = make_harness(tmp_path)
    items = harness.seed_job("job1", "单视频任务", 1)
    with pytest.raises(BiliPreconditionError) as caught:
        await harness.manager.submit_single(
            job_id="job1",
            item_id=items[0]["id"],
            meta=harness.meta(),
            video_path=Path(items[0]["path"]),
            make_cover=False,
            comment="",
            group_mode="existing",
            season_id=None,
        )
    assert caught.value.code == "missing_season_id"
    await harness.manager.stop()


async def test_single_submit_refuses_new_without_a_title(tmp_path):
    """空标题会被 B站 打回来，而那时**视频已经投出去了** —— 所以拦在投稿之前。

    路由层会用任务名兜底（`season_title or job.title`），所以这条只有直接调 manager
    才会碰到；留着它是因为「投出去了但合集建不起来」是一次不可回退的半成品状态。
    """
    harness = make_harness(tmp_path)
    items = harness.seed_job("job1", "单视频任务", 1)
    with pytest.raises(BiliPreconditionError) as caught:
        await harness.manager.submit_single(
            job_id="job1",
            item_id=items[0]["id"],
            meta=harness.meta(),
            video_path=Path(items[0]["path"]),
            make_cover=False,
            comment="",
            group_mode="new",
            season_title="   ",
        )
    assert caught.value.code == "missing_season_title"
    assert harness.transport.called("/season/add") == 0
    await harness.manager.stop()


# ================================================================ 写库的两个并发作者
# 进度与结论**由两个线程分别写**：进度来自 `bili_bin.run_command` 的读数线程
# （回调 → `_store_progress`），结论来自 worker 线程（`_store_result`）。两者写的是同一行的
# `progress` 列，所以这里钉的是同一条不变量：**已经记下的最大值不会被更小的值覆盖**。
def _queued_upload(harness: Harness, upload_id: str = "u1", *, status: str | None = None, **fields) -> None:
    with Session(harness.engine) as session:
        row = BiliUpload(id=upload_id, job_id="job1", status=status or BiliUploadStatus.uploading.value, **fields)
        session.add(row)
        session.commit()


def test_storing_a_result_never_lowers_the_recorded_progress(tmp_path):
    harness = make_harness(tmp_path)
    _queued_upload(harness, progress=0.8)
    harness.manager._store_result(
        "u1",
        UploadResult(index=1, item_id=None, status=BiliUploadStatus.published.value, progress=0.2),
    )
    assert harness.upload("u1").progress == pytest.approx(0.8)


def test_progress_is_ignored_once_the_row_is_no_longer_uploading(tmp_path):
    """结论已经落地之后再来的进度回调必须丢掉。

    它晚到了，说的还是上一个阶段的事 —— 写进去会让一条「已上传」的记录在界面上
    重新动起来（或者把 `published` 之外的状态又改回去）。
    """
    harness = make_harness(tmp_path)
    _queued_upload(harness, status=BiliUploadStatus.published.value, progress=0.95)
    harness.manager._store_progress("u1", 0.3)
    row = harness.upload("u1")
    assert (row.status, row.progress) == (BiliUploadStatus.published.value, pytest.approx(0.95))


def test_progress_writes_for_a_missing_row_are_a_noop(tmp_path):
    """记录被删掉了（用户删任务）也要静默接受：进度回调不该把正在跑的投稿打断。"""
    harness = make_harness(tmp_path)
    harness.manager._store_progress("never-existed", 0.5)
    harness.manager._store_result("never-existed", UploadResult(index=1, item_id=None, status="failed"))


def test_both_progress_and_result_writes_take_the_same_lock(tmp_path):
    """**两个作者必须共用同一把锁** —— 这就是「已提交的进度可能被更小的值覆盖」的修法。

    这条不改行为、也测不出竞态本身（竞态是时序相关的，单线程里跑不出来），它测的是
    **修法是否还在**：`_store_result` 曾经不取锁，于是读数线程可能带着一个过期的
    `progress` 在结论之后才提交。少了这道互斥时，唯一的症状是界面上的进度**倒退**
    —— 而它几乎不可能被复现，所以只能这样钉住成因。
    """
    harness = make_harness(tmp_path)
    _queued_upload(harness)

    entered: list[str] = []
    inner = harness.manager._progress_lock

    class Probe:
        def __enter__(self):
            entered.append(threading.current_thread().name)
            return inner.__enter__()

        def __exit__(self, *exc_info):
            return inner.__exit__(*exc_info)

    harness.manager._progress_lock = Probe()
    harness.manager._store_progress("u1", 0.5)
    harness.manager._store_result("u1", UploadResult(index=1, item_id=None, status=BiliUploadStatus.published.value))
    assert len(entered) == 2, "进度与结论必须各自进一次同一把锁"


# ================================================================ 归集记录的定位
def test_find_upload_treats_a_blank_item_id_as_the_job_level_row(tmp_path):
    """`None` 与空串是同一件事（都表示「整个任务一条记录」）。

    判据收进 SQL 之后最容易出的一处偏差就是把它写成 `column == None` ——
    SQL 里那是恒假，于是每次提交都会新建一行，而不是更新原来那行。
    """
    harness = make_harness(tmp_path)
    with Session(harness.engine) as session:
        session.add(BiliUpload(id="job-level", job_id="job1", item_id=None))
        session.add(BiliUpload(id="item-level", job_id="job1", item_id="i1"))
        session.commit()
        assert harness.manager._find_upload(session, "job1", None).id == "job-level"
        assert harness.manager._find_upload(session, "job1", "").id == "job-level"
        assert harness.manager._find_upload(session, "job1", "i1").id == "item-level"
        assert harness.manager._find_upload(session, "job1", "other") is None


async def test_a_write_targets_the_same_season_row_the_reader_reports(tmp_path):
    """**写入与读取必须选中同一条归集记录。**

    真出现两条时（历史数据、手改库、将来某次改动引入的重复写入），一边取首条、一边取末条
    会让「后台正在更新的批次」和「界面显示的批次」分叉 —— 而两条单独的断言都看不出来，
    因为各自都是自洽的。这条用例把两个方向钉在一起。
    """
    harness = make_harness(
        tmp_path,
        transport=FakeBiliTransport(archives=[{"aid": 1, "bvid": "BV1", "title": TITLE, "state": 0}]),
    )
    items = harness.seed_job("job1", "单视频任务", 1)
    base = datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)
    with Session(harness.engine) as session:
        session.add(BiliSeason(id="season-old", job_id="job1", created_at=base, status=BiliSeasonStatus.grouped.value))
        session.add(
            BiliSeason(
                id="season-new",
                job_id="job1",
                created_at=base + timedelta(minutes=5),
                status=BiliSeasonStatus.failed.value,
            )
        )
        session.commit()

    created = await harness.manager.submit_single(
        job_id="job1",
        item_id=items[0]["id"],
        meta=harness.meta(),
        video_path=Path(items[0]["path"]),
        make_cover=False,
        comment="",
        group_mode="new",
        season_title="新合集",
    )
    await drain(harness.manager)

    with Session(harness.engine) as session:
        reported = read_job_uploads(session, "job1").season.id
        old = session.get(BiliSeason, "season-old")
        new = session.get(BiliSeason, "season-new")
    # 被更新的是「最新那条」，而且正是读模型回报的那条。
    assert reported == "season-new"
    assert new.title == "新合集"
    # 老那条**一个字段都没被碰**：写入不能顺手把首条也改了。
    assert (old.title, old.status) == ("", BiliSeasonStatus.grouped.value)
    assert created.id
    await harness.manager.stop()
