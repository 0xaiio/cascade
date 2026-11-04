/**
 * E-02 · 任务中心的筛选与搜索**判定**。
 *
 * 这是纯函数，不碰 DOM、不碰请求、不改 `jobs`。放在这里而不是直接写进 `JobQueue`，
 * 是为了让「哪一档该显示哪些状态」「命中条目标题要不要算命中」这两条判据能被单测钉住。
 *
 * 设计边界（有意为之）：
 * - 筛选是**展示层**的：`App` 里的全量 `jobs` 仍要留给完成提醒的状态跃迁比对、
 *   B站 徽标与选中集合清理，所以这里只返回一份新数组，绝不回写。
 * - 关键字是**普通子串**，不是正则：用户输 `[` `*` `.` 不该抛异常，也不该意外全中。
 * - 命中条目标题时把整个任务显示出来，并记下 `itemMatchJobIds` 供 UI 自动展开；
 *   **不裁剪 `job.items`** —— 否则用户会以为其它条目不存在，批量删除就会误判范围。
 */

import type { Job } from "../contracts/types";

export type JobStatusFilter = "all" | "active" | "paused" | "failed" | "succeeded" | "cancelled";

export interface JobStatusFilterOption {
  value: JobStatusFilter;
  label: string;
  /** `null` = 这一档不按状态收窄（未知状态也不会被悄悄丢掉）。 */
  statuses: readonly string[] | null;
}

/** 六档覆盖 `JobStatus` 的全部取值加上「在跑」这个合并档，另有一档「全部」。 */
export const JOB_STATUS_FILTERS: readonly JobStatusFilterOption[] = [
  { value: "all", label: "全部状态", statuses: null },
  { value: "active", label: "在跑", statuses: ["queued", "running"] },
  { value: "paused", label: "已暂停", statuses: ["paused"] },
  { value: "failed", label: "失败", statuses: ["failed"] },
  { value: "succeeded", label: "完成", statuses: ["succeeded"] },
  { value: "cancelled", label: "已取消", statuses: ["cancelled"] }
];

export const EMPTY_JOB_FILTER = { status: "all" as JobStatusFilter, query: "" };

export interface JobFilter {
  status: JobStatusFilter;
  query: string;
}

export interface JobFilterResult {
  jobs: Job[];
  total: number;
  hiddenCount: number;
  /** 是否施加了任何筛选。没筛选时计数徽标保持原来的「N 个任务」。 */
  filtered: boolean;
  /** 命中来自条目标题/链接（而非任务本身）的任务 id —— UI 据此自动展开合集。 */
  itemMatchJobIds: Set<string>;
}

function includesText(haystack: string | null | undefined, needle: string): boolean {
  return (haystack ?? "").toLocaleLowerCase().includes(needle);
}

export function filterJobs(jobs: Job[], filter: JobFilter): JobFilterResult {
  const option = JOB_STATUS_FILTERS.find((candidate) => candidate.value === filter.status) ?? JOB_STATUS_FILTERS[0];
  const needle = filter.query.trim().toLocaleLowerCase();
  const filtered = option.statuses !== null || needle.length > 0;
  const itemMatchJobIds = new Set<string>();

  const visible = jobs.filter((job) => {
    if (option.statuses && !option.statuses.includes(job.status)) return false;
    if (!needle) return true;
    // 任务标题 / 链接先判；命中就不用再扫条目。
    if (includesText(job.title, needle) || includesText(job.url, needle)) return true;
    const itemHit = job.items.some(
      (item) => includesText(item.title, needle) || includesText(item.source_url, needle)
    );
    if (itemHit) itemMatchJobIds.add(job.id);
    return itemHit;
  });

  return {
    jobs: visible,
    total: jobs.length,
    hiddenCount: jobs.length - visible.length,
    filtered,
    itemMatchJobIds
  };
}
