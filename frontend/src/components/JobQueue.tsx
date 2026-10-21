import { useEffect, useState } from "react";
import { Check, ChevronDown, Copy, ExternalLink, FileX2, FolderOpen, Gauge, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import type { BiliJobUploads, BiliUpload, Job, JobBatchAction, ResolutionFallback } from "../types";
import { retryableUploadIds, uploadedTargetUrl } from "../biliStatus";
import {
  formatBytesPerSecond,
  formatClock,
  formatDateTime,
  formatFileSize,
  formatItemResolution,
  formatMediaDuration,
  formatPercent
} from "../formatting";
import { resolutionFallbackRestartLabel } from "../quality";
import { BiliJobStatus } from "./BiliJobStatus";
import { BiliUploadButton } from "./BiliUploadButton";
import { SubtitleOpenButton } from "./SubtitleOpenButton";

type MaybePromise = void | Promise<void>;

/** 没有 B站 记录时的默认形态：按钮自己会把它显示成「上传」。 */
const IDLE_BADGE = { badge: "none", badgeText: "", badgeTone: "idle" } as const;

export function JobQueue({
  biliUploads,
  jobs,
  selectedJobIds,
  onBatchAction,
  onBiliRetrySeason,
  onBiliRetryUpload,
  onBiliRetryMany,
  onBiliUpload,
  onCopyLink,
  onDelete,
  onDeleteItems,
  onOpenFolder,
  onOpenItemFolder,
  onOpenSourcePage,
  onOpenSubtitle,
  onOpenVideoPage,
  onPause,
  onPlay,
  onPlayItem,
  onRestart,
  onRestartItem,
  onToggleJobSelection
}: {
  /** 有 B站 记录的任务的徽标（缺省 = 未上传）。**由后端派生**，前端只渲染。 */
  biliUploads: Record<string, BiliJobUploads>;
  jobs: Job[];
  selectedJobIds: Set<string>;
  onBatchAction: (action: JobBatchAction, deleteFiles?: boolean) => void;
  onBiliRetrySeason: (seasonRecordId: string) => void;
  onBiliRetryUpload: (uploadId: string) => void;
  /**
   * 一次补多条：`unverified`（只核对）与 `partial`（有的上去了有的没上去）两档的按钮走这里。
   * **逐条交给服务端按各自终态决定动作**，前端不替它判断哪条该重投、哪条只该核对。
   */
  onBiliRetryMany: (uploadIds: string[]) => void;
  /** `itemId` 为空 = 上传整个任务（单视频任务 / 合集流程）。 */
  onBiliUpload: (jobId: string, itemId: string | null) => void;
  onCopyLink: (sourceUrl: string) => Promise<void>;
  onDelete: (jobId: string, deleteFiles?: boolean) => void;
  onDeleteItems: (jobId: string, itemIds: string[], deleteFiles?: boolean) => void;
  onOpenFolder: (jobId: string) => MaybePromise;
  onOpenItemFolder: (jobId: string, itemId: string) => MaybePromise;
  onOpenSourcePage: (sourceUrl: string) => void;
  /**
   * 打开字幕：`itemId` 为空 = 单视频任务（走任务级接口），否则是合集里的某一条。
   * 动作只有一个 —— 弹系统的「打开方式」，见 `SubtitleOpenButton`。
   */
  onOpenSubtitle: (jobId: string, itemId: string | null) => MaybePromise;
  onOpenVideoPage: (url: string) => void;
  onPause: (jobId: string) => void;
  onPlay: (jobId: string) => MaybePromise;
  onPlayItem: (jobId: string, itemId: string) => MaybePromise;
  onRestart: (jobId: string, resolution?: string) => void;
  onRestartItem: (jobId: string, itemId: string, resolution?: string) => void;
  onToggleJobSelection: (jobId: string) => void;
}) {
  const selectedCount = selectedJobIds.size;
  const [expandedJobIds, setExpandedJobIds] = useState<Record<string, boolean>>({});
  const [selectedItemIdsByJob, setSelectedItemIdsByJob] = useState<Record<string, Set<string>>>({});
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [localActionError, setLocalActionError] = useState<{ key: string; message: string } | null>(null);

  useEffect(() => {
    setExpandedJobIds((current) => {
      const availableJobIds = new Set(jobs.map((job) => job.id));
      const next = Object.fromEntries(Object.entries(current).filter(([jobId]) => availableJobIds.has(jobId)));
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
    setSelectedItemIdsByJob((current) => {
      const availableItems = new Map(jobs.map((job) => [job.id, new Set(job.items.map((item) => item.id))]));
      const next: Record<string, Set<string>> = {};
      for (const [jobId, itemIds] of Object.entries(current)) {
        const available = availableItems.get(jobId);
        if (!available) continue;
        const kept = new Set(Array.from(itemIds).filter((itemId) => available.has(itemId)));
        if (kept.size) next[jobId] = kept;
      }
      return next;
    });
  }, [jobs]);

  function toggleExpanded(jobId: string, isExpanded: boolean) {
    setExpandedJobIds((current) => ({ ...current, [jobId]: !isExpanded }));
  }

  function toggleItemSelection(jobId: string, itemId: string) {
    setSelectedItemIdsByJob((current) => {
      const next = { ...current };
      const selected = new Set(next[jobId] ?? []);
      if (selected.has(itemId)) selected.delete(itemId);
      else selected.add(itemId);
      if (selected.size) next[jobId] = selected;
      else delete next[jobId];
      return next;
    });
  }

  async function copySourceLink(key: string, sourceUrl: string) {
    try {
      await onCopyLink(sourceUrl);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((current) => (current === key ? null : current)), 1500);
    } catch {
      // The parent surfaces clipboard failures in the shared alert area.
    }
  }

  function runLocalFileAction(key: string, action: () => MaybePromise) {
    setLocalActionError(null);
    try {
      Promise.resolve(action()).catch((error) => {
        setLocalActionError({ key, message: localActionErrorMessage(error) });
      });
    } catch (error) {
      setLocalActionError({ key, message: localActionErrorMessage(error) });
    }
  }

  return (
    <section className="panel">
      <div className="panel-title job-title-row">
        <div className="job-title-main">
          <Gauge size={20} />
          <h2>任务中心</h2>
        </div>
        <span className="job-count-badge">{jobs.length ? `${jobs.length} 个任务` : "暂无任务"}</span>
      </div>
      {selectedCount > 0 && (
        <div className="batch-toolbar" aria-label="批量任务操作">
          <span>{selectedCount} 个已选择</span>
          <button type="button" className="ghost-button" onClick={() => onBatchAction("pause")}>
            <Pause size={16} />
            批量暂停
          </button>
          <button type="button" className="ghost-button" onClick={() => onBatchAction("restart")}>
            <RotateCcw size={16} />
            批量重启
          </button>
          <button type="button" className="ghost-button danger" onClick={() => onBatchAction("delete", false)}>
            <Trash2 size={16} />
            批量删除任务
          </button>
          <button type="button" className="ghost-button danger" onClick={() => onBatchAction("delete", true)}>
            <FileX2 size={16} />
            批量删除任务和已下载文件
          </button>
        </div>
      )}
      <div className="job-list">
        {jobs.map((job) => {
          const title = job.title || "未命名任务";
          const isPlaylist = job.total_items > 1;
          const primaryItem = job.items[0] ?? null;
          const canPlayJob = !isPlaylist && Boolean(primaryItem?.output_path);
          const canOpenSingleFolder = !isPlaylist && Boolean(primaryItem?.output_path || job.download_dir);
          // 字幕按钮的可点性来自**后端算好的** `subtitle_path`（真的存在才给值）：
          // 能点却点了 409 的按钮，比灰着的按钮更让人困惑。
          const canOpenSubtitle = !isPlaylist && Boolean(primaryItem?.subtitle_path);
          const canOpenPlaylistFolder = isPlaylist && Boolean(job.download_dir);
          const jobCopyKey = `job:${job.id}`;
          const isJobCopied = copiedKey === jobCopyKey;
          const defaultExpanded = isPlaylist && ["running", "failed"].includes(job.status);
          const isExpanded = isPlaylist ? expandedJobIds[job.id] ?? defaultExpanded : true;
          const jobRestartResolution = job.resolution_fallback?.restart_resolution ?? null;
          const jobRestartLabel = job.resolution_fallback
            ? resolutionFallbackRestartLabel(job.resolution_fallback, "job")
            : undefined;
          const jobLocalActionKey = `job:${job.id}`;
          const jobBili = biliUploads[job.id] ?? null;
          const biliBusy = jobBili?.badge === "uploading";
          // 合集与单视频的分流只看条目数，与下载侧 `isPlaylist` 用同一个判据 ——
          // 两处用不同判据时会出现「下载当合集、上传当单视频」这种自相矛盾的行为。
          const jobVideoReady = isPlaylist
            ? job.items.length > 0 && job.items.every((item) => Boolean(item.output_path))
            : Boolean(primaryItem?.output_path);
          const biliDisabledReason = biliBusy
            ? "正在上传中，等这批跑完"
            : !jobVideoReady
              ? isPlaylist
                ? "还有视频没下载好"
                : "视频还没下载好"
              : null;
          return (
          <article key={job.id} className="job-card">
            <div className="job-row">
              <input
                aria-label={`选择任务 ${title}`}
                className="job-select"
                type="checkbox"
                checked={selectedJobIds.has(job.id)}
                onChange={() => onToggleJobSelection(job.id)}
              />
              <div className="job-main">
                <h3>{title}</h3>
                <p>
                  {job.status} · {job.completed_items}/{job.total_items} 完成
                  {!isPlaylist && ["queued", "running"].includes(job.status) ? " · 单视频任务不受并发设置影响" : ""}
                  {job.error ? ` · ${job.error}` : ""}
                </p>
              </div>
              <div className="job-actions">
                <BiliUploadButton
                  badge={jobBili?.badge ?? IDLE_BADGE.badge}
                  badgeText={jobBili?.badge_text || IDLE_BADGE.badgeText}
                  badgeTone={jobBili?.badge_tone ?? IDLE_BADGE.badgeTone}
                  subject={title}
                  disabledReason={biliDisabledReason}
                  // 任务级「已上传」点进去打开**第一个**稿件（`uploads` 已按条目序号排过）。
                  // 合集页（`collectiondetail?sid=`）要账号 mid，前端不为此多引一次账号请求。
                  openUrl={uploadedTargetUrl(jobBili?.uploads)}
                  onOpenUrl={onOpenVideoPage}
                  // 「待核对 / 部分失败」两档只补**还没成功的那几条**：已经投上去的再投一遍
                  // 会在 B站 留下重复稿件，而那是不可撤销的。
                  retryableIds={retryableUploadIds(jobBili?.uploads)}
                  onRetry={onBiliRetryMany}
                  onClick={() => onBiliUpload(job.id, isPlaylist ? null : primaryItem?.id ?? null)}
                />
                {isPlaylist && (
                  <button
                    className={`icon-button expand-button ${isExpanded ? "is-expanded" : "is-collapsed"}`}
                    type="button"
                    title={isExpanded ? "折叠" : "展开"}
                    aria-label={`${isExpanded ? "折叠" : "展开"} ${title}`}
                    aria-expanded={isExpanded}
                    onClick={() => toggleExpanded(job.id, isExpanded)}
                  >
                    <ChevronDown size={18} />
                  </button>
                )}
                {isPlaylist && (
                  <button
                    className="icon-button"
                    type="button"
                    title={canOpenPlaylistFolder ? "打开合集文件夹" : "合集文件夹尚不可用"}
                    aria-label={`打开合集文件夹 ${title}`}
                    disabled={!canOpenPlaylistFolder}
                    onClick={() => runLocalFileAction(jobLocalActionKey, () => onOpenFolder(job.id))}
                  >
                    <FolderOpen size={18} />
                  </button>
                )}
                {!isPlaylist && (
                  <button
                    className="icon-button"
                    type="button"
                    title={canPlayJob ? "播放视频" : "视频文件尚不可用"}
                    aria-label={`播放 ${title}`}
                    disabled={!canPlayJob}
                    onClick={() => runLocalFileAction(jobLocalActionKey, () => onPlay(job.id))}
                  >
                    <Play size={18} />
                  </button>
                )}
                {!isPlaylist && (
                  <button
                    className="icon-button"
                    type="button"
                    title={canOpenSingleFolder ? "打开视频所在文件夹" : "视频文件夹尚不可用"}
                    aria-label={`打开视频文件夹 ${title}`}
                    disabled={!canOpenSingleFolder}
                    onClick={() => runLocalFileAction(jobLocalActionKey, () => onOpenFolder(job.id))}
                  >
                    <FolderOpen size={18} />
                  </button>
                )}
                {!isPlaylist && (
                  <SubtitleOpenButton
                    disabled={!canOpenSubtitle}
                    subject={title}
                    onOpen={() => runLocalFileAction(jobLocalActionKey, () => onOpenSubtitle(job.id, null))}
                  />
                )}
                <button
                  className="icon-button"
                  type="button"
                  title={isJobCopied ? "已复制" : "复制链接"}
                  aria-label={`${isJobCopied ? "已复制" : "复制链接"} ${title}`}
                  onClick={() => void copySourceLink(jobCopyKey, job.url)}
                >
                  {isJobCopied ? <Check size={18} /> : <Copy size={18} />}
                </button>
                <button
                  className="icon-button"
                  type="button"
                  title="打开 YouTube 页面"
                  aria-label={`打开 YouTube 页面 ${title}`}
                  onClick={() => onOpenSourcePage(job.url)}
                >
                  <ExternalLink size={18} />
                </button>
                {["queued", "running"].includes(job.status) && (
                  <button className="icon-button" type="button" title="暂停" aria-label={`暂停 ${title}`} onClick={() => onPause(job.id)}>
                    <Pause size={18} />
                  </button>
                )}
                {job.status !== "running" && (
                  <button className="icon-button" type="button" title="重启" aria-label={`重启 ${title}`} onClick={() => onRestart(job.id)}>
                    <RotateCcw size={18} />
                  </button>
                )}
                <button
                  className="icon-button danger"
                  type="button"
                  title="仅删除任务"
                  aria-label={`仅删除任务 ${title}`}
                  onClick={() => onDelete(job.id, false)}
                >
                  <Trash2 size={18} />
                </button>
                <button
                  className="icon-button danger"
                  type="button"
                  title="删除任务和已下载文件"
                  aria-label={`删除任务和已下载文件 ${title}`}
                  onClick={() => onDelete(job.id, true)}
                >
                  <FileX2 size={18} />
                </button>
              </div>
            </div>
            {localActionError?.key === jobLocalActionKey && (
              <div className="local-action-error" role="alert">
                {localActionError.message}
              </div>
            )}
            <div className="job-metrics">
              <span>{formatPercent(job.progress)}</span>
              <span>开始 {formatDateTime(job.started_at)}</span>
              <span>结束 {formatDateTime(job.finished_at)}</span>
              <span>分辨率 {job.actual_resolution ?? "检测中"}</span>
              <span>格式 {job.actual_format ?? "检测中"}</span>
              <span>大小 {formatJobVideoSize(job)}</span>
              <span>时长 {formatJobDuration(job)}</span>
              <span>已用 {formatClock(job.elapsed_seconds)}</span>
              <span>剩余 {formatClock(job.eta)}</span>
              {job.speed ? <span>{formatBytesPerSecond(job.speed)}</span> : <span>-- KB/s</span>}
            </div>
            <div className="progress-bar">
              <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
            </div>
            {jobBili && (
              <BiliJobStatus
                uploads={jobBili}
                onRetryUpload={onBiliRetryUpload}
                onRetrySeason={onBiliRetrySeason}
                onOpenVideoPage={onOpenVideoPage}
              />
            )}
            {!isPlaylist && job.resolution_fallback && (
              <ResolutionFallbackNotice
                fallback={job.resolution_fallback}
                restartLabel={jobRestartLabel}
                restartAriaLabel={jobRestartLabel ? `${jobRestartLabel} ${title}` : undefined}
                onRestart={
                  jobRestartResolution
                    ? () => onRestart(job.id, jobRestartResolution)
                    : undefined
                }
              />
            )}
            {job.items.length > 0 && isPlaylist && isExpanded && (
              <div className="item-list">
                {(() => {
                  const selectedItemIds = Array.from(selectedItemIdsByJob[job.id] ?? []);
                  return selectedItemIds.length > 0 ? (
                    <div className="item-batch-toolbar" aria-label={`批量视频操作 ${title}`}>
                      <span>{selectedItemIds.length} 个视频已选择</span>
                      <button
                        type="button"
                        className="ghost-button danger"
                        onClick={() => onDeleteItems(job.id, selectedItemIds, false)}
                      >
                        <Trash2 size={15} />
                        删除已选视频任务
                      </button>
                      <button
                        type="button"
                        className="ghost-button danger"
                        onClick={() => onDeleteItems(job.id, selectedItemIds, true)}
                      >
                        <FileX2 size={15} />
                        删除已选任务和已下载文件
                      </button>
                    </div>
                  ) : null;
                })()}
                {job.items.map((item) => {
                  const itemRestartResolution = item.resolution_fallback?.restart_resolution ?? null;
                  const itemRestartLabel = item.resolution_fallback
                    ? resolutionFallbackRestartLabel(item.resolution_fallback, "item")
                    : undefined;
                  const itemCopyKey = `item:${item.id}`;
                  const itemLocalActionKey = `item:${item.id}`;
                  const isItemCopied = copiedKey === itemCopyKey;
                  const canOpenItemFolder = Boolean(item.output_path || job.download_dir);
                  const canOpenItemSubtitle = Boolean(item.subtitle_path);
                  const itemBili = jobBili?.uploads.find((upload) => upload.item_id === item.id) ?? null;
                  const itemBiliBusy = itemBili?.badge === "uploading" || jobBili?.badge === "uploading";
                  return (
                  <div key={item.id} className="job-item-detail">
                    <div className="item-row">
                      <label className="item-title-select">
                        <input
                          aria-label={`选择视频任务 ${item.title}`}
                          type="checkbox"
                          checked={selectedItemIdsByJob[job.id]?.has(item.id) ?? false}
                          onChange={() => toggleItemSelection(job.id, item.id)}
                        />
                        <span>{item.index}. {item.title} · {item.status}</span>
                      </label>
                      <div className="item-actions">
                        {item.error && !item.resolution_fallback && <span className="item-error">{item.error}</span>}
                        <BiliUploadButton
                          badge={itemBili?.badge ?? IDLE_BADGE.badge}
                          badgeText={itemBili?.badge_text || IDLE_BADGE.badgeText}
                          badgeTone={itemBili?.badge_tone ?? IDLE_BADGE.badgeTone}
                          subject={item.title}
                          compact
                          disabledReason={
                            itemBiliBusy ? "正在上传中" : item.output_path ? null : "视频还没下载好"
                          }
                          // 条目级只认这一条自己的链接：**不拿同任务别的稿件顶替**。
                          openUrl={uploadedTargetUrl(itemBili ? [itemBili] : null)}
                          onOpenUrl={onOpenVideoPage}
                          retryableIds={retryableUploadIds(itemBili ? [itemBili] : null)}
                          onRetry={onBiliRetryMany}
                          onClick={() => onBiliUpload(job.id, item.id)}
                        />
                        <button
                          className="icon-button item-action-button"
                          type="button"
                          title={item.output_path ? "播放视频" : "视频文件尚不可用"}
                          aria-label={`播放 ${item.title}`}
                          disabled={!item.output_path}
                          onClick={() => runLocalFileAction(itemLocalActionKey, () => onPlayItem(job.id, item.id))}
                        >
                          <Play size={16} />
                        </button>
                        <button
                          className="icon-button item-action-button"
                          type="button"
                          title={canOpenItemFolder ? "打开视频所在文件夹" : "视频文件夹尚不可用"}
                          aria-label={`打开视频文件夹 ${item.title}`}
                          disabled={!canOpenItemFolder}
                          onClick={() => runLocalFileAction(itemLocalActionKey, () => onOpenItemFolder(job.id, item.id))}
                        >
                          <FolderOpen size={16} />
                        </button>
                        <SubtitleOpenButton
                          compact
                          disabled={!canOpenItemSubtitle}
                          subject={item.title}
                          onOpen={() =>
                            runLocalFileAction(itemLocalActionKey, () => onOpenSubtitle(job.id, item.id))
                          }
                        />
                        <button
                          className="icon-button item-action-button"
                          type="button"
                          title={isItemCopied ? "已复制" : "复制链接"}
                          aria-label={`${isItemCopied ? "已复制" : "复制链接"} ${item.title}`}
                          onClick={() => void copySourceLink(itemCopyKey, item.source_url)}
                        >
                          {isItemCopied ? <Check size={16} /> : <Copy size={16} />}
                        </button>
                        <button
                          className="icon-button item-action-button"
                          type="button"
                          title="打开 YouTube 页面"
                          aria-label={`打开 YouTube 页面 ${item.title}`}
                          onClick={() => onOpenSourcePage(item.source_url)}
                        >
                          <ExternalLink size={16} />
                        </button>
                        {item.status !== "running" && (
                          <button
                            className="icon-button item-action-button"
                            type="button"
                            title="重启"
                            aria-label={`重启 ${item.title}`}
                            onClick={() => onRestartItem(job.id, item.id)}
                          >
                            <RotateCcw size={16} />
                          </button>
                        )}
                        <button
                          className="icon-button item-action-button danger"
                          type="button"
                          title="仅删除视频任务"
                          aria-label={`仅删除视频任务 ${item.title}`}
                          onClick={() => onDeleteItems(job.id, [item.id], false)}
                        >
                          <Trash2 size={16} />
                        </button>
                        <button
                          className="icon-button item-action-button danger"
                          type="button"
                          title="删除视频任务和已下载文件"
                          aria-label={`删除视频任务和已下载文件 ${item.title}`}
                          onClick={() => onDeleteItems(job.id, [item.id], true)}
                        >
                          <FileX2 size={16} />
                        </button>
                      </div>
                    </div>
                    {localActionError?.key === itemLocalActionKey && (
                      <div className="local-action-error" role="alert">
                        {localActionError.message}
                      </div>
                    )}
                    <div className="item-metrics">
                      <span>{formatPercent(item.progress)}</span>
                      <span>大小 {formatVideoSize(item.total_bytes)}</span>
                      <span>时长 {formatMediaDuration(item.duration)}</span>
                      <span>已下载 {formatVideoSize(item.downloaded_bytes)}</span>
                      <span>分辨率 {formatItemResolution(item)}</span>
                      <span>格式 {item.actual_format ?? "检测中"}</span>
                      <span>已用 {formatClock(item.elapsed_seconds)}</span>
                      <span>剩余 {formatClock(item.eta)}</span>
                      {item.speed ? <span>{formatBytesPerSecond(item.speed)}</span> : <span>-- KB/s</span>}
                    </div>
                    {item.resolution_fallback && (
                      <ResolutionFallbackNotice
                        fallback={item.resolution_fallback}
                        restartLabel={itemRestartLabel}
                        restartAriaLabel={itemRestartLabel ? `${itemRestartLabel} ${item.title}` : undefined}
                        onRestart={
                          itemRestartResolution
                            ? () => onRestartItem(job.id, item.id, itemRestartResolution)
                            : undefined
                        }
                      />
                    )}
                    <div className="progress-bar item-progress">
                      <span style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }} />
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
          </article>
          );
        })}
      </div>
    </section>
  );
}

function localActionErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "本地文件操作失败。";
}

function formatVideoSize(bytes: number | null | undefined): string {
  return bytes == null ? "未知" : formatFileSize(bytes);
}

/**
 * 任务行的时长。**与「大小」同一套折法**：单视频任务就是那条视频的时长；
 * 合集任务按子视频求和，有子视频还没有时长时前缀「已知」。
 * 两处折法不同会让「大小 已知 2.1 GB · 时长 3:20」这种自相矛盾的一行出现。
 */
function formatJobDuration(job: Job): string {
  if (job.items.length === 1) {
    return formatMediaDuration(job.items[0]?.duration);
  }
  const knownDurations = job.items
    .map((item) => item.duration)
    .filter((seconds): seconds is number => typeof seconds === "number" && seconds > 0);
  if (!knownDurations.length) {
    return "未知";
  }
  const totalDuration = knownDurations.reduce((total, seconds) => total + seconds, 0);
  return knownDurations.length === job.items.length
    ? formatMediaDuration(totalDuration)
    : `已知 ${formatMediaDuration(totalDuration)}`;
}

function formatJobVideoSize(job: Job): string {
  if (job.items.length === 1) {
    return formatVideoSize(job.items[0]?.total_bytes);
  }
  const knownSizes = job.items
    .map((item) => item.total_bytes)
    .filter((size): size is number => typeof size === "number" && size > 0);
  if (!knownSizes.length) {
    return "未知";
  }
  const totalSize = knownSizes.reduce((total, size) => total + size, 0);
  return knownSizes.length === job.items.length
    ? formatFileSize(totalSize)
    : `已知 ${formatFileSize(totalSize)}`;
}

function ResolutionFallbackNotice({
  fallback,
  restartLabel,
  restartAriaLabel,
  onRestart
}: {
  fallback: ResolutionFallback;
  restartLabel?: string;
  restartAriaLabel?: string;
  onRestart?: () => void;
}) {
  return (
    <div className="resolution-fallback-note">
      <span>{fallback.message}</span>
      {onRestart && restartLabel && restartAriaLabel && (
        <button className="ghost-button" type="button" aria-label={restartAriaLabel} onClick={onRestart}>
          <RotateCcw size={15} />
          {restartLabel}
        </button>
      )}
    </div>
  );
}

