import { Captions } from "lucide-react";

/**
 * 「打开字幕」按钮。
 *
 * **一个按钮、一个动作**：点下去就弹系统自己的「打开方式」窗口，由用户在系统那份
 * 处理器列表里挑一个程序（勾「始终使用此应用」由系统记住，下次仍从同一个窗口挑，
 * 但列表里默认就是上次那个）。
 *
 * 这里曾经是主段 + 右段箭头的组合控件（主段走文件关联、右段弹选择器）。改成一个，
 * 是因为那个箭头**点下去看起来什么都没发生**：`OpenAs_RunDLL` 弹的是 `OpenWith.exe`
 * 的窗口，标题是「选取应用」，既不含文件名也不属于 `rundll32` 的 pid —— 旧的置前逻辑
 * 两条判据都对不上，窗口就留在浏览器后面，只在任务栏闪一下。用户必须先发现它、
 * 再点一次才能看见对话框。既然「挑程序」是主路径，就把它放在唯一那颗按钮上。
 *
 * **不指定程序**：应用里没有、也不该有编辑器清单（只能是系统那份列表的子集）。
 * 判据与取舍见 `docs/technical.md#打开字幕文件`。
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
  onOpen: () => void;
}) {
  const iconSize = compact ? 16 : 18;
  const buttonClass = compact ? "icon-button item-action-button" : "icon-button";
  return (
    <button
      className={buttonClass}
      type="button"
      title={disabled ? "字幕文件尚不可用" : "打开字幕文件；可选择打开方式"}
      aria-label={`打开字幕文件 ${subject}`}
      disabled={disabled}
      onClick={onOpen}
    >
      <Captions size={iconSize} />
    </button>
  );
}
