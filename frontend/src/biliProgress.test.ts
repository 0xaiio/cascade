import { describe, expect, test } from "vitest";
import { UPLOAD_STAGES, jobUploadProgress, uploadElapsedLabel, uploadStageView } from "./biliProgress";
import { biliUploadPayload } from "./test/appFixtures";
import type { BiliUpload, BiliUploadStatus } from "./types";

/**
 * 上传进度的**判定**。
 *
 * 这里测的是「到哪一步了、在等什么」，不是「画出来好不好看」。最贵的几条是：
 * 已成功的两条**不许**再显示成进行中；`reviewing` 的节点必须全部走完（审核不是第五步，
 * 是「核对之后等平台」）；以及合集里必须选**正在跑的那条**而不是第一条。
 */

function upload(overrides: Partial<BiliUpload> = {}): BiliUpload {
  return { ...biliUploadPayload, ...overrides };
}

describe("四段轨迹", () => {
  test("节点与服务端的三道闸门顺序一致：排队 → 传输 → 提交 → 核对", () => {
    expect(UPLOAD_STAGES.map((stage) => stage.key)).toEqual(["queued", "transfer", "submit", "verify"]);
    expect(UPLOAD_STAGES.map((stage) => stage.label)).toEqual(["排队", "传输", "提交", "核对"]);
  });

  test("`uploading` 内部的三个子状态由服务端的闸门值决定，不另造判断", () => {
    // 0.2 = `pre_upload` 出现（开始准备）；0.6 = `Upload completed`；0.95 = 投稿接口成功。
    const detail = (progress: number) => uploadStageView({ status: "uploading", progress }).detail;
    expect(detail(0)).toBe("正在准备上传");
    expect(detail(0.19)).toBe("正在准备上传");
    expect(detail(0.2)).toBe("正在传输视频文件");
    expect(detail(0.59)).toBe("正在传输视频文件");
    expect(detail(0.6)).toBe("文件传完了，正在提交投稿信息");
    expect(detail(0.95)).toBe("文件传完了，正在提交投稿信息");
  });

  test("八档状态各有归属：在哪一段、是否走完、是否断了", () => {
    const table: Array<[BiliUploadStatus, number, string, boolean, boolean]> = [
      // 状态, 进度, 期望节点键, settled, broken
      ["queued", 0, "queued", false, false],
      ["uploading", 0.6, "submit", false, false],
      ["submitted", 0.95, "verify", false, false],
      ["published", 1, "verify", true, false],
      ["reviewing", 0.95, "verify", true, false],
      ["unverified", 0.95, "verify", true, false],
      ["rejected", 0.95, "verify", true, true],
      ["failed", 0, "verify", true, true]
    ];
    for (const [status, progress, key, settled, broken] of table) {
      const view = uploadStageView({ status, progress });
      expect([status, view.key, view.settled, view.broken]).toEqual([status, key, settled, broken]);
    }
  });

  test("审核不是第五步，而是「核对之后等平台」：四个节点全部走完，且没有百分比可给", () => {
    const view = uploadStageView({ status: "reviewing", progress: 0.95 });
    expect(view.index).toBe(UPLOAD_STAGES.length - 1);
    expect(view.settled).toBe(true);
    expect(view.detail).toBe("已提交，B 站 审核中");
  });
});

describe("任务级聚合", () => {
  test("一条记录都没有 → null（界面本来就不该有 B站 区）", () => {
    expect(jobUploadProgress([])).toBeNull();
  });

  test("单视频上传中：第 1/1 条，计时从开始那一刻起", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", progress: 0.2, started_at: "2026-10-21T03:00:00Z" })
    ]);
    expect(view).not.toBeNull();
    expect(view!.total).toBe(1);
    expect(view!.current).toBe(1);
    expect(view!.done).toBe(0);
    expect(view!.running).toBe(true);
    expect(view!.waiting).toBe(true);
    expect(view!.since).toBe("2026-10-21T03:00:00Z");
    expect(view!.stage.detail).toBe("正在传输视频文件");
  });

  test("合集里选**正在跑的那条**，而不是第一条没成功的", () => {
    const view = jobUploadProgress([
      upload({ id: "a", index: 1, title: "Part one", status: "failed", progress: 0 }),
      upload({
        id: "b",
        index: 2,
        title: "Part two",
        status: "uploading",
        progress: 0.2,
        started_at: "2026-10-21T03:00:00Z"
      }),
      upload({ id: "c", index: 3, title: "Part three", status: "queued" })
    ]);
    expect(view!.current).toBe(2);
    expect(view!.title).toBe("Part two");
    expect(view!.done).toBe(0);
    expect(view!.running).toBe(true);
  });

  test("已确认在平台上的条数单独计数（合集进度用它说话）", () => {
    const view = jobUploadProgress([
      upload({ id: "a", index: 1, status: "published", progress: 1 }),
      upload({ id: "b", index: 2, status: "reviewing", progress: 0.95 }),
      upload({ id: "c", index: 3, status: "uploading", progress: 0.6, started_at: "2026-10-21T03:00:00Z" })
    ]);
    expect(view!.done).toBe(2);
    expect(view!.total).toBe(3);
    expect(view!.current).toBe(3);
  });

  test("全已发布 → 不在等任何东西，进度块不渲染", () => {
    const view = jobUploadProgress([
      upload({ id: "a", index: 1, status: "published", progress: 1 }),
      upload({ id: "b", index: 2, status: "published", progress: 1 })
    ]);
    expect(view!.waiting).toBe(false);
    expect(view!.done).toBe(2);
  });

  test("待核对 / 失败也算「已经有结论」：进度块不说话，明细行说话", () => {
    for (const status of ["unverified", "failed", "rejected"] as BiliUploadStatus[]) {
      const view = jobUploadProgress([upload({ status, progress: 0.95 })]);
      expect([status, view!.waiting]).toEqual([status, false]);
    }
  });

  test("审核中是「等平台」，要显示进度块，但计时换成提交那一刻", () => {
    const view = jobUploadProgress([
      upload({
        status: "reviewing",
        progress: 0.95,
        started_at: "2026-10-21T03:00:00Z",
        finished_at: "2026-10-21T03:08:00Z"
      })
    ]);
    expect(view!.waiting).toBe(true);
    expect(view!.running).toBe(false);
    expect(view!.since).toBe("2026-10-21T03:08:00Z");
  });
});

describe("已用 / 已提交", () => {
  test("在跑时说「已用」，并按服务端给的起点算", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", progress: 0.2, started_at: "2026-10-21T03:00:00Z" })
    ])!;
    const now = Date.parse("2026-10-21T03:01:05Z");
    expect(uploadElapsedLabel(view, now)).toBe("已用 01:05");
  });

  test("等审核时说「已提交」", () => {
    const view = jobUploadProgress([
      upload({
        status: "reviewing",
        finished_at: "2026-10-21T03:08:00Z",
        started_at: "2026-10-21T03:00:00Z"
      })
    ])!;
    const now = Date.parse("2026-10-21T03:20:00Z");
    expect(uploadElapsedLabel(view, now)).toBe("已提交 12:00");
  });

  test("没有起点或起点是坏值 → 不显示时间，而不是显示 0:00", () => {
    const none = jobUploadProgress([upload({ status: "uploading", started_at: null })])!;
    expect(uploadElapsedLabel(none, Date.now())).toBeNull();
    const broken = jobUploadProgress([upload({ status: "uploading", started_at: "not-a-date" })])!;
    expect(uploadElapsedLabel(broken, Date.now())).toBeNull();
  });

  test("时钟偏到起点之前也不显示负数", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", started_at: "2026-10-21T03:09:00Z" })
    ])!;
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:00:00Z"))).toBe("已用 00:00");
  });
});
