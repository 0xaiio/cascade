"""启动时收敛「上一次进程退出留下的孤儿条目」。

进程退出（被杀 / 崩溃 / 正常关闭）时内存里那个 `asyncio.Queue` 一起消失，而 `jobitem.status`
还停在 `queued` / `running`。重启后**没有任何代码**把这两个状态重新交给 worker ——
队列里没有它们的 id，`_queued_item_ids()` 也只在新建/重启任务时被调用。结果是界面上
一排**永远不动、且看不出原因**的任务，用户只能一条条手动删。生产库曾累积
332 个 `queued` + 3 个 `running`（见 `ai/perf/PLAN.md` §8）。

本模块只做一件事：**把条目改成「重启后成立的状态」**，并告诉调用方哪些条目要重新排队。
任务级终态仍然由 `JobManager` 的 `_maybe_finish_job` / `_finish_job` 收敛 ——
同一件事若在这里再写一遍，「什么算结束」就会分裂成两份定义，迟早对不上。

为什么默认是「重新排队」而不是「标记失败」：下载是**幂等且可续传**的
（`build_download_options` 里 `continuedl=True`，`.part` 会接着用），重排队最坏情况是
多下一遍；而标记失败必须由用户逐条点「重启」才能恢复，正是当前这个缺陷的痛点。
B站 上传那次选择「折成失败」的理由（重投会产生重复稿件）在下载侧不成立。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlmodel import Session, select

from .models import Job, JobItem, JobStatus, utc_now

logger = logging.getLogger(__name__)

# 进程退出时**可能**残留在库里的条目状态。`paused` 不在其中：那是用户主动按的，
# 重启后必须还是暂停（`JobManager.pause` 已把条目一并改成 paused）。
ORPHAN_ITEM_STATUSES = (JobStatus.queued.value, JobStatus.running.value)

RESUME_POLICY = "resume"
FAIL_POLICY = "fail"

INTERRUPTED_WHILE_RUNNING_ERROR = "上次退出时这条正在下载，已被中断；当前设置为「标记失败」，点「重启」可重新下载。"
INTERRUPTED_WHILE_QUEUED_ERROR = "上次退出时这条还在排队，没有开始；当前设置为「标记失败」，点「重启」可重新下载。"
DANGLING_ITEM_ERROR = "所属任务已不存在，这条不会再被调度。"
# 与 `JobManager._run_item_work` 里取消时写的是同一句：界面按这一句判定「是取消，不是失败」。
CANCELLED_ITEM_ERROR = "Cancelled"

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class OrphanRecoveryReport:
    """一次启动扫描的结果。数量为 0 表示「库本来就是干净的」。"""

    policy: str = RESUME_POLICY
    requeued_item_ids: list[str] = field(default_factory=list)
    failed_item_ids: list[str] = field(default_factory=list)
    cancelled_item_ids: list[str] = field(default_factory=list)
    paused_item_ids: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.requeued_item_ids)
            + len(self.failed_item_ids)
            + len(self.cancelled_item_ids)
            + len(self.paused_item_ids)
        )


def normalize_policy(value: str | None) -> str:
    """只认 `fail`，其余一律当 `resume`。

    取值写错不该让应用启动不了（配置错一个字就开不了机，用户没有任何线索），
    但也不能静默生效 —— 调用方负责在值被改写时打一条 warning。
    """
    return FAIL_POLICY if (value or "").strip().lower() == FAIL_POLICY else RESUME_POLICY


def recover_orphaned_items(session: Session, *, policy: str = RESUME_POLICY) -> OrphanRecoveryReport:
    """把残留的 `queued` / `running` 条目收敛掉，返回报告（调用方负责 commit）。"""
    policy = normalize_policy(policy)
    report = OrphanRecoveryReport(policy=policy)
    items = session.exec(select(JobItem).where(JobItem.status.in_(ORPHAN_ITEM_STATUSES))).all()
    if not items:
        return report

    jobs = {
        job.id: job
        for job in session.exec(select(Job).where(Job.id.in_({item.job_id for item in items}))).all()
    }
    # 有已取消条目的任务视为「正在被取消」：`JobManager.cancel()` 只把**排队中**的任务状态
    # 改成 cancelled，运行中的任务靠内存里的 `self._cancelled` 集合收尾 —— 那个集合随进程
    # 一起消失。若只看任务状态，重启会把用户明明取消过的任务接着下完。
    cancelling_job_ids = set(
        session.exec(select(JobItem.job_id).where(JobItem.status == JobStatus.cancelled.value)).all()
    )

    # 按「任务创建时间 → 条目序号」恢复，而不是按 id 的字典序：332 条的积压要按当初的
    # 下单顺序跑，否则用户看到的是随机顺序。
    ordered = sorted(
        items,
        key=lambda item: (
            _job_created_at(jobs.get(item.job_id)),
            item.job_id,
            item.index,
        ),
    )

    resumed_job_ids: set[str] = set()
    for item in ordered:
        job = jobs.get(item.job_id)
        if job is None:
            _retire(item, JobStatus.cancelled.value, DANGLING_ITEM_ERROR)
            report.cancelled_item_ids.append(item.id)
            continue
        if job.status == JobStatus.paused.value:
            # 用户按的暂停优先于一切策略：重启后必须还是暂停，否则「暂停」就是个谎言。
            _retire(item, JobStatus.paused.value, None)
            report.paused_item_ids.append(item.id)
            _remember_job(report, job.id)
            continue
        if job.status == JobStatus.cancelled.value or job.id in cancelling_job_ids:
            _retire(item, JobStatus.cancelled.value, CANCELLED_ITEM_ERROR)
            report.cancelled_item_ids.append(item.id)
            if job.status != JobStatus.cancelled.value:
                job.status = JobStatus.cancelled.value
                job.current_item_title = None
                job.speed = None
                job.eta = None
                job.finished_at = utc_now()
                job.updated_at = job.finished_at
                session.add(job)
            _remember_job(report, job.id)
            continue
        if policy == FAIL_POLICY:
            message = (
                INTERRUPTED_WHILE_RUNNING_ERROR
                if item.status == JobStatus.running.value
                else INTERRUPTED_WHILE_QUEUED_ERROR
            )
            _retire(item, JobStatus.failed.value, message)
            report.failed_item_ids.append(item.id)
            _remember_job(report, job.id)
            continue

        _requeue(item)
        report.requeued_item_ids.append(item.id)
        _remember_job(report, job.id)
        if job.id not in resumed_job_ids:
            resumed_job_ids.add(job.id)
            # 本次运行重新计时：`elapsed_seconds` 是「下载花了多久」，不是「从下单到现在」。
            # 留着上次的 started_at 会把停机那几天算进去（实测 3 天）。
            job.started_at = None
            job.finished_at = None
            job.error = None
            job.updated_at = utc_now()
            session.add(job)

    session.flush()
    if report.total:
        logger.warning(
            "启动恢复：上次退出留下 %s 个未完成条目（重新排队 %s / 标记失败 %s / 取消 %s / 暂停 %s，策略=%s）",
            report.total,
            len(report.requeued_item_ids),
            len(report.failed_item_ids),
            len(report.cancelled_item_ids),
            len(report.paused_item_ids),
            policy,
        )
    return report


def _job_created_at(job: Job | None) -> datetime:
    if job is None or job.created_at is None:
        return _EPOCH
    created_at = job.created_at
    return created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)


def _remember_job(report: OrphanRecoveryReport, job_id: str) -> None:
    if job_id not in report.job_ids:
        report.job_ids.append(job_id)


def _requeue(item: JobItem) -> None:
    """把条目放回「等着被 worker 领走」的状态。

    进度类字段一律清空：`.part` 到底还剩多少只有 yt-dlp 知道，界面先显示 0 再由
    第一个进度回调纠正，比留一个上次的 87% 诚实（那个 87% 可能对应一个已经被删掉的临时文件）。
    """
    item.status = JobStatus.queued.value
    item.progress = 0.0
    item.downloaded_bytes = None
    item.total_bytes = None
    item.speed = None
    item.eta = None
    item.error = None
    item.started_at = None
    item.finished_at = None
    item.updated_at = utc_now()


def _retire(item: JobItem, status: str, error: str | None) -> None:
    item.status = status
    item.error = error
    item.speed = None
    item.eta = None
    item.updated_at = utc_now()
    item.finished_at = None if status == JobStatus.paused.value else item.updated_at
