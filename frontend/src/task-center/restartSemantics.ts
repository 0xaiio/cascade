/**
 * E-03 · 「重启」到底做什么 —— 界面上的**唯一文案源**。
 *
 * 取证自 `JobManager.restart` / `restart_item`（见
 * [`ai/feature/E-03-restart-semantics.md`](../../../ai/feature/E-03-restart-semantics.md)）：
 * 重启把任务与条目重新排队、清空进度/大小/错误/时间/产物路径，**重跑一次预检**；
 * 不传清晰度时沿用原选项；磁盘上的 `.part` 不删，由 yt-dlp 断点续传；
 * 已下载文件、任务历史与 `JobEvent` 都不动。
 *
 * 为什么单独一个文件：任务级、条目级、批量、换清晰度四个入口都在 `JobQueue` 里，
 * 各写一句就会再次出现「文档说一套、按钮说一套」。文案由后端行为反推，
 * 不从字段名猜；改语义时先改 `backend/tests/test_restart_semantics.py`。
 *
 * **按钮可见文字仍是「重启」**：取证结论是它与「重新排队 + 重跑预检」一致，
 * 不一致的是用户脑中的「从零重下」预期 —— 缺的是说明，不是名字。
 */

/** 会被清掉的（界面上的进度与这一轮的记录）。 */
const RESET_PART = "清空界面上的进度、大小、时间与错误记录，重新排队并重跑一次预检（字幕计划也会重算）";

/** 不会被清掉的（磁盘与历史）。 */
const KEEP_PART = "不会从零重下：磁盘上没下完的 .part 会断点续传，已下载的文件和任务历史都不会被删除";

/** 沿用选项这一条，只有「换清晰度重启」不适用。 */
const SAME_OPTIONS_PART = "沿用原来的清晰度与下载选项";

export const RESTART_SUMMARY = `${RESET_PART}；${SAME_OPTIONS_PART}；${KEEP_PART}。`;

/** 任务行上的图标按钮。 */
export function jobRestartHint(): string {
  return `重启整个任务：${RESTART_SUMMARY}暂停过的任务也点它恢复，没有单独的「继续」按钮。`;
}

/** 合集里某一条的图标按钮。 */
export function itemRestartHint(): string {
  return `重启这一条：${RESET_PART}；${SAME_OPTIONS_PART}；${KEEP_PART}。其它条目不受影响。`;
}

/** 批量工具条。 */
export function batchRestartHint(): string {
  return `批量重启所选任务：每个任务都会${RESTART_SUMMARY}`;
}

/** 清晰度降级提示里的「以 720p 重启」——唯一会改选项的那一个入口。 */
export function resolutionRestartHint(resolution: string): string {
  return `以 ${resolution} 重启：改用这个清晰度，${RESET_PART}；${KEEP_PART}。`;
}
