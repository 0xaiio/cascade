import { describe, expect, test } from "vitest";
import type { AnalyzeResponse } from "../contracts/types";
import { analyzePayload } from "../test/appFixtures";
import { effectiveSubtitleLanguages } from "./subtitles";

/**
 * 「默认字幕语言」必须收敛到**这个视频真的有的**语言（ai/bug-fix/039）。
 *
 * 为什么不收敛就是缺陷：`subtitle_languages` 非空时后端不再按主语言挑轨
 * （显式选择优先），yt-dlp 拿一个视频里不存在的语言会匹配不到任何轨 —— 静默不下字幕、
 * 也不报错，任务照样成功。界面上却写着「已选 1 项：en」。
 */
function analysisWith(human: string[], auto: string[]): AnalyzeResponse {
  return {
    ...analyzePayload,
    subtitles: human.map((language) => ({ language, name: null, formats: ["vtt"] })),
    automatic_subtitles: auto.map((language) => ({ language, name: null, formats: ["vtt"] }))
  } as AnalyzeResponse;
}

describe("subtitles · 默认语言收敛", () => {
  test("视频有这条轨时原样保留", () => {
    expect(effectiveSubtitleLanguages(analysisWith(["en"], []), ["en"])).toEqual(["en"]);
    // 人工没有、自动有，也算「有」：下载时会降级到 ASR。
    expect(effectiveSubtitleLanguages(analysisWith([], ["en"]), ["en"])).toEqual(["en"]);
  });

  test("视频没有的语言被丢掉，一个都不剩就返回空（= 交给后端的字幕计划）", () => {
    expect(effectiveSubtitleLanguages(analysisWith([], ["zh-Hans"]), ["en"])).toEqual([]);
  });

  test("多语言偏好保序、去重，只留下该视频真有的", () => {
    const analysis = analysisWith(["en"], ["zh-Hans"]);
    expect(effectiveSubtitleLanguages(analysis, ["zh-Hans", "en"])).toEqual(["zh-Hans", "en"]);
    expect(effectiveSubtitleLanguages(analysis, ["en", "en", "pt-BR"])).toEqual(["en"]);
  });

  test("偏好为空时结果为空（空 = 自动，不是「不下字幕」）", () => {
    expect(effectiveSubtitleLanguages(analysisWith(["en"], ["zh-Hans"]), [])).toEqual([]);
  });

  test("不修改传入的偏好数组", () => {
    const preferred = ["en", "ja"];
    effectiveSubtitleLanguages(analysisWith(["en"], []), preferred);
    expect(preferred).toEqual(["en", "ja"]);
  });
});
