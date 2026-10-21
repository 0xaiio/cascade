import type { BiliBadge, BiliSeasonStatus, BiliSubtitleState, BiliUploadStatus } from "./types";

/**
 * 稿件 / 合集状态的**判定**。取值本身在 `types.ts`（与服务端 `models.py` 的枚举一一对应），
 * 这里放「这些取值怎么分类」。
 *
 * 为什么值得单独一个模块：状态判定原先以字面量数组的形式散在组件里
 * （`["failed", "rejected", "unverified"].includes(...)`、`season.status === "failed"`），
 * 而**服务端早就有这两组语义**（`bili_policy.ACCEPTED_STATUSES` / `REJECTED_STATUSES`）。
 * 前端每复制一份，就多一处「平台加了一档状态而这里没跟」的机会 ——
 * 那时的表现是**重试按钮该出现却不出现**，或者一条已成功的稿件被摆上重试按钮。
 *
 * 判据一律是「用户此刻该做什么」，不是「这个状态长什么样」。
 */

/** 重试**真的有意义**的稿件状态。
 *
 * - `failed` / `rejected`：重新投稿。
 * - `unverified`：**只重新对账**（投稿命令已经成功过，重投等于真的投第二遍）。
 *
 * 已成功的那两档（`published` / `reviewing`）**不在其中** —— 服务端会把它们挡成
 * 「什么都没做」，但按钮摆在那里就是在暗示用户去点。
 */
export const RETRYABLE_UPLOAD_STATUSES: readonly BiliUploadStatus[] = ["failed", "rejected", "unverified"];

/** 重试归集有意义的两档：失败了，或者还没开始（`pending` 说明上一轮没跑完）。 */
export const RETRYABLE_SEASON_STATUSES: readonly BiliSeasonStatus[] = ["failed", "pending"];

/** 「这条稿件出问题了」。`unverified` **不算** —— 那是「我们还不知道」，不是「这一步没成」。 */
export const BAD_UPLOAD_STATUSES: readonly BiliUploadStatus[] = ["failed", "rejected"];

/** 「这次归集出问题了」。只有 `failed` 一档 —— `skipped` 是用户自己选的终态，不是问题。 */
export const BAD_SEASON_STATUSES: readonly BiliSeasonStatus[] = ["failed"];

/**
 * 「字幕这一步出问题了」。只有 `failed` 一档 ——
 * `skipped` 是「这个视频本来就没有字幕可传」，是常态；`posted` 是已经传好了。
 *
 * **它与稿件状态无关**：一条 `published` 的稿件可以同时是字幕 `failed`。这正是要分开判的原因
 * （见 `types.BiliSubtitleState`）。
 */
export const BAD_SUBTITLE_STATES: readonly BiliSubtitleState[] = ["failed"];

/** 「字幕这一步有话要说」。`skipped` 不在其中：每条没字幕的稿件都写一行会把任务卡片塞满，
 * 而投稿表单里早就把「无字幕」写清楚了。 */
export const NOTABLE_SUBTITLE_STATES: readonly BiliSubtitleState[] = ["posted", "failed"];

function includes<T extends string>(values: readonly T[], value: T): boolean {
  return values.includes(value);
}

export function isRetryableUpload(status: BiliUploadStatus): boolean {
  return includes(RETRYABLE_UPLOAD_STATUSES, status);
}

export function isRetryableSeason(status: BiliSeasonStatus): boolean {
  return includes(RETRYABLE_SEASON_STATUSES, status);
}

export function isBadUpload(status: BiliUploadStatus): boolean {
  return includes(BAD_UPLOAD_STATUSES, status);
}

export function isBadSeason(status: BiliSeasonStatus): boolean {
  return includes(BAD_SEASON_STATUSES, status);
}

export function isBadSubtitle(state: BiliSubtitleState): boolean {
  return includes(BAD_SUBTITLE_STATES, state);
}

export function isNotableSubtitle(state: BiliSubtitleState): boolean {
  return includes(NOTABLE_SUBTITLE_STATES, state);
}

/** 徽标里「还没上传」那一档。按钮文案要在这一档换成动作词（「上传」而不是「未上传」）。 */
export const BADGE_NONE: BiliBadge = "none";

/** 徽标里「正在跑」那一档。**这一档的按钮什么都不能做** —— 在跑的东西不需要再点一次。 */
export const BADGE_UPLOADING: BiliBadge = "uploading";

/**
 * 徽标里「已经传上去了」那一档。按钮在这一档改成「进入 B 站」——
 * 再点一次只会投出第二条同名稿件，那不是用户此刻想做的事。
 */
export const BADGE_UPLOADED: BiliBadge = "uploaded";

/**
 * 徽标里「我们不知道它在不在平台上」那一档（对账没查到）。
 *
 * **它不等于失败，也不等于没上传。** 投稿命令是成功过的，所以重投就是这个视频的**第二遍**。
 * 这一档唯一有意义的动作是「再去平台上核对一次」。
 */
export const BADGE_UNVERIFIED: BiliBadge = "unverified";

/** 徽标里「有成功的也有没成功的」那一档。**已成功的那几条绝不能再投**。 */
export const BADGE_PARTIAL: BiliBadge = "partial";

/** 徽标里「这一步没成」那一档。只有这一档适合重新走一遍投稿表单（元数据可能要改）。 */
export const BADGE_FAILED: BiliBadge = "failed";

/** 「已上传」按钮点下去要打开的 B 站页面。
 *
 * 传进来的数组**只能放本次点击真正对应的稿件**：
 *
 * - 任务级放该任务的全部稿件（后端已按条目序号排过，取第一条有链接的 = 第一个视频）；
 * - 条目级**只放这一条**，多一条都不给 —— 点第 3 集却打开第 1 集，比不给链接更糟：
 *   用户会以为那就是第 3 集，然后去评论第 1 集。
 *
 * 返回 `null` 表示「本地没记下 bvid」。这不是异常：徽标可以由**合集已归集**派生而来，
 * 那条路径下可能一条带链接的稿件都没有。调用方要据此退回「重新上传」那条老路，
 * 而不是给一个点下去什么都不发生的绿按钮。
 */
export function uploadedTargetUrl(
  uploads: readonly { video_url: string | null }[] | null | undefined
): string | null {
  if (!uploads) return null;
  for (const upload of uploads) {
    if (upload.video_url) return upload.video_url;
  }
  return null;
}

/**
 * 「现在点这一下，该去补哪几条」——`unverified` 与 `partial` 两档共用的那条判据。
 *
 * 两档的共同点是**已经有记录可能躺在平台上了**，所以按钮只能补「还没成功的那几条」，
 * 不能把整批重投一遍。返回的是**记录 id**，具体走「重新排队」还是「只核对」由服务端
 * 按每条自己的终态决定（`bili_manager.retry_upload` 的三条分支）——
 * 前端再判一次就会出现两套真相。
 */
export function retryableUploadIds(
  uploads: readonly { id: string; status: BiliUploadStatus }[] | null | undefined
): string[] {
  if (!uploads) return [];
  return uploads.filter((upload) => isRetryableUpload(upload.status)).map((upload) => upload.id);
}

/** 一条稿件在任务卡片里是否**有话说**（值得渲染一行）。
 *
 * 字幕失败也要算：那条稿件本身可能已经 `published` 并且有 `video_url`（本来就会渲染），
 * 但**万一它没有链接**（平台没回 bvid），字幕失败就会连一行都没有 —— 于是「字幕没传上去」
 * 这件事在界面上彻底消失。把这一档并进来，渲染与否就不再取决于一个无关字段。
 */
export function isNotableUpload(upload: {
  error: string | null;
  video_url: string | null;
  subtitle_state: BiliSubtitleState;
}): boolean {
  return Boolean(upload.error || upload.video_url || isBadSubtitle(upload.subtitle_state));
}
