import type { FormEvent } from "react";
import { Cookie, Gauge, ListVideo, Loader2 } from "lucide-react";
import { CookieHowToPopover, CookieSection } from "../cookies/CookieSection";
import type { Settings } from "../contracts/types";

/**
 * 解析面板：链接输入、cookies 行（状态 + 上传/清除）、解析按钮。
 *
 * cookies **只能来自文件**：浏览器扩展导出后在这里上传，或运行
 * `scripts/export_cookies_via_cdp.py` 写到 `data/cookies.txt`。这里曾经有一个
 * 「从浏览器导入」的下拉框与按钮，已移除 —— 本机 Edge 的 cookie 是 v20 app-bound 加密，
 * 离线必然解不开，而失败分支还会 `taskkill` 掉用户所有 Edge 窗口（见 ai/bug-fix/038）。
 * 面板因此不再持有任何 cookies 状态：启用与否只看上层传入的 `settings`。
 *
 * 布局约束：`.cookie-inline-primary` 里「选择 cookies」与「清除 cookies」必须共基线，
 * 改样式前先读 [ai/ui/001](../../../ai/ui/001-cookie-buttons-not-on-the-same-baseline.md) ——
 * 那条 8px 的错位来自基类规则被同优先级的变体静默吃掉。
 */
export function UrlAnalyzer({
  settings,
  url,
  duplicateWarning,
  isAnalyzing,
  onAnalyze,
  onCookieDelete,
  onCookieUpload,
  onUrlChange
}: {
  settings: Settings | null;
  url: string;
  duplicateWarning: boolean;
  isAnalyzing: boolean;
  onAnalyze: (event: FormEvent) => void;
  onCookieDelete: () => void;
  onCookieUpload: (file: File | null) => void;
  onUrlChange: (value: string) => void;
}) {
  return (
    <form className="panel url-panel" onSubmit={onAnalyze}>
      <div className="panel-title">
        <ListVideo size={20} />
        <div>
          <h2>解析链接</h2>
        </div>
      </div>
      <label className="field">
        <span>视频或 playlist 链接</span>
        <textarea value={url} onChange={(event) => onUrlChange(event.target.value)} rows={3} />
      </label>
      <div className="cookie-inline">
        <div className="cookie-inline-primary">
          <span className="cookie-inline-status">
            <Cookie size={16} />
            {settings?.cookies_enabled ? "已启用 cookies" : "未上传 cookies"}
            {/* 说明挂在状态文字旁，而不是混进右侧的动作按钮里 —— 它解释的是这一行，不是一个动作。 */}
            <CookieHowToPopover />
          </span>
          <div className="cookie-inline-actions">
            <label className="file-button compact-file-button">
              <span>选择 cookies</span>
              <input
                aria-label="选择 cookies"
                type="file"
                accept=".txt"
                onChange={(event) => onCookieUpload(event.target.files?.[0] ?? null)}
              />
            </label>
            <button className="ghost-button cookie-clear-button" type="button" onClick={onCookieDelete} disabled={!settings?.cookies_enabled}>
              清除 cookies
            </button>
          </div>
        </div>
      </div>
      <CookieSection cookiesEnabled={Boolean(settings?.cookies_enabled)} />
      {settings && !settings.cookies_enabled && (
        <p className="hint" role="status">
          未配置 cookies 时 YouTube 媒体流 403 概率显著上升，建议先导入 cookies 再下载。
        </p>
      )}
      {duplicateWarning && <p className="hint">这个链接已经在下载历史中出现过。</p>}
      <button className="primary-button" type="submit" disabled={isAnalyzing || !url.trim()}>
        {isAnalyzing ? <Loader2 className="spin" size={18} /> : <Gauge size={18} />}
        解析链接
      </button>
    </form>
  );
}
