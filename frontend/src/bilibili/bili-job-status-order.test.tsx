import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { BiliJobStatus } from "./BiliJobStatus";
import { biliUploadPayload } from "../test/appFixtures";
import type { BiliAutoRetry, BiliJobUploads, BiliSeason, BiliUpload } from "../contracts/types";

/**
 * 视频状态提示区的**行序与文案**（一张卡片里三行话：审核 → 合集 → 字幕）。
 *
 * 三件事只有在真渲染时才能一起验：
 *
 * 1. **顺序**：稿件过没过审决定了下面两行会不会有变化，所以它在最上面。
 *    原先合集在第一行 —— 而合集之所以等着，正是因为审核还没过，读起来是「果 → 因」。
 * 2. **审核那行不写「审核中」**：前缀已经说了是「审核：」，再跟一个状态词是同一句话说两遍；
 *    但**已发布之后仍要带上「已发布」**，那时用户真正要看的是「到底发出去没有」。
 * 3. **每行只留一句结论**：合集那行原来是 `状态 + 原因 + 建议` 三句拼在一起。
 */

afterEach(cleanup);

function season(overrides: Partial<BiliSeason> = {}): BiliSeason {
  return {
    id: "season-1",
    job_id: "job-1",
    title: "合集",
    desc: "",
    season_id: 9001,
    status: "pending",
    status_text: "等待归集",
    upload_ids: [biliUploadPayload.id],
    // 服务端此刻会同时给出这三句 —— 界面只该留下第一句。
    error: "视频还在 B站 排队审核，暂时没法加进合集",
    advice: "等 B站 审核通过后点「重试归集」—— 它会先去平台核对分P编号，再归集",
    auto_retry: null,
    ...overrides
  };
}

function uploads(upload: BiliUpload, seasonValue: BiliSeason | null = null): BiliJobUploads {
  return {
    job_id: "job-1",
    badge: "uploading",
    badge_text: "上传中",
    badge_tone: "busy",
    uploads: [upload],
    season: seasonValue
  };
}

function renderStatus(value: BiliJobUploads) {
  const { container, rerender } = render(
    <BiliJobStatus
      uploads={value}
      onRetryUpload={() => {}}
      onRetrySeason={async () => true}
      onOpenVideoPage={() => {}}
    />
  );
  return { container, rerender };
}

/**
 * 三行话**各自的标签文本**，按渲染顺序取（`.bili-job-status` 是 grid，DOM 顺序就是视觉顺序）。
 *
 * 只取 `.bili-status-label` 而不是整行 `textContent`：整行里还有「在 B 站查看」这个按钮的文字，
 * 拿整行去比字符串会把一个按钮也算进「这句话」里。
 */
function lines(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".bili-status-line .bili-status-label")].map(
    (n) => n.textContent ?? ""
  );
}

describe("视频状态提示区 · 行序", () => {
  test("自上而下：审核 → 合集 → 字幕", () => {
    const { container } = renderStatus(
      uploads(
        {
          ...biliUploadPayload,
          status: "reviewing",
          status_text: "审核中",
          subtitle_state: "failed",
          subtitle_message: "稿件已提交（reviewing），但平台还没给出分P编号，字幕暂时传不了"
        },
        season()
      )
    );

    const text = lines(container);
    expect(text).toHaveLength(3);
    // 位置就是判据：DOM 顺序 == 视觉顺序（grid 单列）。
    expect(text[0]).toMatch(/^审核：/);
    expect(text[1]).toMatch(/^合集：/);
    expect(text[2]).toMatch(/^字幕：/);
  });

  test("审核那行在最上方，即使合集与字幕都还没话说", () => {
    const { container } = renderStatus(
      uploads({ ...biliUploadPayload, status: "published", status_text: "已发布" }, null)
    );

    const text = lines(container);
    expect(text).toHaveLength(1);
    expect(text[0]).toMatch(/^审核：/);
  });
});

describe("视频状态提示区 · 审核那一行", () => {
  test("审核中：只写「审核：<标题>」，不再写「审核中」，也不再带条目序号", () => {
    const { container } = renderStatus(uploads({ ...biliUploadPayload, status: "reviewing", status_text: "审核中" }));

    expect(
      screen.getByText(`审核：${biliUploadPayload.title}`)
    ).toBeInTheDocument();
    // 「审核中」与「1. 」都不该再出现 —— 前缀已经把它们的意思说完了。
    // 只看**状态行**：进度块里那句「已提交，B 站 审核中」说的是上传到哪一步了，是另一件事。
    expect(container.querySelector(".bili-status-line")?.textContent).not.toContain("审核中");
    expect(screen.queryByText(/^1\. /)).not.toBeInTheDocument();
    // 链接照旧：审核中也能去 B 站看这一条。
    expect(screen.getByRole("button", { name: `在 B 站打开 ${biliUploadPayload.title}` })).toBeInTheDocument();
  });

  test("审核通过：仍显示「已发布」与「在 B 站查看」", () => {
    renderStatus(uploads({ ...biliUploadPayload, status: "published", status_text: "已发布" }));

    expect(screen.getByText(`审核：${biliUploadPayload.title} · 已发布`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `在 B 站打开 ${biliUploadPayload.title}` })).toBeInTheDocument();
  });

  test("状态一变，同一行就地改写（不是多出一行）", () => {
    const { container, rerender } = renderStatus(
      uploads({ ...biliUploadPayload, status: "reviewing", status_text: "审核中" })
    );
    expect(lines(container)).toHaveLength(1);
    expect(lines(container)[0]).toBe(`审核：${biliUploadPayload.title}`);

    rerender(
      <BiliJobStatus
        uploads={uploads({ ...biliUploadPayload, status: "published", status_text: "已发布" })}
        onRetryUpload={() => {}}
        onRetrySeason={async () => true}
        onOpenVideoPage={() => {}}
      />
    );

    // 还是那一行，只是句尾补上了「已发布」。
    expect(lines(container)).toHaveLength(1);
    expect(lines(container)[0]).toBe(`审核：${biliUploadPayload.title} · 已发布`);
  });

  test("失败仍是失败：「审核：<标题>」不能把失败说成还在审核", () => {
    renderStatus(
      uploads({
        ...biliUploadPayload,
        status: "failed",
        status_text: "上传失败",
        video_url: null,
        error: "投稿命令退出码 1",
        advice: "看日志"
      })
    );

    expect(screen.getByText(`审核：${biliUploadPayload.title} · 上传失败`)).toBeInTheDocument();
    expect(screen.getByText("投稿命令退出码 1")).toBeInTheDocument();
  });
});

describe("视频状态提示区 · 合集那一行", () => {
  test("等待归集：只有一句话，不再拼原因与建议", () => {
    renderStatus(uploads({ ...biliUploadPayload, status: "reviewing", status_text: "审核中" }, season()));

    expect(screen.getByText("合集：等待 B 站视频审核后进行归集")).toBeInTheDocument();
    // 服务端给的那两句长文案不再出现 —— 它们说的是同一件事。
    expect(screen.queryByText(/暂时没法加进合集/)).not.toBeInTheDocument();
    expect(screen.queryByText(/先去平台核对分P编号/)).not.toBeInTheDocument();
    // 手动按钮照旧。
    expect(screen.getByRole("button", { name: "重试归集" })).toBeEnabled();
  });

  test("归集失败：原因与建议照旧要留下（那不是重复）", () => {
    renderStatus(
      uploads(
        { ...biliUploadPayload, status: "published", status_text: "已发布" },
        season({ status: "failed", status_text: "归集失败", error: "合集不存在了", advice: "重建合集" })
      )
    );

    expect(screen.getByText("合集：归集失败")).toBeInTheDocument();
    expect(screen.getByText("合集不存在了")).toBeInTheDocument();
    expect(screen.getByText("重建合集")).toBeInTheDocument();
  });
});

describe("视频状态提示区 · 倒计时", () => {
  test("两处都是 mm:ss，并按秒递减", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
    try {
      const retry: BiliAutoRetry = {
        state: "waiting",
        attempts: 0,
        next_attempt_at: "2026-11-03T12:00:35Z",
        last_result: "waiting",
        last_message: "视频还在 B站 排队审核，暂时没法加进合集"
      };
      const { container } = renderStatus(
        uploads(
          {
            ...biliUploadPayload,
            status: "reviewing",
            status_text: "审核中",
            subtitle_state: "failed",
            subtitle_message: "稿件已提交（reviewing），但平台还没给出分P编号",
            auto_retry: retry
          },
          season({ auto_retry: { ...retry, next_attempt_at: "2026-11-03T12:02:00Z" } })
        )
      );

      // 合集那一处：还剩 2 分钟；字幕那一处：还剩 35 秒。**两处都精确到秒**。
      expect(screen.getByText("下一次自动重试 02:00")).toBeInTheDocument();
      expect(screen.getByText("下一次自动重试 00:35")).toBeInTheDocument();
      // 原来那句含糊话不该再出现。
      expect(screen.queryByText(/不到 1 分钟/)).not.toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(screen.getByText("下一次自动重试 01:59")).toBeInTheDocument();
      expect(screen.getByText("下一次自动重试 00:34")).toBeInTheDocument();
      expect(container.querySelectorAll(".bili-auto-retry")).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
