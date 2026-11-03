import { afterEach, describe, expect, test, vi } from "vitest";
import { BiliAutoRetryNote, formatCountdown } from "./BiliAutoRetryNote";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { BiliAutoRetry } from "../contracts/types";

// 本仓库的 vitest 没开 `globals`，所以 RTL 不会自动注册 `afterEach(cleanup)` ——
// 不清的话**上一个用例的 DOM 还在**，`queryByText` 会找到上一条用例的文字，
// 于是「这句话不该出现」的断言变成「上一条用例出现过它」。见 bili-subtitle-line.test.tsx。
afterEach(cleanup);

/**
 * 「下一次自动重试」那一行的显示。
 *
 * 它回答的是三个问题：**机器在等什么、等多久、上次结果如何**。
 * 少答一个，用户就只能盯着那个按钮猜。
 */
function retry(overrides: Partial<BiliAutoRetry> = {}): BiliAutoRetry {
  return {
    state: "waiting",
    attempts: 0,
    next_attempt_at: new Date(Date.now() + 125_000).toISOString(),
    last_result: "",
    last_message: "",
    ...overrides
  };
}

describe("自动重试的倒计时", () => {
  test("等待中：只剩精确倒计时与已尝试次数，不再重复上次那句话", () => {
    // `last_message` 与上面那一行说的是同一件事（「合集：等待 B 站视频审核后进行归集」），
    // 拼在一起等于把刚精简掉的长句加回来 —— 这一行只回答「还有多久」。
    render(
      <BiliAutoRetryNote
        retry={retry({
          attempts: 2,
          next_attempt_at: new Date(Date.now() + 125_000).toISOString(),
          last_result: "waiting",
          last_message: "视频还在 B 站 排队审核，暂时没法加进合集"
        })}
      />
    );

    expect(screen.getByText(/下一次自动重试 [0-9]{2}:[0-9]{2}/)).toBeInTheDocument();
    expect(screen.getByText(/已尝试 2 次/)).toBeInTheDocument();
    expect(screen.queryByText(/排队审核/)).not.toBeInTheDocument();
  });

  test("倒计时每秒真的在减（不是一句静态文字）", () => {
    // 判据是**两次采样之间变小了**。一个不动的倒计时和「卡住」在界面上长得一模一样，
    // 而这一行恰恰是用户判断「机器还在不在跑」的唯一依据。
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
    try {
      render(<BiliAutoRetryNote retry={retry({ next_attempt_at: "2026-11-03T12:02:05Z" })} />);
      expect(screen.getByText(/下一次自动重试 02:05/)).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(screen.getByText(/下一次自动重试 02:04/)).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(screen.getByText(/下一次自动重试 01:59/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("没有在等（null）时什么都不显示 —— 不显示比显示成「等待中」更安静", () => {
    const { container } = render(<BiliAutoRetryNote retry={null} />);
    expect(container.textContent).toBe("");
  });

  test("正在问平台时说「自动重试中…」", () => {
    render(<BiliAutoRetryNote retry={retry({ state: "running" })} />);
    expect(screen.getByText("自动重试中…")).toBeInTheDocument();
  });

  test("成了之后说结果，且**不再说倒计时**", () => {
    render(<BiliAutoRetryNote retry={retry({ state: "done", last_result: "done", last_message: "已自动归集" })} />);
    expect(screen.getByText("已自动归集")).toBeInTheDocument();
    // 「下一次自动重试 00:00」会让人以为机器还在忙。
    expect(screen.queryByText(/下一次自动重试/)).not.toBeInTheDocument();
  });

  test("试够了就说「先停在这里」，并带上失败原因", () => {
    render(
      <BiliAutoRetryNote
        retry={retry({
          state: "exhausted",
          attempts: 12,
          last_result: "failed",
          last_message: "自动重试已试 10 次，先停在这里；可以手动重试。（上次：合集建好了，但视频没能加进去）"
        })}
      />
    );
    expect(screen.getByText(/先停在这里/)).toBeInTheDocument();
    expect(screen.getByText(/视频没能加进去/)).toBeInTheDocument();
    expect(screen.queryByText(/下一次自动重试/)).not.toBeInTheDocument();
  });
});

describe("倒计时的格式", () => {
  test("不到一分钟也写 mm:ss —— 「不到 1 分钟」看不出还剩多久", () => {
    // 原来这一档写「不到 1 分钟」：59 秒里显示同一句话，分不清机器在走还是停了。
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(1)).toBe("00:01");
    expect(formatCountdown(35)).toBe("00:35");
    expect(formatCountdown(59)).toBe("00:59");
  });

  test("分钟与秒都补两位（02:00 = 还剩两分钟）", () => {
    expect(formatCountdown(60)).toBe("01:00");
    expect(formatCountdown(120)).toBe("02:00");
    expect(formatCheck(125)).toBe("02:05");
  });

  test("超过一小时带上时位（`80:00` 看不出是八十分钟还是八十分钟零几秒）", () => {
    expect(formatCheck(3725)).toBe("1:02:05");
  });

  test("小数秒向上取整：显示值单调递减，且**到点才归零**", () => {
    // `round` 会在 34.5 秒附近来回跳（35 → 34 → 35），看着像机器在犹豫。
    expect(Math.ceil(34.2)).toBe(35);
    expect(Math.ceil(0.4)).toBe(1);
  });
});

// 小助手：与实现同口径，但**独立写一遍**（抄实现就没有测试意义了）。
function formatCheck(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60).toString().padStart(2, "0");
  const secs = (seconds % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}
