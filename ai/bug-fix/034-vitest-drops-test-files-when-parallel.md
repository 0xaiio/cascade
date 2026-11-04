# 034 · vitest 并行跑会丢掉整个测试文件，而汇总行仍写着 passed

## 问题

`npx vitest run --environment jsdom`（不带 `--no-file-parallelism`）有时只跑一部分文件：

| 时间 | 命令 | 结果 |
| --- | --- | --- |
| 2026-10-28（记于 [`ai/bug-fix/README.md`](README.md) 遗留 25） | 连续四次并行 | 21 / 20 / 19 / 15 个文件，286 / 275 / 261 / 172 条，**每次都报 passed** |
| 2026-11-04 14:15 | 并行 | **24 文件 / 255 条**（应 25 / 332），1 条 unhandled error |
| 2026-11-04 14:18 | 并行 | **23 文件**（丢 `bili-upload.test.tsx`、`task-center-files.test.tsx`），退出码 1 |
| 2026-11-04 14:22 | 并行 | **24 文件 / 212 条**，2 条 unhandled error，退出码 1 |

**丢的不是固定那一批**：三次丢的集合不同。这不是某条用例的问题，是闸门本身。

## 根因

不是「vitest 不可靠」，是**本机的文件系统 shim 与并行转译缓存打架**。

并行模式下每个 worker 都要把转译后的模块写进同一个临时缓存目录，再通过 module runner
`fetch` 回来。本机的 fs shim（WorkBuddy CLI 注入的 `node-brokered-fs-shim`）对同一路径的
并发写回 `EPERM`：

```
Error: EPERM: operation not permitted, open
  'C:\Users\hfwei\AppData\Local\Temp\Xykbo2H6riEg6qZlNncOA\web\40b9f1f37d5bd759c353e90078138580f67eb091'
 ❯ Object.writeFile node:internal/fs/promises
 ❯ wrappedPromisesWriteFile …/node-brokered-fs-shim.cjs:724
 ❯ Proxy.fetch node_modules/vitest/dist/chunks/resolveConfig.rBxzbVsl.js:6643
```

`Proxy.fetch` 失败 ⇒ 那个文件的模块取不到 ⇒ 该文件**从未被收集**。它不会出现在失败列表里，
也不计入 `Tests` 总数；`Test Files 24 passed` 里的 `passed` 只描述「跑了的那些」。

所以：

- 只 grep `Tests … passed` 的人看到的是绿；
- 只看退出码的人**今天**会看到红（有 unhandled error 时 vitest 退出 1），但 2026-10-28 那几次
  连 unhandled error 都没有 —— 退出码也是 0。**两条捷径都不可靠**。

唯一可靠的判据是「**跑了几个文件** 与 磁盘上有几个测试文件」对上。

## 修法

1. **`npm test` 默认串行**：`vitest run --environment jsdom --no-file-parallelism`。
   串行下只有一个 worker 使用缓存，竞争消失；实测 25/25 稳定（今天是 26/26）。
2. **加一条核数命令**：`npm run test:counts` → `frontend/scripts/verify-test-counts.mjs`。
   它自己带 `--no-file-parallelism` 跑一遍、拿 vitest 的 JSON 报告，把
   `testResults[].name` 与磁盘上的 `src/**/*.test.{ts,tsx}` 做**集合比较**；
   少一个文件就打印清单并退出 1。加 `-- --parallel` 可以故意用并行跑，用来复现丢文件。
3. **配置本身有用例钉住**：`frontend/src/test/test-runner.test.ts` 断言 `npm test` 带
   `--no-file-parallelism` 与 `--environment jsdom`、`test:counts` 指向核数脚本。
   删掉这些参数不会让任何业务用例变红 —— 只有这条会。

## 验证

```
$ node scripts/verify-test-counts.mjs --parallel
[核数] 磁盘 25 个文件 / 实跑 23 个文件
[核数] **有 2 个测试文件根本没跑**：
  - bilibili/bili-upload.test.tsx
  - task-center/task-center-files.test.tsx
[核数] 失败：这次「全绿」不可信。      (exit 1)

$ npm test
Test Files  26 passed (26)
      Tests  335 passed (335)          (exit 0)

$ node scripts/verify-test-counts.mjs
[核数] 磁盘 26 个文件 / 实跑 26 个文件
[核数] 通过：文件集合与磁盘一致。      (exit 0)
```

## 仍然存在的边界

- **核数只保证「文件都跑了」**，不保证「每条用例都跑了」。文件内的用例被跳过（`it.skip`）
  不在比较范围内 —— 那属于该文件自己的事。
- **串行更慢**：26 个文件 / 335 条约 35 秒（并行约 14 秒）。这是拿时间换「全绿可信」。
- 根因是本机 fs shim 的并发写限制，换一台没有 shim 的机器并行可能一直是对的 ——
  但**闸门不能以「本机可能没问题」为前提**，所以默认值取串行。
