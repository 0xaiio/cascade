import { describe, expect, it } from "vitest";

import pkgJson from "../../package.json";

/**
 * 测试闸门**自己**的配置也不能被悄悄改坏。
 *
 * 背景（`ai/bug-fix/034`）：`npx vitest run` 在并行模式下会把测试文件整个丢掉 ——
 * 转译缓存写不进去（本机 fs shim 回 `EPERM`），那个文件的用例既不算失败也不算跳过，
 * 只是**不存在**，而汇总行照样写着 passed。实测同一份代码：串行 25 文件 / 332 条，
 * 并行 24 文件 / 255 条、23 文件 / 232 条，两次丢的不是同一批。
 *
 * 所以 `--no-file-parallelism` 不是「跑得慢一点的偏好」，它是这个项目里
 * 「测试全绿」这句话成立的前提。删掉它不会让任何业务用例变红，只会让闸门失效 ——
 * 因此只能在这里钉住。
 */
// 直接 import JSON：本机的 fs shim 对 `readFileSync(new URL(...))` 会回
// "The URL must be of scheme file"，读不了文件是环境问题，不该让闸门失效。
const pkg = pkgJson as unknown as {
  scripts: Record<string, string>;
};

describe("测试脚本的闸门配置", () => {
  it("npm test 必须关掉文件级并行（否则会静默丢文件）", () => {
    expect(pkg.scripts.test).toContain("--no-file-parallelism");
  });

  it("npm test 必须带 jsdom 环境（组件测试默认环境是 node）", () => {
    expect(pkg.scripts.test).toContain("--environment jsdom");
  });

  it("必须有一条核数命令，且它跑的是核数脚本", () => {
    // 只报「N passed」不足以证明跑全了：串行也要能说出「磁盘上几个文件、实跑几个」。
    expect(pkg.scripts["test:counts"]).toContain("verify-test-counts.mjs");
  });
});
