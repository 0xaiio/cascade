import { Fragment } from "react";
import { AlertTriangle, ExternalLink, RotateCcw } from "lucide-react";
import {
  isBadSeason,
  isBadSubtitle,
  isBadUpload,
  isNotableSubtitle,
  isNotableUpload,
  isRetryableSeason,
  isRetryableUpload
} from "../biliStatus";
import type { BiliJobUploads } from "../types";

/**
 * 任务卡片里的 B站 上传明细与「下一步」入口。
 *
 * 只在**有话说**的时候渲染一行：稿件的失败原因 + 服务端给的下一步，以及合集归集的结论。
 * 没有记录、也没有任何要点开的东西时，这里什么都不显示 —— 任务卡片已经很密，不堆装饰。
 *
 * 两个刻意的做法：
 *
 * 1. **重试按钮只在「重试真的有意义」时出现**：`failed`/`rejected`（重新投稿）与
 *    `unverified`（只重新核对，不重投）。已成功的条目不给重试按钮 —— 服务端也会把它挡成
 *    「什么都没做」，但按钮摆在那就是在暗示用户去点。
 * 2. **「待核对」与「失败」用不同的措辞**：前者是「我们还不知道」，后者是「这一步没成」。
 *    把两者都说成失败，用户会去重投一条**已经投上去**的稿件。
 */
export function BiliJobStatus({
  uploads,
  onRetryUpload,
  onRetrySeason,
  onOpenVideoPage
}: {
  uploads: BiliJobUploads;
  onRetryUpload: (uploadId: string) => void;
  onRetrySeason: (seasonRecordId: string) => void;
  onOpenVideoPage: (url: string) => void;
}) {
  const season = uploads.season;
  const notable = uploads.uploads.filter(isNotableUpload);
  if (!season && !notable.length) return null;

  return (
    <div className="bili-job-status">
      {season && (
        <div className={`bili-status-line ${isBadSeason(season.status) ? "is-bad" : ""}`}>
          <span className="bili-status-label">合集：{season.status_text}</span>
          {season.error && <span className="bili-status-detail">{season.error}</span>}
          {season.advice && <span className="bili-status-advice">{season.advice}</span>}
          {isRetryableSeason(season.status) && (
            <button className="ghost-button bili-retry" type="button" onClick={() => onRetrySeason(season.id)}>
              <RotateCcw size={15} />
              重试归集
            </button>
          )}
        </div>
      )}
      {notable.map((upload) => {
        // 先取出这个值：它在下面的闭包里要用，而对象属性的窄化**不会**穿过闭包 ——
        // 于是只能写成 `upload.video_url as string` 这种把类型系统关掉的写法。
        const videoUrl = upload.video_url;
        // 字幕这一行只在**有话要说**时出现（`posted` / `failed`）。`skipped` 是「这个视频
        // 本来就没字幕」，投稿表单里已经写过「无字幕」，每行再写一遍只会把任务卡片塞满。
        const subtitleNotable = isNotableSubtitle(upload.subtitle_state) && Boolean(upload.subtitle_message);
        const subtitleBad = isBadSubtitle(upload.subtitle_state);
        return (
          // 用 Fragment 而不是包一层 div：`.bili-job-status` 是 `display: grid` + `gap`，
          // 多包一层就把「一行一个网格项」的节奏打断，行距会变成两层叠加。
          <Fragment key={upload.id}>
            <div className={`bili-status-line ${isBadUpload(upload.status) ? "is-bad" : ""}`}>
              <span className="bili-status-label">
                {upload.index}. {upload.title} · {upload.status_text}
              </span>
              {videoUrl && (
                <button
                  className="bili-link"
                  type="button"
                  onClick={() => onOpenVideoPage(videoUrl)}
                  aria-label={`在 B 站打开 ${upload.title}`}
                >
                  <ExternalLink size={14} />
                  在 B 站查看
                </button>
              )}
              {upload.error && <span className="bili-status-detail">{upload.error}</span>}
              {upload.advice && <span className="bili-status-advice">{upload.advice}</span>}
              {isRetryableUpload(upload.status) && (
                <button className="ghost-button bili-retry" type="button" onClick={() => onRetryUpload(upload.id)}>
                  <RotateCcw size={15} />
                  {upload.status === "unverified" ? "重新核对" : "重试"}
                </button>
              )}
              {upload.status === "unverified" && <AlertTriangle size={14} className="bili-warn-icon" />}
            </div>
            {subtitleNotable && (
              // 字幕与稿件**分两行说**：一条 `published` 的稿件可以同时是字幕 `failed`，
              // 挤进上面那行会让「已上传」和「字幕失败」看起来互相矛盾。
              <div className={`bili-status-line bili-subtitle-line ${subtitleBad ? "is-bad" : ""}`}>
                {subtitleBad && <AlertTriangle size={14} className="bili-warn-icon" />}
                <span className="bili-status-label">CC 字幕</span>
                <span className="bili-status-detail">{upload.subtitle_message}</span>
                {subtitleBad && (
                  // 复用同一个重试入口：服务端看到「已发布 + 字幕失败」只会重传字幕，
                  // **不会再投一遍视频**（见 `bili_manager.retry_upload`）。
                  <button
                    className="ghost-button bili-retry"
                    type="button"
                    onClick={() => onRetryUpload(upload.id)}
                    aria-label={`重新上传字幕 ${upload.title}`}
                  >
                    <RotateCcw size={15} />
                    重传字幕
                  </button>
                )}
              </div>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
