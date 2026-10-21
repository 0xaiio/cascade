import { Fragment, useState } from "react";
import { AlertTriangle, ExternalLink, RotateCcw } from "lucide-react";
import { jobUploadProgress } from "../biliProgress";
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
import { BiliUploadProgress } from "./BiliUploadProgress";

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
 *
 * 第三条是后补的：**在跑的时候必须先说话**。上传中的稿件没有错误、没有链接、也没有字幕结论，
 * 按「有话说」的判据本来是**一行都不渲染**的 —— 于是点了上传之后卡片上什么都不动。
 * 所以上传进度单独成一块（`BiliUploadProgress`），并且让「有没有话说」也把它算进去。
 *
 * 第四条也是后补的：**「重试归集」点下去必须在原地留下痕迹**。那次点击在前提不满足时
 * （视频还在审核、平台还没给分P编号）**一个归集请求都不会发**，行里的文案因此一字不变；
 * 而唯一的回执在页顶，卡片滚在视口里的时候它在一千多像素之外（实测 `.notice` 的
 * `top = -1115px`）。于是这个按钮在用户眼里是死的。这里补两件可观察的东西：
 * 点击过程中的「归集中…」+ 禁用态，和结束后的「上次重试 HH:MM:SS」落款。
 * 见 ai/ui/007。
 */
export function BiliJobStatus({
  uploads,
  onRetryUpload,
  onRetrySeason,
  onOpenVideoPage
}: {
  uploads: BiliJobUploads;
  onRetryUpload: (uploadId: string) => void;
  /** 返回 `true` 表示这一次请求真的跑完了（不论结论是归集成功、还是「还不能归集」）。 */
  onRetrySeason: (seasonRecordId: string) => Promise<boolean>;
  onOpenVideoPage: (url: string) => void;
}) {
  const season = uploads.season;
  const notable = uploads.uploads.filter(isNotableUpload);
  // 上传进度只在「需要等」的时候出现（在跑 / 等审核）；它也算「有话说」。
  const waiting = jobUploadProgress(uploads.uploads)?.waiting ?? false;
  /** 归集重试的临时状态。键是那一行的 id —— 换了合集就不该沿用上一批的落款。 */
  const [seasonRetry, setSeasonRetry] = useState<{ id: string; busy: boolean; at: string | null } | null>(
    null
  );
  if (!season && !notable.length && !waiting) return null;

  const retrying = seasonRetry !== null && season !== null && seasonRetry.id === season.id && seasonRetry.busy;
  const retriedAt = seasonRetry !== null && season !== null && seasonRetry.id === season.id ? seasonRetry.at : null;

  async function handleRetrySeason(seasonRecordId: string) {
    if (retrying) return;
    setSeasonRetry({ id: seasonRecordId, busy: true, at: retriedAt });
    const ok = await onRetrySeason(seasonRecordId);
    // 失败时只收起「归集中…」而不写落款：落款的意思是「这一下真的跑完了」，
    // 请求本身挂了就不该留下它（错误另有 `.alert` 那条通道）。
    setSeasonRetry({
      id: seasonRecordId,
      busy: false,
      at: ok ? new Date().toLocaleTimeString("zh-CN", { hour12: false }) : retriedAt
    });
  }

  return (
    <div className="bili-job-status">
      {waiting && <BiliUploadProgress uploads={uploads.uploads} />}
      {season && (
        <div className={`bili-status-line ${isBadSeason(season.status) ? "is-bad" : ""}`}>
          <span className="bili-status-label">合集：{season.status_text}</span>
          {season.error && <span className="bili-status-detail">{season.error}</span>}
          {season.advice && <span className="bili-status-advice">{season.advice}</span>}
          {isRetryableSeason(season.status) && (
            <>
              <button
                className="ghost-button bili-retry"
                type="button"
                disabled={retrying}
                onClick={() => void handleRetrySeason(season.id)}
              >
                <RotateCcw size={15} />
                {retrying ? "归集中…" : "重试归集"}
              </button>
              {retriedAt && <span className="bili-retry-stamp">上次重试 {retriedAt}</span>}
            </>
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
