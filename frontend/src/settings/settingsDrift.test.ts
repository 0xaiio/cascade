import { describe, expect, test } from "vitest";

import type { SettingEffective } from "../contracts/types";
import { driftedSettings, settingsDriftText } from "./settingsDrift";

/**
 * 「库里 vs 进程实际」的文案（[ai/bug-fix/035](../../../ai/bug-fix/035-diagnostics-shows-the-stored-value.md)）。
 *
 * 它有一条硬约束：**没有差异时必须返回 `null`** —— 一致的时候冒出一句「配置正常」，
 * 既占位置又让人以为它在盯着什么。
 */
function item(overrides: Partial<SettingEffective>): SettingEffective {
  return { key: "download_dir", stored: null, runtime: "D:\\downloads", differs: false, ...overrides };
}

describe("settingsDriftText", () => {
  test("没有差异时一个字都不说", () => {
    expect(settingsDriftText([])).toBeNull();
    expect(settingsDriftText(null)).toBeNull();
    expect(settingsDriftText([item({ stored: "D:\\downloads" })])).toBeNull();
  });

  test("只挑出 differs 的项，并把两个值都写出来", () => {
    const items = [
      item({ key: "download_dir", stored: "D:\\elsewhere", differs: true }),
      item({ key: "default_concurrency", stored: null, runtime: "5" }),
    ];

    expect(driftedSettings(items)).toHaveLength(1);
    const text = settingsDriftText(items);
    expect(text).toContain("有 1 项设置的「进程实际值」与库里不同");
    expect(text).toContain("下载目录：库里 D:\\elsewhere，进程用 D:\\downloads");
    // 没差异的那一项不许出现在句子里。
    expect(text).not.toContain("并发数");
  });

  test("库里的值是空串时写成「未设置」，不要留一个空白", () => {
    const text = settingsDriftText([item({ key: "proxy", stored: "", runtime: "http://127.0.0.1:7890", differs: true })]);

    expect(text).toContain("代理：库里 （未设置），进程用 http://127.0.0.1:7890");
  });

  test("不认识的键露出键名，不猜一个中文名", () => {
    const text = settingsDriftText([item({ key: "some_new_knob", stored: "1", runtime: "0", differs: true })]);

    expect(text).toContain("some_new_knob：库里 1，进程用 0");
  });

  test("多项差异按顺序全列出来，不是只报第一条", () => {
    const text = settingsDriftText([
      item({ key: "download_dir", stored: "A", differs: true }),
      item({ key: "bili_auto_retry_max_attempts", stored: "9", runtime: "3", differs: true }),
    ]);

    expect(text).toContain("有 2 项");
    expect(text).toContain("下载目录");
    expect(text).toContain("自动重试上限");
  });
});
