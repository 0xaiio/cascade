/**
 * E-02 · 筛选/搜索在**真实任务中心**上的行为。
 *
 * 与 `jobFilter.test.ts`（纯判定）分工：这里证明控件接上了、列表真的跟着变、
 * 生命周期状态变化（SSE 推帧）之后筛选结果会重算、以及批量选择不被筛选悄悄改写。
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import { jobPayload, pausedJobPayload, playlistJobPayload } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

function cards(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(".job-card"));
}

function titles(): string[] {
  return cards().map((card) => card.querySelector("h3")?.textContent?.trim() ?? "");
}

function emptyState(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".job-filter-empty");
}

async function renderQueue() {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "任务中心" });
  await waitFor(() => expect(cards()).toHaveLength(3));
  return user;
}

async function pickStatus(user: ReturnType<typeof userEvent.setup>, value: string) {
  await user.selectOptions(screen.getByLabelText("任务状态筛选"), value);
}

/** 让 SSE 推一帧，触发 `App` 重拉 `/api/jobs`（状态变化的真实来源）。 */
function pushServerEvent() {
  act(() => {
    for (const source of mockState.eventSources) {
      source.onmessage?.(new MessageEvent("message", { data: "{}" }));
    }
  });
}

describe("App · 任务中心筛选与搜索（E-02）", () => {
  test("默认不筛选：全部任务可见，计数徽标保持原来的「N 个任务」", async () => {
    await renderQueue();

    expect(titles()).toEqual(["Running video", "Paused video", "Playlist batch"]);
    expect(screen.getByLabelText("任务状态筛选")).toHaveValue("all");
    expect(document.querySelector(".job-count-badge")).toHaveTextContent("3 个任务");
    expect(screen.queryByRole("button", { name: "清空筛选" })).not.toBeInTheDocument();
  });

  test("选「在跑」隐藏暂停的任务，徽标显示「显示 N / 共 M」", async () => {
    const user = await renderQueue();
    await pickStatus(user, "active");

    expect(titles()).toEqual(["Running video", "Playlist batch"]);
    expect(document.querySelector(".job-count-badge")).toHaveTextContent("显示 2 / 共 3 个任务");
  });

  test("选「已暂停」只留下暂停的任务", async () => {
    const user = await renderQueue();
    await pickStatus(user, "paused");

    expect(titles()).toEqual(["Paused video"]);
  });

  test("一个都不匹配时给空态与可照做的「清空筛选」，点完恢复全部", async () => {
    const user = await renderQueue();
    await pickStatus(user, "failed");

    expect(cards()).toHaveLength(0);
    expect(emptyState()).toHaveTextContent("没有符合条件的任务：共 3 个，已隐藏 3 个");
    await user.click(screen.getAllByRole("button", { name: "清空筛选" })[0]);

    expect(titles()).toHaveLength(3);
    expect(emptyState()).toBeNull();
  });

  test("标题搜索不区分大小写", async () => {
    const user = await renderQueue();
    await user.type(screen.getByLabelText("搜索任务标题"), "pLaYlIsT");

    expect(titles()).toEqual(["Playlist batch"]);
    expect(document.querySelector(".job-count-badge")).toHaveTextContent("显示 1 / 共 3 个任务");
  });

  test("搜索命中条目标题时自动展开合集，让命中的那一条看得见", async () => {
    mockState.jobs = [
      jobPayload,
      pausedJobPayload,
      { ...playlistJobPayload, status: "succeeded" } // 成功态默认折叠，才能验证「因搜索而展开」
    ];
    const user = await renderQueue();
    await user.type(screen.getByLabelText("搜索任务标题"), "part two");

    expect(titles()).toEqual(["Playlist batch"]);
    expect(screen.getByText(/Part two/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /折叠|展开/ })).toHaveAttribute("aria-expanded", "true");
  });

  test("状态与关键字叠加时两个条件同时生效", async () => {
    const user = await renderQueue();
    await pickStatus(user, "active");
    await user.type(screen.getByLabelText("搜索任务标题"), "paused");

    expect(cards()).toHaveLength(0);
    await user.clear(screen.getByLabelText("搜索任务标题"));
    expect(titles()).toEqual(["Running video", "Playlist batch"]);
  });

  test("SSE 推来状态变化后，当前筛选会按新状态重算", async () => {
    const user = await renderQueue();
    await pickStatus(user, "active");
    expect(titles()).toEqual(["Running video", "Playlist batch"]);

    mockState.jobs = [{ ...jobPayload, status: "succeeded" }, pausedJobPayload, playlistJobPayload];
    pushServerEvent();

    await waitFor(() => expect(titles()).toEqual(["Playlist batch"]));
    expect(document.querySelector(".job-count-badge")).toHaveTextContent("显示 1 / 共 3 个任务");
  });

  test("批量选择不受筛选影响：被隐藏的选中项仍然计数并给出提示", async () => {
    const user = await renderQueue();
    await user.click(screen.getByLabelText("选择任务 Running video"));
    expect(screen.getByLabelText("批量任务操作")).toHaveTextContent("1 个已选择");

    await pickStatus(user, "succeeded");
    expect(cards()).toHaveLength(0);
    expect(screen.getByLabelText("批量任务操作")).toHaveTextContent("1 个已选择");
    expect(screen.getByLabelText("批量任务操作")).toHaveTextContent("其中 1 个当前被筛选隐藏");
  });

  test("任务列表为空时筛选条仍在，徽标说「暂无任务」且没有假空态", async () => {
    mockState.jobs = [];
    render(<App />);
    await screen.findByRole("heading", { name: "任务中心" });

    expect(document.querySelector(".job-count-badge")).toHaveTextContent("暂无任务");
    expect(screen.getByLabelText("任务状态筛选")).toBeInTheDocument();
    expect(screen.queryByText(/没有符合条件的任务/)).not.toBeInTheDocument();
  });

  test("搜索框里的正则元字符按普通文字处理，界面不崩", async () => {
    await renderQueue();
    // userEvent 把 "[" 当成键盘描述符语法，这里直接改值，测的是判定而不是输入法。
    fireEvent.change(screen.getByLabelText("搜索任务标题"), { target: { value: "[" } });

    expect(cards()).toHaveLength(0);
    expect(emptyState()).toHaveTextContent("没有符合条件的任务");
  });
});
