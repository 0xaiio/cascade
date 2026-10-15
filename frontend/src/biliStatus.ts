import type { BiliBadge, BiliSeasonStatus, BiliUploadStatus } from "./types";

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

/** 徽标里「还没上传」那一档。按钮文案要在这一档换成动作词（「上传」而不是「未上传」）。 */
export const BADGE_NONE: BiliBadge = "none";

/** 一条稿件在任务卡片里是否**有话说**（值得渲染一行）。 */
export function isNotableUpload(upload: { error: string | null; video_url: string | null }): boolean {
  return Boolean(upload.error || upload.video_url);
}
