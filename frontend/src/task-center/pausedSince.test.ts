/**
 * E-05 · 暂停时长的阈值、取整与措辞。
 *
 * 时间解析复用 `parseServerTime`（UTC 是唯一口径），这里只钉住「什么时候显示」
 * 与「显示什么」；不发通知这一条由 notifications 的终态集合与集成用例共同守住。
 */

import { describe, expect, test } from "vitest";
import { PAUSED_NOTICE_THRESHOLD_MS, PAUSED_TICK_MS, pausedDurationLabel } from "./pausedSince";
import { TERMINAL_JOB_STATUSES } from "../notifications/notifications";

const NOW = Date.parse("2026-11-04T12:00:00Z");
const HOUR = 60 * 60 * 1000;
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe("暂停时长文案（E-05）", () => {
  test("阈值是两小时，且按分钟而非秒表推进", () => {
    expect(PAUSED_NOTICE_THRESHOLD_MS).toBe(2 * HOUR);
    expect(PAUSED_TICK_MS).toBe(60 * 1000);
  });

  test("不到两小时不打扰", () => {
    expect(pausedDurationLabel(ago(HOUR), NOW)).toBeNull();
    expect(pausedDurationLabel(ago(2 * HOUR - 1), NOW)).toBeNull();
    expect(pausedDurationLabel(ago(0), NOW)).toBeNull();
  });

  test("正好两小时开始显示，按整小时向下取整", () => {
    expect(pausedDurationLabel(ago(2 * HOUR), NOW)).toBe("已暂停 2 小时");
    expect(pausedDurationLabel(ago(2 * HOUR + 59 * 60 * 1000), NOW)).toBe("已暂停 2 小时");
    expect(pausedDurationLabel(ago(3 * HOUR), NOW)).toBe("已暂停 3 小时");
  });

  test("超过一天换成「天 + 小时」，不写 26 小时", () => {
    expect(pausedDurationLabel(ago(23 * HOUR), NOW)).toBe("已暂停 23 小时");
    expect(pausedDurationLabel(ago(24 * HOUR), NOW)).toBe("已暂停 1 天");
    expect(pausedDurationLabel(ago(25 * HOUR), NOW)).toBe("已暂停 1 天 1 小时");
    expect(pausedDurationLabel(ago(48 * HOUR), NOW)).toBe("已暂停 2 天");
  });

  test("历史任务的近似起点必须带「约」", () => {
    expect(pausedDurationLabel(ago(3 * HOUR), NOW, true)).toBe("已暂停约 3 小时");
    expect(pausedDurationLabel(ago(3 * HOUR), NOW, false)).toBe("已暂停 3 小时");
  });

  test("空值、坏值、未来时间都不编数字", () => {
    expect(pausedDurationLabel(null, NOW)).toBeNull();
    expect(pausedDurationLabel("", NOW)).toBeNull();
    expect(pausedDurationLabel("not-a-date", NOW)).toBeNull();
    expect(pausedDurationLabel(new Date(NOW + 5 * HOUR).toISOString(), NOW)).toBeNull();
  });

  test("没有偏移量时也按 UTC 解析（跨接口时间口径）", () => {
    const naive = new Date(NOW - 3 * HOUR).toISOString().replace("Z", "");
    expect(pausedDurationLabel(naive, NOW)).toBe("已暂停 3 小时");
  });

  test("页面开着时同一个起点会随时间推进", () => {
    expect(pausedDurationLabel(ago(3 * HOUR), NOW)).toBe("已暂停 3 小时");
    expect(pausedDurationLabel(ago(3 * HOUR), NOW + HOUR)).toBe("已暂停 4 小时");
  });

  test("paused 不是终态：完成提醒的集合仍然只有 succeeded / failed", () => {
    // E-05 是任务行上的一行提示，不新增通知渠道（014 的有意选择）。
    expect(TERMINAL_JOB_STATUSES.has("paused")).toBe(false);
    expect([...TERMINAL_JOB_STATUSES].sort()).toEqual(["failed", "succeeded"]);
  });
});
