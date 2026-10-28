import { describe, expect, test } from "vitest";
import { UPLOAD_STAGES, jobUploadProgress, uploadElapsedLabel, uploadStageView } from "./biliProgress";
import { biliUploadPayload } from "../test/appFixtures";
import type { BiliUpload, BiliUploadStatus } from "../contracts/types";

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
    expect(view!.startedAt).toBe("2026-10-21T03:00:00Z");
    // 还在跑就没有终点：`finished_at` 是「上一次跑完」的时刻，拿它算时长会得出负数或 0。
    expect(view!.finishedAt).toBeNull();
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

  test("审核中是「等平台」：进度块留着，但两个端点都要给（时长是定值，不是从此刻起算）", () => {
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
    expect(view!.startedAt).toBe("2026-10-21T03:00:00Z");
    expect(view!.finishedAt).toBe("2026-10-21T03:08:00Z");
    // 审核期**不推进**秒表：`running` 是那个唯一判据。
    expect(view!.running).toBe(false);
  });
});

describe("已用 / 上传用时", () => {
  test("在跑时说「已用」，并按服务端给的起点算", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", progress: 0.2, started_at: "2026-10-21T03:00:00Z" })
    ])!;
    const now = Date.parse("2026-10-21T03:01:05Z");
    expect(uploadElapsedLabel(view, now)).toBe("已用 01:05");
  });

  test("等审核时给的是**定值**：上传用时 = 终点 − 起点，与此刻几点无关", () => {
    const view = jobUploadProgress([
      upload({
        status: "reviewing",
        finished_at: "2026-10-21T03:08:00Z",
        started_at: "2026-10-21T03:00:00Z"
      })
    ])!;
    // 同一份数据，两个「现在」—— 值必须一模一样。旧实现量的是「等审核等了多久」，
    // 于是这个数字会一路涨到几小时（实测 Ep 125 的 6:41:18），见 ai/ui/008。
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:20:00Z"))).toBe("上传用时 08:00");
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T09:20:00Z"))).toBe("上传用时 08:00");
  });

  test("没有起点或起点是坏值 → 不显示时间，而不是显示 0:00", () => {
    const none = jobUploadProgress([upload({ status: "uploading", started_at: null })])!;
    expect(uploadElapsedLabel(none, Date.now())).toBeNull();
    const broken = jobUploadProgress([upload({ status: "uploading", started_at: "not-a-date" })])!;
    expect(uploadElapsedLabel(broken, Date.now())).toBeNull();
  });

  test("跑完却没有终点 → 不显示，也不拿此刻顶上", () => {
    const view = jobUploadProgress([
      upload({ status: "reviewing", started_at: "2026-10-21T03:00:00Z", finished_at: null })
    ])!;
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:20:00Z"))).toBeNull();
  });

  test("时钟偏到起点之前也不显示负数", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", started_at: "2026-10-21T03:09:00Z" })
    ])!;
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:00:00Z"))).toBe("已用 00:00");
  });

  /**
   * 上面几条**全部**用带 `Z` 的时间戳，而服务端从来不发带 `Z` 的 ——
   * 这就是 Ep 125 那个「52 秒显示成 8:00:52」能一直藏着的原因（ai/bug-fix/020）。
   * 下面这组用**服务端真正发出来的那种**（naive 的 UTC 墙上时间）。
   */
  test("起点是「不带偏移量的 UTC」时，按 UTC 解释（否则本机 UTC+8 会多出 8 小时）", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", progress: 0.2, started_at: "2026-10-21T03:00:00" })
    ])!;
    // `now` 也用同一种写法 —— 这里的对照是「两端按同一套规则解析」，与本地时区无关。
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:01:05Z"))).toBe("已用 01:05");
  });

  test("等审核时也一样：naive 时间戳按 UTC 算，不是本地", () => {
    const view = jobUploadProgress([
      upload({
        status: "reviewing",
        finished_at: "2026-10-21T03:08:00",
        started_at: "2026-10-21T03:00:00"
      })
    ])!;
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:20:00Z"))).toBe("上传用时 08:00");
  });

  test("真实样本：Ep 125 那条（52 秒）显示成上传用时，而不是 6 个多小时的「已提交」", () => {
    // 取自本机库里那条真实记录：started/finished 相隔 52.55 秒（服务端 elapsed_seconds=52，
    // 界面按四舍五入给 00:53）。判据是**量级**与**措辞**：这是「上传用掉了多久」，
    // 不是「从提交到现在多久」。
    const view = jobUploadProgress([
      upload({
        status: "reviewing",
        progress: 0.95,
        started_at: "2026-10-21T10:29:38.727960",
        finished_at: "2026-10-21T10:30:31.280768"
      })
    ])!;
    const label = uploadElapsedLabel(view, Date.parse("2026-10-22T01:04:00Z"))!;
    expect(label).toMatch(/^上传用时 00:5[23]$/);
    // 三段式的「小时:分:秒」正是旧版那个 6 小时数字的形状 —— 它再出现就说明又去替审核计时了。
    expect(label).not.toMatch(/^\D*\d+:\d\d:\d\d$/);
  });

  test("带偏移量的时间戳照旧按偏移量算（服务端的 `Z` 与别的时区都要支持）", () => {
    const view = jobUploadProgress([
      upload({ status: "uploading", progress: 0.2, started_at: "2026-10-21T11:00:00+08:00" })
    ])!;
    // `11:00+08:00` 就是 `03:00Z`。
    expect(uploadElapsedLabel(view, Date.parse("2026-10-21T03:01:05Z"))).toBe("已用 01:05");
  });
});
