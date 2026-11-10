import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import type { AnalyzeResponse } from "../contracts/types";
import App from "../App";
import { analyzePayload } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

/**
 * A-03 / ai/bug-fix/039：设置里的默认字幕语言（出厂 `["en"]`）不能原样塞进一个
 * 没有英文轨的视频 —— 那会让后端绕过「按主语言挑轨」，yt-dlp 匹配不到任何轨，
 * **静默不下字幕**，而界面还写着「已选 1 项：en」且摘不掉。
 *
 * 收敛之后：命中就保留，一个都不命中就留空 —— 空 = 自动（按视频主语言挑一条）。
 */

function analysisWith(human: string[], auto: string[]): AnalyzeResponse {
  return {
    ...analyzePayload,
    subtitles: human.map((language) => ({ language, name: null, formats: ["vtt"] })),
    automatic_subtitles: auto.map((language) => ({ language, name: null, formats: ["vtt"] }))
  } as AnalyzeResponse;
}

async function analyzeWithSubs(user: UserEvent, human: string[], auto: string[]) {
  mockState.analyze = analysisWith(human, auto);
  render(<App />);
  await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
  await user.click(screen.getByRole("button", { name: "解析链接" }));
  await screen.findByText("Batch");
}

function lastJobBody(): Record<string, { subtitle_languages?: string[] }> {
  const calls = (fetch as unknown as { mock: { calls: [string, { body?: string }][] } }).mock.calls;
  const last = calls.at(-1);
  return JSON.parse(String(last?.[1]?.body));
}

describe("App · 默认字幕语言要收敛到视频真有的语言", () => {
  test("视频只有中文轨时不再假装选着 en，提交的是空列表（= 自动）", async () => {
    const user = userEvent.setup();
    await analyzeWithSubs(user, [], ["zh-Hans"]);

    // 关键：没有「已选 1 项：en」这种摘不掉的语言。
    expect(screen.queryByRole("button", { name: /已选 1 项：en/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "自动（按视频语言）" })).toBeInTheDocument();
    expect(screen.getByText(/未选择时按视频主语言自动挑一条/)).toBeInTheDocument();

    // 下拉里也没有 en 的复选框 —— 它本来就不存在。
    await user.click(screen.getByRole("button", { name: "自动（按视频语言）" }));
    expect(screen.queryByLabelText("字幕 en")).not.toBeInTheDocument();
    expect(screen.getByLabelText("字幕 zh-Hans")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "加入下载队列" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/jobs", expect.objectContaining({ method: "POST" })));
    expect(lastJobBody().options.subtitle_languages).toEqual([]);
  });

  test("视频有 en 轨时默认值照旧生效", async () => {
    const user = userEvent.setup();
    await analyzeWithSubs(user, ["en"], ["zh-Hans"]);

    expect(screen.getByRole("button", { name: /已选 1 项：en/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "加入下载队列" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/jobs", expect.objectContaining({ method: "POST" })));
    expect(lastJobBody().options.subtitle_languages).toEqual(["en"]);
  });

  test("收敛之后仍可手动显式选一条（显式选择优先于自动）", async () => {
    const user = userEvent.setup();
    await analyzeWithSubs(user, [], ["zh-Hans"]);

    await user.click(screen.getByRole("button", { name: "自动（按视频语言）" }));
    await user.click(screen.getByLabelText("字幕 zh-Hans"));
    await user.keyboard("{Escape}");

    expect(screen.getByRole("button", { name: /已选 1 项：zh-Hans/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "加入下载队列" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/jobs", expect.objectContaining({ method: "POST" })));
    expect(lastJobBody().options.subtitle_languages).toEqual(["zh-Hans"]);
  });
});
