/**
 * E-04 · 任务行内的「克隆」面板。
 *
 * 只暴露**两项**可改的下载选项，其余一律沿用原任务：请求体里没出现的字段
 * 由后端从 `options_json` 整份重建，所以这里不能把 `DownloadOptions()` 的
 * 出厂默认值当成「原设置」送上去。清晰度的写法由请求层校验（026）。
 *
 * 回执就近显示：区域级错误不上升为页面级红条（FR-55）。
 * `onSubmit` 必须返回 `Promise<string | null>` —— 写成 `void asyncFn` 会让
 * `await` 立刻拿到 undefined，加载态一帧都不出现（ai/ui/007 的教训）。
 */

import { useState } from "react";
import type { CloneJobOverrides } from "../shared/api";
import type { DownloadMode } from "../contracts/types";

const RESOLUTIONS = ["best", "2160p", "1440p", "1080p", "720p", "480p", "360p"];
const MODES: Array<{ value: DownloadMode; label: string }> = [
  { value: "video_subtitles", label: "视频 + 字幕" },
  { value: "video_only", label: "仅视频" },
  { value: "subtitles_only", label: "仅字幕" }
];

export function CloneJobPanel({
  jobTitle,
  onSubmit,
  onClose
}: {
  jobTitle: string;
  onSubmit: (overrides: CloneJobOverrides) => Promise<string | null>;
  onClose: () => void;
}) {
  const [resolution, setResolution] = useState("");
  const [mode, setMode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setPending(true);
    setError(null);
    const overrides: CloneJobOverrides = {};
    if (resolution) overrides.resolution = resolution;
    if (mode) overrides.mode = mode as DownloadMode;

    let failure: string | null;
    try {
      failure = await onSubmit(overrides);
    } catch (err) {
      failure = err instanceof Error ? err.message : "克隆任务失败";
    } finally {
      setPending(false);
    }
    if (failure) {
      setError(failure);
      return;
    }
    onClose();
  }

  return (
    <div className="clone-job-panel" role="group" aria-label={`克隆任务 ${jobTitle}`}>
      <p className="clone-job-note">
        用同一个链接建一个新任务，只改下面选的项，其余沿用原设置。原任务、它的下载历史与 B站 投稿记录都不变；
        如果原任务开着「跳过已下载」，本地已下好的成品会被命中，不会重复下载。
      </p>
      <div className="clone-job-fields">
        <label>
          <span>清晰度</span>
          <select
            aria-label="副本清晰度"
            value={resolution}
            onChange={(event) => setResolution(event.target.value)}
          >
            <option value="">沿用原设置</option>
            {RESOLUTIONS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>下载模式</span>
          <select aria-label="副本下载模式" value={mode} onChange={(event) => setMode(event.target.value)}>
            <option value="">沿用原设置</option>
            {MODES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p className="clone-job-error" role="alert">
          {error}
        </p>
      )}
      <div className="clone-job-actions">
        <button className="primary-button" type="button" disabled={pending} onClick={() => void submit()}>
          {pending ? "创建中…" : "创建副本"}
        </button>
        <button className="ghost-button" type="button" disabled={pending} onClick={onClose}>
          取消
        </button>
      </div>
    </div>
  );
}
