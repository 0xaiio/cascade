import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import App from "./App";
import { jobPayload, settingsPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";
import { ChimePlayer, useCompletionToasts } from "./notifications";
import type { Job } from "./types";

/**
 * 「任务完成提醒」的行为测试。
 *
 * 这个文件存在的理由不是覆盖率，而是**钉住一个曾经不存在的东西**：`notify_on_complete`
 * 在前后端都只有开关、没有任何实现（`ai/plan/brainstorming.md` H-01）。所以这里的每一条
 * 断言都必须给出「那条分支被执行过」的**正面证据** —— 浮层的文本、合成器起振的次数 ——
 * 而不是「没有报错」。
 *
 * 提示音用替身 `AudioContext` 计数而不是真出声：`jsdom` 里没有 Web Audio，
 * 而 `ChimePlayer` 的工厂是**可注入**的，正好让「播了几次」变成可断言的数。
 */

installAppHarness();

let startedOscillators = 0;

/** 替身音频上下文。只实现 `ChimePlayer` 真正用到的那几个方法。 */
function fakeAudioContext(state: "running" | "suspended" = "running") {
  const oscillators: unknown[] = [];
  return {
    state,
    currentTime: 0,
    destination: {},
    resume: vi.fn(async () => undefined),
    createGain: () => ({
      gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
      connect: vi.fn()
    }),
    createOscillator: () => {
      const node = {
        type: "",
        frequency: { setValueAtTime: vi.fn() },
        connect: vi.fn(),
        start: vi.fn(() => {
          startedOscillators += 1;
        }),
        stop: vi.fn()
      };
      oscillators.push(node);
      return node;
    },
    oscillators
  };
}

function asContext(value: ReturnType<typeof fakeAudioContext>): AudioContext {
  return value as unknown as AudioContext;
}

beforeEach(() => {
  startedOscillators = 0;
  vi.stubGlobal("AudioContext", class {
    state = "running";
    currentTime = 0;
    destination = {};
    resume = vi.fn(async () => undefined);
    createGain = () => ({ gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() }, connect: vi.fn() });
    createOscillator = () => ({
      type: "",
      frequency: { setValueAtTime: vi.fn() },
      connect: vi.fn(),
      start: vi.fn(() => {
        startedOscillators += 1;
      }),
      stop: vi.fn()
    });
  });
});

/** 「一条浮层都没有」。比逐条 `queryByText` 更强：它不看文案，看的是那个容器。 */
function expectNoToast() {
  expect(document.querySelectorAll(".toast")).toHaveLength(0);
}

/** 让 SSE 推一帧，触发 `App` 重拉 `/api/jobs`。 */
function pushServerEvent() {
  act(() => {
    for (const source of mockState.eventSources) {
      source.onmessage?.(new MessageEvent("message", { data: "{}" }));
    }
  });
}

function runningJob(overrides: Partial<Job> = {}): Job {
  return { ...jobPayload, id: "job-notify", notify_on_complete: true, status: "running", ...overrides };
}

async function renderWith(job: Job, mode: typeof settingsPayload.completion_notification) {
  mockState.jobs = [job];
  mockState.settings = { ...settingsPayload, completion_notification: mode };
  render(<App />);
  // 等首帧任务列表渲染完 —— 没有这一步，`previousJobStatuses` 还没记下 running，
  // 后面的终态就成了「一进来就已经跑完」，不该提醒的分支反而会命中。
  await screen.findByText("Running video");
}

/* ------------------------------------------------------------------ 触发条件 */

describe("任务完成提醒 · 触发条件", () => {
  test("任务跑到 succeeded 时提醒：浮层 + 提示音", async () => {
    await renderWith(runningJob(), "both");
    mockState.jobs = [runningJob({ status: "succeeded", completed_items: 1, progress: 100 })];
    pushServerEvent();

    expect(await screen.findByText("下载完成")).toBeInTheDocument();
    expect(screen.getByText("《Running video》已下载 1/1 项。")).toBeInTheDocument();
    expect(startedOscillators).toBe(2); // 一声「叮」的两个音
  });

  test("失败也提醒，并且写明下一步", async () => {
    await renderWith(runningJob(), "both");
    mockState.jobs = [runningJob({ status: "failed", failed_items: 1, error: "1 item(s) failed." })];
    pushServerEvent();

    expect(await screen.findByText("下载失败")).toBeInTheDocument();
    expect(screen.getByText(/可在任务中心查看原因/)).toBeInTheDocument();
  });

  test("逐任务开关关掉时，这个任务不提醒", async () => {
    await renderWith(runningJob({ notify_on_complete: false }), "both");
    mockState.jobs = [runningJob({ notify_on_complete: false, status: "succeeded", completed_items: 1 })];
    pushServerEvent();

    await waitFor(() => expect(startedOscillators).toBe(0));
    expectNoToast();
  });

  test("打开页面时就已经跑完的历史任务不提醒", async () => {
    // 首帧直接是 succeeded：用户没有「看着它跑完」这回事，播报等于把历史翻出来念一遍。
    await renderWith(runningJob({ status: "succeeded", completed_items: 1 }), "both");
    await waitFor(() => expect(startedOscillators).toBe(0));
    expectNoToast();
  });

  test("cancelled 不提醒 —— 取消是用户自己点的", async () => {
    await renderWith(runningJob(), "both");
    mockState.jobs = [runningJob({ status: "cancelled" })];
    pushServerEvent();

    await waitFor(() => expect(startedOscillators).toBe(0));
    expectNoToast();
  });
});

/* ------------------------------------------------------------------ 提醒方式 */

describe("任务完成提醒 · 方式", () => {
  test("off：一个都不发", async () => {
    await renderWith(runningJob(), "off");
    mockState.jobs = [runningJob({ status: "succeeded", completed_items: 1 })];
    pushServerEvent();

    await waitFor(() => expect(startedOscillators).toBe(0));
    expectNoToast();
  });

  test("sound：只出声，不弹浮层", async () => {
    await renderWith(runningJob(), "sound");
    mockState.jobs = [runningJob({ status: "succeeded", completed_items: 1 })];
    pushServerEvent();

    await waitFor(() => expect(startedOscillators).toBe(2));
    expectNoToast();
  });

  test("toast：只弹浮层，不出声", async () => {
    await renderWith(runningJob(), "toast");
    mockState.jobs = [runningJob({ status: "succeeded", completed_items: 1 })];
    pushServerEvent();

    expect(await screen.findByText("下载完成")).toBeInTheDocument();
    expect(startedOscillators).toBe(0);
  });
});

/* ---------------------------------------------------------------------- 提示音 */

describe("提示音", () => {
  test("上下文没被解锁时如实报 blocked，不抛异常", () => {
    const context = fakeAudioContext("suspended");
    const player = new ChimePlayer(() => asContext(context));
    expect(player.play()).toBe("blocked");
    expect(context.resume).toHaveBeenCalled();
  });

  test("环境没有 Web Audio 时报 unsupported", () => {
    expect(new ChimePlayer(() => null).play()).toBe("unsupported");
  });

  test("正常时排进两个音（C6 → G6）", () => {
    const context = fakeAudioContext("running");
    const player = new ChimePlayer(() => asContext(context));
    expect(player.play()).toBe("played");
    expect(context.oscillators).toHaveLength(2);
  });
});

/* ------------------------------------------------------------------ 浮层队列 */

describe("浮层队列", () => {
  test("到点自己缩回", async () => {
    const { result } = renderHook(() => useCompletionToasts(50));
    act(() => {
      result.current.push({ tone: "ok", title: "下载完成", body: "正文" });
    });
    expect(result.current.toasts).toHaveLength(1);
    await waitFor(() => expect(result.current.toasts).toHaveLength(0));
  });

  test("标签页不可见时不倒计时，等可见了再接着走", async () => {
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const { result } = renderHook(() => useCompletionToasts(50));
    act(() => {
      result.current.push({ tone: "ok", title: "下载完成", body: "正文" });
    });
    // 后台没有倒计时：浮层必须还在，否则用户切回来时什么都没了。
    await new Promise((resolve) => window.setTimeout(resolve, 200));
    expect(result.current.toasts).toHaveLength(1);

    hidden.mockReturnValue(false);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(result.current.toasts).toHaveLength(0));
    hidden.mockRestore();
  });

  test("同时最多挂三条", async () => {
    const { result } = renderHook(() => useCompletionToasts(60_000));
    act(() => {
      for (let index = 0; index < 5; index += 1) {
        result.current.push({ tone: "ok", title: `第 ${index} 条`, body: "正文" });
      }
    });
    await waitFor(() => expect(result.current.toasts).toHaveLength(3));
    expect(result.current.toasts.map((toast) => toast.title)).toEqual(["第 2 条", "第 3 条", "第 4 条"]);
  });
});

/* ---------------------------------------------------------------------- 界面 */

describe("提醒的界面", () => {
  test("「完成后通知」出厂是勾上的", async () => {
    await renderWith(runningJob(), "both");
    expect(await screen.findByRole("checkbox", { name: "完成后通知" })).toBeChecked();
  });

  test("全局设成不提醒时，那个开关勾不动，并且写明原因", async () => {
    await renderWith(runningJob(), "off");
    const toggle = await screen.findByRole("checkbox", { name: "完成后通知" });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/提醒方式在设置里被设成了「不提醒」/)).toBeInTheDocument();
  });

  test("改提醒方式会保存到服务端", async () => {
    await renderWith(runningJob(), "both");
    await userEvent.selectOptions(await screen.findByLabelText("任务完成提醒方式"), "sound");
    await waitFor(() => expect(mockState.settings.completion_notification).toBe("sound"));
  });

  test("预览按当前方式演示一次", async () => {
    await renderWith(runningJob(), "both");
    await userEvent.click(await screen.findByRole("button", { name: /预览/ }));
    expect(await screen.findByText("这是一条样例通知")).toBeInTheDocument();
    expect(startedOscillators).toBe(2);
  });

  test("浮层可以手动关掉", async () => {
    await renderWith(runningJob(), "toast");
    mockState.jobs = [runningJob({ status: "succeeded", completed_items: 1 })];
    pushServerEvent();
    await userEvent.click(await screen.findByRole("button", { name: /关闭提醒/ }));
    await waitFor(() => expect(screen.queryByText("下载完成")).not.toBeInTheDocument());
  });
});
