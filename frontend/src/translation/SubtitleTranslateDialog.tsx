import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import type { TranslationCue, TranslationJob } from "../contracts/types";
import {
  cancelTranslation,
  editTranslationCue,
  exportTranslation,
  getTranslation,
  retryTranslation,
  setTranslationMode,
  startTranslation,
  uploadBiliTranslation
} from "./translation";

/**
 * 字幕翻译抽屉：打开即开始翻译，逐条实时填入，可即时校对。
 *
 * 三条刻意的选择：
 * 1. **没有向导**：入口属于某一条字幕，弹窗里只做这一件事；多一步确认就是多一次流失。
 * 2. **原文只读、译文可编辑**：原文是证据不是产物；人工修订在后端被锁定，重跑不覆盖。
 * 3. **轮询 + SSE 双保险**：SSE 事件让进度立刻刷新，轮询保证事件丢了也能收敛（
 *   浏览器重连、后端刚重启都可能丢事件）。
 */

type Filter = "all" | "failed" | "edited";

export function SubtitleTranslateDialog({
  jobId,
  itemId,
  title,
  refreshToken,
  onClose
}: {
  jobId: string;
  itemId: string | null;
  title: string;
  refreshToken: number;
  onClose: () => void;
}) {
  const [job, setJob] = useState<TranslationJob | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [uploadNote, setUploadNote] = useState("");
  // 切模式后既有导出文件是旧排版，必须重新导出才能上传。
  const [modeDirty, setModeDirty] = useState(false);
  const jobIdRef = useRef("");

  useEffect(() => {
    let alive = true;
    startTranslation(jobId, itemId)
      .then((created) => {
        if (!alive) return;
        jobIdRef.current = created.id;
        setJob(created);
      })
      .catch((err: Error) => setError(err.message));
    return () => {
      alive = false;
    };
  }, [jobId, itemId]);

  useEffect(() => {
    if (!jobIdRef.current || !job || !["queued", "running"].includes(job.state)) return;
    const timer = window.setInterval(() => {
      getTranslation(jobIdRef.current)
        .then(setJob)
        .catch(() => undefined);
    }, 700);
    return () => window.clearInterval(timer);
  }, [job?.state]);

  useEffect(() => {
    if (!jobIdRef.current || !refreshToken) return;
    getTranslation(jobIdRef.current).then(setJob).catch(() => undefined);
  }, [refreshToken]);

  const active = job ? ["queued", "running"].includes(job.state) : false;
  const visible = useMemo(() => {
    if (!job) return [];
    return job.cues.filter((cue) => {
      if (filter === "failed" && cue.state !== "failed") return false;
      if (filter === "edited" && !cue.edited) return false;
      if (!query.trim()) return true;
      const needle = query.trim().toLowerCase();
      return cue.source.toLowerCase().includes(needle) || cue.translation.toLowerCase().includes(needle);
    });
  }, [job, filter, query]);

  async function run(action: string, work: () => Promise<unknown>) {
    setBusy(action);
    setError("");
    try {
      const result = await work();
      if (result && typeof result === "object" && "cues" in result) {
        setJob(result as TranslationJob);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function saveCue(cue: TranslationCue, text: string) {
    if (!job || text === cue.translation) return;
    await run("保存译文", () => editTranslationCue(job.id, cue.index, text));
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal translation-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="字幕翻译"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <div className="modal-heading">
            <h2>字幕翻译</h2>
            <p>{title}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭字幕翻译">
            ✕
          </button>
        </header>

        <div className="modal-body translation-body">
          {!job && !error && <p className="translation-hint">正在准备字幕…</p>}
          {error && <p className="translation-error" role="alert">{error}</p>}
          {job && (
            <>
              <div className="translation-meta">
                <span>源：{job.source_language || "未知语言"}</span>
                <span>目标：简体中文（zh）</span>
                <span>{job.total} 条</span>
                <span className={`translation-state is-${job.state}`}>{STATE_LABEL[job.state] || job.state}</span>
              </div>
              <div className="translation-mode" role="group" aria-label="呈现方式">
                <span>呈现方式</span>
                <button
                  type="button"
                  aria-pressed={job.display_mode === "zh"}
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void run("切换呈现", async () => {
                      setModeDirty(true);
                      setUploadNote("");
                      return setTranslationMode(job.id, "zh");
                    })
                  }
                >
                  仅中文
                </button>
                <button
                  type="button"
                  aria-pressed={job.display_mode === "bilingual"}
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void run("切换呈现", async () => {
                      setModeDirty(true);
                      setUploadNote("");
                      return setTranslationMode(job.id, "bilingual");
                    })
                  }
                >
                  中英双语（同屏）
                </button>
                {modeDirty && <span className="translation-mode-hint">排版已变更，请重新导出后再上传</span>}
              </div>
              <div className="translation-progress" aria-label="翻译进度">
                <div className="translation-progress-bar">
                  <span style={{ width: `${job.total ? (job.translated / job.total) * 100 : 0}%` }} />
                </div>
                <span>
                  {job.translated} / {job.total} 条
                  {job.failed ? ` · ${job.failed} 条失败` : ""}
                </span>
              </div>
              {job.error && <p className="translation-error">{job.error}</p>}
              {active && !job.error && job.translated === 0 && job.failed === 0 && (
                <div className="translation-booting" role="status" aria-live="polite">
                  <Loader2 size={18} className="translation-spinner" aria-hidden="true" />
                  <div>
                    <strong>正在启动本机模型服务…</strong>
                    <span>首次约需十几秒，随后自动开始翻译，无需任何操作</span>
                  </div>
                </div>
              )}

              <div className="translation-tools">
                <button
                  type="button"
                  className="ghost-button"
                  disabled={!job.failed || Boolean(busy) || active}
                  onClick={() => void run("重试失败项", () => retryTranslation(job.id))}
                >
                  重试失败项
                </button>
                {active && (
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={Boolean(busy)}
                    onClick={() => void run("取消", () => cancelTranslation(job.id))}
                  >
                    取消
                  </button>
                )}
                <label className="translation-filter">
                  筛选
                  <select value={filter} onChange={(event) => setFilter(event.target.value as Filter)}>
                    <option value="all">全部</option>
                    <option value="failed">仅看失败</option>
                    <option value="edited">仅看已改</option>
                  </select>
                </label>
                <input
                  className="translation-search"
                  placeholder="搜索原文或译文"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>

              <div className="translation-cues">
                {visible.length === 0 && <p className="translation-hint">没有符合条件的条目。</p>}
                {visible.map((cue) => (
                  <CueRow
                    key={cue.index}
                    cue={cue}
                    bilingual={job.display_mode === "bilingual"}
                    disabled={Boolean(busy)}
                    onSave={saveCue}
                  />
                ))}
              </div>
            </>
          )}
        </div>

        <footer className="modal-footer">
          <button type="button" className="ghost-button" onClick={onClose}>关闭</button>
          {job?.display_mode === "bilingual" && (
            <span className="translation-mode-hint">同一 zh 轨是覆盖，不会多一条轨</span>
          )}
          {uploadNote && <span className="translation-upload-note">{uploadNote}</span>}
          <button
            type="button"
            className="ghost-button"
            disabled={!job || active || busy !== ""}
            onClick={() =>
              void run("导出", async () => {
                await exportTranslation(job!.id);
                setModeDirty(false);
              })
            }
          >
            {job?.display_mode === "bilingual" ? "导出双语 SRT / BCC" : "导出中文 + 对照 SRT"}
          </button>
          <button
            type="button"
            className="primary-button"
            title={modeDirty ? "排版已变更，上传前会自动重新导出" : "把中文字幕作为 CC 轨传到 B站"}
            disabled={!job || active || busy !== ""}
            onClick={() =>
              void run("上传中文字幕", async () => {
                // 「导出」是本地留存的 optional 动作，不该成为上传的前置门槛。
                // 但模式切换后磁盘上还是旧排版，必须在上传前重导一次。
                if (modeDirty || !job!.output_path) {
                  await exportTranslation(job!.id);
                  setModeDirty(false);
                }
                const result = await uploadBiliTranslation(job!.id);
                setUploadNote(
                  result.translated_subtitle_state === "posted"
                    ? "中文字幕已挂到 B站（zh 轨，与原字幕并存）"
                    : result.translated_subtitle_message
                );
              })
            }
          >
            上传中文字幕到 B站
          </button>
        </footer>
      </section>
    </div>
  );
}

const STATE_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "翻译中",
  succeeded: "已完成",
  failed: "部分失败",
  cancelled: "已取消"
};

function CueRow({
  cue,
  bilingual,
  disabled,
  onSave
}: {
  cue: TranslationCue;
  bilingual: boolean;
  disabled: boolean;
  onSave: (cue: TranslationCue, text: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(cue.translation);
  useEffect(() => setDraft(cue.translation), [cue.translation]);
  return (
    <article className={`translation-cue ${cue.state === "failed" ? "is-failed" : ""}`}>
      <div className="translation-cue-head">
        <span className="translation-time">
          {formatTime(cue.start)} → {formatTime(cue.end)}
        </span>
        <span className="translation-index">#{cue.index + 1}</span>
        {cue.edited && <span className="translation-edited">已改</span>}
        {cue.state === "failed" && <span className="translation-failed">翻译失败</span>}
      </div>
      <p className="translation-source">{cue.source}</p>
      {bilingual && cue.translation && (
        <p className="translation-bilingual-preview" aria-label={`B站 双语预览 ${cue.index + 1}`}>
          {`${cue.source.replace(/\s+/g, " ").trim()}\n${cue.translation.replace(/\s+/g, " ").trim()}`}
        </p>
      )}
      <textarea
        aria-label={`第 ${cue.index + 1} 条译文`}
        className="translation-target"
        value={draft}
        rows={2}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => void onSave(cue, draft)}
      />
      {cue.error && <p className="translation-cue-error">{cue.error}</p>}
    </article>
  );
}

function formatTime(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}
