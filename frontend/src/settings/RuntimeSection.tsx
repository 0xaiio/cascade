import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2, RefreshCw } from "lucide-react";
import { getDiagnostics, getSettings, refreshRuntimeDiagnostics } from "../shared/api";
import type { Diagnostics, Settings } from "../contracts/types";

/**
 * 运行环境自检：JS 运行时（解析 YouTube 的 `n` 参数要它）与本机日志位置。
 *
 * 它**不属于代理**。原先这段代码长在 `ProxySection.tsx` 里，因为两者都由
 * `GET /api/diagnostics` 供数 —— 那是数据的巧合，不是功能的归属：代理回答「能不能连出去」，
 * 这里回答「这台机器上的组件齐不齐」。设置面板分区后它单独成组（[ai/ui/010]），
 * 同一个接口不妨碍拆成两个组件。
 *
 * [ai/ui/010]: ../../../ai/ui/010-settings-panel-is-a-flat-list.md
 */

function CopyableCommand({ command, label }: { command: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <span className="command-row">
      <code className="command-code">{command}</code>
      <button className="ghost-button command-copy" type="button" onClick={() => void copy()}>
        <Copy size={14} />
        {copied ? "已复制" : (label ?? "复制")}
      </button>
    </span>
  );
}

export function RuntimeSection({ onSettingsChange }: { onSettingsChange: (settings: Settings) => void }) {
  const [runtime, setRuntime] = useState<Diagnostics | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void getDiagnostics()
      .then(setRuntime)
      .catch(() => setRuntime(null));
  }, []);

  async function rerun() {
    setBusy(true);
    setError("");
    try {
      setRuntime(await refreshRuntimeDiagnostics());
      // 重新探测可能改变后端对可用能力的判断（例如刚装好 ffmpeg / Node），
      // 把权威设置回灌一次，免得面板上还写着旧结论。
      onSettingsChange(await getSettings());
    } catch (err) {
      setError(err instanceof Error ? err.message : "自检失败");
    } finally {
      setBusy(false);
    }
  }

  const jsRuntimeReady = runtime?.dependencies?.js_runtime === true;
  const jsRuntimeError = typeof runtime?.dependencies?.js_runtime_error === "string" ? runtime.dependencies.js_runtime_error : null;

  return (
    <div className="runtime-section">
      <div className={`runtime-block ${jsRuntimeReady ? "is-ok" : "is-warn"}`}>
        <span className="runtime-head">
          {jsRuntimeReady ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：{jsRuntimeReady ? "可用" : "不可用"}
        </span>
        {jsRuntimeReady ? (
          <p className="hint">
            {String(runtime?.dependencies?.js_runtime_name ?? "")}　{runtime?.dependencies?.js_runtime_path as string}　
            v{runtime?.dependencies?.js_runtime_version as string}
          </p>
        ) : (
          <>
            <p className="hint">
              装一个 Node 18+ 或 Deno 即可。装完点「重新自检」。
              这个组件不可用时，最典型的症状是下载报 <code>The page needs to be reloaded.</code>，而不是给出任何有用的提示。
            </p>
            {jsRuntimeError && <p className="diag-error">自检报错：{jsRuntimeError}</p>}
          </>
        )}
        {runtime?.sanitized_environment && runtime.sanitized_environment.length > 0 && (
          <p className="hint">
            启动时已自动摘除会打坏 JS 运行时的宿主环境变量：
            {runtime.sanitized_environment.map((item) => `${item.name}（${item.reason.split("：")[0]}）`).join("、")}
          </p>
        )}
        <div className="diag-actions">
          <button className="ghost-button" type="button" onClick={() => void rerun()} disabled={busy}>
            {busy ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
            重新自检
          </button>
        </div>
        {runtime?.log_file && (
          <p className="hint">
            日志文件：<code>{runtime.log_file}</code> —— 下载失败时先看这个文件的最后几十行。
            <br />
            <CopyableCommand command={runtime.log_file} label="复制路径" />
          </p>
        )}
      </div>
      {error && <p className="diag-error">{error}</p>}
    </div>
  );
}
