import { CheckCircle2, XCircle } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  analyzeUrl,
  batchJobAction,
  createBiliCollection,
  createBiliUpload,
  createJob,
  deleteJob,
  deleteJobItems,
  deleteCookies,
  getBiliPrefill,
  getBiliUploadsSummary,
  getSettings,
  importBrowserCookies,
  listBiliSeasons,
  listJobs,
  openJobFolder,
  openJobItemFolder,
  pauseJob,
  playJobItemVideo,
  playJobVideo,
  restartJob,
  restartJobItem,
  retryBiliSeason,
  retryBiliUpload,
  updateSettings,
  uploadCookies
} from "./api";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { BiliUploadDialog } from "./components/BiliUploadDialog";
import { DownloadOptionsPanel } from "./components/DownloadOptionsPanel";
import { JobQueue } from "./components/JobQueue";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatusPill } from "./components/StatusPill";
import { UrlAnalyzer } from "./components/UrlAnalyzer";
import { browserCookieLockFromError, type BrowserCookieLock } from "./cookieLock";
import { chooseAvailableResolution } from "./quality";
import { effectiveSubtitleSourceForAnalysis } from "./subtitles";
import type {
  AnalyzeResponse,
  BiliGroupMode,
  BiliJobUploads,
  BiliMetadata,
  BiliPrefill,
  BiliSeasonList,
  DownloadOptions,
  Job,
  JobBatchAction,
  Settings
} from "./types";

const INITIAL_OPTIONS: DownloadOptions = {
  mode: "video_subtitles",
  resolution: "1440p",
  format_id: null,
  subtitle_languages: [],
  subtitle_source: "both",
  subtitle_format: "best",
  playlist_items: null,
  write_metadata: false,
  write_thumbnail: false,
  skip_existing: true,
  speed_limit_kbps: null,
  retries: 10,
  notify_on_complete: false
};

/**
 * 徽标里算「跑完了」的档位。
 *
 * `unverified`（投上去了但核对不上）也算跑完 —— 否则这条记录会一直停在「上传中」的沉默里，
 * 而它其实需要用户去看一眼。在途的 `uploading` 不算，避免把「开始」当「结束」播报。
 */
const BILI_SETTLED_BADGES = new Set(["uploaded", "partial", "unverified", "failed"]);

/** 上传表单弹窗的入参：服务端预填的数据 + 点的是哪一条（单视频用）。 */
type BiliDialogState = { prefill: BiliPrefill; isCollection: boolean; itemId: string | null };

/**
 * 应用容器：**只做状态编排、请求发起与布局**。
 *
 * 展示职责一律在 `components/` 下（解析面板、解析结果、下载选项、任务中心、设置、状态点），
 * 纯计算在 `quality.ts` / `subtitles.ts` / `formatting.ts` / `cookieLock.ts`。
 * 判断一段新代码该放哪：**它需要 `useState`/请求吗？** 需要就留在这里（或抽成 hook），
 * 只把 props 变成界面就去 `components/`。
 *
 * 三条容易被改坏的约定：
 * 1. **设置是权威来源**：`applySettings` 回灌后，`options.speed_limit_kbps` / `retries` 跟随设置走，
 *    而不是反过来 —— 这两个字段是运行时设置，不是本次请求的临时值。
 * 2. **每次解析都会重置选择**：`applyAnalysisResult` 用设置里的默认字幕语言覆盖当前选择
 *    （出厂 `["en"]`）。副作用是可能留下一个该视频没有、下拉里也摘不掉的语言，见 ai/ui/README.md。
 * 3. **列表数据以服务端为准**：SSE 每来一条事件就重新拉一次 `/api/jobs`，本地只做乐观增删。
 *
 * B站 上传这一块的额外约定：
 * 4. **徽标由服务端派生，前端只渲染**（`biliUploads[jobId].badge*`）。前端不自己判「算不算已上传」——
 *    那个判据要同时看稿件与合集两条记录，写到前端必然与后端漂移。
 * 5. **「跑完了」的提示靠比对上一次的徽标**，不能只看当前值：`biliUploads` 在每条 SSE 事件后都会
 *    被重拉，不比对就会把「已经是已上传」反复播报一遍。
 */
export default function App() {
  const [url, setUrl] = useState("");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [options, setOptions] = useState<DownloadOptions>(INITIAL_OPTIONS);
  const [selectedItems, setSelectedItems] = useState<Set<number>>(new Set());
  const [settings, setSettings] = useState<Settings | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [browserCookieLock, setBrowserCookieLock] = useState<BrowserCookieLock | null>(null);
  const [history, setHistory] = useState<string[]>(() => JSON.parse(localStorage.getItem("download-history") ?? "[]"));
  const [runtimeSaveMessage, setRuntimeSaveMessage] = useState("");
  const runtimeSaveRequestSeq = useRef(0);
  const runtimeSaveClearTimer = useRef<number | null>(null);
  const [biliUploads, setBiliUploads] = useState<Record<string, BiliJobUploads>>({});
  const [biliDialog, setBiliDialog] = useState<BiliDialogState | null>(null);
  /** 账号下的已有合集。`null` = 还在读；空数组 = 确实没有合集。合集表单才用得到。 */
  const [seasonList, setSeasonList] = useState<BiliSeasonList | null>(null);
  const [biliNotice, setBiliNotice] = useState<string | null>(null);
  /** 上一次观察到的徽标。用来区分「刚刚跑完」与「本来就是这状态」。 */
  const previousBiliBadges = useRef<Record<string, string>>({});

  useEffect(() => {
    void getSettings().then(applySettings).catch((err) => setError(err.message));
    void listJobs().then(setJobs).catch(() => setJobs([]));
    void refreshBiliUploads();
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = () => {
      void listJobs().then(setJobs).catch(() => undefined);
      void refreshBiliUploads();
    };
    return () => source.close();
  }, []);

  // 上传是后台跑的，界面只能靠这条轮询后的比对来给「完成了」的提示。
  // **必须比对上一次的徽标**：`biliUploads` 在每条 SSE 事件后都会被重拉，
  // 不比对就会把「已经是已上传」反复播报一遍。
  useEffect(() => {
    const settled: string[] = [];
    for (const summary of Object.values(biliUploads)) {
      const before = previousBiliBadges.current[summary.job_id];
      if (before && before !== summary.badge && BILI_SETTLED_BADGES.has(summary.badge)) {
        const title = jobs.find((job) => job.id === summary.job_id)?.title || "任务";
        settled.push(`《${title}》${summary.badge_text}`);
      }
    }
    previousBiliBadges.current = Object.fromEntries(
      Object.values(biliUploads).map((summary) => [summary.job_id, summary.badge])
    );
    if (settled.length) setBiliNotice(settled.join("；"));
  }, [biliUploads, jobs]);

  useEffect(() => {
    if (!biliNotice) return;
    const timer = window.setTimeout(() => setBiliNotice(null), 10000);
    return () => window.clearTimeout(timer);
  }, [biliNotice]);

  useEffect(
    () => () => {
      if (runtimeSaveClearTimer.current !== null) {
        window.clearTimeout(runtimeSaveClearTimer.current);
      }
    },
    []
  );

  useEffect(() => {
    setSelectedJobIds((current) => new Set(Array.from(current).filter((jobId) => jobs.some((job) => job.id === jobId))));
  }, [jobs]);

  const subtitleLanguages = useMemo(() => {
    const human = analysis?.subtitles.map((item) => item.language) ?? [];
    const auto = analysis?.automatic_subtitles.map((item) => item.language) ?? [];
    return Array.from(new Set([...human, ...auto])).sort();
  }, [analysis]);

  const duplicateWarning = url.trim().length > 0 && history.includes(url.trim());

  function applySettings(updated: Settings) {
    setSettings(updated);
    setOptions((current) => ({
      ...current,
      speed_limit_kbps: updated.default_speed_limit_kbps,
      retries: updated.default_retries
    }));
  }

  function applyAnalysisResult(result: AnalyzeResponse) {
    setAnalysis(result);
    setSelectedItems(new Set(result.entries.map((entry) => entry.index)));
    setOptions((current) => ({
      ...current,
      resolution: chooseAvailableResolution(result, settings?.default_resolution ?? current.resolution),
      format_id: null,
      subtitle_languages: settings?.default_subtitle_languages ?? current.subtitle_languages
    }));
    setBrowserCookieLock(null);
  }

  async function runAnalyze(targetUrl: string) {
    applyAnalysisResult(await analyzeUrl(targetUrl));
  }

  function handleAppError(err: unknown, fallback: string, pendingAnalyzeUrl: string | null = null) {
    const lock = browserCookieLockFromError(err, pendingAnalyzeUrl);
    if (lock) {
      setBrowserCookieLock(lock);
      setError(lock.message);
      return;
    }
    setError(err instanceof Error ? err.message : fallback);
  }

  async function handleAnalyze(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsAnalyzing(true);
    const targetUrl = url.trim();
    try {
      await runAnalyze(targetUrl);
    } catch (err) {
      handleAppError(err, "解析失败", targetUrl);
    } finally {
      setIsAnalyzing(false);
    }
  }

  async function handleCreateJob() {
    if (!analysis) return;
    setError(null);
    setIsSubmitting(true);
    const playlistItems = analysis.is_playlist ? Array.from(selectedItems).sort((a, b) => a - b) : null;
    try {
      const job = await createJob(analysis.url, {
        ...options,
        format_id: null,
        subtitle_source: effectiveSubtitleSourceForAnalysis(analysis, options.subtitle_source),
        playlist_items: playlistItems
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      const nextHistory = Array.from(new Set([analysis.url, ...history])).slice(0, 20);
      setHistory(nextHistory);
      localStorage.setItem("download-history", JSON.stringify(nextHistory));
    } catch (err) {
      handleAppError(err, "创建任务失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  function updateOption<K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) {
    setOptions((current) => ({ ...current, [key]: value }));
  }

  async function updateRuntimeDownloadOption<K extends "speed_limit_kbps" | "retries">(key: K, value: DownloadOptions[K]) {
    const requestSeq = runtimeSaveRequestSeq.current + 1;
    runtimeSaveRequestSeq.current = requestSeq;
    if (runtimeSaveClearTimer.current !== null) {
      window.clearTimeout(runtimeSaveClearTimer.current);
      runtimeSaveClearTimer.current = null;
    }
    updateOption(key, value);
    setRuntimeSaveMessage("保存中...");
    try {
      const updated = await updateSettings(
        key === "speed_limit_kbps"
          ? { default_speed_limit_kbps: value as number | null }
          : { default_retries: value as number }
      );
      if (runtimeSaveRequestSeq.current === requestSeq) {
        applySettings(updated);
        setRuntimeSaveMessage("已保存");
      }
    } catch (err) {
      if (runtimeSaveRequestSeq.current === requestSeq) {
        setRuntimeSaveMessage("保存失败");
      }
      throw err;
    } finally {
      if (runtimeSaveRequestSeq.current === requestSeq) {
        runtimeSaveClearTimer.current = window.setTimeout(() => {
          setRuntimeSaveMessage("");
          runtimeSaveClearTimer.current = null;
        }, 1800);
      }
    }
  }

  function updateQuality(resolution: string) {
    setOptions((current) => ({ ...current, resolution, format_id: null }));
  }

  async function handleCookieUpload(file: File | null) {
    if (!file) return;
    await uploadCookies(file);
    applySettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleCookieDelete() {
    await deleteCookies();
    applySettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleBrowserCookieImport(browser: string, closeBrowserIfLocked = false) {
    await importBrowserCookies(browser, closeBrowserIfLocked);
    applySettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleLockedBrowserCookieImport() {
    if (!browserCookieLock) return;
    const confirmed = window.confirm("将关闭所有 Edge 窗口以释放 cookies 数据库，然后重新导入。是否继续？");
    if (!confirmed) return;
    const pendingAnalyzeUrl = browserCookieLock.pendingAnalyzeUrl;
    setError(null);
    await handleBrowserCookieImport("edge", true);
    if (!pendingAnalyzeUrl) return;
    setIsAnalyzing(true);
    try {
      await runAnalyze(pendingAnalyzeUrl);
    } catch (err) {
      handleAppError(err, "解析失败", pendingAnalyzeUrl);
    } finally {
      setIsAnalyzing(false);
    }
  }

  // ---------------------------------------------------------------- B站 上传
  /**
   * 重拉 B站 徽标。**失败时不弹错误条**：这条请求只负责给按钮上色，拿不到就退回「未上传」，
   * 不该打扰正在跑的下载主流程。
   */
  async function refreshBiliUploads() {
    try {
      const summary = await getBiliUploadsSummary();
      setBiliUploads(Object.fromEntries(summary.jobs.map((entry) => [entry.job_id, entry])));
    } catch {
      // 见上：静默退回「未上传」。
    }
  }

  /**
   * 打开上传窗口。`itemId` 为空 = 整个任务（单视频任务 / 合集流程），
   * 由服务端在 `prefill.kind` 里告诉我们该走哪种表单 —— **不在前端按条目数自己判断**。
   */
  async function handleBiliUpload(jobId: string, itemId: string | null) {
    setError(null);
    setBiliNotice(null);
    // **合集列表在开窗之前就开始读**，而不是等弹窗渲染完再取：
    // 弹窗一出现就该能看到能选哪些合集，等它自己加载会先闪一下空下拉。
    // 读失败**不阻止开窗** —— 用户可能只是要投稿不归集，不该因为一个可选
    // 功能读不到就打不开表单。`seasonList` 会带着原因进去，界面上会显示。
    setSeasonList(null);
    const prefillPromise = getBiliPrefill(jobId, itemId);
    const seasonsPromise = listBiliSeasons().catch(() => null);
    try {
      const prefill = await prefillPromise;
      if (prefill.kind === "collection") {
        void seasonsPromise.then((list) => setSeasonList(list)).catch(() => undefined);
      }
      setBiliDialog({ prefill, isCollection: prefill.kind === "collection", itemId });
    } catch (err) {
      setError(biliMessage(err, "打不开上传窗口"));
    }
  }

  /**
   * 提交上传。**异常直接抛回弹窗**（在表单里就近报错），不写 `setError` ——
   * 用户需要一边看着自己填的内容一边改，而不是去页顶找红条。
   */
  async function handleBiliSubmit(values: {
    metadata: BiliMetadata;
    makeCover: boolean;
    comment: string;
    groupMode: BiliGroupMode;
    seasonId: number | null;
    seasonTitle: string;
  }) {
    if (!biliDialog) return;
    const { prefill, isCollection, itemId } = biliDialog;
    let result: BiliJobUploads;
    try {
      result = isCollection
        // 合集名**用用户在表单里填的那个**（弹窗预填成任务名，所以不改就是任务名），
        // 简介没有输入框、仍用服务端给的值（与后端 `season_title or job.title` 同口径）。
        // 「加入已有合集」时这两个值不参与归集 —— 用的是平台上那个合集自己的名字，照传无害。
        ? await createBiliCollection(
            prefill.job_id,
            values.metadata,
            values.seasonTitle || prefill.job_title,
            prefill.season_desc,
            values.makeCover,
            values.comment,
            values.groupMode,
            values.seasonId
          )
        : await createBiliUpload(prefill.job_id, itemId, values.metadata, values.makeCover, values.comment);
    } catch (err) {
      throw new Error(biliMessage(err, "提交失败"));
    }
    setBiliUploads((current) => ({ ...current, [result.job_id]: result }));
    setBiliDialog(null);
    setSeasonList(null);
    setBiliNotice(
      isCollection
        ? values.groupMode === "none"
          ? `已开始上传《${prefill.job_title}》的 ${prefill.items.length} 个视频，不归集。`
          : values.groupMode === "existing"
            ? `已开始上传《${prefill.job_title}》的 ${prefill.items.length} 个视频，跑完会加进你选的合集。`
            : `已开始上传《${prefill.job_title}》的 ${prefill.items.length} 个视频，逐个投完后会自动归入同一个合集。`
        : `已开始上传《${values.metadata.title}》，跑完这里会变成「已上传」。`
    );
  }

  async function handleBiliRetryUpload(uploadId: string) {
    try {
      setBiliNotice((await retryBiliUpload(uploadId)).message);
      await refreshBiliUploads();
    } catch (err) {
      setError(biliMessage(err, "重试失败"));
    }
  }

  async function handleBiliRetrySeason(seasonRecordId: string) {
    try {
      setBiliNotice((await retryBiliSeason(seasonRecordId)).message);
      await refreshBiliUploads();
    } catch (err) {
      setError(biliMessage(err, "重试归集失败"));
    }
  }

  function updateJobInList(job: Job) {
    setJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
  }

  function toggleJobSelection(jobId: string) {
    setSelectedJobIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }

  async function handlePauseJob(jobId: string) {
    updateJobInList(await pauseJob(jobId));
  }

  async function handleRestartJob(jobId: string, resolution?: string) {
    updateJobInList(await restartJob(jobId, resolution));
  }

  async function handleRestartJobItem(jobId: string, itemId: string, resolution?: string) {
    updateJobInList(await restartJobItem(jobId, itemId, resolution));
  }

  async function handleDeleteJob(jobId: string, deleteFiles = false) {
    if (deleteFiles && !window.confirm("将删除该任务记录及其已下载的视频、字幕、metadata、缩略图和 description 等相关文件。是否继续？")) {
      return;
    }
    await deleteJob(jobId, deleteFiles);
    setJobs((current) => current.filter((job) => job.id !== jobId));
  }

  async function handleDeleteJobItems(jobId: string, itemIds: string[], deleteFiles = false) {
    if (!itemIds.length) return;
    if (deleteFiles && !window.confirm("将删除所选视频任务记录及其已下载的视频、字幕、metadata、缩略图和 description 等相关文件。是否继续？")) {
      return;
    }
    const response = await deleteJobItems(jobId, itemIds, deleteFiles);
    if (response.job_deleted) {
      setJobs((current) => current.filter((job) => job.id !== jobId));
      setSelectedJobIds((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
      return;
    }
    if (response.job) {
      updateJobInList(response.job);
    }
  }

  async function handleBatchAction(action: JobBatchAction, deleteFiles = false) {
    const jobIds = Array.from(selectedJobIds);
    if (!jobIds.length) return;
    if (action === "delete" && deleteFiles && !window.confirm("将删除所选任务记录及其已下载的视频、字幕、metadata、缩略图和 description 等相关文件。是否继续？")) {
      return;
    }
    const response = await batchJobAction(action, jobIds, action === "delete" ? deleteFiles : false);
    if (action === "delete") {
      const deleted = new Set(response.affected_job_ids);
      setJobs((current) => current.filter((job) => !deleted.has(job.id)));
      setSelectedJobIds(new Set());
      return;
    }
    setJobs((current) =>
      current.map((job) => response.jobs.find((updatedJob) => updatedJob.id === job.id) ?? job)
    );
  }

  async function handleCopySourceLink(sourceUrl: string) {
    try {
      const clipboard = navigator.clipboard;
      if (!clipboard?.writeText) {
        throw new Error("当前浏览器不支持剪贴板写入。");
      }
      await clipboard.writeText(sourceUrl);
    } catch (err) {
      const message = err instanceof Error ? err.message : "复制链接失败。";
      setError(message);
      throw err;
    }
  }

  /** 打开一个外部网页（原视频页、B站 稿件页）。同一个动作，只写一处 —— `noopener` 策略要一致。 */
  function openExternalPage(url: string) {
    window.open(url, "_blank", "noopener,noreferrer");
  }

  return (
    <main className="app-shell">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>YouTube Downloader</h1>
            <p>本机视频、playlist 和字幕下载控制台</p>
          </div>
          <div className="status-strip">
            <StatusPill ok={settings?.ffmpeg?.ffmpeg} label="ffmpeg" />
            <StatusPill ok={settings?.cookies_enabled} label="cookies" />
          </div>
        </header>

        {error && (
          <div className="alert" role="alert">
            <XCircle size={18} />
            {error}
          </div>
        )}

        {biliNotice && (
          <div className="notice" role="status">
            <CheckCircle2 size={18} />
            {biliNotice}
          </div>
        )}

        <section className="grid">
          <div className="primary-column">
            <UrlAnalyzer
              settings={settings}
              url={url}
              duplicateWarning={duplicateWarning}
              browserCookieLock={browserCookieLock}
              isAnalyzing={isAnalyzing}
              onAnalyze={handleAnalyze}
              onBrowserCookieImport={(browser) => handleBrowserCookieImport(browser).catch((err) => handleAppError(err, "导入 cookies 失败"))}
              onCookieDelete={() => void handleCookieDelete().catch((err) => handleAppError(err, "清除 cookies 失败"))}
              onCookieUpload={(file) => void handleCookieUpload(file).catch((err) => handleAppError(err, "上传 cookies 失败"))}
              onLockedBrowserCookieImport={() =>
                void handleLockedBrowserCookieImport().catch((err) =>
                  handleAppError(err, "导入 cookies 失败", browserCookieLock?.pendingAnalyzeUrl ?? null)
                )
              }
              onUrlChange={setUrl}
            />
            {analysis && (
              <AnalysisPanel
                analysis={analysis}
                options={options}
                selectedItems={selectedItems}
                setSelectedItems={setSelectedItems}
              />
            )}
            <JobQueue
              biliUploads={biliUploads}
              jobs={jobs}
              selectedJobIds={selectedJobIds}
              onBatchAction={(action, deleteFiles) => void handleBatchAction(action, deleteFiles).catch((err) => setError(err.message))}
              onBiliRetrySeason={(seasonRecordId) => void handleBiliRetrySeason(seasonRecordId)}
              onBiliRetryUpload={(uploadId) => void handleBiliRetryUpload(uploadId)}
              onBiliUpload={(jobId, itemId) => void handleBiliUpload(jobId, itemId)}
              onDelete={(jobId, deleteFiles) => void handleDeleteJob(jobId, deleteFiles).catch((err) => setError(err.message))}
              onDeleteItems={(jobId, itemIds, deleteFiles) =>
                void handleDeleteJobItems(jobId, itemIds, deleteFiles).catch((err) => setError(err.message))
              }
              onCopyLink={(sourceUrl) => handleCopySourceLink(sourceUrl)}
              onPause={(jobId) => void handlePauseJob(jobId).catch((err) => setError(err.message))}
              onOpenFolder={(jobId) => openJobFolder(jobId)}
              onOpenItemFolder={(jobId, itemId) => openJobItemFolder(jobId, itemId)}
              onOpenSourcePage={openExternalPage}
              onOpenVideoPage={openExternalPage}
              onPlay={(jobId) => playJobVideo(jobId)}
              onPlayItem={(jobId, itemId) => playJobItemVideo(jobId, itemId)}
              onRestart={(jobId, resolution) => void handleRestartJob(jobId, resolution).catch((err) => setError(err.message))}
              onRestartItem={(jobId, itemId, resolution) => void handleRestartJobItem(jobId, itemId, resolution).catch((err) => setError(err.message))}
              onToggleJobSelection={toggleJobSelection}
            />
          </div>

          <aside className="side-column">
            <DownloadOptionsPanel
              analysis={analysis}
              ffmpegAvailable={Boolean(settings?.ffmpeg?.ffmpeg)}
              options={options}
              subtitleLanguages={subtitleLanguages}
              isSubmitting={isSubmitting}
              onCreateJob={handleCreateJob}
              onOptionChange={updateOption}
              onQualityChange={updateQuality}
              runtimeSaveMessage={runtimeSaveMessage}
              onRuntimeOptionChange={(key, value) =>
                void updateRuntimeDownloadOption(key, value).catch((err) => handleAppError(err, "保存下载设置失败"))
              }
            />
            {settings && <SettingsPanel settings={settings} onSettingsChange={applySettings} onError={setError} />}
          </aside>
        </section>
      </section>
      {biliDialog && (
        <BiliUploadDialog
          prefill={biliDialog.prefill}
          isCollection={biliDialog.isCollection}
          seasons={seasonList}
          onCancel={() => {
            setBiliDialog(null);
            setSeasonList(null);
          }}
          onSubmit={handleBiliSubmit}
        />
      )}
    </main>
  );
}

/**
 * 把接口错误拼成一句人话。
 *
 * B站 上传的 409（前置条件不满足：没装投稿工具、没登录）会在 `detail` 里一起给出 `advice`，
 * 只显示 `message` 等于把「下一步做什么」丢掉 —— 那是本功能的验收口径之一。
 */
function biliMessage(err: unknown, fallback: string): string {
  const message = err instanceof Error ? err.message : fallback;
  if (!(err instanceof ApiError) || !err.detail || typeof err.detail !== "object") {
    return message;
  }
  const advice = err.detail.advice?.trim();
  return advice ? `${message} —— ${advice}` : message;
}
