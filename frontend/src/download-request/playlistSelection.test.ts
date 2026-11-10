import { describe, expect, test } from "vitest";
import {
  addIndices,
  areAllSelected,
  filterEntries,
  invertIndices,
  parseIndexRanges,
  removeIndices
} from "./playlistSelection";

/**
 * 纯判定层。这里钉的是「语义」，不是「界面上有没有那个输入框」：
 *
 * - 范围是**追加**，不是替换；
 * - 范围支持单点、闭区间、开区间（`5-` / `-5`），序号按条目表的 `#` 列；
 * - 写错时**整次不生效**并给出原因，不部分执行（半个范围比报错更难查）。
 */
describe("playlistSelection · 范围解析", () => {
  test("单点、闭区间、两端开区间", () => {
    expect(parseIndexRanges("3", 10).indices).toEqual([3]);
    expect(parseIndexRanges("2-4", 10).indices).toEqual([2, 3, 4]);
    expect(parseIndexRanges("-3", 10).indices).toEqual([1, 2, 3]);
    expect(parseIndexRanges("8-", 10).indices).toEqual([8, 9, 10]);
  });

  test("逗号（半角 / 全角）与空格都算分隔，结果去重且升序", () => {
    expect(parseIndexRanges("1,3，5 7", 10).indices).toEqual([1, 3, 5, 7]);
    expect(parseIndexRanges("2,2,1-3", 10).indices).toEqual([1, 2, 3]);
  });

  test("越界的端点被夹到合法区间，整段都在界外才算错", () => {
    expect(parseIndexRanges("0-2", 10).indices).toEqual([1, 2]);
    expect(parseIndexRanges("1-20", 10).indices).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(parseIndexRanges("11-20", 10).problems.length).toBeGreaterThan(0);
  });

  test("写错时整次不生效，并且说清错在哪", () => {
    const garbage = parseIndexRanges("abc", 10);
    expect(garbage.indices).toEqual([]);
    expect(garbage.problems[0]).toContain("abc");

    expect(parseIndexRanges("5-3", 10).problems[0]).toContain("起点大于终点");
    expect(parseIndexRanges("99", 10).problems[0]).toContain("超出范围");
    expect(parseIndexRanges("", 10).problems).toHaveLength(1);
    expect(parseIndexRanges("1-3", 0).problems).toHaveLength(1);
  });

  test("坏 token 与好 token 混在一起时，由调用方按 problems 整次丢弃", () => {
    const parsed = parseIndexRanges("1-3,abc", 10);
    expect(parsed.indices).toEqual([1, 2, 3]);
    expect(parsed.problems).toHaveLength(1);
    // 调用方按 problems 非空就整次丢弃 —— 这条断言把「怎么用」也钉住。
    expect(parsed.problems.length > 0).toBe(true);
  });
});

describe("playlistSelection · 标题筛选", () => {
  const entries = [
    { index: 1, title: "第 1 讲 · 数据库" },
    { index: 2, title: "第 2 讲 · 编译原理" },
    { index: 3, title: "第 3 讲 · Database" }
  ];

  test("普通子串、不区分大小写、忽略前后空格", () => {
    expect(filterEntries(entries, "数据库").map((entry) => entry.index)).toEqual([1]);
    expect(filterEntries(entries, "DATABASE").map((entry) => entry.index)).toEqual([3]);
    expect(filterEntries(entries, "  讲 · 编译  ").map((entry) => entry.index)).toEqual([2]);
  });

  test("空查询原样返回（同一个数组，不制造无谓的重渲染）", () => {
    expect(filterEntries(entries, "")).toBe(entries);
    expect(filterEntries(entries, "   ")).toBe(entries);
  });

  test("没有命中时返回空数组，不抛错", () => {
    expect(filterEntries(entries, "不存在")).toEqual([]);
  });
});

describe("playlistSelection · 集合运算", () => {
  test("追加不取消已选，去重", () => {
    const result = addIndices(new Set([1]), [2, 3, 3]);
    expect([...result].sort()).toEqual([1, 2, 3]);
  });

  test("移除只影响给定序号", () => {
    expect([...removeIndices(new Set([1, 2, 3]), [2])].sort()).toEqual([1, 3]);
  });

  test("反选只在给定集合里翻转", () => {
    expect([...invertIndices(new Set([1, 5]), [1, 2])].sort()).toEqual([2, 5]);
  });

  test("全选判定：空集合不算全选", () => {
    expect(areAllSelected(new Set([1, 2]), [1, 2])).toBe(true);
    expect(areAllSelected(new Set([1]), [1, 2])).toBe(false);
    expect(areAllSelected(new Set(), [])).toBe(false);
  });

  test("不修改传入的 Set（纯函数）", () => {
    const original = new Set([1]);
    addIndices(original, [2]);
    invertIndices(original, [1]);
    removeIndices(original, [1]);
    expect([...original]).toEqual([1]);
  });
});
