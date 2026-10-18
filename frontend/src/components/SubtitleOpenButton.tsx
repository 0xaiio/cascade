import { Captions, ChevronDown } from "lucide-react";
import type { SubtitleOpenMode } from "../types";

/**
 * 「打开字幕」按钮。
 *
 * 它是**一个组合控件、两个动作**：主段用系统记住的关联程序打开，右段弹出系统的
 * 「打开方式」窗口来挑另一个程序。分成两段而不是「一个按钮 + 修饰键（Shift 点击）」，
 * 是因为修饰键在界面上看不见 —— 看不见的入口等于没做；而「换个程序打开」确实是用户
 * 会想改的事。
 *
 * **两段都不指定程序。** 主段交给文件关联，右段交给系统自己的处理器列表 ——
 * 应用里没有、也不该有编辑器清单：它只能是系统那份列表的子集，还会与系统各记一份
 * 「上次用的是谁」。所以「记住上一次用的查看器」由系统负责（在窗口里勾「始终使用」）。
 * 详见 `docs/technical.md#打开字幕文件`。
 */
export function SubtitleOpenButton({
  compact = false,
  disabled,
  subject,
  onOpen
}: {
  compact?: boolean;
  disabled: boolean;
  /** 视频标题，用于无障碍名称（同一份列表里每行都有一个同名按钮）。 */
  subject: string;
  onOpen: (mode: SubtitleOpenMode) => void;
}) {
  const iconSize = compact ? 16 : 18;
  const unavailable = "字幕文件尚不可用";
  const buttonClass = compact ? "icon-button item-action-button" : "icon-button";
  return (
    <span className="split-button" role="group" aria-label={`打开字幕 ${subject}`}>
      <button
        className={buttonClass}
        type="button"
        title={disabled ? unavailable : "打开字幕文件"}
        aria-label={`打开字幕文件 ${subject}`}
        disabled={disabled}
        onClick={() => onOpen("default")}
      >
        <Captions size={iconSize} />
      </button>
      <button
        className={`${buttonClass} split-button-toggle`}
        type="button"
        title={disabled ? unavailable : "选择打开方式"}
        aria-label={`选择打开方式 ${subject}`}
        disabled={disabled}
        onClick={() => onOpen("chooser")}
      >
        <ChevronDown size={compact ? 11 : 12} />
      </button>
    </span>
  );
}
