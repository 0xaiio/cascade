import { BADGE_NONE } from "../biliStatus";
import type { BiliBadge, BiliBadgeTone } from "../types";
import { BiliIcon } from "./BiliIcon";

/**
 * 任务行/条目行上的 B站 上传按钮：**B站 图标 + 一句人话**。
 *
 * 两个刻意的取舍：
 *
 * 1. **按钮上带文字，而不是纯图标**。任务行那一排按钮都是纯图标（靠 tooltip 说明），
 *    但「上传到 B站」是这个功能的主操作，而 tooltip 在触屏上根本看不到。
 *    需求里的原话是「B站图标 + 上传语义」——图标配一个状态词才算表达完整。
 * 2. **文案用「上传」而不是后端徽标里的「未上传」**。同一档状态，对按钮来说是个**动作**
 *    （点它是去上传），对徽标来说是个**状态**。后端给的是状态词，这里只在 idle 档换成动作词，
 *    其余档原样显示 —— 免得前端自己造一套状态文案，两处必然会漂移。
 *
 * 状态色只落在这一个小胶囊上（`is-ok` / `is-warn` …），图标本身恒为品牌色。
 */
export function BiliUploadButton({
  badge,
  badgeText,
  badgeTone,
  subject,
  disabledReason,
  compact = false,
  onClick
}: {
  badge: BiliBadge;
  badgeText: string;
  badgeTone: BiliBadgeTone;
  /** 主语（任务标题或视频标题），只用于 aria-label 与 tooltip。 */
  subject: string;
  /** 非空 = 禁用，并把这句话作为禁用原因显示出来。 */
  disabledReason?: string | null;
  compact?: boolean;
  onClick: () => void;
}) {
  const disabled = Boolean(disabledReason);
  const idle = badge === BADGE_NONE;
  const label = idle ? "上传" : badgeText;
  const action = idle ? "上传到 B 站" : `B站：${badgeText}`;
  const title = disabled ? `${action}（暂不可用：${disabledReason}）` : `${action}${idle ? "" : " · 点击重新上传"}`;
  return (
    <button
      className={`bili-button ${compact ? "is-compact" : ""} tone-${badgeTone}`}
      type="button"
      title={title}
      aria-label={`${action} ${subject}`}
      disabled={disabled}
      onClick={onClick}
    >
      <BiliIcon size={compact ? 16 : 18} />
      <span className="bili-button-label">{label}</span>
    </button>
  );
}
