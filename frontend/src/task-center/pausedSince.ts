/**
 * E-05 · 「暂停了多久」的判定与文案。
 *
 * 这是**一行提示，不是新通知渠道**：完成提醒（014）有意只覆盖 `succeeded`/`failed`，
 * `paused` 不是终态，长暂停的语义补全只落在任务行上，不进 toast、不响声音。
 *
 * 时间是服务端给的 `paused_at`（UTC，带 Z）；`parseServerTime` 是唯一解析入口。
 * 补列之前的历史任务由读模型标成「近似」，文案因此必须带「约」——
 * 不把猜测伪装成精确值。
 */

import { parseServerTime } from "../shared/formatting";

/** 不到两小时不打扰；阈值是「手动暂停后忘了」，不是「暂停了一下」。 */
export const PAUSED_NOTICE_THRESHOLD_MS = 2 * 60 * 60 * 1000;

const HOUR_MS = 60 * 60 * 1000;
const HOURS_PER_DAY = 24;

/** 页面开着时按分钟重算这个标签，不需要秒表精度。 */
export const PAUSED_TICK_MS = 60 * 1000;

export function pausedDurationLabel(
  pausedAt: string | null,
  nowMs: number,
  approximate = false
): string | null {
  if (!pausedAt) return null;
  const since = parseServerTime(pausedAt);
  if (since === null) return null;

  const elapsed = nowMs - since;
  // 时钟回拨、未来时间、坏值都不编一个「已暂停 -1 小时」。
  if (!Number.isFinite(elapsed) || elapsed < PAUSED_NOTICE_THRESHOLD_MS) return null;

  const hours = Math.floor(elapsed / HOUR_MS);
  const days = Math.floor(hours / HOURS_PER_DAY);
  const restHours = hours % HOURS_PER_DAY;
  const span = days > 0
    ? `${days} 天${restHours > 0 ? ` ${restHours} 小时` : ""}`
    : `${hours} 小时`;
  return `已暂停${approximate ? "约 " : " "}${span}`;
}
