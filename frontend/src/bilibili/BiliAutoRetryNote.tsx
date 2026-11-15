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
 * **四种状态各说各的话，而且永不写「00:00」**：
 *
 * | 状态 | 文案 |
 * | --- | --- |
 * | 还没到点 | `下一次自动重试 00:35 · 已重试 1 次` |
 * | 到点了、还没轮到它跑 | `即将自动重试 · 已重试 1 次` |
 * | 正在问平台 | `正在自动重试（第 2 次）…` |
 * | `done` / `exhausted` | 说结果，**不再说倒计时** |
 *
 * 为什么「到点」要单独成一档：`next_attempt_at` 只表示「不早于这个时刻」，真正去问要等
 * 调度器下一轮扫库。把它渲染成 `00:00` 是**一句假话** —— 用户读到的是「到点了却不重试」
 * （见 ai/ui/013）。`00:00` 只在一种情况下出现才诚实：这一秒真的在跑，而那时状态
 * 已经是 `running` 了。
 *
 * 次数**每一档都写**（0 也写「还没重试过」）：它回答的是「到底动过没有」，而这正是
 * `00:00` 那个疑问的另一半 —— 原先 `attempts === 0` 时不显示，于是「一次都没试过」
 * 这件事在界面上不可见。
 *
 * 倒计时是**按秒真的在减**的：它由服务端给的截止时刻 `next_attempt_at` 算出，
 * 不是本地累减 —— 后者在标签页被挂起（浏览器节流 `setInterval`）之后会落后几分钟。
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
    // 「第 N 次」= 已经完成 N-1 次 ⇒ 手上这次是第 N 次。`attempts` 是**跑完才加**的，
    // 所以这里 +1 才对得上（跑完那一瞬间界面会变成「已重试 N 次」）。
    return (
      <span className="bili-auto-retry" role="status">
        正在自动重试（第 {retry.attempts + 1} 次）…
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
  // `Math.ceil` 而不是 `round`：它让显示值**单调递减**，而且**到点才归零**。
  // `round` 会在 34.5 秒附近来回跳（35 → 34 → 35），看着像机器在犹豫。
  const seconds = nextAt === null ? null : Math.max(0, Math.ceil((nextAt - now) / 1000));
  // 三档：不知道时间 / 还有时间 / 时间到了但还没轮到它跑。**都不写 00:00**。
  const head =
    seconds === null
      ? "自动重试已排队"
      : seconds > 0
        ? `下一次自动重试 ${formatCountdown(seconds)}`
        : "即将自动重试";

  return (
    // `aria-live="off"`：这一行**每秒都在变**，交给读屏软件就是每秒念一次。
    // 状态的变化（`running` / `done` / `exhausted`）在别的分支里，那些值得播报。
    <span className="bili-auto-retry" role="status" aria-live="off">
      {head} · {attemptsLabel(retry.attempts)}
      {/* 等待中**不再重复上次那句话**：它与上面那一行说的是同一件事
          （「合集：等待 B 站视频审核后进行归集」），拼在一起等于把刚精简掉的长句加回来。
          失败原因属于 `exhausted` 那一档，那里照旧带出来。 */}
    </span>
  );
}

/**
 * 「重试过几次」这句话。**0 次也要说出来** —— 用户看到「即将自动重试」时，
 * 第一个疑问就是「那到底试过没有」，而原先 `attempts === 0` 时这一段是空的。
 */
export function attemptsLabel(attempts: number): string {
  const safe = Math.max(0, Math.floor(attempts));
  return safe === 0 ? "还没重试过" : `已重试 ${safe} 次`;
}

/**
 * 倒计时的显示格式：**`mm:ss`**（`00:35` = 还剩 35 秒，`02:00` = 还剩 2 分钟）。
 *
 * 原来不到一分钟写「不到 1 分钟」—— 那是为了回避 `00:00` 像卡住，代价是**看不出还剩多久**：
 * 用户在 59 秒里看到的是同一句话，分不清机器在走还是停了。现在精确到秒，
 * 秒数由**真实截止时刻**算出来（不是本地累减），所以它一定会在到点那一刻归零。
 *
 * 超过一小时仍用 `h:mm:ss`：`80:00` 这种写法看不出是八十分钟还是八十分钟零几秒。
 */
export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor((safe % 3600) / 60).toString().padStart(2, "0");
  const secs = (safe % 60).toString().padStart(2, "0");
  const hours = Math.floor(safe / 3600);
  return hours ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}
