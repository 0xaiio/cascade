import type { AnalyzeResponse, DownloadOptions, SubtitleFormat, SubtitleSource } from "../contracts/types";

/**
 * 字幕相关的**展示文案与选择归一**。
 *
 * 这一组函数只看「解析结果 + 选项」，不发请求、不读 localStorage，因此可以脱离组件单独验证。
 * 语言值的形状见 `@/types` 与后端 `_map_subtitles()`：是**语言代码**（`en` / `zh-Hans`），
 * 不是展示名 —— 写断言时不要用 `English` 这类名字。
 */

/**
 * 把设置里的「默认字幕语言」收敛到**这个视频真的有的**语言。
 *
 * 为什么必须收敛：解析时把默认值（出厂 `["en"]`）原样放进选项，而这个选项一旦非空，
 * 后端就**不再**按视频主语言挑轨（`_options_with_subtitle_plan` 里「显式选择优先」）。
 * 于是一个只有中文字幕的视频会拿到 `subtitleslangs=["en"]` —— yt-dlp 匹配不到任何轨，
 * **静默不下字幕、也不报错**，界面上却写着「已选 1 项：en」。收敛之后：
 * 命中的语言照旧生效；一个都不命中就留空，把选择权交回后端的字幕计划（按主语言挑一条）。
 *
 * 保序去重：设置里的顺序就是偏好顺序，`filter` 不会打乱它。
 */
export function effectiveSubtitleLanguages(analysis: AnalyzeResponse, preferred: string[]): string[] {
  const available = new Set(
    [...analysis.subtitles, ...analysis.automatic_subtitles].map((item) => item.language)
  );
  return Array.from(new Set(preferred)).filter((code) => available.has(code));
}

/** 下载选项面板里那行「字幕：来源 X · 格式 Y」的说明文案。 */
export function formatSubtitleInfo(analysis: AnalyzeResponse | null, options: DownloadOptions): string {
  if (!analysis) {
    return "字幕：待解析 · 来源 两者都要 · 格式 最佳";
  }
  if (options.mode === "video_only") {
    return "字幕：无字幕（仅视频模式）";
  }

  const hasHuman = analysis.subtitles.length > 0;
  const hasAuto = analysis.automatic_subtitles.length > 0;
  if (!hasHuman && !hasAuto) {
    return "字幕：无字幕";
  }

  const source = subtitleSourceDescription(options.subtitle_source, hasHuman, hasAuto);
  const format = subtitleFormatLabel(options.subtitle_format);
  return `字幕：来源 ${source} · 格式 ${format}`;
}

/**
 * 提交任务前把「两者都要」这类诉求收敛成**该视频真的有的那一类**。
 *
 * 之所以在提交前收敛而不是让后端兜底：后端只能忽略不存在的来源，而界面需要如实告诉用户
 * 「你选的那类没有，实际下的是另一类」。
 */
export function effectiveSubtitleSourceForAnalysis(analysis: AnalyzeResponse, source: SubtitleSource): SubtitleSource {
  const hasHuman = analysis.subtitles.length > 0;
  const hasAuto = analysis.automatic_subtitles.length > 0;
  if (source === "both" && hasHuman !== hasAuto) return hasHuman ? "human" : "auto";
  if (source === "human" && !hasHuman && hasAuto) return "auto";
  if (source === "auto" && !hasAuto && hasHuman) return "human";
  return source;
}

function subtitleSourceDescription(source: SubtitleSource, hasHuman: boolean, hasAuto: boolean): string {
  if (source === "both") {
    if (hasHuman && hasAuto) return "两者都要（人工字幕 + 自动字幕）";
    if (hasHuman) return "人工字幕（自动字幕缺失，已 fallback）";
    return "自动字幕（人工字幕缺失，已 fallback）";
  }
  if (source === "human") {
    return hasHuman ? "人工字幕" : "自动字幕（人工字幕缺失，已 fallback）";
  }
  return hasAuto ? "自动字幕" : "人工字幕（自动字幕缺失，已 fallback）";
}

function subtitleFormatLabel(format: SubtitleFormat): string {
  if (format === "srt") return "SRT";
  if (format === "vtt") return "VTT";
  return "最佳";
}
