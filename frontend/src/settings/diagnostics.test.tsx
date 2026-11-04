import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "../App";
import { diagnosticsPayload, diagnosticsWithDrift, diagnosticsWithoutJsRuntime } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

describe("App · 自检信息", () => {
  test("surfaces a broken JS runtime with the raw reason and a refresh button", async () => {
    const user = userEvent.setup();
    mockState.diagnostics = diagnosticsWithoutJsRuntime;
    render(<App />);

    // 先等**只可能来自 payload** 的那段文本。`：不可用` 不能用来判断「自检回来了」——
    // `ProxySection.tsx` 里 `jsRuntimeReady = runtime?.dependencies?.js_runtime === true`，
    // runtime 还是 null（自检未返回）时它同样是 false，于是这个文案在**加载前就已经成立**。
    // 原来这一个是 getByText（同步），它只是碰巧靠同文件里前 58 个用例把调度「跑热」了才通过；
    // 拆分后它成了本文件第 1 例，冷启动下必失败 —— 实测：HEAD 原文件全量 69 passed，
    // 但单独 `-t` 跑这一条即失败（见 ai/refactor/008-*.md 的取证）。
    expect(await screen.findByText(/自检报错：/)).toBeInTheDocument();
    expect(screen.getByText(/JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：不可用/)).toBeInTheDocument();
    expect(screen.getByText(/ERR_ACCESS_DENIED/)).toBeInTheDocument();
    expect(screen.getByText(/The page needs to be reloaded\./)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新自检" }));
    await waitFor(() => {
      expect(mockState.runtimeRefreshCount).toBe(1);
    });
  });

  test("surfaces a config that the running process has not picked up", async () => {
    // 库里已经改了、进程还在用旧值 —— 这类故障没有异常也没有日志，诊断是唯一的第一现场。
    mockState.diagnostics = diagnosticsWithDrift;
    render(<App />);

    const drift = await screen.findByText(/有 1 项设置的「进程实际值」与库里不同/);
    expect(drift.textContent).toContain("下载目录：库里 D:/elsewhere，进程用 D:/downloads");
  });

  test("stays quiet when store and runtime agree", async () => {
    // 一致时不该出现任何「配置正常」之类的话：那是噪音，还会让人以为它在盯着什么。
    mockState.diagnostics = diagnosticsPayload;
    render(<App />);

    expect(await screen.findByText(/日志文件：/)).toBeInTheDocument();
    expect(screen.queryByText(/进程实际值/)).toBeNull();
  });

  test("shows the log file path so users can find the evidence", async () => {
    render(<App />);

    // 路径出现两次：一行正文里直接可见，一次在「复制路径」按钮旁的代码块里。
    expect(await screen.findByText(/日志文件：/)).toBeInTheDocument();
    const logPaths = screen.getAllByText(/data\\logs\\app\.log/);
    expect(logPaths.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: /复制路径/ })).toBeInTheDocument();
  });
});
