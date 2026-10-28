import { BADGE_NONE, BADGE_PARTIAL, BADGE_UPLOADED, BADGE_UPLOADING, BADGE_UNVERIFIED } from "./biliStatus";
import type { BiliBadge, BiliBadgeTone } from "../contracts/types";
import { BiliIcon } from "./BiliIcon";

/**
 * 每一档的「点下去做什么」的一句话。**tooltip 与无障碍名共用同一句** ——
 * 视觉与读屏说同一件事，两处各写一份必然漂移。
 *
 * 措辞本身就是产品判断，逐条都有理由：
 *
 * - `已上传`：「已上传」是终态，再点一次会在账号里留下第二条同名稿件。
 * - `待核对`：**明确说「不会重投」**。这一档最容易被误判成失败而去重投，而投稿命令
 *   其实已经成功过一次，平台上很可能已经有这条稿件（见 `docs/user-manual.md` 那张三档表）。
 * - `上传中`：这一档按钮是灰的，提示要解释**为什么不能点**，而不是许诺一个点了会发生的动作。
 */
const HINTS = {
  uploaded: "B 站已上传；点击进入 B 站",
  unverified: "B 站还没确认这条稿件；点击重新核对（不会重投）",
  uploading: "正在上传中；跑完会自动变成「已上传」或「失败」"
} as const;

/**
 * 任务行/条目行上的 B站 上传按钮：**B站 图标 + 一句人话**。
 *
 * 四个刻意的取舍：
 *
 * 1. **按钮上带文字，而不是纯图标**。任务行那一排按钮都是纯图标（靠 tooltip 说明），
 *    但「上传到 B站」是这个功能的主操作，而 tooltip 在触屏上根本看不到。
 *    需求里的原话是「B站图标 + 上传语义」——图标配一个状态词才算表达完整。
 * 2. **文案用「上传」而不是后端徽标里的「未上传」**。同一档状态，对按钮来说是个**动作**
 *    （点它是去上传），对徽标来说是个**状态**。后端给的是状态词，这里只在 idle 档换成动作词，
 *    其余档原样显示 —— 免得前端自己造一套状态文案，两处必然会漂移。
 * 3. **改的是「点下去做什么」，不是「长什么样」**。胶囊的文案与配色恒等于服务端徽标，
 *    只有点击与 tooltip 按档位分流：`uploaded` → 打开稿件页；`unverified` / `partial` →
 *    **只补没成功的那几条**；`uploading` → 什么都不做（灰着）。
 *    把这几档也做成「统一打开上传表单」，是拿一个**不可撤销**的误操作换一句整齐的文案。
 * 4. **只有 `failed` 走投稿表单**：那一步确实没成，元数据可能还要改；而它没有任何记录
 *    在平台上，重投不会产生第二条稿件。
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
  retryableIds = [],
  onRetry,
  compact = false,
  onClick
}: {
  badge: BiliBadge;
  badgeText: string;
  badgeTone: BiliBadgeTone;
  /** 主语（任务标题或视频标题），只用于 aria-label 与 tooltip。 */
  subject: string;
  /** 非空 = 把这句话作为「暂时不能上传」的原因显示出来。**只约束上传，不约束打开链接与核对**（见下）。 */
  disabledReason?: string | null;
  /** `uploaded` 档要打开的 B 站页面；`null` = 本地没记下链接。由 `uploadedTargetUrl` 算。 */
  openUrl?: string | null;
  /** 有它 + `openUrl` 才把 `uploaded` 档改成「进入 B 站」。 */
  onOpenUrl?: (url: string) => void;
  /** `unverified` / `partial` 两档要补的那几条记录 id，由 `retryableUploadIds` 算。空 = 不接管。 */
  retryableIds?: string[];
  /** 有它 + `retryableIds` 非空才把这两档改成「补没成功的」。 */
  onRetry?: (uploadIds: string[]) => void;
  compact?: boolean;
  /** 剩下的档位（`none` / `failed` / 已上传但没链接）的点击 = 打开上传表单。 */
  onClick: () => void;
}) {
  // 四种动作按「谁最不可能造成重复投稿」排优先级。`uploading` 排第一：
  // 这一档压根不该有动作，哪怕调用方漏传了 disabledReason 也不能让它点下去。
  const waiting = badge === BADGE_UPLOADING;
  const opens = badge === BADGE_UPLOADED && Boolean(openUrl) && Boolean(onOpenUrl);
  const retries =
    (badge === BADGE_UNVERIFIED || badge === BADGE_PARTIAL) && retryableIds.length > 0 && Boolean(onRetry);
  // 上传与「补没成功的」都要本地文件；**打开链接与核对不需要** ——
  // 传完把文件删掉是常态，稿件还在平台上，按钮不该连页面/核对都点不开。
  const disabled = waiting || (!opens && !retries && Boolean(disabledReason));
  const idle = badge === BADGE_NONE;
  const label = idle ? "上传" : badgeText;

  const action = idle
    ? "上传到 B 站"
    : opens
      ? "B 站已上传"
      : retries
        ? badge === BADGE_UNVERIFIED
          ? "重新核对"
          : `重试没成功的 ${retryableIds.length} 条`
        : `B站：${badgeText}`;

  const title = waiting
    ? HINTS.uploading
    : opens
      ? HINTS.uploaded
      : retries
        ? badge === BADGE_UNVERIFIED
          ? HINTS.unverified
          : `有 ${retryableIds.length} 条还没投上去；点击只重试这几条，不会重投已上传的`
        : disabled
          ? `${action}（暂不可用：${disabledReason}）`
          : `${action}${idle ? "" : " · 点击重新上传"}`;

  // 「已上传」与「待核对」这两档的 tooltip 本身就是完整的一句话，读屏也照这句念；
  // 其余档沿用 `<动作> <主语>` 的老格式（`failed` 的动作是「B站：失败」，保持不变）。
  const ariaLabel = opens
    ? `${HINTS.uploaded} ${subject}`
    : retries && badge === BADGE_UNVERIFIED
      ? `${HINTS.unverified} ${subject}`
      : `${action} ${subject}`;

  return (
    <button
      className={`bili-button ${compact ? "is-compact" : ""} tone-${badgeTone}`}
      type="button"
      title={title}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => {
        // 闭包里取外部值要显式判空：`opens` / `retries` 是派生值，类型收窄穿不过去。
        if (opens && openUrl) onOpenUrl?.(openUrl);
        else if (retries) onRetry?.(retryableIds);
        else onClick();
      }}
    >
      <BiliIcon size={compact ? 16 : 18} />
      <span className="bili-button-label">{label}</span>
    </button>
  );
}
