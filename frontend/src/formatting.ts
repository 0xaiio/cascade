export function formatDuration(seconds: number | null): string {
  if (!seconds) return "--:--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${mins}:${secs}`;
}

/**
 * 视频时长，形如 `1:04:05`（时:分:秒）。
 *
 * 与 `formatClock`（已用/剩余）刻意不同：那两处是耗时，不到一小时时省掉「时」更顺眼；
 * 时长是**视频的属性**，三段位数固定，扫一眼就能和「大小」区分开。
 * 拿不到时长（平台没给、或旧任务还没补上）显示「未知」——与同一行的「大小 未知」同一种说法。
 */
export function formatMediaDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "未知";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60).toString().padStart(2, "0");
  const secs = Math.floor(total % 60).toString().padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

export function formatPercent(value: number | null | undefined): string {
  return `${Math.max(0, Math.min(100, value ?? 0)).toFixed(1)}%`;
}

export function formatClock(seconds: number | null | undefined): string {
  const safeSeconds = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60).toString().padStart(2, "0");
  const secs = Math.floor(safeSeconds % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}

/**
 * 解析服务端给的时间戳，**一律按 UTC 解释**。
 *
 * 为什么不能直接 `Date.parse`：ECMAScript 规定**不带偏移量的 date-time 串按本地时间解析**，
 * 而服务端存的是 UTC 墙上时间（SQLite 不存时区，见 `schemas.UtcDateTime`）。在本机 UTC+8，
 * 一个 `2026-10-21T10:29:38` 会被当成 10:29:38 的**本地**时间，比真实时刻早了 8 小时 ——
 * 于是「已用 52 秒」被算成「8:00:52」。这类偏差不报错，只是数字变大，所以它藏了很久
 * （用例里全是带 `Z` 的假数据）。
 *
 * 带了偏移量（`Z` / `+00:00` / `+08:00`）就按偏移量算：这里判据是「**哪一端都可能给**」。
 * 服务端已经补上 `Z` 了，这段兜底是为了让「某一处又忘了」不会退回一个静默的 8 小时谎报。
 */
export function parseServerTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value.trim());
  const parsed = Date.parse(hasOffset ? value : `${value.trim()}Z`);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * 一个时刻显示成 `YYYY-MM-DD HH:MM:SS`，**按本地时区**。
 *
 * 原先是把字符串切片直接显示，于是 UTC 的墙上时间被当成本地时间摆给用户
 * （本机 18:29 开始的一条任务显示成「开始 10:29:38」）。显示值必须和用户手上的钟一致。
 */
export function formatDateTime(value: string | null | undefined): string {
  const parsed = parseServerTime(value);
  if (parsed === null) return "--";
  const date = new Date(parsed);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "大小未知";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function formatItemResolution(item: { actual_width: number | null; actual_height: number | null }): string {
  if (item.actual_width == null || item.actual_height == null) {
    return "检测中";
  }
  return `${item.actual_width}x${item.actual_height}`;
}

export function formatBytesPerSecond(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB/s`;
  }
  return `${(bytes / 1024).toFixed(1)} KB/s`;
}
