"""`bili_read_model`：把多条记录折成界面上的**那一档**状态。

这里承载本功能唯一一条「多条记录 → 一档状态」的聚合规则。它有真实的分支
（在途 / 全成功 / 全失败 / 有成功有失败 / 全待核对 / 有合集无稿件），
而每一条走错都会让界面**自相矛盾**：任务行的 B站 图标说「已上传」，展开后条目行说「失败」。

所以用例的重点是**穷尽这张真值表**，而不是逐个字段抄一遍。

读写模型本身是同步纯函数（没有线程），所以这里用内存 SQLite + `StaticPool`：
一个连接、无文件 I/O，比每个用例建一个文件库快一个数量级，也不涉及跨线程共享连接的隐患。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.bilibili.bili_policy import (
    ACCEPTED_STATUSES,
    BADGE_FAILED,
    BADGE_NONE,
    BADGE_PARTIAL,
    BADGE_UNVERIFIED,
    BADGE_UPLOADED,
    BADGE_UPLOADING,
    REJECTED_STATUSES,
)
from app.bilibili.bili_read_model import (
    IN_FLIGHT_STATUSES,
    bilibili_video_url,
    derive_badge,
    item_index_map,
    latest_season,
    list_seasons,
    read_all_job_uploads,
    read_job_uploads,
    read_season,
    read_upload,
    season_upload_ids,
)
from app.contracts.models import BiliSeason, BiliSeasonStatus, BiliUpload, BiliUploadStatus, Job, JobItem


@pytest.fixture
def engine():
    made = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(made)
    try:
        yield made
    finally:
        made.dispose()


def upload(status: str, **fields) -> BiliUpload:
    """一条**没有落库**的稿件行。`derive_badge` 只读 `.status`，不需要真的存进去。"""
    return BiliUpload(id=fields.pop("id", f"u-{status}"), job_id=fields.pop("job_id", "j1"), status=status, **fields)


def season(status: str) -> BiliSeason:
    return BiliSeason(id="s1", job_id="j1", status=status)


# ================================================================ 徽标真值表
@pytest.mark.parametrize(
    ("label", "statuses", "season_status", "expected"),
    [
        ("什么都没有", [], None, BADGE_NONE),
        ("还没有任何记录但已有合集", [], BiliSeasonStatus.pending.value, BADGE_UNVERIFIED),
        (
            "只有合集、且已归集",
            [],
            BiliSeasonStatus.grouped.value,
            BADGE_UPLOADED,
        ),
        ("排队中", [BiliUploadStatus.queued.value], None, BADGE_UPLOADING),
        ("上传中", [BiliUploadStatus.uploading.value], None, BADGE_UPLOADING),
        # `submitted` 是「命令跑完、正在对账」的窗口，对外仍是进行中。
        ("正在对账", [BiliUploadStatus.submitted.value], None, BADGE_UPLOADING),
        ("一条已发布", [BiliUploadStatus.published.value], None, BADGE_UPLOADED),
        ("一条审核中", [BiliUploadStatus.reviewing.value], None, BADGE_UPLOADED),
        ("全部待核对", [BiliUploadStatus.unverified.value], None, BADGE_UNVERIFIED),
        ("全部待核对（多条）", [BiliUploadStatus.unverified.value] * 3, None, BADGE_UNVERIFIED),
        ("全部失败", [BiliUploadStatus.failed.value], None, BADGE_FAILED),
        ("全部被退回", [BiliUploadStatus.rejected.value], None, BADGE_FAILED),
        (
            "有成功有失败",
            [BiliUploadStatus.published.value, BiliUploadStatus.failed.value],
            None,
            BADGE_PARTIAL,
        ),
        (
            "有成功有待核对",
            [BiliUploadStatus.published.value, BiliUploadStatus.unverified.value],
            None,
            BADGE_UPLOADED,
        ),
        (
            "有成功有在途",
            [BiliUploadStatus.published.value] * 8 + [BiliUploadStatus.uploading.value],
            None,
            BADGE_UPLOADING,
        ),
        # 视频传上去了、合集没建成 —— 界面必须体现出来，否则用户会以为一切都好了。
        ("稿件全成功但合集失败", [BiliUploadStatus.published.value], BiliSeasonStatus.failed.value, BADGE_PARTIAL),
        ("稿件有失败且合集也失败", [BiliUploadStatus.failed.value], BiliSeasonStatus.failed.value, BADGE_FAILED),
        ("正在归集", [BiliUploadStatus.published.value], BiliSeasonStatus.grouping.value, BADGE_UPLOADING),
        ("归集完成", [BiliUploadStatus.published.value], BiliSeasonStatus.grouped.value, BADGE_UPLOADED),
    ],
)
def test_badge_truth_table(label, statuses, season_status, expected):
    uploads = [upload(status, id=f"u{i}") for i, status in enumerate(statuses)]
    badge = derive_badge(uploads, season(season_status) if season_status else None)
    assert badge == expected, label


def test_one_upload_still_in_flight_keeps_the_whole_job_as_uploading():
    """8 条成功 2 条在跑时说「已上传」，会让用户关掉页面、以为可以收工了。"""
    uploads = [upload(BiliUploadStatus.published.value, id=f"u{i}") for i in range(8)]
    uploads += [upload(BiliUploadStatus.uploading.value, id="a"), upload(BiliUploadStatus.queued.value, id="b")]
    assert derive_badge(uploads, None) == BADGE_UPLOADING


def test_grouping_counts_as_uploading_even_when_every_upload_succeeded():
    uploads = [upload(BiliUploadStatus.published.value)]
    assert derive_badge(uploads, season(BiliSeasonStatus.grouping.value)) == BADGE_UPLOADING


def test_a_failed_season_never_shows_as_fully_uploaded():
    """**最容易被漏掉的一档**：稿件全成功、合集没建成，说「已上传」就是在撒谎。"""
    badge = derive_badge([upload(BiliUploadStatus.published.value)], season(BiliSeasonStatus.failed.value))
    assert badge != BADGE_UPLOADED
    assert badge == BADGE_PARTIAL


def test_an_unverified_only_batch_is_never_reported_as_uploaded():
    """对账查不到时只能说「待核对」—— 说成功会让用户不再回来看。"""
    assert derive_badge([upload(BiliUploadStatus.unverified.value)], None) == BADGE_UNVERIFIED


def test_a_rejected_upload_counts_as_failure_not_success():
    assert derive_badge([upload(BiliUploadStatus.rejected.value)], None) == BADGE_FAILED


def test_the_badge_rule_classifies_every_status_exactly_once():
    """成败两组集合住在 `bili_policy`，`derive_badge` 直接读它们 —— 这条钉住「同源」。

    口径：「在途 / 成功 / 失败」三组两两不相交，且**除 `unverified` 外**每一种状态都必须
    被其中一组认领 —— `unverified` 是唯一「谁都没归类」的那一档，它折成「待核对」。
    新加一个状态却忘了归类，会在这里露出来，而不是等线上多出一条永远显示「待核对」的稿件。
    """
    groups = (IN_FLIGHT_STATUSES, ACCEPTED_STATUSES, REJECTED_STATUSES)
    everything = {status.value for status in BiliUploadStatus}
    covered = set().union(*groups)
    # 三个集合两两不相交（一个状态被两组认领 = 同一行在不同分支里成败不一致）。
    for index, group in enumerate(groups):
        for other in groups[index + 1 :]:
            assert not (group & other), (sorted(group), sorted(other))
    # 未被任何一组认领的**只能**是 `unverified`；多出一个就是有状态忘了归类。
    assert everything - covered == {BiliUploadStatus.unverified.value}, sorted(everything - covered)
    # 而「没被归类」必须折成待核对，不能折成成功。
    assert derive_badge([upload(BiliUploadStatus.unverified.value)], None) == BADGE_UNVERIFIED


def test_badge_only_moves_forward_when_a_pending_season_is_added():
    """合集刚建好还没归集时不该让徽标倒退成「未上传」。"""
    with_season = derive_badge([upload(BiliUploadStatus.published.value)], season(BiliSeasonStatus.pending.value))
    without = derive_badge([upload(BiliUploadStatus.published.value)], None)
    assert with_season == without == BADGE_UPLOADED


# ================================================================ 单条读模型
def test_read_upload_projects_the_row_and_derives_its_own_badge():
    row = upload(BiliUploadStatus.published.value, bvid="BV1xx411c7mD", tid="122", progress=0.95)
    view = read_upload(row, index=3)
    assert view.index == 3
    assert view.badge == BADGE_UPLOADED
    assert view.badge_text == "已上传"
    assert view.badge_tone == "ok"
    assert view.status_text == "已发布"
    assert view.tid_label == "知识 · 野生技术协会"
    assert view.video_url == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_read_upload_has_no_link_without_a_bvid():
    assert read_upload(upload(BiliUploadStatus.failed.value)).video_url is None


def test_read_upload_defaults_the_index_to_one():
    assert read_upload(upload(BiliUploadStatus.queued.value)).index == 1


def test_read_upload_keeps_the_reason_and_the_next_step():
    """失败原因与「下一步」是两栏，缺一栏用户就只知道坏了、不知道该干什么。"""
    row = upload(
        BiliUploadStatus.failed.value,
        error="磁盘没有剩余空间了",
        advice="腾出空间后重试",
    )
    view = read_upload(row)
    assert view.error == "磁盘没有剩余空间了"
    assert view.advice == "腾出空间后重试"


@pytest.mark.parametrize(
    ("bvid", "expected"),
    [(None, None), ("", None), ("BV1", "https://www.bilibili.com/video/BV1")],
)
def test_bilibili_video_url_handles_blank_values(bvid, expected):
    assert bilibili_video_url(bvid) == expected


# ================================================================ 合集读模型
def test_read_season_parses_the_upload_ids():
    row = BiliSeason(id="s1", job_id="j1", status="grouped", upload_ids_json='["a","b"]', season_id=990011)
    view = read_season(row)
    assert view.upload_ids == ["a", "b"]
    assert view.season_id == 990011
    assert view.status_text.startswith("已提交归集")


def test_read_season_tolerates_a_corrupt_upload_ids_column():
    """这一列是 JSON 文本，坏了也不该让整个任务的上传面板打不开。"""
    row = BiliSeason(id="s1", job_id="j1", status="pending", upload_ids_json="{ not json")
    assert read_season(row).upload_ids == []


def test_read_season_handles_an_empty_column():
    assert read_season(BiliSeason(id="s1", job_id="j1", status="pending")).upload_ids == []


# ================================================================ 任务级读模型
def seed(engine, job_id: str, item_indexes: dict[str, int]) -> None:
    with Session(engine) as session:
        session.add(Job(id=job_id, url="https://youtu.be/x", title="任务", options_json="{}"))
        for item_id, index in item_indexes.items():
            session.add(
                JobItem(
                    id=item_id,
                    job_id=job_id,
                    source_url=f"https://youtu.be/{item_id}",
                    title=f"第 {index} 集",
                    index=index,
                    status="succeeded",
                )
            )
        session.commit()


def add_upload(engine, job_id: str, *, upload_id: str, item_id: str | None, status: str, **fields) -> None:
    with Session(engine) as session:
        session.add(BiliUpload(id=upload_id, job_id=job_id, item_id=item_id, status=status, title=f"稿件{upload_id}", **fields))
        session.commit()


def test_job_uploads_are_ordered_by_the_item_index(engine):
    """顺序来源只有一个：`JobItem.index`。在 B站 记录里再存一份序号必然漂移。"""
    seed(engine, "j1", {"i1": 1, "i2": 2, "i3": 3})
    for upload_id, item_id in (("u2", "i2"), ("u1", "i1"), ("u3", "i3")):
        add_upload(engine, "j1", upload_id=upload_id, item_id=item_id, status=BiliUploadStatus.published.value)
    with Session(engine) as session:
        view = read_job_uploads(session, "j1")
    assert [row.id for row in view.uploads] == ["u1", "u2", "u3"]
    assert [row.index for row in view.uploads] == [1, 2, 3]


def test_uploads_without_a_known_item_go_last(engine):
    """抓不到序号的记录（条目被删了）不该把有顺序的那些挤到后面去。"""
    seed(engine, "j1", {"i1": 1})
    add_upload(engine, "j1", upload_id="orphan", item_id=None, status=BiliUploadStatus.unverified.value)
    add_upload(engine, "j1", upload_id="u1", item_id="i1", status=BiliUploadStatus.published.value)
    with Session(engine) as session:
        view = read_job_uploads(session, "j1")
    assert [row.id for row in view.uploads] == ["u1", "orphan"]


def test_item_index_map_reads_the_indexes_from_the_items(engine):
    seed(engine, "j1", {"i1": 5, "i2": 9})
    with Session(engine) as session:
        assert item_index_map(session, "j1") == {"i1": 5, "i2": 9}


def test_job_uploads_reports_the_job_badge_and_each_items_own_badge(engine):
    """任务行与条目行必须显示**同一个**结论 —— 前端各算一遍就会出现自相矛盾的界面。"""
    seed(engine, "j1", {"i1": 1, "i2": 2})
    add_upload(engine, "j1", upload_id="u1", item_id="i1", status=BiliUploadStatus.published.value)
    add_upload(engine, "j1", upload_id="u2", item_id="i2", status=BiliUploadStatus.failed.value)
    with Session(engine) as session:
        view = read_job_uploads(session, "j1")
    assert view.badge == BADGE_PARTIAL
    assert [row.badge for row in view.uploads] == [BADGE_UPLOADED, BADGE_FAILED]


def test_job_uploads_uses_the_latest_season_row(engine):
    """一个任务最多一个合集记录：再投一次是「重试」，不是新建一个。"""
    seed(engine, "j1", {"i1": 1})
    with Session(engine) as session:
        session.add(BiliSeason(id="s-old", job_id="j1", title="旧", status=BiliSeasonStatus.pending.value))
        session.add(BiliSeason(id="s-new", job_id="j1", title="新", status=BiliSeasonStatus.grouped.value))
        session.commit()
    with Session(engine) as session:
        view = read_job_uploads(session, "j1")
    assert view.season is not None and view.season.id in {"s-old", "s-new"}
    assert view.season.status_text.startswith("已提交归集")


def test_job_uploads_of_an_empty_job_is_a_blank_view(engine):
    seed(engine, "j1", {})
    with Session(engine) as session:
        view = read_job_uploads(session, "j1")
    assert view.badge == BADGE_NONE
    assert view.badge_text == "未上传"
    assert view.uploads == []
    assert view.season is None


# ================================================================ 汇总
def test_the_summary_lists_only_jobs_that_have_records(engine):
    """没有记录的任务 = 未上传，前端不必知道这件事 ——「N 个任务 = N 个请求」正是要避免的。"""
    seed(engine, "j1", {"i1": 1})
    seed(engine, "j2", {"i2": 1})
    seed(engine, "j3", {})
    add_upload(engine, "j2", upload_id="u2", item_id="i2", status=BiliUploadStatus.published.value)
    with Session(engine) as session:
        summary = read_all_job_uploads(session)
    assert [jobs.job_id for jobs in summary.jobs] == ["j2"]


def test_a_season_alone_also_puts_a_job_in_the_summary(engine):
    """合集记录也是记录：只建了合集还没投视频时，图标不能停在「未上传」。"""
    seed(engine, "j1", {"i1": 1})
    with Session(engine) as session:
        session.add(BiliSeason(id="s1", job_id="j1", title="合集", status=BiliSeasonStatus.pending.value))
        session.commit()
    with Session(engine) as session:
        summary = read_all_job_uploads(session)
    assert [jobs.job_id for jobs in summary.jobs] == ["j1"]
    assert summary.jobs[0].badge == BADGE_UNVERIFIED


def test_the_summary_is_sorted_by_job_id(engine):
    for job_id in ("jz", "ja", "jm"):
        seed(engine, job_id, {f"{job_id}-i1": 1})
        add_upload(engine, job_id, upload_id=f"{job_id}-u", item_id=f"{job_id}-i1", status=BiliUploadStatus.published.value)
    with Session(engine) as session:
        summary = read_all_job_uploads(session)
    assert [jobs.job_id for jobs in summary.jobs] == ["ja", "jm", "jz"]


def test_elapsed_seconds_is_filled_in_once_the_upload_finished(engine):
    """耗时是给用户看「这花了我多久」的，起止时间缺一个就不该编一个数出来。"""
    started = datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)
    finished = datetime(2026, 10, 14, 3, 1, 30, tzinfo=UTC)
    row = upload(BiliUploadStatus.published.value, started_at=started, finished_at=finished)
    assert read_upload(row).elapsed_seconds == pytest.approx(90.0)


def test_elapsed_seconds_ticks_while_still_running(engine):
    """还在跑时给的是「已经跑了多久」，**不是 `None`** —— 界面上的耗时不应该是空白。"""
    started = datetime.now(UTC) - timedelta(seconds=30)
    row = upload(BiliUploadStatus.uploading.value, started_at=started)
    value = read_upload(row).elapsed_seconds
    assert value is not None and 29 <= value < 120


def test_elapsed_seconds_is_zero_without_a_start_time(engine):
    """还没开始跑（排队中）时没有起算点，只能给 0 —— 编一个数出来更糟。"""
    assert read_upload(upload(BiliUploadStatus.queued.value)).elapsed_seconds == 0


# ================================================================ 归集记录的选取与那串 id
# 这一组锁的是「写入与读取必须选中同一条」。判据失守时的表现是**静默的**：
# 界面显示的批次不是后台正在更新的那一个 —— 看起来一切正常，直到用户去 B站 找合集。
def add_season(engine, season_id: str, *, job_id: str = "j1", created_at=None, upload_ids: str = "[]"):
    with Session(engine) as session:
        row = BiliSeason(id=season_id, job_id=job_id, upload_ids_json=upload_ids)
        if created_at is not None:
            row.created_at = created_at
        session.add(row)
        session.commit()


def test_list_seasons_is_ordered_so_the_selection_is_deterministic(engine):
    """无序返回时，同一个请求在不同时刻会选中不同的行 —— 界面于是会「闪」。"""
    base = datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)
    add_season(engine, "s-late", created_at=base + timedelta(minutes=5))
    add_season(engine, "s-early", created_at=base)
    add_season(engine, "s-mid", created_at=base + timedelta(minutes=1))
    with Session(engine) as session:
        assert [row.id for row in list_seasons(session, "j1")] == ["s-early", "s-mid", "s-late"]


def test_latest_season_picks_the_most_recent_row(engine):
    base = datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)
    add_season(engine, "s-old", created_at=base)
    add_season(engine, "s-new", created_at=base + timedelta(minutes=5))
    add_season(engine, "s-middle", created_at=base + timedelta(minutes=1))
    with Session(engine) as session:
        assert latest_season(session, "j1").id == "s-new"


def test_latest_season_is_none_when_the_job_has_no_record(engine):
    """没有记录是**正常结论**（单视频默认「不归集」就是这一档），不是异常。"""
    with Session(engine) as session:
        assert latest_season(session, "j1") is None


def test_write_and_read_agree_on_which_record_is_current(engine):
    """`latest_season` 的全部意义：`_upsert_season` 与 `read_job_uploads` 选中同一条。

    真出现两条记录时（历史数据、或将来某次改动引入的重复写入），两处各挑一条会让
    「后台在更新的那条」与「界面显示的那条」分叉，而任何一条单独的断言都看不出来。
    """
    base = datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)
    add_season(engine, "s-first", created_at=base)
    add_season(engine, "s-second", created_at=base + timedelta(minutes=5))
    with Session(engine) as session:
        assert latest_season(session, "j1").id == "s-second"
        assert read_job_uploads(session, "j1").season.id == "s-second"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ('["u1", "u2"]', ["u1", "u2"]),
        ("[]", []),
        ("", []),
        ("not json at all", []),
        ('{"a": 1}', []),
        ("[1, 2]", ["1", "2"]),
        ("[null]", ["None"]),
    ],
)
def test_season_upload_ids_tolerates_every_shape_the_column_can_be_in(stored, expected):
    """这一列是 JSON **文本**，历史上被三种写法各自解析过。坏值一律当空表：
    一条读不懂的记录不该让整个任务的徽标算不出来（那会让「失败」看起来像「未上传」）。"""
    row = BiliSeason(id="s1", job_id="j1", upload_ids_json=stored)
    assert season_upload_ids(row) == expected


def test_season_upload_ids_of_a_null_column_is_empty():
    """`upload_ids_json` 允许为 `None`（老行补列时会是这样）。"""
    assert season_upload_ids(BiliSeason(id="s1", job_id="j1", upload_ids_json=None)) == []
