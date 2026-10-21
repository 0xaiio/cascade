import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import App from "./App";
import { jobPayload, playlistJobPayload } from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 任务中心：本地文件操作与链接", () => {
  test("plays downloaded single videos and playlist items from task center", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4" }]
      },
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\one.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "播放 Running video" }));
    await user.click(screen.getByRole("button", { name: "播放 Part one" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/play", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/play",
      expect.objectContaining({ method: "POST" })
    );
  });

  test("disables play buttons before downloaded files are known", async () => {
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "播放 Running video" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "播放 Part one" })).toBeDisabled();
  });

  test("opens downloaded single video and playlist item folders from task center", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4" }]
      },
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\one.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Running video" }));
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Part one" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/open-folder", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/open-folder",
      expect.objectContaining({ method: "POST" })
    );
  });

  test("opens a task folder even before a final output path is known", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        download_dir: "D:\\Videos",
        items: [{ ...jobPayload.items[0], output_path: null }]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Running video" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/open-folder", expect.objectContaining({ method: "POST" }));
  });

  test("shows local file action failures beside the affected task", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\missing.mp4" }]
      }
    ];
    mockState.localFileActionFailure = "视频文件不存在。";
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "播放 Running video" }));

    const jobCard = screen.getByText("Running video").closest(".job-card");
    expect(jobCard).toBeInTheDocument();
    const localAlert = within(jobCard as HTMLElement).getByRole("alert");
    expect(localAlert).toHaveClass("local-action-error");
    expect(localAlert).toHaveTextContent("视频文件不存在。");
  });

  test("shows playlist item local file action failures beside the affected item", async () => {
    mockState.jobs = [
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\missing.mp4" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    mockState.localFileActionFailure = "视频文件不存在。";
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开视频文件夹 Part one" }));

    const itemDetail = screen.getByText("1. Part one · running").closest(".job-item-detail");
    expect(itemDetail).toBeInTheDocument();
    const localAlert = within(itemDetail as HTMLElement).getByRole("alert");
    expect(localAlert).toHaveClass("local-action-error");
    expect(localAlert).toHaveTextContent("视频文件不存在。");
  });

  test("opens playlist folders from task center", async () => {
    mockState.jobs = [
      {
        ...playlistJobPayload,
        download_dir: "D:\\Videos\\Playlist batch"
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开合集文件夹 Playlist batch" }));

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-playlist/open-folder", expect.objectContaining({ method: "POST" }));
  });

  test("copies single video playlist and playlist item source links", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText }
    });
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制链接 Running video" }));
    expect(writeText).toHaveBeenCalledWith("https://youtu.be/running");
    expect(screen.getByRole("button", { name: "已复制 Running video" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制链接 Playlist batch" }));
    expect(writeText).toHaveBeenCalledWith("https://youtube.com/playlist?list=abc");

    await user.click(screen.getByRole("button", { name: "复制链接 Part one" }));
    expect(writeText).toHaveBeenCalledWith("https://youtu.be/one");
  });

  test("opens YouTube pages for single video playlist and playlist items", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开 YouTube 页面 Running video" }));
    expect(openSpy).toHaveBeenCalledWith("https://youtu.be/running", "_blank", "noopener,noreferrer");

    await user.click(screen.getByRole("button", { name: "打开 YouTube 页面 Playlist batch" }));
    expect(openSpy).toHaveBeenCalledWith("https://youtube.com/playlist?list=abc", "_blank", "noopener,noreferrer");

    await user.click(screen.getByRole("button", { name: "打开 YouTube 页面 Part one" }));
    expect(openSpy).toHaveBeenCalledWith("https://youtu.be/one", "_blank", "noopener,noreferrer");
  });

  test("opens subtitles of a single video and of a playlist item", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4", subtitle_path: "D:\\Videos\\running.en.vtt" }]
      },
      {
        ...playlistJobPayload,
        items: [
          { ...playlistJobPayload.items[0], output_path: "D:\\Videos\\Playlist\\one.mp4", subtitle_path: "D:\\Videos\\Playlist\\one.zh-Hans.srt" },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开字幕文件 Running video" }));
    await user.click(screen.getByRole("button", { name: "打开字幕文件 Part one" }));

    // 不带 `mode`：缺省就是「弹系统的打开方式」，那个缺省写在服务端（`job_files.py`），
    // 前端不跟它各写一遍 —— 两处各写一次，先信谁就会变成一个说不清的问题。
    expect(fetch).toHaveBeenCalledWith("/api/jobs/job-running/open-subtitle", expect.objectContaining({ method: "POST" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/open-subtitle",
      expect.objectContaining({ method: "POST" })
    );
  });

  test("the subtitle button is the only control and asks the system to choose the program", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4", subtitle_path: "D:\\Videos\\running.en.vtt" }]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "打开字幕文件 Running video" });
    // 曾经它右边还有一个「选择打开方式」的小箭头：点下去看起来毫无反应（窗口弹在浏览器后面），
    // 用户得先找到那个窗口再点一次。现在只剩一颗按钮，点击本身就是「挑程序」。
    expect(screen.queryByRole("button", { name: "选择打开方式 Running video" })).toBeNull();
    expect(button).toHaveAttribute("title", "打开字幕文件；可选择打开方式");

    await user.click(button);

    const subtitleCalls = vi.mocked(fetch).mock.calls.filter(([input]) => String(input).includes("/open-subtitle"));
    expect(subtitleCalls).toHaveLength(1);
    // 整条 URL 相等：带任何查询串（`?mode=default` / `?mode=chooser`）都算没走缺省。
    expect(subtitleCalls[0][0]).toBe("/api/jobs/job-running/open-subtitle");
  });

  test("keeps the subtitle button disabled until a subtitle file exists", async () => {
    render(<App />);

    // 出厂夹具里的条目没有下到字幕（`subtitle_path: null`）。可点却点了报 409 的按钮，
    // 比灰着的按钮更让人困惑 —— 所以这里必须两条都是禁用的，并说明原因。
    expect(await screen.findByText("Running video")).toBeInTheDocument();
    const jobSubtitle = screen.getByRole("button", { name: "打开字幕文件 Running video" });
    expect(jobSubtitle).toBeDisabled();
    expect(jobSubtitle).toHaveAttribute("title", "字幕文件尚不可用");
    expect(screen.getByRole("button", { name: "打开字幕文件 Part one" })).toBeDisabled();
  });

  test("shows subtitle failures beside the affected task", async () => {
    mockState.jobs = [
      {
        ...jobPayload,
        items: [{ ...jobPayload.items[0], output_path: "D:\\Videos\\running.mp4", subtitle_path: "D:\\Videos\\running.en.vtt" }]
      }
    ];
    mockState.localFileActionFailure = "字幕文件尚不可用。";
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Running video")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开字幕文件 Running video" }));

    const jobCard = screen.getByText("Running video").closest(".job-card");
    expect(jobCard).toBeInTheDocument();
    expect(within(jobCard as HTMLElement).getByRole("alert")).toHaveTextContent("字幕文件尚不可用。");
  });
});
