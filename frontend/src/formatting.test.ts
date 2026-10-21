import { describe, expect, test } from "vitest";
import { formatDateTime, formatMediaDuration, parseServerTime } from "./formatting";

/**
 * 纯格式化函数。
 *
 * `parseServerTime` / `formatDateTime` 钉的是**时区**这件事（ai/bug-fix/020）：
 * 服务端存的是 UTC，而 `Date.parse` 会把不带偏移量的串按**本地**时间解释。
 * 在 UTC+8 上，那让「已用 52 秒」显示成「8:00:52」。这类偏差不报错，只是数字变大，
 * 所以这里必须有一层不依赖本地时区的断言。
 */

/** 把一个时刻渲染成本地时区的 `YYYY-MM-DD HH:MM:SS`（测试自己算一份，不复用被测实现）。 */
function localStamp(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

describe("parseServerTime", () => {
  test("不带偏移量的串按 UTC 解释 —— 那正是服务端发出来的那种", () => {
    // 服务端存 UTC，SQLite 不存时区，读回来发出去就是没有 `Z` 的串。
    expect(parseServerTime("2026-10-21T10:29:38.727960")).toBe(
      Date.parse("2026-10-21T10:29:38.727960Z")
    );
  });

  test("带了偏移量就按偏移量算（服务端已经补上 `Z`，别的时区也要支持）", () => {
    expect(parseServerTime("2026-10-21T10:29:38Z")).toBe(Date.parse("2026-10-21T10:29:38Z"));
    expect(parseServerTime("2026-10-21T18:29:38+08:00")).toBe(Date.parse("2026-10-21T10:29:38Z"));
    expect(parseServerTime("2026-10-21T11:29:38+01:00")).toBe(Date.parse("2026-10-21T10:29:38Z"));
  });

  test("空值与坏值都给 null，不给 0 —— 调用方据此「不显示」而不是「显示 0:00」", () => {
    expect(parseServerTime(null)).toBeNull();
    expect(parseServerTime(undefined)).toBeNull();
    expect(parseServerTime("")).toBeNull();
    expect(parseServerTime("not-a-date")).toBeNull();
  });
});

describe("formatDateTime", () => {
  test("按**本地时区**显示，不把 UTC 的墙上时间当成本地时间摆出来", () => {
    // 原先是切片直出，于是本机 18:29 开始的任务显示成「开始 10:29:38」。
    expect(formatDateTime("2026-10-21T10:29:38Z")).toBe(localStamp("2026-10-21T10:29:38Z"));
    expect(formatDateTime("2026-10-21T10:29:38.727960")).toBe(
      localStamp("2026-10-21T10:29:38.727960Z")
    );
  });

  test("不带秒与带微秒的两种形态都对齐到同一秒", () => {
    expect(formatDateTime("2026-10-21T10:29:38")).toBe(formatDateTime("2026-10-21T10:29:38.000000Z"));
  });

  test("空值显示「--」；坏值也显示「--」而不是 Invalid Date", () => {
    expect(formatDateTime(null)).toBe("--");
    expect(formatDateTime(undefined)).toBe("--");
    expect(formatDateTime("")).toBe("--");
    expect(formatDateTime("not-a-date")).toBe("--");
  });
});

describe("formatMediaDuration", () => {
  test("always renders 时:分:秒 so a duration never looks like elapsed time", () => {
    expect(formatMediaDuration(185)).toBe("0:03:05");
    expect(formatMediaDuration(3725)).toBe("1:02:05");
    expect(formatMediaDuration(3600 * 10)).toBe("10:00:00");
  });

  test("floors fractional seconds instead of rounding up past the real length", () => {
    expect(formatMediaDuration(59.9)).toBe("0:00:59");
  });

  test("treats a missing or unusable duration as unknown instead of 0:00:00", () => {
    expect(formatMediaDuration(null)).toBe("未知");
    expect(formatMediaDuration(0)).toBe("未知");
    expect(formatMediaDuration(Number.NaN)).toBe("未知");
  });
});