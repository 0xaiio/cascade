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

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  return value.replace("T", " ").replace(/\.\d+Z?$/, "").replace(/Z$/, "").slice(0, 19);
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
