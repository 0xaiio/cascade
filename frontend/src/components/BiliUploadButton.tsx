import { BADGE_NONE, BADGE_UPLOADED } from "../biliStatus";
import type { BiliBadge, BiliBadgeTone } from "../types";
import { BiliIcon } from "./BiliIcon";

/**
 * 「已上传」这一档的 tooltip 与无障碍名**共用同一句** —— 视觉与读屏说同一件事。
 *
 * 措辞本身是一个产品判断：`已上传` 是终态，再点它不该重新投稿（那会在 B站 留下第二条
 * 同名稿件），所以这一点要明说是「进入 B 站」，而不是含糊的「点击查看」。
 */
const UPLOADED_HINT = "B 站已上传；点击进入 B 站";

/**
 * 任务行/条目行上的 B站 上传按钮：**B站 图标 + 一句人话**。
 *
 * 三个刻意的取舍：
 *
 * 1. **按钮上带文字，而不是纯图标**。任务行那一排按钮都是纯图标（靠 tooltip 说明），
 *    但「上传到 B站」是这个功能的主操作，而 tooltip 在触屏上根本看不到。
 *    需求里的原话是「B站图标 + 上传语义」——图标配一个状态词才算表达完整。
 * 2. **文案用「上传」而不是后端徽标里的「未上传」**。同一档状态，对按钮来说是个**动作**
 *    （点它是去上传），对徽标来说是个**状态**。后端给的是状态词，这里只在 idle 档换成动作词，
 *    其余档原样显示 —— 免得前端自己造一套状态文案，两处必然会漂移。
 * 3. **`uploaded` 档改变的是「点下去做什么」，不是「长什么样」**：胶囊仍是绿的、文字仍是
 *    「已上传」，只有点击与 tooltip 换成「进入 B 站」。把这一档也做得跟别的档一样去重投，
 *    是拿一个高频误操作换一句「统一的文案」。
 *
 * 状态色只落在这一个小胶囊上（`is-ok` / `is-warn` …），图标本身恒为品牌色。
 */
export function BiliUploadButton({
  badge,
  badgeText,
  badgeTone,
  subject,
  disabledReason,
  openUrl = null,
  onOpenUrl,
  compact = false,
  onClick
}: {
  badge: BiliBadge;
  badgeText: string;
  badgeTone: BiliBadgeTone;
  /** 主语（任务标题或视频标题），只用于 aria-label 与 tooltip。 */
  subject: string;
  /** 非空 = 把这句话作为「暂时不能上传」的原因显示出来。**只约束上传，不约束打开链接**（见下）。 */
  disabledReason?: string | null;
  /** `uploaded` 档要打开的 B 站页面；`null` = 本地没记下链接。由 `uploadedTargetUrl` 算。 */
  openUrl?: string | null;
  /** 有它 + `openUrl` 才把 `uploaded` 档改成「进入 B 站」。 */
  onOpenUrl?: (url: string) => void;
  compact?: boolean;
  /** 非 `uploaded` 档（或没有链接时）的点击 = 打开上传表单。 */
  onClick: () => void;
}) {
  // 有链接的「已上传」才改行为。没有链接（徽标由合集已归集派生）时保持老路：
  // 「点击重新上传」在那时是**事实**，不是误导。
  const opens = badge === BADGE_UPLOADED && Boolean(openUrl) && Boolean(onOpenUrl);
  // 稿件已经在平台上了，能不能再打开它跟**本地视频文件还在不在**无关 ——
  // 用户传完删掉本地文件之后，这个按钮不该连 B站 页面都点不开。
  const disabled = !opens && Boolean(disabledReason);
  const idle = badge === BADGE_NONE;
  const label = idle ? "上传" : badgeText;
  const action = idle ? "上传到 B 站" : opens ? "B 站已上传" : `B站：${badgeText}`;
  const title = opens
    ? UPLOADED_HINT
    : disabled
      ? `${action}（暂不可用：${disabledReason}）`
      : `${action}${idle ? "" : " · 点击重新上传"}`;
  return (
    <button
      className={`bili-button ${compact ? "is-compact" : ""} tone-${badgeTone}`}
      type="button"
      title={title}
      aria-label={opens ? `${UPLOADED_HINT} ${subject}` : `${action} ${subject}`}
      disabled={disabled}
      onClick={() => {
        // 闭包里取 `openUrl` 要显式判空：`opens` 是派生值，类型收窄穿不过去。
        if (opens && openUrl) onOpenUrl?.(openUrl);
        else onClick();
      }}
    >
      <BiliIcon size={compact ? 16 : 18} />
      <span className="bili-button-label">{label}</span>
    </button>
  );
}
