import { describe, expect, test, vi } from "vitest";
import { CHECK_TIMEOUT_MS, runHealthCheck } from "./biliHealth";
import { biliStatusPayload } from "./test/appFixtures";
import type { BiliStatus } from "./types";

/**
 * 「重新体检」的执行策略。
 *
 * 这里测的是**判据**：探多久、超时了退到哪、网络错误要不要照旧抛。界面只负责显示文案，
 * 所以可以用二十毫秒的时限把三条分支各测一遍 —— 不必在界面测试里摆弄假定时器
 * （那是上一版写法，它把别的用例一起拖进了超时）。
 */

// 只用来区分「哪一份结论回来了」，所以一个不同身份的对象就够。
const OFFLINE: BiliStatus = { ...biliStatusPayload };

describe("体检策略", () => {
  test("账号探测按时回来就用它的结论，不额外做离线体检", async () => {
    const loadOffline = vi.fn(async () => OFFLINE);
    const result = await runHealthCheck(async () => biliStatusPayload, loadOffline, 50);

    expect(result.timedOut).toBe(false);
    expect(result.status).toBe(biliStatusPayload);
    expect(loadOffline).not.toHaveBeenCalled();
  });

  test("探测迟迟不回 → 退回离线结论，并标记 timedOut", async () => {
    const loadProbe = () => new Promise<BiliStatus>(() => {});
    const loadOffline = vi.fn(async () => OFFLINE);
    const result = await runHealthCheck(loadProbe, loadOffline, 20);

    expect(result.timedOut).toBe(true);
    expect(result.status).toBe(OFFLINE);
    expect(loadOffline).toHaveBeenCalledTimes(1);
  });

  test("网络错误照旧抛出去 —— 超时能降级，真读不到不能装作读到了", async () => {
    const boom = new Error("net down");
    const loadOffline = vi.fn(async () => OFFLINE);
    await expect(runHealthCheck(async () => Promise.reject(boom), loadOffline, 50)).rejects.toBe(boom);
    expect(loadOffline).not.toHaveBeenCalled();
  });

  test("超时那一半也不成功后，错误同样抛出去（不能吞成一句「体检完成」）", async () => {
    const boom = new Error("offline also failed");
    await expect(
      runHealthCheck(() => new Promise<BiliStatus>(() => {}), async () => Promise.reject(boom), 10)
    ).rejects.toBe(boom);
  });

  test("兜底时限是 12 秒（与「按需读合集列表」同一个量级）", () => {
    expect(CHECK_TIMEOUT_MS).toBe(12_000);
  });
});
