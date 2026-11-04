/**
 * E-03 · 提示真的挂到了四个重启入口上，且点击行为没被文案改动带偏。
 *
 * `aria-label` 刻意保持「重启 + 标题」：读屏与既有集成用例都按它找按钮，
 * 详细语义放在 `title`（悬停提示），两者不互相顶替。
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import {
  playlistFallbackJobPayload,
  playlistJobPayload,
  singleFallbackJobPayload
} from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";
import { batchRestartHint, itemRestartHint, jobRestartHint, resolutionRestartHint } from "./restartSemantics";

installAppHarness();

describe("App · 重启按钮的语义提示（E-03）", () => {
  test("任务行按钮给出完整语义，点击仍走重启接口", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "任务中心" });

    const button = screen.getByRole("button", { name: "重启 Paused video" });
    expect(button).toHaveAttribute("title", jobRestartHint());
    expect(button.getAttribute("title")).toContain("不会从零重下");

    await user.click(button);
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/jobs/job-paused/restart",
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  test("合集条目按钮说的是「只影响这一条」", async () => {
    mockState.jobs = [playlistJobPayload];
    render(<App />);
    await screen.findByText("Playlist batch");

    const button = screen.getByRole("button", { name: "重启 Part two" });
    expect(button).toHaveAttribute("title", itemRestartHint());
    expect(button.getAttribute("title")).toContain("其它条目不受影响");
  });

  test("批量重启按钮也带同一套语义", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "任务中心" });
    await user.click(screen.getByLabelText("选择任务 Running video"));

    const button = screen.getByRole("button", { name: /批量重启/ });
    expect(button).toHaveAttribute("title", batchRestartHint());
  });

  test("任务级「以 720p 重启」不承诺沿用原选项", async () => {
    mockState.jobs = [singleFallbackJobPayload];
    render(<App />);
    const button = await screen.findByRole("button", { name: /以 720p 重启任务/ });

    expect(button).toHaveAttribute("title", resolutionRestartHint("720p"));
    expect(button.getAttribute("title")).not.toContain("沿用原来的清晰度");
  });

  test("条目级「以 720p 重启」用的是同一份文案", async () => {
    mockState.jobs = [playlistFallbackJobPayload];
    render(<App />);
    const button = await screen.findByRole("button", { name: /以 720p 重启 Part two/ });

    expect(button).toHaveAttribute("title", resolutionRestartHint("720p"));
  });

  test("读屏用的 aria-label 仍是「重启 + 标题」，没有被长提示顶替", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "任务中心" });

    const button = screen.getByRole("button", { name: "重启 Paused video" });
    expect(button).toHaveAttribute("aria-label", "重启 Paused video");
    expect(button.getAttribute("title")).not.toBe("重启 Paused video");
  });
});
