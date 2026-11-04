/**
 * E-04 · 「克隆任务」在真实任务中心里的接线。
 *
 * 重点不是「按钮能点」，而是：只送改过的项（不把出厂默认当原设置）、
 * 回执留在面板内、加载态真的出现、以及新任务进入列表后原任务仍在。
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "../App";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

const CLONE_BUTTON = "克隆任务 Paused video";

async function openPanel() {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "任务中心" });
  await user.click(screen.getByRole("button", { name: CLONE_BUTTON }));
  return user;
}

function cards(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(".job-card"));
}

function cloneCalls() {
  return vi.mocked(fetch).mock.calls.filter(([url, init]) =>
    String(url).endsWith("/clone") && (init as RequestInit | undefined)?.method === "POST"
  );
}

describe("App · 克隆任务（E-04）", () => {
  test("每个任务行都有克隆入口，点开是任务行内的面板而不是弹窗", async () => {
    const user = await openPanel();

    expect(screen.getAllByRole("button", { name: /^克隆任务 / })).toHaveLength(3);
    expect(screen.getByRole("button", { name: CLONE_BUTTON })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("group", { name: CLONE_BUTTON })).toBeInTheDocument();
    expect(screen.getByText(/原任务、它的下载历史与 B站 投稿记录都不变/)).toBeInTheDocument();
    expect(screen.getAllByText(/跳过已下载/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: CLONE_BUTTON }));
    expect(screen.queryByRole("group", { name: CLONE_BUTTON })).not.toBeInTheDocument();
  });

  test("「沿用原设置」时请求体不带任何覆盖项", async () => {
    const user = await openPanel();
    await user.click(screen.getByRole("button", { name: "创建副本" }));

    await waitFor(() => expect(cloneCalls()).toHaveLength(1));
    const [url, init] = cloneCalls()[0];
    expect(url).toBe("/api/jobs/job-paused/clone");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).body).toBe("{}");
  });

  test("只把改过的两项送上去，其余由后端沿用原任务", async () => {
    const user = await openPanel();
    await user.selectOptions(screen.getByLabelText("副本清晰度"), "720p");
    await user.selectOptions(screen.getByLabelText("副本下载模式"), "video_only");
    await user.click(screen.getByRole("button", { name: "创建副本" }));

    await waitFor(() => expect(cloneCalls()).toHaveLength(1));
    expect(JSON.parse(String((cloneCalls()[0][1] as RequestInit).body))).toEqual({
      resolution: "720p",
      mode: "video_only"
    });
  });

  test("成功后新任务进入列表、原任务仍在、面板收起", async () => {
    const user = await openPanel();
    const before = cards().length;
    await user.click(screen.getByRole("button", { name: "创建副本" }));

    await waitFor(() => expect(cards()).toHaveLength(before + 1));
    expect(screen.getByRole("button", { name: CLONE_BUTTON })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: CLONE_BUTTON })).not.toBeInTheDocument();
  });

  test("服务端的 409 显示在面板内，不上升为页面级红条", async () => {
    mockState.cloneJobFailure = "磁盘空间不足：本次下载预计需要 912.00 MiB，当前可用 10.00 MiB。请清理该分区后重试。";
    const user = await openPanel();
    await user.click(screen.getByRole("button", { name: "创建副本" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("磁盘空间不足");
    expect(screen.getByRole("group", { name: CLONE_BUTTON })).toBeInTheDocument();
    expect(document.querySelector(".error-banner")).toBeNull();
    expect(cards()).toHaveLength(3);
  });

  test("取消不发请求", async () => {
    const user = await openPanel();
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(cloneCalls()).toHaveLength(0);
    expect(screen.queryByRole("group", { name: CLONE_BUTTON })).not.toBeInTheDocument();
  });

  test("提交期间有「创建中…」加载态，且按钮禁用", async () => {
    mockState.cloneJobDelayMs = 120;
    const user = await openPanel();
    await user.click(screen.getByRole("button", { name: "创建副本" }));

    const pendingButton = await screen.findByRole("button", { name: "创建中…" });
    expect(pendingButton).toBeDisabled();
    await waitFor(() => expect(screen.queryByRole("group", { name: CLONE_BUTTON })).not.toBeInTheDocument());
  });
});
