import { describe, expect, test } from "vitest";
import {
  BADGE_NONE,
  BAD_SEASON_STATUSES,
  BAD_UPLOAD_STATUSES,
  RETRYABLE_SEASON_STATUSES,
  RETRYABLE_UPLOAD_STATUSES,
  isBadSeason,
  isBadSubtitle,
  isBadUpload,
  isNotableSubtitle,
  isNotableUpload,
  isRetryableSeason,
  isRetryableUpload
} from "./biliStatus";
import type { BiliSeasonStatus, BiliSubtitleState, BiliUploadStatus } from "./types";

/**
 * 状态判定。
 *
 * 这里测的不是「某个值等于某个值」，而是**分类的结果**：每一档状态在界面上会变成
 * 「有没有重试按钮」「算不算出问题」。判据是「用户此刻该做什么」，所以最贵的一条是
 * **`unverified` 不许被算成失败** —— 把它当失败，用户会去重投一条已经投上去的稿件。
 */

const ALL_UPLOAD_STATUSES: BiliUploadStatus[] = [
  "queued",
  "uploading",
  "submitted",
  "published",
  "reviewing",
  "rejected",
  "failed",
  "unverified"
];

const ALL_SEASON_STATUSES: BiliSeasonStatus[] = ["pending", "grouping", "grouped", "skipped", "failed"];

describe("稿件状态", () => {
  test("可重试的三档：失败、被退回、待核对", () => {
    const retryable = ALL_UPLOAD_STATUSES.filter(isRetryableUpload);
    expect(retryable).toEqual(["rejected", "failed", "unverified"]);
  });

  test("已经成功的那两档不摆重试按钮（服务端会挡成「什么都没做」，按钮本身就是误导）", () => {
    expect(isRetryableUpload("published")).toBe(false);
    expect(isRetryableUpload("reviewing")).toBe(false);
  });

  test("在途的三档也不摆重试按钮（它们正在跑）", () => {
    for (const status of ["queued", "uploading", "submitted"] as BiliUploadStatus[]) {
      expect(isRetryableUpload(status)).toBe(false);
    }
  });

  test("「出问题」只有失败与被退回 —— 待核对**不算**", () => {
    const bad = ALL_UPLOAD_STATUSES.filter(isBadUpload);
    expect(bad).toEqual(["rejected", "failed"]);
    expect(isBadUpload("unverified")).toBe(false);
  });

  test("可重试 ⊃ 出问题，多出来的那个正好是待核对", () => {
    for (const status of BAD_UPLOAD_STATUSES) {
      expect(isRetryableUpload(status)).toBe(true);
    }
    const extra = RETRYABLE_UPLOAD_STATUSES.filter((status) => !BAD_UPLOAD_STATUSES.includes(status));
    expect(extra).toEqual(["unverified"]);
  });

  test("八档状态各有归宿：可重试 / 其余（成功 + 在途），没有哪一档两边都算", () => {
    // 这张表是手抄的 —— TS 的联合类型没有运行时反射，所以它保证的是**归类完整**，
    // 而不是「类型里加了新档就会红」。新档要在这里补一行，同时想清楚它该落在哪边。
    const rest: BiliUploadStatus[] = ["published", "reviewing", "queued", "uploading", "submitted"];
    expect(new Set([...RETRYABLE_UPLOAD_STATUSES, ...rest]).size).toBe(ALL_UPLOAD_STATUSES.length);
    for (const status of rest) {
      expect(isRetryableUpload(status)).toBe(false);
      expect(isBadUpload(status)).toBe(false);
    }
  });
});

describe("合集状态", () => {
  test("可重试的两档：失败，以及还没跑完的 pending", () => {
    const retryable = ALL_SEASON_STATUSES.filter(isRetryableSeason);
    expect(retryable).toEqual(["pending", "failed"]);
  });

  test("归集完成与正在归集都不摆重试按钮", () => {
    expect(isRetryableSeason("grouped")).toBe(false);
    expect(isRetryableSeason("grouping")).toBe(false);
  });

  test("skipped 是用户自己选的终态，不算出问题", () => {
    expect(isBadSeason("skipped")).toBe(false);
    expect(isRetryableSeason("skipped")).toBe(false);
  });

  test("「归集出问题」只有失败一档", () => {
    const bad = ALL_SEASON_STATUSES.filter(isBadSeason);
    expect(bad).toEqual(["failed"]);
    expect(BAD_SEASON_STATUSES).toEqual(["failed"]);
  });

  test("合集的四个集合常量与判定函数同源（改一个忘另一个会在这里红）", () => {
    for (const status of RETRYABLE_SEASON_STATUSES) {
      expect(isRetryableSeason(status)).toBe(true);
    }
    for (const status of ALL_SEASON_STATUSES) {
      expect(isRetryableSeason(status)).toBe(RETRYABLE_SEASON_STATUSES.includes(status));
      expect(isBadSeason(status)).toBe(BAD_SEASON_STATUSES.includes(status));
    }
  });
});

describe("徽标与条目可见性", () => {
  test("「还没上传」那一档的取值与服务端一致", () => {
    expect(BADGE_NONE).toBe("none");
  });

  test("有错误或有视频链接的条目才值得渲染一行", () => {
    expect(isNotableUpload({ error: "失败了", video_url: null, subtitle_state: "skipped" })).toBe(true);
    expect(isNotableUpload({ error: null, video_url: "https://www.bilibili.com/video/BV1xx411c7mD", subtitle_state: "skipped" })).toBe(true);
    expect(isNotableUpload({ error: "失败了", video_url: "https://www.bilibili.com/video/BV1xx411c7mD", subtitle_state: "skipped" })).toBe(true);
  });

  test("两样都没有就不渲染 —— 空字符串等同于「没有」，不是「有」", () => {
    expect(isNotableUpload({ error: null, video_url: null, subtitle_state: "skipped" })).toBe(false);
    expect(isNotableUpload({ error: "", video_url: "", subtitle_state: "posted" })).toBe(false);
  });

  test("字幕失败也算「有话说」—— 否则没有 bvid 的稿件会把这件事藏掉", () => {
    expect(isNotableUpload({ error: null, video_url: null, subtitle_state: "failed" })).toBe(true);
  });
});

describe("CC 字幕的两档判据与稿件状态无关", () => {
  const ALL_SUBTITLE_STATES: BiliSubtitleState[] = ["posted", "skipped", "failed"];

  test("只有失败算「出问题」：已上传的稿件也可能字幕失败", () => {
    expect(ALL_SUBTITLE_STATES.filter(isBadSubtitle)).toEqual(["failed"]);
  });

  test("posted 与 failed 都要说一句，skipped 不必（表单里已经写过「无字幕」）", () => {
    expect(ALL_SUBTITLE_STATES.filter(isNotableSubtitle)).toEqual(["posted", "failed"]);
  });
});
