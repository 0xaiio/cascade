import { Captions } from "lucide-react";

/**
 * 「译为中文」的组合图标：**CC 字幕图标 + 右下角「中」字角标**。
 *
 * 为什么是两个符号叠在一起：字幕图标单独出现会和旁边的「打开字幕」按钮混为一体（两者
 * 都只是 CC）；纯「中」字又不说明操作对象是字幕。CC 表示「对字幕做事」，角标的「中」
 * 表示「目标语言是中文」，一眼就能和「打开字幕」区分开。
 *
 * 图标本身不承载可访问名称 —— 按钮的 `aria-label` 仍写全「译为中文 <标题>」，
 * 屏幕阅读器不会把「中」读成一个孤立的字。
 */
export function TranslateToChineseIcon({ size = 18 }: { size?: number }) {
  return (
    <span className="translate-zh-icon" aria-hidden="true">
      <Captions size={size} strokeWidth={2} />
      <span className="translate-zh-badge">中</span>
    </span>
  );
}
