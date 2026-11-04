/**
 * E-02 · 筛选判定的纯函数层。
 *
 * 这里钉住三件事：状态档位覆盖全部生命周期取值、关键字是普通子串（不是正则）、
 * 命中条目标题只影响「显示哪个任务」而不裁剪条目列表。
 */

import { describe, expect, test } from "vitest";
import type { Job } from "../contracts/types";
import { EMPTY_JOB_FILTER, filterJobs, JOB_STATUS_FILTERS } from "./jobFilter";
import { jobPayload, pausedJobPayload, playlistJobPayload } from "../test/appFixtures";

function job(id: string, title: string, status: string): Job {
  return {
    ...jobPayload,
    id,
    title,
    status,
    total_items: 1,
    items: [
      {
        ...jobPayload.items[0],
        id: `${id}-item`,
        job_id: id,
        title: `${title} item`,
        source_url: `https://youtu.be/${id}`,
        status
      }
    ]
  };
}

const ALL = [
  job("a", "Alpha 任务", "queued"),
  job("b", "Beta 任务", "running"),
  pausedJobPayload,
  job("d", "Delta 任务", "failed"),
  job("e", "Echo 任务", "succeeded"),
  job("f", "Foxtrot 任务", "cancelled")
];

describe("任务中心筛选判定（E-02）", () => {
  test("全部状态不收窄任何任务，且标记成「未筛选」", () => {
    const result = filterJobs(ALL, EMPTY_JOB_FILTER);
    expect(result.jobs.map((item) => item.id)).toEqual(ALL.map((item) => item.id));
    expect(result.filtered).toBe(false);
    expect(result.hiddenCount).toBe(0);
    expect(result.total).toBe(ALL.length);
    expect(result.jobs).not.toBe(ALL); // 返回新数组，不改调用方那份全量列表
    expect(ALL).toHaveLength(6);
  });

  test("「在跑」合并 queued 与 running 两档", () => {
    const result = filterJobs(ALL, { status: "active", query: "" });
    expect(result.jobs.map((item) => item.id)).toEqual(["a", "b"]);
    expect(result.hiddenCount).toBe(4);
    expect(result.filtered).toBe(true);
  });

  test.each([
    ["paused", ["job-paused"]],
    ["failed", ["d"]],
    ["succeeded", ["e"]],
    ["cancelled", ["f"]]
  ] as const)("%s 档只显示对应状态", (status, expected) => {
    expect(filterJobs(ALL, { status, query: "" }).jobs.map((item) => item.id)).toEqual([...expected]);
  });

  test("六档合起来恰好覆盖 JobStatus 的全部六个取值", () => {
    const covered = JOB_STATUS_FILTERS.flatMap((option) => option.statuses ?? []);
    expect(new Set(covered)).toEqual(new Set(["queued", "running", "paused", "failed", "succeeded", "cancelled"]));
  });

  test("未知状态在「全部」档不会被悄悄丢掉", () => {
    const withUnknown = [...ALL, job("g", "Future 任务", "archived-by-a-newer-version")];
    expect(filterJobs(withUnknown, EMPTY_JOB_FILTER).jobs).toHaveLength(7);
    expect(filterJobs(withUnknown, { status: "active", query: "" }).jobs.map((item) => item.id)).toEqual(["a", "b"]);
  });

  test("关键字不区分大小写，且首尾空格不算进搜索词", () => {
    expect(filterJobs(ALL, { status: "all", query: "  ALPHA  " }).jobs.map((item) => item.id)).toEqual(["a"]);
    expect(filterJobs(ALL, { status: "all", query: "   " }).filtered).toBe(false);
  });

  test("关键字命中任务链接", () => {
    const withLinks = [job("x", "X", "queued"), { ...job("y", "Y", "failed"), url: "https://youtu.be/only-y" }];
    expect(filterJobs(withLinks, { status: "all", query: "ONLY-Y" }).jobs.map((item) => item.id)).toEqual(["y"]);
    expect(filterJobs(withLinks, { status: "all", query: "youtu.be/running" }).jobs.map((item) => item.id)).toEqual(["x"]);
  });

  test("关键字命中条目标题时显示整个任务并记下要自动展开", () => {
    const result = filterJobs([playlistJobPayload], { status: "all", query: "part one" });
    expect(result.jobs.map((item) => item.id)).toEqual(["job-playlist"]);
    expect(result.itemMatchJobIds.has("job-playlist")).toBe(true);
  });

  test("关键字命中条目链接也记下要自动展开", () => {
    const result = filterJobs([playlistJobPayload], { status: "all", query: "youtu.be/two" });
    expect(result.itemMatchJobIds.has("job-playlist")).toBe(true);
  });

  test("命中条目标题不裁剪 job.items（批量操作的范围不能被筛选改写）", () => {
    const result = filterJobs([playlistJobPayload], { status: "all", query: "part two" });
    expect(result.jobs[0].items).toHaveLength(playlistJobPayload.items.length);
  });

  test("正则元字符按普通子串处理，不抛也不意外全中", () => {
    expect(() => filterJobs(ALL, { status: "all", query: "[" })).not.toThrow();
    expect(filterJobs(ALL, { status: "all", query: "*" }).jobs).toHaveLength(0);
    expect(filterJobs(ALL, { status: "all", query: "[" }).jobs).toHaveLength(0);
    expect(filterJobs(ALL, { status: "all", query: "Alpha 任务[" }).jobs).toHaveLength(0);
  });

  test("状态与关键字叠加时两个条件都要满足", () => {
    const result = filterJobs(ALL, { status: "active", query: "beta" });
    expect(result.jobs.map((item) => item.id)).toEqual(["b"]);
    expect(filterJobs(ALL, { status: "failed", query: "beta" }).jobs).toHaveLength(0);
  });

  test("空列表与「一个都不匹配」都给出可读的计数", () => {
    expect(filterJobs([], EMPTY_JOB_FILTER)).toMatchObject({ total: 0, hiddenCount: 0, filtered: false });
    const none = filterJobs(ALL, { status: "all", query: "不存在的标题" });
    expect(none.jobs).toHaveLength(0);
    expect(none.hiddenCount).toBe(6);
  });

  test("标题缺字段时不抛异常，只当作没命中", () => {
    const broken = { ...jobPayload, id: "broken", title: null, url: null, items: [] } as unknown as Job;
    expect(() => filterJobs([broken], { status: "all", query: "alpha" })).not.toThrow();
    expect(filterJobs([broken], { status: "all", query: "alpha" }).jobs).toHaveLength(0);
  });
});
