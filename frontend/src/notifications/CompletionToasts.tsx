import { AlertTriangle, CheckCircle2, X, XCircle } from "lucide-react";
import type { CompletionNotice } from "../contracts/types";

/**
 * 右下角的任务完成提醒。
 *
 * 三条设计约束：
 * 1. **自动缩回**：由 `useCompletionToasts` 的倒计时负责，这里只呈现；
 * 2. **悬停暂停**：鼠标停在上面就是在读，此时缩回是纯粹的打扰（`onMouseEnter/Leave`）；
 * 3. **不抢焦点**：整个浮层没有 `autofocus`，也不拦点击 —— 它是告知，不是对话框。
 *    因此关闭按钮是唯一可交互元素，且带 `aria-label`。
 *
 * `role="status"` + `aria-live="polite"`：屏幕阅读器会读出来，但不会打断当前朗读。
 */
const TONE_ICON = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  bad: XCircle
} as const;

export function CompletionToasts({
  toasts,
  onDismiss,
  onPause,
  onResume
}: {
  toasts: CompletionNotice[];
  onDismiss: (id: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
}) {
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" aria-label="任务完成提醒">
      {toasts.map((toast) => {
        const Icon = TONE_ICON[toast.tone];
        return (
          <div
            key={toast.id}
            className={`toast tone-${toast.tone}`}
            role="status"
            aria-live="polite"
            onMouseEnter={() => onPause(toast.id)}
            onMouseLeave={() => onResume(toast.id)}
          >
            <Icon size={18} className="toast-icon" />
            <div className="toast-text">
              <strong>{toast.title}</strong>
              <span>{toast.body}</span>
            </div>
            <button
              type="button"
              className="toast-close"
              aria-label={`关闭提醒：${toast.title}`}
              onClick={() => onDismiss(toast.id)}
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
