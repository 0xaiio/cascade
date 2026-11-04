import type { SettingEffective } from "../contracts/types";

/**
 * 「库里 vs 进程实际」的差异怎么念给用户听。
 *
 * 为什么单独一个纯函数：这句话要能被单测（jsdom 里渲染整页才知道对不对，太慢），
 * 而且它有一条硬约束 —— **只在真的有差异时出现**。一致时冒出一句「配置正常」
 * 只是噪音，还会让人以为它在盯着什么。
 *
 * 见 [ai/bug-fix/035](../../../ai/bug-fix/035-diagnostics-shows-the-stored-value.md)。
 */
const LABELS: Record<string, string> = {
  download_dir: "下载目录",
  default_concurrency: "并发数",
  default_resolution: "默认清晰度",
  default_speed_limit_kbps: "限速",
  default_retries: "重试次数",
  default_subtitle_languages: "默认字幕语言",
  aria2c_connections: "单视频并发下载数",
  proxy: "代理",
  completion_notification: "完成提醒方式",
  bili_auto_retry_enabled: "B站 自动重试开关",
  bili_auto_retry_interval_seconds: "自动重试间隔",
  bili_auto_retry_max_attempts: "自动重试上限",
};

/** 只挑出真的有差异的项。`stored === null` 表示库里没写过 —— 后端不会把它算作差异。 */
export function driftedSettings(items: SettingEffective[] | null | undefined): SettingEffective[] {
  return (items ?? []).filter((item) => item.differs);
}

function label(key: string): string {
  // 未登记的键直接露出键名：宁可难看，也不要显示一个猜出来的中文名。
  return LABELS[key] ?? key;
}

function value(text: string | null | undefined): string {
  return text === null || text === undefined || text === "" ? "（未设置）" : text;
}

/**
 * 一句能照着查的话；没有差异时返回 `null`。
 *
 * 措辞刻意不写「需重启生效」：差异的成因有两种 —— 别的进程改了库（重新加载设置即可），
 * 或者某处持有了构造时的快照（必须改代码/重启）。诊断能证明的只有「两者不同」。
 */
export function settingsDriftText(items: SettingEffective[] | null | undefined): string | null {
  const drifted = driftedSettings(items);
  if (drifted.length === 0) return null;
  const detail = drifted
    .map((item) => `${label(item.key)}：库里 ${value(item.stored)}，进程用 ${value(item.runtime)}`)
    .join("；");
  return `有 ${drifted.length} 项设置的「进程实际值」与库里不同 —— ${detail}。多半是另一个进程改过配置；重启后端或重新打开设置页会重新加载。`;
}
