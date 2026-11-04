import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "../App";
import { cookieHealthGoogleOnlyPayload, cookieRequiredMessage } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

describe("App · cookies 上传与校验", () => {
  test("integrates cookies controls into the analyzer panel", async () => {
    const user = userEvent.setup();
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form");
    expect(analyzer).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).getByText("未上传 cookies")).toBeInTheDocument();
    expect(
      within(analyzer as HTMLElement).getByText(/未配置 cookies 时 YouTube 媒体流 403 概率显著上升/)
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Cookies" })).not.toBeInTheDocument();

    const file = new File(["cookie"], "cookies.txt", { type: "text/plain" });
    expect(within(analyzer as HTMLElement).getByText("选择 cookies")).toBeInTheDocument();
    expect(within(analyzer as HTMLElement).queryByText("选择 cookies.txt")).not.toBeInTheDocument();
    await user.upload(within(analyzer as HTMLElement).getByLabelText("选择 cookies"), file);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/cookies",
        expect.objectContaining({ method: "POST", body: expect.any(FormData) })
      );
    });
    expect(await within(analyzer as HTMLElement).findByText("已启用 cookies")).toBeInTheDocument();
    expect(
      within(analyzer as HTMLElement).queryByText(/未配置 cookies 时 YouTube 媒体流 403 概率显著上升/)
    ).not.toBeInTheDocument();

    await user.click(within(analyzer as HTMLElement).getByRole("button", { name: "清除 cookies" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/cookies", expect.objectContaining({ method: "DELETE" }));
    });
  });

  test("does not offer any browser-import entry", async () => {
    // 本机 Edge 的 cookie 是 v20 app-bound 加密，那条路必然失败，失败分支还会
    // taskkill 掉用户所有 Edge 窗口。入口移除后，界面上不该再出现它的任何形态：
    // 按钮、下拉框、以及「关闭 Edge 并导入」的锁库提示（见 ai/bug-fix/038）。
    render(<App />);

    const analyzer = (await screen.findByRole("heading", { name: "解析链接" })).closest("form") as HTMLElement;
    expect(within(analyzer).queryByRole("button", { name: /从浏览器导入|导入中/ })).toBeNull();
    expect(within(analyzer).queryByLabelText("浏览器 cookies 来源")).toBeNull();
    expect(within(analyzer).queryByText(/关闭 Edge/)).toBeNull();
    // 上传这条路必须还在 —— 移除的是「替用户去读浏览器」，不是「用户自己给文件」。
    expect(within(analyzer).getByText("选择 cookies")).toBeInTheDocument();
  });

  test("shows the export path when YouTube requires a login", async () => {
    mockState.analyzeCookieRequired = true;
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("视频或 playlist 链接"), "https://youtube.com/playlist?list=abc");
    await user.click(screen.getByRole("button", { name: "解析链接" }));

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("YouTube 要求登录或通过 bot 校验");
    expect(banner).toHaveTextContent(cookieRequiredMessage.split("\n")[1]);
    // 旧入口的两个动作都不该再被提起：它们指向的按钮已经不存在了。
    expect(banner).not.toHaveTextContent("关闭 Edge");
    expect(banner).not.toHaveTextContent("从浏览器导入");
    // 只尝试一次：失败后不再「自动刷新 cookies 再试一遍」。
    const analyzeCalls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(([input]) =>
      String(input).endsWith("/api/analyze")
    );
    expect(analyzeCalls).toHaveLength(1);
    expect(screen.queryByText("Batch")).not.toBeInTheDocument();
  });

  test("verifies cookies and reports the login verdict with domain evidence", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "校验 cookies（联网确认登录态）" }));

    await waitFor(() => {
      expect(mockState.cookieVerifyBodies).toEqual([{ deep: true }]);
    });

    expect(await screen.findByText(/登录态有效 —— 登录态有效：YouTube 已识别为已登录/)).toBeInTheDocument();
    expect(screen.getByText(/youtube\.com 域上：23/)).toBeInTheDocument();
    expect(screen.getByText(/SID、HSID、SAPISID、LOGIN_INFO/)).toBeInTheDocument();
    // 收窄到「联网校验」那一行：单独 /已登录/ 会同时命中结论行和这一行。
    expect(screen.getByText(/联网校验：已登录/)).toBeInTheDocument();
  });

  test("explains a google.com-only cookie jar and how to fix it", async () => {
    const user = userEvent.setup();
    mockState.cookieHealth = cookieHealthGoogleOnlyPayload;
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "校验 cookies（联网确认登录态）" }));

    expect(await screen.findByText(/域名不对，等于没配/)).toBeInTheDocument();
    expect(await screen.findByText(/「只导出到 \.google\.com」的典型症状/)).toBeInTheDocument();
  });

  test("can run the cookie check offline without touching the network", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "只做离线体检" }));

    await waitFor(() => {
      expect(mockState.cookieVerifyBodies).toEqual([{ deep: false }]);
    });
  });
});
