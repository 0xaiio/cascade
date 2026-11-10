import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import { analyzePayload } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

/**
 * A-02：playlist 条目的批量选择（范围 / 反选 / 标题筛选）。
 *
 * 这一组用例盯的是**语义**，不是控件存在：
 * - 范围是**追加**（已勾选的不会因为范围而消失）；
 * - 筛选只改显示，不改选择；被筛掉的已勾选条目必须被**数出来**（「其中 N 条被筛选隐藏」）；
 * - 批量动作只作用于当前显示的行；
 * - 写错了就整次不生效，并在原地给出原因；
 * - 换一份解析结果后，筛选与范围不会跟着带到新列表上。
 */

function playlistOf(total: number) {
  return {
    ...analyzePayload,
    entries: Array.from({ length: total }, (_, index) => ({
      index: index + 1,
      id: `v${index + 1}`,
      // 单双号两种标题，方便用「数据库 / 编译原理」验证筛选；序号也写进标题便于肉眼对照。
      title: `第 ${index + 1} 讲 · ${index % 2 === 0 ? "数据库" : "编译原理"}`,
      url: `https://youtu.be/v${index + 1}`,
      duration: 60,
      thumbnail: null
    }))
  };
}

async function analyzePlaylist(user: UserEvent, total = 8) {
  mockState.analyze = playlistOf(total);
  render(<App />);
  await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
  await user.click(screen.getByRole("button", { name: "解析链接" }));
  await screen.findByText("Batch");
}

/** 解析完默认**全选**（沿用既有行为）。要从空开始测，就先把选择清空。 */
async function clearSelection(user: UserEvent) {
  await user.click(screen.getByRole("button", { name: "清空选择" }));
  expect(screen.getByText(/已选 0 \/ \d+/)).toBeInTheDocument();
}

describe("App · playlist 条目的批量选择", () => {
  test("范围是追加：已勾选的一条都不取消", async () => {
    const user = userEvent.setup();
    await analyzePlaylist(user);
    await clearSelection(user);

    await user.click(screen.getByLabelText("选择 第 1 讲 · 数据库"));
    await user.type(screen.getByLabelText("范围"), "2-3");
    await user.click(screen.getByRole("button", { name: "选中范围内的条目" }));

    expect(screen.getByLabelText("选择 第 2 讲 · 编译原理")).toBeChecked();
    expect(screen.getByLabelText("选择 第 3 讲 · 数据库")).toBeChecked();
    // 关键：原来的第 1 条**还在**。若是替换语义，这里会变回未勾选。
    expect(screen.getByLabelText("选择 第 1 讲 · 数据库")).toBeChecked();
    expect(screen.getByText(/已加入 2 条（不会取消已选）/)).toBeInTheDocument();
    expect(screen.getByText(/已选 3 \/ 8/)).toBeInTheDocument();
  });

  test("反选只翻转当前显示的行，连点两次回到原状", async () => {
    const user = userEvent.setup();
    await analyzePlaylist(user);
    // 解析完默认全选，所以第一次反选应当清空。
    await user.click(screen.getByRole("button", { name: "反选" }));
    expect(screen.getByText(/已选 0 \/ 8/)).toBeInTheDocument();
    expect(screen.getByLabelText("选择 第 4 讲 · 编译原理")).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "反选" }));
    expect(screen.getByText(/已选 8 \/ 8/)).toBeInTheDocument();
    expect(screen.getByLabelText("选择 第 4 讲 · 编译原理")).toBeChecked();
  });

  test("筛选只改显示：隐藏的已勾选条目保持勾选，并被数出来", async () => {
    const user = userEvent.setup();
    await analyzePlaylist(user);
    await clearSelection(user);

    await user.click(screen.getByLabelText("选择 第 1 讲 · 数据库"));
    await user.type(screen.getByLabelText("按标题筛选条目"), "编译");

    // 只剩单号之外的 4 条。
    expect(screen.queryByLabelText("选择 第 1 讲 · 数据库")).not.toBeInTheDocument();
    expect(screen.getByLabelText("选择 第 2 讲 · 编译原理")).toBeInTheDocument();
    expect(screen.getByText(/已选 1 \/ 8（其中 1 条被筛选隐藏）/)).toBeInTheDocument();

    // 全选只作用于显示的行：4 条加入，隐藏的第 1 条仍在选择里。
    await user.click(screen.getByRole("button", { name: "全选" }));
    expect(screen.getByText(/已选 5 \/ 8（其中 1 条被筛选隐藏）/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.getByLabelText("选择 第 1 讲 · 数据库")).toBeChecked();
  });

  test("范围在筛选下也只作用于显示的行，并说明跳过了几条", async () => {
    const user = userEvent.setup();
    await analyzePlaylist(user);
    await clearSelection(user);

    await user.type(screen.getByLabelText("按标题筛选条目"), "数据库");
    await user.type(screen.getByLabelText("范围"), "1-8");
    await user.click(screen.getByRole("button", { name: "选中范围内的条目" }));

    expect(screen.getByText(/已加入 4 条；4 条被筛选隐藏，未加入/)).toBeInTheDocument();
    expect(screen.getByText(/已选 4 \/ 8/)).toBeInTheDocument();
  });

  test("写错范围时整次不生效，并在原地说明原因", async () => {
    const user = userEvent.setup();
    await analyzePlaylist(user);
    await clearSelection(user);

    await user.type(screen.getByLabelText("范围"), "abc");
    await user.click(screen.getByRole("button", { name: "选中范围内的条目" }));

    expect(screen.getByRole("alert")).toHaveTextContent("看不懂「abc」");
    expect(screen.getByText(/已选 0 \/ 8/)).toBeInTheDocument();

    // 改一下输入，错误立刻收回（不让旧错误挂在新输入旁边）。
    await user.type(screen.getByLabelText("范围"), "1");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("换一份解析结果后，筛选与范围不会带过去", async () => {
    const user = userEvent.setup();
    await analyzePlaylist(user);

    await user.type(screen.getByLabelText("按标题筛选条目"), "编译");
    await user.type(screen.getByLabelText("范围"), "2-3");

    await user.click(screen.getByRole("button", { name: "解析链接" }));

    await waitFor(() => expect(screen.getByLabelText("按标题筛选条目")).toHaveValue(""));
    expect(screen.getByLabelText("范围")).toHaveValue("");
    // 8 条又都在了（筛选没有留下来）。
    expect(screen.getByLabelText("选择 第 1 讲 · 数据库")).toBeInTheDocument();
  });
});
