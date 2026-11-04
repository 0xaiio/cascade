#!/usr/bin/env node
/**
 * 核数闸门：**跑了几个文件**必须等于**磁盘上有几个测试文件**。
 *
 * 为什么需要它：`npx vitest run` 在并行模式下会因为「转译缓存写不进去」而静默丢掉整个
 * 测试文件（见 ai/bug-fix/034），退出码仍可能是 0，汇总行也仍是 `passed`。只报
 * 「N passed」而不报文件数，等于把闸门交给它自己声明。
 *
 * 用法：
 *   node scripts/verify-test-counts.mjs            # 按 package.json 的 test 脚本跑（串行）
 *   node scripts/verify-test-counts.mjs --parallel # 故意用并行跑一遍（用来复现丢文件）
 *
 * 判据两条，任一条不满足都退出 1：
 *   1. 实跑的文件集合 == 磁盘上的 `src/**\/*.test.{ts,tsx}` 集合；
 *   2. vitest 自己的退出码为 0。
 */

import { readdirSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = fileURLToPath(new URL("..", import.meta.url));
const srcDir = join(root, "src");

/** 磁盘上的测试文件（相对 `src/`，统一用 `/`）。 */
export function listTestFiles(dir) {
  const found = [];
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (/\.test\.(ts|tsx)$/.test(entry.name)) {
        found.push(relative(dir, full).split(sep).join("/"));
      }
    }
  };
  walk(dir);
  return found.sort();
}

/** 比较两个集合，返回缺失/多余的清单（空数组 = 一致）。 */
export function diffFileSets(expected, actual) {
  const want = new Set(expected);
  const got = new Set(actual);
  return {
    missing: expected.filter((name) => !got.has(name)),
    unexpected: actual.filter((name) => !want.has(name)),
  };
}

function main(argv) {
  const extra = argv.filter((arg) => arg !== "--parallel");
  const parallel = argv.includes("--parallel");
  const outputFile = join(tmpdir(), `cascade-vitest-${process.pid}.json`);
  const args = ["vitest", "run", "--environment", "jsdom"];
  if (!parallel) args.push("--no-file-parallelism");
  args.push(...extra, "--reporter=json", `--outputFile=${outputFile}`);

  console.log(`[核数] 运行：npx ${args.join(" ")}`);
  const result = spawnSync("npx", args, { cwd: root, stdio: "inherit", shell: true });

  let ran = [];
  try {
    const report = JSON.parse(readFileSync(outputFile, "utf-8"));
    ran = (report.testResults ?? [])
      .map((item) => relative(srcDir, item.name).split(sep).join("/"))
      .sort();
  } catch (error) {
    console.error(`[核数] 读不到 vitest 的 JSON 报告：${error.message}`);
    return 1;
  } finally {
    rmSync(outputFile, { force: true });
  }

  const expected = listTestFiles(srcDir);
  const { missing, unexpected } = diffFileSets(expected, ran);
  console.log(`[核数] 磁盘 ${expected.length} 个文件 / 实跑 ${ran.length} 个文件`);

  let failed = false;
  if (missing.length) {
    failed = true;
    console.error(`[核数] **有 ${missing.length} 个测试文件根本没跑**：`);
    for (const name of missing) console.error(`  - ${name}`);
  }
  if (unexpected.length) {
    failed = true;
    console.error("[核数] 报告里出现了磁盘上没有的文件：");
    for (const name of unexpected) console.error(`  - ${name}`);
  }
  if (result.status !== 0) {
    failed = true;
    console.error(`[核数] vitest 退出码 ${result.status}`);
  }
  if (failed) {
    console.error("[核数] 失败：这次「全绿」不可信。");
    return 1;
  }
  console.log("[核数] 通过：文件集合与磁盘一致。");
  return 0;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.exit(main(process.argv.slice(2)));
}
