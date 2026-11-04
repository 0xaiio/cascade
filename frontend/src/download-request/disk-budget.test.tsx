/**
 * E-01 · 解析面板里的磁盘预算提示与建任务被拒的回执。
 *
 * 前端只做两件事：把服务端那句 `message` 原样显示、按 `status` 配色；
 * **不在浏览器里重算任何数字**。最后一条用例证明 409 是服务端拒绝的，
 * 不是前端自己把按钮锁住了 —— 只断言「没成功」会把两者混为一谈。
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import type { DiskBudget } from "../contracts/types";
import { analyzePayload } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

const OK: DiskBudget = {
  status: "ok",
  estimated_bytes: 400 * 1024 * 1024,
  free_bytes: 8 * 1024 ** 3,
  required_bytes: 912 * 1024 * 1024,
  message: "本次下载预计需要 912.00 MiB（含余量），当前可用 8.00 GiB。"
};

const INSUFFICIENT: DiskBudget = {
  status: "insufficient",
  estimated_bytes: 400 * 1024 * 1024,
  free_bytes: 10 * 1024 * 1024,
  required_bytes: 912 * 1024 * 1024,
  message: "磁盘空间不足：本次下载预计需要 912.00 MiB，当前可用 10.00 MiB。请清理该分区后重试。"
};

const UNKNOWN: DiskBudget = {
  status: "unknown",
  estimated_bytes: null,
  free_bytes: 8 * 1024 ** 3,
  required_bytes: null,
  message: "暂时无法预估本次下载需要的空间（平台没给文件大小）。请先确认下载目录所在分区的余量。"
};

async function analyze() {
  const user = userEvent.setup();
  render(<App />);
  await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
  await user.click(screen.getByRole("button", { name: "解析链接" }));
  expect(await screen.findByText("Batch")).toBeInTheDocument();
  return user;
}

describe("App · 磁盘预算提示（E-01）", () => {
  test("shows the server-written estimate without any warning styling when it fits", async () => {
    mockState.analyze = { ...analyzePayload, disk_budget: OK };
    await analyze();

    const hint = screen.getByText(OK.message);
    expect(hint).toHaveClass("disk-budget-hint", "is-ok");
    expect(hint).not.toHaveClass("is-insufficient");
    expect(hint).toHaveAttribute("data-status", "ok");
  });

  test("marks an insufficient budget as a warning and keeps every number", async () => {
    mockState.analyze = { ...analyzePayload, disk_budget: INSUFFICIENT };
    await analyze();

    const hint = screen.getByText(INSUFFICIENT.message);
    expect(hint).toHaveClass("is-insufficient");
    expect(hint).toHaveTextContent("912.00 MiB");
    expect(hint).toHaveTextContent("10.00 MiB");
  });

  test("an unknown estimate is a hint, not an error", async () => {
    mockState.analyze = { ...analyzePayload, disk_budget: UNKNOWN };
    await analyze();

    const hint = screen.getByText(UNKNOWN.message);
    expect(hint).toHaveClass("is-unknown");
    expect(hint).not.toHaveClass("is-insufficient");
  });

  test("an older backend without the field renders nothing rather than a fake number", async () => {
    mockState.analyze = { ...analyzePayload, disk_budget: null };
    await analyze();

    expect(document.querySelector(".disk-budget-hint")).toBeNull();
  });

  test("a 409 from POST /api/jobs shows the server's numbers as a page-level error", async () => {
    mockState.analyze = { ...analyzePayload, disk_budget: OK };
    mockState.jobCreateFailure = INSUFFICIENT.message;
    const user = await analyze();

    await user.click(screen.getByRole("button", { name: "加入下载队列" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("磁盘空间不足");
    });
    expect(screen.getByRole("alert")).toHaveTextContent("10.00 MiB");
    expect(fetch).toHaveBeenCalledWith("/api/jobs", expect.objectContaining({ method: "POST" }));
  });
});
