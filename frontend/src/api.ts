import { GROUP_MODE_EXISTING } from "./biliForm";
import type {
  AnalyzeResponse,
  ApiErrorDetail,
  BiliGroupMode,
  BiliJobUploads,
  BiliJobUploadsSummary,
  BiliLoginPoll,
  BiliMetadata,
  BiliPrefill,
  BiliQrcode,
  BiliRetry,
  BiliSeasonList,
  BiliSeasonRetry,
  BiliStatus,
  CookieHealth,
  CookieStatus,
  DeleteJobItemsResponse,
  Diagnostics,
  DownloadOptions,
  Job,
  JobBatchAction,
  JobBatchActionResponse,
  ProxyTestResult,
  Settings
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: ApiErrorDetail | string | null
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail ?? null;
    if (detail && typeof detail === "object") {
      throw new ApiError(detail.message ?? `Request failed with ${response.status}`, response.status, detail);
    }
    throw new ApiError(detail ?? `Request failed with ${response.status}`, response.status, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export function getSettings(): Promise<Settings> {
  return request<Settings>("/api/settings");
}

export function updateSettings(settings: Partial<Settings>): Promise<Settings> {
  return request<Settings>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export function selectDownloadDirectory(): Promise<Settings> {
  return request<Settings>("/api/settings/download-dir/select", { method: "POST" });
}

export function analyzeUrl(url: string): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>("/api/analyze", {
    method: "POST",
    body: JSON.stringify({ url, cookies_enabled: true })
  });
}

export function listJobs(): Promise<Job[]> {
  return request<Job[]>("/api/jobs");
}

export function createJob(url: string, options: DownloadOptions): Promise<Job> {
  return request<Job>("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ url, options })
  });
}

export function cancelJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}

export function pauseJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/pause`, { method: "POST" });
}

export function restartJob(jobId: string, resolution?: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/restart`, restartRequest(resolution));
}

export function restartJobItem(jobId: string, itemId: string, resolution?: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/items/${itemId}/restart`, restartRequest(resolution));
}

export function playJobVideo(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/play`, { method: "POST" });
}

export function openJobFolder(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/open-folder`, { method: "POST" });
}

export function playJobItemVideo(jobId: string, itemId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/items/${itemId}/play`, { method: "POST" });
}

export function openJobItemFolder(jobId: string, itemId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/items/${itemId}/open-folder`, { method: "POST" });
}

export function deleteJob(jobId: string, deleteFiles = false): Promise<void> {
  const query = deleteFiles ? "?delete_files=true" : "";
  return request<void>(`/api/jobs/${jobId}${query}`, { method: "DELETE" });
}

export function deleteJobItems(jobId: string, itemIds: string[], deleteFiles = false): Promise<DeleteJobItemsResponse> {
  return request<DeleteJobItemsResponse>(`/api/jobs/${jobId}/items/delete`, {
    method: "POST",
    body: JSON.stringify({ item_ids: itemIds, delete_files: deleteFiles })
  });
}

export function batchJobAction(action: JobBatchAction, jobIds: string[], deleteFiles = false): Promise<JobBatchActionResponse> {
  return request<JobBatchActionResponse>("/api/jobs/batch", {
    method: "POST",
    body: JSON.stringify({ action, job_ids: jobIds, delete_files: action === "delete" ? deleteFiles : false })
  });
}

export function uploadCookies(file: File): Promise<CookieStatus> {
  const data = new FormData();
  data.append("file", file);
  return request<CookieStatus>("/api/cookies", {
    method: "POST",
    body: data
  });
}

export function importBrowserCookies(browser: string, closeBrowserIfLocked = false): Promise<CookieStatus> {
  return request<CookieStatus>("/api/cookies/from-browser", {
    method: "POST",
    body: JSON.stringify({ browser, close_browser_if_locked: closeBrowserIfLocked })
  });
}

export function deleteCookies(): Promise<CookieStatus> {
  return request<CookieStatus>("/api/cookies", { method: "DELETE" });
}

/**
 * 用当前生效的代理真实探测一次 YouTube。
 *
 * `proxy` 传 undefined = 用已保存的设置；传具体值 = 先试这个地址（不保存）。
 */
export function testProxy(proxy?: string): Promise<ProxyTestResult> {
  return request<ProxyTestResult>("/api/proxy/test", {
    method: "POST",
    body: JSON.stringify(proxy === undefined ? {} : { proxy })
  });
}

/** 校验 cookies 是否真的能登录。deep=false 只做离线体检（不联网）。 */
export function verifyCookies(deep = true): Promise<CookieHealth> {
  return request<CookieHealth>("/api/cookies/verify", {
    method: "POST",
    body: JSON.stringify({ deep })
  });
}

/** 重新做一次 JS 运行时/依赖探测（装完 Node/Deno 之后用）。 */
export function refreshRuntimeDiagnostics(): Promise<Diagnostics> {
  return request<Diagnostics>("/api/diagnostics/runtime", { method: "POST" });
}

export function getDiagnostics(): Promise<Diagnostics> {
  return request<Diagnostics>("/api/diagnostics");
}

// ---------------------------------------------------------------- B站 上传
/**
 * B站 上传的环境体检。
 *
 * `probeAccount=false` 只做离线体检（不请求 B站）：没有网络时也能看出「工具装没装、登录文件在不在」。
 */
export function getBiliStatus(probeAccount = true): Promise<BiliStatus> {
  return request<BiliStatus>(`/api/bilibili/status?probe_account=${probeAccount ? "true" : "false"}`);
}

/** 修复 WinGet `Links` 下那个 0 字节的 `biliup.exe` 空壳，然后返回重新体检的结论。 */
export function repairBiliTool(): Promise<BiliStatus> {
  return request<BiliStatus>("/api/bilibili/tools/repair", { method: "POST" });
}

/** 生成扫码登录二维码。返回 **SVG 文本**（内联渲染），不是位图。 */
export function createBiliQrcode(): Promise<BiliQrcode> {
  return request<BiliQrcode>("/api/bilibili/login/qrcode", { method: "POST" });
}

export function pollBiliLogin(qrcodeKey: string): Promise<BiliLoginPoll> {
  return request<BiliLoginPoll>(`/api/bilibili/login/poll?qrcode_key=${encodeURIComponent(qrcodeKey)}`);
}

/** 导入一份已有的登录文件（`biliup` 的 LoginInfo 或 Netscape cookies.txt 都能认）。 */
export function uploadBiliCookies(file: File): Promise<BiliStatus> {
  const data = new FormData();
  data.append("file", file);
  return request<BiliStatus>("/api/bilibili/cookies", { method: "POST", body: data });
}

export function deleteBiliCookies(): Promise<BiliStatus> {
  return request<BiliStatus>("/api/bilibili/cookies", { method: "DELETE" });
}

/** 预填上传表单：单视频给一组值，合集给「模板 + 逐条预览 + 超限定位」。 */
export function getBiliPrefill(jobId: string, itemId?: string | null): Promise<BiliPrefill> {
  const query = itemId ? `&item_id=${encodeURIComponent(itemId)}` : "";
  return request<BiliPrefill>(`/api/bilibili/prefill?job_id=${encodeURIComponent(jobId)}${query}`);
}

/** 所有有 B站 记录的任务的徽标。任务中心一次拿全，避免「N 个任务 = N 个请求」。 */
export function getBiliUploadsSummary(): Promise<BiliJobUploadsSummary> {
  return request<BiliJobUploadsSummary>("/api/bilibili/uploads/summary");
}

/**
 * 账号下的已有合集，供投稿界面下拉选择。
 *
 * **未登录也返回 200**（列表为空 + `error` 说明原因），所以这里不做异常处理：
 * 界面要显示那句原因，而不是把它变成一句「加载失败」。
 */
export function listBiliSeasons(): Promise<BiliSeasonList> {
  return request<BiliSeasonList>("/api/bilibili/seasons");
}

export function getBiliJobUploads(jobId: string): Promise<BiliJobUploads> {
  return request<BiliJobUploads>(`/api/bilibili/uploads?job_id=${encodeURIComponent(jobId)}`);
}

/**
 * 归集方式那一组字段。**两条提交路径共用**（单视频 / 合集）。
 *
 * `season_title` 只在 `new` 时才有意义，但照传无害：服务端在 `existing` 时用的是平台上
 * 那个合集自己的名字。`season_id` 则相反 —— `existing` 之外**一个字符都不能带**：
 * 带上一个残留编号会让服务端按「有 season_id 就不建合集」处理，
 * 「新建合集」就悄悄变成了往老合集里塞视频（`bili_manager._upsert_season` 的优先级）。
 *
 * 这段判断原本在两条路径里各写了一遍。它错起来的代价是**静默的**（归集去了错的合集），
 * 所以只留一份 —— 复制时最容易漏掉的正是那行 `...(groupMode === "existing" ? …)`。
 */
function biliGroupFields(
  groupMode: BiliGroupMode,
  seasonId: number | null,
  seasonTitle: string
): Record<string, unknown> {
  return {
    group_mode: groupMode,
    season_title: seasonTitle,
    ...(groupMode === GROUP_MODE_EXISTING ? { season_id: seasonId } : {})
  };
}

export function createBiliUpload(
  jobId: string,
  itemId: string | null,
  metadata: BiliMetadata,
  makeCover: boolean,
  comment: string,
  groupMode: BiliGroupMode,
  seasonId: number | null,
  seasonTitle: string
): Promise<BiliJobUploads> {
  return request<BiliJobUploads>("/api/bilibili/uploads", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      item_id: itemId,
      metadata,
      make_cover: makeCover,
      comment,
      ...biliGroupFields(groupMode, seasonId, seasonTitle)
    })
  });
}

export function createBiliCollection(
  jobId: string,
  template: BiliMetadata,
  seasonTitle: string,
  seasonDesc: string,
  makeCover: boolean,
  comment: string,
  groupMode: BiliGroupMode,
  seasonId: number | null
): Promise<BiliJobUploads> {
  return request<BiliJobUploads>("/api/bilibili/collections", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      template,
      season_desc: seasonDesc,
      make_cover: makeCover,
      comment,
      ...biliGroupFields(groupMode, seasonId, seasonTitle)
    })
  });
}

/** 重试一条稿件。已成功的条目不会被重投（服务端保证），返回 `action` 说明实际做了什么。 */
export function retryBiliUpload(uploadId: string): Promise<BiliRetry> {
  return request<BiliRetry>(`/api/bilibili/uploads/${encodeURIComponent(uploadId)}/retry`, { method: "POST" });
}

/** 重试合集归集。已有合集时只补加视频，不会重复建合集。 */
export function retryBiliSeason(seasonRecordId: string): Promise<BiliSeasonRetry> {
  return request<BiliSeasonRetry>(`/api/bilibili/collections/${encodeURIComponent(seasonRecordId)}/group`, {
    method: "POST"
  });
}

function restartRequest(resolution?: string): RequestInit {
  return resolution
    ? { method: "POST", body: JSON.stringify({ resolution }) }
    : { method: "POST" };
}
