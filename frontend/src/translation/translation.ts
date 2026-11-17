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
