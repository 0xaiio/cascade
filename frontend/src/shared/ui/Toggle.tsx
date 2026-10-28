import type { ReactNode } from "react";

/**
 * 勾选行：左侧图标 + 文案，右侧原生 checkbox。纯展示，状态由调用方持有。
 *
 * `disabled` 时**必须**同时给 `disabledReason`：一个勾不动又不说为什么的开关，
 * 与「勾了没反应」是同一种名实不符，只是换了个方向。理由渲染在这一行下面。
 */
export function Toggle({
  icon,
  label,
  checked,
  onChange,
  disabled = false,
  disabledReason
}: {
  icon: ReactNode;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  disabledReason?: string;
}) {
  // 用 Fragment 而不是包一层 div：`.toggle-list` 是 grid，`.toggle-row` 靠「自己是 grid item
  // 被拉满整行」才把 checkbox 顶到右缘 —— 多套一层块级元素会让它退回 inline-flex 的
  // 收缩宽度，checkbox 就跑到文案右边去了。
  return (
    <>
      <label className="toggle-row">
        <span>{icon}{label}</span>
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
      {disabled && disabledReason && <p className="toggle-disabled-note">{disabledReason}</p>}
    </>
  );
}
