/**
 * E-03 · 按钮提示必须把「重启 = 什么被清、什么被留」说全。
 *
 * 这些断言是对用户手册那张表的镜像：少一条，界面就会再次出现「用户以为要从零重下」。
 */

import { describe, expect, test } from "vitest";
import {
  RESTART_SUMMARY,
  batchRestartHint,
  itemRestartHint,
  jobRestartHint,
  resolutionRestartHint
} from "./restartSemantics";

const HINTS = [
  jobRestartHint(),
  itemRestartHint(),
  batchRestartHint(),
  resolutionRestartHint("720p")
];

describe("重启语义文案（E-03）", () => {
  test("结论句覆盖「重置」「沿用」「保留」三组事实", () => {
    expect(RESTART_SUMMARY).toContain("重新排队");
    expect(RESTART_SUMMARY).toContain("重跑一次预检");
    expect(RESTART_SUMMARY).toContain("清空界面上的进度、大小、时间与错误记录");
    expect(RESTART_SUMMARY).toContain("沿用原来的清晰度与下载选项");
    expect(RESTART_SUMMARY).toContain("不会从零重下");
    expect(RESTART_SUMMARY).toContain(".part 会断点续传");
    expect(RESTART_SUMMARY).toContain("已下载的文件和任务历史都不会被删除");
  });

  test.each(HINTS)("四个入口都写明「不会从零重下」与「.part 续传」", (hint) => {
    expect(hint).toContain("不会从零重下");
    expect(hint).toContain(".part 会断点续传");
    expect(hint).toContain("已下载的文件");
  });

  test("任务级点明暂停后的恢复也只有这一个入口", () => {
    expect(jobRestartHint()).toContain("暂停过的任务也点它恢复");
    expect(jobRestartHint()).toContain("没有单独的「继续」按钮");
  });

  test("条目级点明只影响这一条", () => {
    expect(itemRestartHint()).toContain("其它条目不受影响");
  });

  test("批量级点明每个任务都走同一套语义", () => {
    expect(batchRestartHint()).toContain("每个任务");
    expect(batchRestartHint()).toContain("重新排队");
  });

  test("换清晰度是唯一不「沿用原选项」的入口", () => {
    const hint = resolutionRestartHint("720p");
    expect(hint).toContain("以 720p 重启");
    expect(hint).toContain("改用这个清晰度");
    expect(hint).not.toContain("沿用原来的清晰度");
    expect(jobRestartHint()).toContain("沿用原来的清晰度");
  });
});
