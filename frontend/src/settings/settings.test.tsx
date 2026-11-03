import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import { proxyTestFailurePayload, settingsPayload } from "../test/appFixtures";
import { ensureExpanded, exactRow, installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

describe("App · 设置面板与代理检测", () => {
  test("autosaves concurrency without a save settings button", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByLabelText("下载目录")).toBeInTheDocument();
    const concurrency = screen.getByLabelText("并发（同时下载的视频数；单个视频无效）");
    expect(concurrency).toBeInTheDocument();
    await waitFor(() => expect(concurrency).toHaveValue(2));
    expect(screen.queryByText("默认跟随 CPU core 数量，可按需覆盖。")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/默认清晰度/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存设置" })).not.toBeInTheDocument();

    await user.clear(concurrency);
    await user.type(concurrency, "4");
    await user.tab();

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ default_concurrency: 4 })
        })
      );
    });
  });

  test("autosaves aria2c connections and clamps them to four", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    const connections = screen.getByLabelText("单视频并发下载数（1–4）");
    await waitFor(() => expect(connections).toHaveValue(2));

    await user.clear(connections);
    await user.type(connections, "9");
    await user.tab();

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ aria2c_connections: 4 })
        })
      );
    });
  });

  test("autosaves the proxy and sends null when cleared back to auto", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    const proxy = screen.getByLabelText("代理（留空 = 自动；填 direct 强制直连）");

    await user.type(proxy, "127.0.0.1:1080");
    await user.tab();

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ proxy: "127.0.0.1:1080" })
        })
      );
    });

    await user.clear(proxy);
    await user.tab();

    // 清空 = 回到「自动」（null），不是「强制直连」——后者要显式填 direct。
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ proxy: null })
        })
      );
    });
  });

  test("shows which proxy is actually in effect", async () => {
    mockState.settings = {
      ...settingsPayload,
      proxy_source: "system",
      proxy_effective: "http://127.0.0.1:7890"
    };
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();

    expect(
      screen.getByText("当前生效：http://127.0.0.1:7890（来源：Windows 系统代理）")
    ).toBeInTheDocument();
  });

  test("selects download directory with a folder dialog", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByDisplayValue("downloads")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "选择文件夹" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings/download-dir/select",
        expect.objectContaining({ method: "POST" })
      );
    });
    expect(screen.getByDisplayValue("D:\\Videos")).toBeInTheDocument();
  });

  test("shows save status when runtime download settings are saved", async () => {
    mockState.settingsUpdateDelayMs = 80;
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("限速 KB/s（清空：不限速）"), "5");

    expect(await screen.findByText("保存中...")).toBeInTheDocument();
    expect(await screen.findByText("已保存")).toBeInTheDocument();
  });

  test("shows save failure when runtime download settings cannot be saved", async () => {
    mockState.settingsUpdateShouldFail = true;
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("限速 KB/s（清空：不限速）"), "5");

    await waitFor(() => expect(screen.getAllByText("保存失败").length).toBeGreaterThan(0));
  });

  test("tests the saved proxy and shows the failure with next steps", async () => {
    const user = userEvent.setup();
    mockState.proxyTest = proxyTestFailurePayload;
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "检测代理" }));

    await waitFor(() => {
      expect(mockState.proxyTestBodies).toEqual([{}]);
    });

    const result = await screen.findByText(/访问失败/);
    expect(result).toBeInTheDocument();
    expect(screen.getByText("来源：你手动填写的地址（setting）")).toBeInTheDocument();
    expect(screen.getByText(exactRow("LI", "HTTP 状态：无响应　耗时：2008 ms　收到：0 字节"))).toBeInTheDocument();
    expect(screen.getByText(/确认代理软件正在运行、端口一致/)).toBeInTheDocument();
    expect(screen.getByText(/若代理软件实际没开/)).toBeInTheDocument();
  });

  test("can try the address in the input without saving it", async () => {
    const user = userEvent.setup();
    render(<App />);

    const proxy = await screen.findByLabelText("代理（留空 = 自动；填 direct 强制直连）");
    await user.type(proxy, "127.0.0.1:10809");
    await user.click(screen.getByRole("button", { name: "先试输入框里的地址" }));

    await waitFor(() => {
      expect(mockState.proxyTestBodies).toEqual([{ proxy: "127.0.0.1:10809" }]);
    });
    // 关键：试地址不等于保存。
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/settings",
      expect.objectContaining({ method: "PUT" })
    );
    expect(await screen.findByText(/返回 HTTP 200/)).toBeInTheDocument();
  });

  test("offers a preset table of local proxy ports next to the proxy field", async () => {
    const user = userEvent.setup();
    render(<App />);

    const head = await screen.findByRole("button", { name: "说明：常见代理软件的本地端口" });
    // 说明默认收起，不占主功能区版面。
    expect(head).toHaveAttribute("aria-expanded", "false");
    await ensureExpanded(head);

    expect(screen.getByText("Clash / Clash Verge / Mihomo")).toBeInTheDocument();
    expect(screen.getByText("v2rayN")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "127.0.0.1:10809" }));
    expect(screen.getByLabelText("代理（留空 = 自动；填 direct 强制直连）")).toHaveValue("127.0.0.1:10809");
    // 只填进输入框，不立即保存。
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/settings",
      expect.objectContaining({ method: "PUT" })
    );
  });

  test("自动重试：勾选框与标签同行，三个数字按新范围显示", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    const name = "B站 自动重试（等审核通过后自动归集 / 自动补字幕）";
    const label = screen.getByText(name);
    const checkbox = screen.getByRole("checkbox", { name });

    // 「放在字样右侧」= 同一个标签行里、在标签**之后**。
    // jsdom 不做布局，所以判据只能是 DOM 顺序 —— 量坐标在这里量不出东西。
    const row = label.parentElement as HTMLElement;
    expect(row).toHaveClass("field-label-row");
    expect(row.contains(checkbox)).toBe(true);
    expect(label.compareDocumentPosition(checkbox) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const interval = screen.getByLabelText("自动重试间隔（秒，10–300）");
    expect(interval).toHaveValue(60);
    expect(interval).toHaveAttribute("min", "10");
    expect(interval).toHaveAttribute("max", "300");

    const attempts = screen.getByLabelText("自动重试上限（次，1–10）");
    expect(attempts).toHaveValue(3);
    expect(attempts).toHaveAttribute("min", "1");
    expect(attempts).toHaveAttribute("max", "10");
  });

  test("自动重试：关掉开关后两个数字真的改不动", async () => {
    const user = userEvent.setup();
    render(<App />);

    const name = "B站 自动重试（等审核通过后自动归集 / 自动补字幕）";
    const checkbox = await screen.findByRole("checkbox", { name });
    const interval = screen.getByLabelText("自动重试间隔（秒，10–300）");
    const attempts = screen.getByLabelText("自动重试上限（次，1–10）");
    expect(checkbox).toBeChecked();
    expect(interval).toBeEnabled();
    expect(attempts).toBeEnabled();

    await user.click(checkbox);

    // 关掉这件事**要真的发给后端**：界面关了而机器还在问，是两套状态。
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ bili_auto_retry_enabled: false })
        })
      );
    });
    // 给一个不会跑的东西调参数只会让人以为它还在跑 ⇒ 两项都得 disabled。
    await waitFor(() => expect(interval).toBeDisabled());
    expect(attempts).toBeDisabled();

    await user.click(checkbox);

    await waitFor(() => expect(interval).toBeEnabled());
    expect(attempts).toBeEnabled();
  });
});
