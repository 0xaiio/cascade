import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

/**
 * 设置面板的功能分区。
 *
 * 这里钉的不是「有没有那几行字」，而是**归属**：某个控件到底在哪个分区里，
 * 以及分区顺序有没有被重排。判据一律走可访问角色（`role="region"` / `h3`），
 * 不走 class 名 —— class 是样式实现细节，改一次样式就会让这些断言假红或假绿。
 *
 * 顺序是产品决定：按用户碰到它们的先后排（下载 → 联网 → 提醒 → 投稿 → 排障）。
 * 改动顺序要同时改这里、`SettingsPanel.tsx` 的注释与 `docs/user-manual.md` 的分区表。
 */
const GROUP_ORDER = ["下载与存储", "代理与网络", "完成提醒", "B站 上传", "运行环境"];

function settingsPanel(): HTMLElement {
  return screen.getByRole("heading", { name: "设置" }).closest("section") as HTMLElement;
}

function group(name: string): HTMLElement {
  return within(settingsPanel()).getByRole("region", { name });
}

async function renderSettings(): Promise<HTMLElement> {
  render(<App />);
  await screen.findByRole("heading", { name: "设置" });
  return settingsPanel();
}

describe("App · 设置面板的功能分区", () => {
  test("五个分区按用户旅程排列，标题比面板标题低一级", async () => {
    const panel = await renderSettings();

    const regions = within(panel).getAllByRole("region");
    expect(
      regions.map((region) => within(region).getByRole("heading", { level: 3 }).textContent?.trim())
    ).toEqual(GROUP_ORDER);

    // 每个分区的可访问名字必须来自它自己的 h3：标题与 aria-labelledby 各写一份字符串时，
    // 改了标题却忘了改 id，读屏与测试看到的就还是旧名字。
    for (const region of regions) {
      const id = region.getAttribute("aria-labelledby");
      expect(id).toBeTruthy();
      expect(region.querySelector(`#${id}`)).toBe(region.querySelector("h3"));
    }
  });

  test("每一项都落在它所属的分区里，且不在别的分区里", async () => {
    await renderSettings();

    const storage = group("下载与存储");
    expect(within(storage).getByLabelText("下载目录")).toBeInTheDocument();
    expect(within(storage).getByLabelText("并发（同时下载的视频数；单个视频无效）")).toBeInTheDocument();
    expect(within(storage).getByLabelText("单视频并发下载数（1–4）")).toBeInTheDocument();
    // 反证：代理属于另一区。只正向断言的话，把同一项在两组里各渲染一遍也会「通过」。
    expect(within(storage).queryByLabelText(/^代理（/)).toBeNull();
    expect(within(storage).queryByRole("button", { name: "重新体检" })).toBeNull();

    const network = group("代理与网络");
    expect(within(network).getByLabelText("代理（留空 = 自动；填 direct 强制直连）")).toBeInTheDocument();
    expect(within(network).getByRole("button", { name: "检测代理" })).toBeInTheDocument();
    expect(within(network).getByRole("button", { name: "先试输入框里的地址" })).toBeInTheDocument();
    expect(within(network).getByRole("button", { name: "说明：常见代理软件的本地端口" })).toBeInTheDocument();
    expect(within(network).queryByLabelText("下载目录")).toBeNull();

    const notify = group("完成提醒");
    expect(within(notify).getByLabelText("任务完成提醒方式")).toBeInTheDocument();
    expect(within(notify).getByRole("button", { name: "预览" })).toBeInTheDocument();
    expect(within(notify).queryByRole("button", { name: "检测代理" })).toBeNull();

    const bili = group("B站 上传");
    // 挂载即体检，按钮在这一瞬间是「体检中…」：等它跑完再找「重新体检」。
    expect(await within(bili).findByRole("button", { name: "重新体检" })).toBeInTheDocument();
    expect(within(bili).getByRole("button", { name: "扫码登录" })).toBeInTheDocument();
    expect(within(bili).getByRole("checkbox", { name: /B站 自动重试/ })).toBeInTheDocument();
    expect(within(bili).getByLabelText("自动重试间隔（秒，10–300）")).toBeInTheDocument();
    expect(within(bili).getByLabelText("自动重试上限（次，1–10）")).toBeInTheDocument();

    const runtime = group("运行环境");
    expect(within(runtime).getByRole("button", { name: "重新自检" })).toBeInTheDocument();
    expect(within(runtime).getByText(/日志文件：/)).toBeInTheDocument();
  });

  // 「JS 运行时」原先长在代理区块里，只因为两者共用 GET /api/diagnostics。
  // 数据同源不是功能同类：代理回答「能不能连出去」，它回答「本机组件齐不齐」。
  // 这条断言固定那次拆分 —— 谁把它挪回代理区，这里立刻红。
  test("JS 运行时归「运行环境」，不在「代理与网络」里", async () => {
    await renderSettings();

    expect(within(group("运行环境")).getByText(/JS 运行时（解析 YouTube 的 n 参数/)).toBeInTheDocument();
    expect(within(group("代理与网络")).queryByText(/JS 运行时/)).toBeNull();
  });

  test("分区不改变交互：在「代理与网络」里试地址，回执仍留在同一区", async () => {
    const user = userEvent.setup();
    await renderSettings();
    const network = group("代理与网络");

    await user.type(
      within(network).getByLabelText("代理（留空 = 自动；填 direct 强制直连）"),
      "127.0.0.1:10809"
    );
    await user.click(within(network).getByRole("button", { name: "先试输入框里的地址" }));

    expect(await within(network).findByText(/返回 HTTP 200/)).toBeInTheDocument();
    // 试地址不等于保存：这次点击不该把输入框里的值写进设置。
    expect(fetch).not.toHaveBeenCalledWith("/api/settings", expect.objectContaining({ method: "PUT" }));
    // 重新渲染之后分区结构没有被拆掉。
    expect(within(settingsPanel()).getAllByRole("region")).toHaveLength(GROUP_ORDER.length);
  });

  test("分区不改变交互：「B站 上传」里关掉自动重试，同组两个数字立刻改不动", async () => {
    const user = userEvent.setup();
    await renderSettings();
    const bili = group("B站 上传");
    const checkbox = within(bili).getByRole("checkbox", { name: /B站 自动重试/ });
    const interval = within(bili).getByLabelText("自动重试间隔（秒，10–300）");
    const attempts = within(bili).getByLabelText("自动重试上限（次，1–10）");

    expect(checkbox).toBeChecked();
    await user.click(checkbox);

    await waitFor(() => expect(checkbox).not.toBeChecked());
    expect(interval).toBeDisabled();
    expect(attempts).toBeDisabled();
  });

  // 保存回执是**面板级**的：它说的是「刚才那一下存没存上」，不属于任何一个功能域。
  // 若它被塞进最后一个分区，读起来就成了「运行环境保存成功」。
  test("保存回执挂在面板级，不被读成最后一个分区的事", async () => {
    mockState.settingsUpdateDelayMs = 120;
    const user = userEvent.setup();
    await renderSettings();

    const concurrency = screen.getByLabelText("并发（同时下载的视频数；单个视频无效）");
    await user.clear(concurrency);
    await user.type(concurrency, "4");
    await user.tab();

    const status = await screen.findByText("保存中...");
    expect(status.closest(".settings-group")).toBeNull();
    expect(status.closest("section")).toBe(settingsPanel());
  });
});
