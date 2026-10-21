import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import App from "./App";
import { jobPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

/** 把一个时刻渲染成本地时区的 `YYYY-MM-DD HH:MM:SS`（不复用被测实现）。 */
function localStamp(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

/** 同样的字符串做成正则：那一格是「开始 2026-05-15 18:00:00」，要的是**子串**匹配。 */
function stampOf(iso: string): RegExp {
  return new RegExp(localStamp(iso).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
}

describe("App · 任务中心：进度与详情呈现", () => {
  test("shows live progress percentage elapsed time and eta in task center", async () => {
    render(<App />);

    expect((await screen.findAllByText("34.0%")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("已用 00:42").length).toBeGreaterThan(0);
    expect(screen.getAllByText("剩余 00:10").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2.0 KB/s").length).toBeGreaterThan(0);
  });

  test("shows task start time end time and actual resolution in task center", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    // 时间戳**按本地时区显示**（服务端给的是 UTC，见 ai/bug-fix/020）。这里在测试里
    // 独立算一份期望值，而不是抄一串「2026-05-15 10:00:00」—— 后者在 UTC 时区之外
    // 断言的正是「把 UTC 墙上时间当本地时间」那个缺陷。
    expect(screen.getAllByText(stampOf("2026-05-15T10:00:00Z")).length).toBeGreaterThan(0);
    expect(screen.getByText(stampOf("2026-05-15T10:05:00Z"))).toBeInTheDocument();
    expect(screen.getAllByText(/1920x1080/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1280x720/).length).toBeGreaterThan(0);
    expect(screen.getByText(/混合分辨率/)).toBeInTheDocument();
    expect(screen.getAllByText(/格式 mp4 · avc1 \+ mp4a/).length).toBeGreaterThan(0);
    expect(screen.getByText(/格式 混合格式/)).toBeInTheDocument();
  });

  test("does not repeat single video item details in task center", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.queryByText("1. Running video · running")).not.toBeInTheDocument();
    expect(screen.getByText("1. Part one · running")).toBeInTheDocument();
  });

  test("shows playlist item size progress timing and speed details", async () => {
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText("1. Part one · running")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("大小 已知 10.0 MB")).toBeInTheDocument();
    expect(screen.getByText("大小 10.0 MB")).toBeInTheDocument();
    expect(screen.getByText("已下载 5.0 MB")).toBeInTheDocument();
    expect(screen.getAllByText("已用 00:42").length).toBeGreaterThan(0);
    expect(screen.getByText("剩余 00:20")).toBeInTheDocument();
    expect(screen.getAllByText("2.0 KB/s").length).toBeGreaterThan(0);
    expect(screen.getByText("大小 未知")).toBeInTheDocument();
    expect(screen.getByText("已下载 未知")).toBeInTheDocument();
    expect(screen.getAllByText("分辨率 1920x1080").length).toBeGreaterThan(0);
    expect(screen.getAllByText("格式 mp4 · avc1 + mp4a").length).toBeGreaterThan(0);
  });

  test("shows duration next to the size on task rows and item rows", async () => {
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    // 单视频任务：任务行就是那条视频本体（3 分 05 秒 → 时:分:秒，时位不补零）。
    expect(screen.getAllByText("时长 0:03:05")).toHaveLength(2);
    // 合集：任务行按子视频求和；有一条还不知道时长，所以前缀「已知」——与「大小」同一套折法，
    // 否则同一行会出现「大小 已知 2.1 GB · 时长 10:00」这种自相矛盾的说法。
    expect(screen.getByText("时长 已知 0:10:00")).toBeInTheDocument();
    expect(screen.getByText("时长 0:10:00")).toBeInTheDocument();
    expect(screen.getByText("时长 未知")).toBeInTheDocument();
  });

  test("keeps average speed visible after a job completes", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        status: "succeeded",
        progress: 100,
        speed: 1024,
        eta: null,
        completed_items: 1,
        finished_at: "2026-05-15T10:01:00Z",
        items: []
      }
    ];
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getByText("1.0 KB/s")).toBeInTheDocument();
  });

  test("shows concrete single video failure reason in task center", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        status: "failed",
        progress: 40,
        error: "YouTube 媒体流连接中断，请重新导入 cookies 后重试。",
        failed_items: 1,
        speed: 1536,
        eta: null,
        items: []
      }
    ];
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getByText(/YouTube 媒体流连接中断/)).toBeInTheDocument();
    expect(screen.getByText("1.5 KB/s")).toBeInTheDocument();
  });
});
