import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import { BiliJobStatus } from "./components/BiliJobStatus";
import { biliUploadPayload } from "./test/appFixtures";
import type { BiliJobUploads, BiliUpload } from "./types";

/**
 * 字幕结论在任务卡片里怎么呈现。
 *
 * 这一行的存在本身就是需求：「字幕有没有传上去」必须看得见、且**能补**。
 * 之前这块渲染一直没有用例，于是它可以在「后端三档状态齐全、前端不画」的状态下
 * 悄悄漏掉 —— 用户看得到稿件已上传，却永远看不到字幕那一行、也没有补传入口。
 *
 * 三条判据：
 * 1. `posted` / `failed` 才画这一行，`skipped` 不画（每条没字幕的稿件写一行会把卡片塞满，
 *    而投稿表单里已经写过「无字幕」）；
 * 2. 只有失败才给「重传字幕」按钮，点了要打到**这一条**的回调；
 * 3. 稿件本身「没话说」（没 `video_url`、没 `error`）时，字幕失败**仍然要单独撑起一行** ——
 *    否则「字幕没传上去」这件事在界面上彻底消失。
 */

// 本仓库的 vitest 没开 `globals`，所以 RTL 不会自动注册 `afterEach(cleanup)` ——
// 少了这一句，同一个文件里第二个 `render` 会看到上一个用例留下的 DOM（报「匹配到多个元素」）。
afterEach(cleanup);

function job(upload: BiliUpload): BiliJobUploads {
  return { job_id: "job-1", badge: "uploaded", badge_text: "已上传", badge_tone: "ok", uploads: [upload], season: null };
}

function renderStatus(upload: BiliUpload) {
  const onRetryUpload = vi.fn();
  const { container } = render(
    <BiliJobStatus
      uploads={job(upload)}
      onRetryUpload={onRetryUpload}
      onRetrySeason={async () => true}
      onOpenVideoPage={() => {}}
    />
  );
  return { onRetryUpload, container };
}

describe("B站 上传明细 · CC 字幕那一行", () => {
  test("字幕传好了：显示一句结论，不给重传按钮", () => {
    renderStatus({
      ...biliUploadPayload,
      subtitle_state: "posted",
      subtitle_lan: "en",
      subtitle_message: "CC 字幕已上传（英文），观众可在播放器里开关"
    });

    expect(screen.getByText("CC 字幕")).toBeInTheDocument();
    expect(screen.getByText(/CC 字幕已上传（英文）/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重新上传字幕/ })).not.toBeInTheDocument();
  });

  test("字幕失败：带警告并给「重传字幕」，点了打到这一条", async () => {
    const user = userEvent.setup();
    const { onRetryUpload } = renderStatus({
      ...biliUploadPayload,
      subtitle_state: "failed",
      subtitle_message: "稿件已提交（reviewing），但平台还没给出分P编号，字幕暂时传不了"
    });

    expect(screen.getByText("CC 字幕")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: `重新上传字幕 ${biliUploadPayload.title}` });
    await user.click(retry);
    expect(onRetryUpload).toHaveBeenCalledWith(biliUploadPayload.id);
  });

  test("本来就没有字幕：不画这一行", () => {
    const { container } = render(
      <BiliJobStatus
        uploads={job({ ...biliUploadPayload, subtitle_state: "skipped", subtitle_message: "没有已下载的字幕，跳过字幕上传" })}
        onRetryUpload={() => {}}
        onRetrySeason={async () => true}
        onOpenVideoPage={() => {}}
      />
    );

    expect(screen.queryByText("CC 字幕")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".bili-subtitle-line")).toHaveLength(0);
  });

  test("稿件本身没话说时，字幕失败仍然单独撑起一行", () => {
    // 平台没回 bvid（`video_url` 为空）、也没有错误与建议 —— 按稿件判据这一条不该渲染。
    const { container } = renderStatus({
      ...biliUploadPayload,
      video_url: null,
      error: null,
      advice: null,
      subtitle_state: "failed",
      subtitle_message: "字幕转换失败：SRT 里没有可用条目"
    });

    expect(container.querySelectorAll(".bili-subtitle-line")).toHaveLength(1);
    expect(screen.getByText(/字幕转换失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新上传字幕/ })).toBeInTheDocument();
  });

  test("稿件状态与字幕状态互不冒充：已上传 + 字幕失败，徽标文案仍是「已上传」", () => {
    const { container } = renderStatus({
      ...biliUploadPayload,
      status: "published",
      status_text: "已上传",
      subtitle_state: "failed",
      subtitle_message: "字幕文件不存在，跳过字幕上传"
    });

    const lines = container.querySelectorAll(".bili-status-line");
    expect(lines).toHaveLength(2);
    expect(lines[0].textContent).toContain("已上传");
    expect(lines[1].className).toContain("is-bad");
  });
});
