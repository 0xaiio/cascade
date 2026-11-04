/**
 * E-05 · 长暂停提示在真实任务中心里的行为。
 *
 * 与 `pausedSince.test.ts`（纯判定）分工：这里证明提示挂在**任务行上**、
 * 随 SSE 的状态变化消失/重现，以及它**没有**变成一条通知（014 的边界）。
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import App from "../App";
import { pausedJobPayload } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

const HOUR = 60 * 60 * 1000;
const isoAgo = (ms: number) => new Date(Date.now() - ms).toISOString();

function paused(overrides: Partial<typeof pausedJobPayload> = {}) {
  return {
    ...pausedJobPayload,
    // 留半小时余量：`nowMs` 冻结在组件挂载时刻，正好 3 小时会被向下取整成 2 小时。
    paused_at: isoAgo(3 * HOUR + 30 * 60 * 1000),
    paused_since_approximate: false,
    ...overrides
  };
}

function pushServerEvent() {
  act(() => {
    for (const source of mockState.eventSources) {
      source.onmessage?.(new MessageEvent("message", { data: "{}" }));
    }
  });
}

function notice(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".paused-since");
}

describe("App · 长时间暂停提示（E-05）", () => {
  test("暂停超过阈值时任务行显示时长与可照做的下一步，但不发通知", async () => {
    mockState.jobs = [paused()];
    render(<App />);

    expect(await screen.findByText(/已暂停 3 小时/)).toBeInTheDocument();
    expect(notice()).toHaveTextContent("点「重启」继续（不会从零重下）");
    expect(notice()).toHaveAttribute("data-approximate", "false");
    // 014 的有意选择：paused 不是终态，长暂停只补一行提示，不进浮层队列。
    expect(document.querySelector(".toast")).toBeNull();
  });

  test("刚暂停不久不打扰", async () => {
    mockState.jobs = [paused({ paused_at: isoAgo(30 * 60 * 1000) })];
    render(<App />);
    await screen.findByText("Paused video");

    expect(notice()).toBeNull();
  });

  test("历史任务的起点是近似值，文案带「约」", async () => {
    // 读模型对补列前的任务用 updated_at 兜底并标 approximate，时间照常给过来。
    mockState.jobs = [paused({ paused_since_approximate: true })];
    render(<App />);

    expect(await screen.findByText(/已暂停约 3 小时/)).toBeInTheDocument();
    expect(notice()).toHaveAttribute("data-approximate", "true");
  });

  test("起点缺失时不编数字", async () => {
    mockState.jobs = [paused({ paused_at: null })];
    render(<App />);
    await screen.findByText("Paused video");

    expect(notice()).toBeNull();
  });

  test("非暂停态即使残留 paused_at 也不显示", async () => {
    mockState.jobs = [paused({ status: "succeeded" })];
    render(<App />);
    await screen.findByText("Paused video");

    expect(notice()).toBeNull();
  });

  test("SSE 推来状态变化后提示消失，重新暂停后再次出现", async () => {
    mockState.jobs = [paused()];
    render(<App />);
    expect(await screen.findByText(/已暂停 3 小时/)).toBeInTheDocument();

    mockState.jobs = [paused({ status: "queued", paused_at: null })];
    pushServerEvent();
    await waitFor(() => expect(notice()).toBeNull());

    mockState.jobs = [paused()];
    pushServerEvent();
    await waitFor(() => expect(notice()).toHaveTextContent("已暂停 3 小时"));
    expect(document.querySelector(".toast")).toBeNull();
  });
});
