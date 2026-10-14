/**
 * B站 图标：一台带天线的电视机（B站 品牌形象），描边用 `currentColor`，由调用方决定颜色。
 *
 * 关于这个图形本身，有一条必须说清的边界：它是**按 B站 品牌形象的几何特征手绘的 SVG**，
 * 不是官方素材的拷贝。刻意用「圆角机身 + 两根天线 + 两只眼睛」这几个简单基本形拼出来，
 * 而不是从别处抄一条复杂的 path —— 复杂 path 抄错了看不出来（见 ai/ui/003 里那条
 * 「看起来对、错了也看不出来」的教训），而基本形对错一眼可辨。
 *
 * 品牌色 `#FB7299` 只用在**这个图标本身**（design §10）：按钮的其余样式与既有
 * `.icon-button` / `.ghost-button` 完全一致，否则整个任务列表会被粉色淹没。
 */
export function BiliIcon({ size = 18 }: { size?: number }) {
  return (
    <svg
      className="bili-icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {/* 两根天线 */}
      <path d="M6.8 4.6 9.2 7.4" />
      <path d="M17.2 4.6 14.8 7.4" />
      {/* 机身 */}
      <rect x="3" y="7.4" width="18" height="12.6" rx="3.6" />
      {/* 两只眼睛 */}
      <path d="M9.6 12.2v3.2" />
      <path d="M14.4 12.2v3.2" />
    </svg>
  );
}
