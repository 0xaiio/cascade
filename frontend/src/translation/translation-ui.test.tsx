import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import { biliPrefillSinglePayload, jobPayload } from "../test/appFixtures";
import { BiliUploadDialog } from "../bilibili/BiliUploadDialog";
import { JobQueue } from "../task-center/JobQueue";
import type { TranslationJob } from "../contracts/types";
import { SubtitleTranslateDialog } from "./SubtitleTranslateDialog";
import {
  cancelTranslation,
  editTranslationCue,
  exportTranslation,
  getTranslation,
  retryTranslation,
  startTranslation,
  uploadBiliTranslation
} from "./translation";

vi.mock("./translation", () => ({
  startTranslation: vi.fn(),
  getTranslation: vi.fn(),
  editTranslationCue: vi.fn(),
  retryTranslation: vi.fn(),
  cancelTranslation: vi.fn(),
  exportTranslation: vi.fn(),
  deleteTranslation: vi.fn(),
  probeTranslation: vi.fn(),
  uploadBiliTranslation: vi.fn()
}));

// vitest 没开 globals ⇒ RTL 不会自动清理 DOM。
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const JOB: TranslationJob = {
  id: "tr-1",
  job_id: "job-running",
  item_id: jobPayload.items[0].id,
  source_path: "D://Videos//a.en.vtt",
  source_language: "en",
  state: "succeeded",
  total: 2,
  translated: 1,
  failed: 1,
  source_sha256: "abc",
  output_path: "D://Videos//a.zh.srt",
  bilingual_path: "D://Videos//a.bilingual.srt",
  error: "1 条没译出来，点重试只补这些条目",
  cues: [
    {
      index: 0,
      start: 1,
      end: 2,
      source: "Hello world",
      translation: "你好世界",
      edited: false,
      state: "translated",
      error: null
    },
    {
      index: 1,
      start: 3,
      end: 4,
      source: "Second line",
      translation: "",
      edited: false,
      state: "failed",
      error: "本地模型暂时不可用"
    }
  ]
};

describe("字幕翻译抽屉", () => {
  test("打开就按 job/item 开始翻译，并画出进度与逐条对照", async () => {
    vi.mocked(startTranslation).mockResolvedValue(JOB);
    render(
      <SubtitleTranslateDialog
        jobId="job-running"
        itemId={jobPayload.items[0].id}
        title="Running video"
        refreshToken={0}
        onClose={() => {}}
      />
    );

    expect(await screen.findByText("字幕翻译")).toBeInTheDocument();
    expect(startTranslation).toHaveBeenCalledWith("job-running", jobPayload.items[0].id);
    expect(screen.getByText("1 / 2 条 · 1 条失败")).toBeInTheDocument();
    expect(screen.getByText("Hello world")).toBeInTheDocument();
    expect(screen.getByText("Second line")).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 条译文")).toHaveValue("你好世界");
  });

  test("筛选只显示失败项，空白译文确实没有拿原文顶上", async () => {
    vi.mocked(startTranslation).mockResolvedValue(JOB);
    const user = userEvent.setup();
    render(
      <SubtitleTranslateDialog
        jobId="job-running"
        itemId={null}
        title="视频"
        refreshToken={0}
        onClose={() => {}}
      />
    );
    await screen.findByText("字幕翻译");
    await user.selectOptions(screen.getByRole("combobox"), "failed");
    expect(screen.queryByText("Hello world")).not.toBeInTheDocument();
    expect(screen.getByText("Second line")).toBeInTheDocument();
    expect(screen.getByLabelText("第 2 条译文")).toHaveValue("");
  });

  test("编辑译文失焦后打到对应条目的接口；重试与导出各自可达", async () => {
    vi.mocked(startTranslation).mockResolvedValue(JOB);
    vi.mocked(editTranslationCue).mockResolvedValue(JOB);
    // 重试完成后仍是 succeeded，否则「导出」按钮会被进行中状态禁用，测不到导出本身。
    vi.mocked(retryTranslation).mockResolvedValue({
      ...JOB,
      failed: 0,
      error: null,
      cues: JOB.cues.map((cue) => ({ ...cue, state: "translated" as const, translation: "译文" }))
    });
    vi.mocked(exportTranslation).mockResolvedValue({
      srt_path: "a.zh.srt",
      bilingual_path: "a.bilingual.srt",
      cue_count: 2
    });
    const user = userEvent.setup();
    render(
      <SubtitleTranslateDialog
        jobId="job-running"
        itemId={null}
        title="视频"
        refreshToken={0}
        onClose={() => {}}
      />
    );
    await screen.findByText("字幕翻译");

    const textarea = screen.getByLabelText("第 1 条译文");
    await user.clear(textarea);
    await user.type(textarea, "我自己改的");
    await user.tab();
    expect(editTranslationCue).toHaveBeenCalledWith("tr-1", 0, "我自己改的");

    await user.click(screen.getByRole("button", { name: "重试失败项" }));
    expect(retryTranslation).toHaveBeenCalledWith("tr-1");

    await user.click(screen.getByRole("button", { name: /导出中文 \+ 对照 SRT/ }));
    expect(exportTranslation).toHaveBeenCalledWith("tr-1");
  });

  test("SSE 刷新令牌变化时只重拉这一条任务", async () => {
    vi.mocked(startTranslation).mockResolvedValue(JOB);
    vi.mocked(getTranslation).mockResolvedValue({ ...JOB, translated: 2, failed: 0, state: "succeeded" });
    const { rerender } = render(
      <SubtitleTranslateDialog
        jobId="job-running"
        itemId={null}
        title="视频"
        refreshToken={0}
        onClose={() => {}}
      />
    );
    await screen.findByText("字幕翻译");
    expect(getTranslation).not.toHaveBeenCalled();
    rerender(
      <SubtitleTranslateDialog
        jobId="job-running"
        itemId={null}
        title="视频"
        refreshToken={7}
        onClose={() => {}}
      />
    );
    expect(await screen.findByText("1 / 2 条 · 1 条失败")).toBeInTheDocument();
    expect(getTranslation).toHaveBeenCalledWith("tr-1");
  });

  test("导出后可把 zh 轨挂到 B站，并如实显示结论", async () => {
    vi.mocked(startTranslation).mockResolvedValue(JOB);
    vi.mocked(uploadBiliTranslation).mockResolvedValue({
      ...(await import("../test/appFixtures")).biliUploadPayload,
      translated_subtitle_state: "posted",
      translated_subtitle_message: "CC 字幕已上传（8 条，zh）"
    });
    const user = userEvent.setup();
    render(
      <SubtitleTranslateDialog
        jobId="job-running"
        itemId={null}
        title="视频"
        refreshToken={0}
        onClose={() => {}}
      />
    );
    await screen.findByText("字幕翻译");
    await user.click(screen.getByRole("button", { name: "上传中文字幕到 B站" }));
    expect(uploadBiliTranslation).toHaveBeenCalledWith("tr-1");
    expect(await screen.findByText(/zh 轨，与原字幕并存/)).toBeInTheDocument();
  });
});

describe("两个入口", () => {
  test("任务中心的「译为中文」紧贴字幕按钮，点了带出 job 与条目", async () => {
    const user = userEvent.setup();
    const onTranslateSubtitle = vi.fn();
    const withSubtitle = {
      ...jobPayload,
      total_items: 1,
      items: [{ ...jobPayload.items[0], subtitle_path: "D://Videos//a.en.vtt" }]
    };
    render(
      <JobQueue
        biliUploads={{}}
        jobs={[withSubtitle]}
        selectedJobIds={new Set()}
        onBatchAction={() => {}}
        onCloneJob={async () => null}
        onBiliRetrySeason={async () => true}
        onBiliRetryUpload={() => {}}
        onBiliRetryMany={() => {}}
        onBiliUpload={() => {}}
        onCopyLink={async () => {}}
        onDelete={() => {}}
        onDeleteItems={() => {}}
        onOpenFolder={() => {}}
        onOpenItemFolder={() => {}}
        onOpenSourcePage={() => {}}
        onOpenSubtitle={() => {}}
        onTranslateSubtitle={onTranslateSubtitle}
        onOpenVideoPage={() => {}}
        onPause={() => {}}
        onPlay={() => {}}
        onPlayItem={() => {}}
        onRestart={() => {}}
        onRestartItem={() => {}}
        onToggleJobSelection={() => {}}
      />
    );
    await user.click(screen.getByRole("button", { name: `译为中文 ${jobPayload.title}` }));
    expect(onTranslateSubtitle).toHaveBeenCalledWith("job-running", jobPayload.items[0].id);
  });

  test("投稿预览表的字幕列也有入口", async () => {
    const user = userEvent.setup();
    const onTranslateSubtitle = vi.fn();
    render(
      <BiliUploadDialog
        prefill={biliPrefillSinglePayload}
        isCollection={false}
        seasons={null}
        loadSeasons={() => {}}
        onCancel={() => {}}
        onSubmit={async () => {}}
        onTranslateSubtitle={onTranslateSubtitle}
      />
    );
    const entry = screen.getByRole("button", { name: /^译为中文 / });
    await user.click(entry);
    expect(onTranslateSubtitle).toHaveBeenCalledWith(
      biliPrefillSinglePayload.job_id,
      expect.any(String)
    );
  });
});
