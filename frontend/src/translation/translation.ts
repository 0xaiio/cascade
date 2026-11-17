import { request } from "../shared/api";
import type {
  BiliUpload,
  TranslationEditRequest,
  TranslationExport,
  TranslationJob,
  TranslationProbe
} from "../contracts/types";

/** 字幕翻译 API。所有路径与后端 `routers/translation.py` 一一对应。 */

export function probeTranslation(): Promise<TranslationProbe> {
  return request<TranslationProbe>("/api/translation/probe", { method: "POST" });
}

/** 入口先查：命中就打开上次成果；404 才新建。不触发翻译、不发模型请求。 */
export function lookupTranslation(jobId: string, itemId: string | null): Promise<TranslationJob> {
  const query = new URLSearchParams({ job_id: jobId });
  if (itemId) query.set("item_id", itemId);
  return request<TranslationJob>(`/api/translation/lookup?${query.toString()}`);
}

/** 用系统默认程序打开中文字幕；路径由服务端推导。 */
export function openTranslatedSubtitle(jobId: string, itemId: string | null): Promise<void> {
  const query = new URLSearchParams({ job_id: jobId });
  if (itemId) query.set("item_id", itemId);
  return request<void>(`/api/translation/open?${query.toString()}`, { method: "POST" });
}

export function startTranslation(jobId: string, itemId: string | null): Promise<TranslationJob> {
  return request<TranslationJob>("/api/translation/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, item_id: itemId })
  });
}

export function getTranslation(id: string): Promise<TranslationJob> {
  return request<TranslationJob>(`/api/translation/jobs/${encodeURIComponent(id)}`);
}

export function setTranslationMode(
  id: string,
  mode: "zh" | "bilingual"
): Promise<TranslationJob> {
  return request<TranslationJob>(`/api/translation/jobs/${encodeURIComponent(id)}/mode`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode })
  });
}

export function editTranslationCue(id: string, index: number, text: string): Promise<TranslationJob> {
  const body: TranslationEditRequest = { text };
  return request<TranslationJob>(`/api/translation/jobs/${encodeURIComponent(id)}/cues/${index}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}

export function retryTranslation(id: string): Promise<TranslationJob> {
  return request<TranslationJob>(`/api/translation/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
}

export function cancelTranslation(id: string): Promise<TranslationJob> {
  return request<TranslationJob>(`/api/translation/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

export function exportTranslation(id: string): Promise<TranslationExport> {
  return request<TranslationExport>(`/api/translation/jobs/${encodeURIComponent(id)}/export`, { method: "POST" });
}

export function uploadBiliTranslation(id: string): Promise<BiliUpload> {
  return request<BiliUpload>(`/api/translation/jobs/${encodeURIComponent(id)}/upload-bili`, {
    method: "POST"
  });
}

export function deleteTranslation(id: string): Promise<void> {
  return request<void>(`/api/translation/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
}
