import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import type { AnalyzeResponse } from "../contracts/types";
import App from "../App";
import { analyzePayload } from "../test/appFixtures";
import { installAppHarness, mockState } from "../test/appHarness";

installAppHarness();

/**
 * A-04：解析阶段就标出「本地已有」，让人在点下载之前知道会重下几条。
 *
 * 文案的口径必须与真正生效的机制一致：默认「跳过已下载」才不会再下一遍；
 * 关掉那个开关就会覆盖重下。所以提示写的是「默认」，并且指出在哪儿改。
 */

function payloadWithLocal(playlist: boolean, locals: boolean[]): AnalyzeResponse {
  return {
    ...analyzePayload,
    is_playlist: playlist,
    entries: analyzePayload.entries.map((entry, index) => ({ ...entry, local: locals[index] ?? false })),
    local: playlist ? false : (locals[0] ?? false)
  } as AnalyzeResponse;
}

async function analyze(user: UserEvent, payload: AnalyzeResponse, url = "https://youtube.com/playlist?list=abc") {
  mockState.analyze = payload;
  render(<App />);
  await user.clear(screen.getByLabelText("视频或 playlist 链接"));
  await user.type(screen.getByLabelText("视频或 playlist 链接"), url);
  await user.click(screen.getByRole("button", { name: "解析链接" }));
  await screen.findByText("Batch");
}

describe("App · 本地已有成品的标注", () => {
  test("合集：已有的行带「已有」徽标，并汇总成一句话", async () => {
    const user = userEvent.setup();
    await analyze(user, payloadWithLocal(true, [false, true]));

    // 只有第二条有徽标；第一条是同列的破折号。
    expect(screen.getAllByText("已有")).toHaveLength(1);
    expect(screen.getAllByText("—")).toHaveLength(1);
    expect(screen.getByText(/本地已有 1 条/)).toBeInTheDocument();
    // 写清「默认」与去哪儿改，否则等于替用户做了决定。
    expect(screen.getByText(/默认「跳过已下载」/)).toBeInTheDocument();
    expect(screen.getByText(/可在下载选项里关掉/)).toBeInTheDocument();
  });

  test("合集里一条都不在本地时不出现任何提示", async () => {
    const user = userEvent.setup();
    await analyze(user, payloadWithLocal(true, [false, false]));

    expect(screen.queryByText("已有")).not.toBeInTheDocument();
    expect(screen.queryByText(/本地已有/)).not.toBeInTheDocument();
  });

  test("单视频：本地已有同名产物时单独一句提示", async () => {
    const user = userEvent.setup();
    await analyze(user, payloadWithLocal(false, [true]), "https://youtu.be/abc123");

    expect(screen.getByText(/本地已有同名产物/)).toBeInTheDocument();
  });

  test("旧后端没有这个字段时按「没有」渲染，不编提示", async () => {
    const user = userEvent.setup();
    const payload = payloadWithLocal(false, [false]) as AnalyzeResponse & { local?: boolean };
    delete payload.local;
    payload.entries = payload.entries.map((entry) => {
      const copy = { ...entry };
      delete copy.local;
      return copy;
    });
    await analyze(user, payload, "https://youtu.be/abc123");

    expect(screen.queryByText(/本地已有/)).not.toBeInTheDocument();
  });
});
