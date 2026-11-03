import { useEffect, useState } from "react";
import { parseServerTime } from "../shared/formatting";
import type { BiliAutoRetry } from "../contracts/types";

/**
 * 「下一次自动重试」的**倒计时与状态**。
 *
 * 为什么单独一个组件：这一行有自己的**时间轴**（每过一秒就该变），而它挂在
 * 任务卡片里 —— 卡片的重渲染频率是 SSE 驱动的（几秒一次），拿它做秒表会跳。
 * 自己起一个计时器，倒计时才走得稳（与 `BiliUploadProgress` 的秒表同一套做法）。
 *
 * 三种状态各自说不同的话：
 * - `waiting`：机器在等 ⇒ 说「下一次自动重试 01:23」+ 上次结果；
 * - `running`：正在问平台 ⇒ 说「自动重试中…」；
 * - `done` / `exhausted`：不再自动重试 ⇒ 说结果，**不再说倒计时**
 *   （`done` 之后还说「下一次 00:00」会让人以为机器还在忙）。
 */
export function BiliAutoRetryNote({ retry }: { retry: BiliAutoRetry | null }) {
  const [now, setNow] = useState(() => Date.now());

  // 只在「真的要显示倒计时」时才起计时器：`done` / `exhausted` 不需要每秒重算。
  const counting = retry?.state === "waiting" && Boolean(retry.next_attempt_at);
  useEffect(() => {
    if (!counting) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [counting, retry?.next_attempt_at]);

  if (!retry) return null;

  if (retry.state === "running") {
    return (
      <span className="bili-auto-retry" role="status">
        自动重试中…
      </span>
    );
  }

  if (retry.state === "done") {
    return (
      <span className="bili-auto-retry is-done" role="status">
        {retry.last_message || "已自动完成"}
      </span>
    );
  }

  if (retry.state === "exhausted") {
    return (
      <span className="bili-auto-retry is-stopped" role="status">
        {retry.last_message || `自动重试已试 ${retry.attempts} 次，先停在这里`}
      </span>
    );
  }

  const nextAt = parseServerTime(retry.next_attempt_at);
  const seconds = nextAt === null ? null : Math.max(0, Math.round((nextAt - now) / 1000));
  const clock = seconds === null ? null : formatCountdown(seconds);

  return (
    <span className="bili-auto-retry" role="status">
      {clock === null ? "自动重试已排队" : `下一次自动重试 ${clock}`}
      {retry.attempts > 0 && ` · 已尝试 ${retry.attempts} 次`}
      {retry.last_message && ` · ${retry.last_message}`}
    </span>
  );
}

/** `01:23` / `1:02:03`。**不到一分钟就写「不到 1 分钟」**而不是 `00:00` ——
 * 一个显示 `00:00` 的倒计时看着像「卡住了」。 */
export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  if (safe < 60) return "不到 1 分钟";
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60).toString().padStart(2, "0");
  const secs = (safe % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}
