# E-03　「重启」的语义写清楚

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §E-03。2026-11-04 落地，一项一个提交。

## 结论（取证自 `JobManager.restart` / `restart_item`）

用户点「重启」= **把任务重新排队、重跑一次预检，清空界面上的这一轮记录；磁盘上的 `.part` 由 yt-dlp 断点续传**。既不是「从零重下」，也不是「从暂停点直接接着跑」（没有那种接口）。

| 会重置 | 会保留 | 不会做 |
| --- | --- | --- |
| 任务与条目状态 → `queued`；进度、已下载/总字节、速度、ETA、错误、开始/结束时间；`output_path`、实际分辨率/格式、条目级 `options_json`、降级三列；任务级计数与 `current_item_title` | 任务级 `options_json`（原清晰度与下载选项）、`JobEvent` 历史、已下载的文件、磁盘上的 `.part`、B站 投稿记录 | 不删 `.part`、不删成品、不删事件历史；不自动换清晰度；不弹确认框 |

补充事实：

- **重跑预检**：条目重新入队 → `_run_item` → `_prepare_download` → `prepare_download`，计划分辨率、格式、文件大小与字幕计划都重新算一遍。有端到端用例数 `prepare_download` 的调用。
- **沿用清晰度**：不传 `resolution` 就沿用 `job.options_json`；`item.options_json` 被清空后回落到任务级选项。只有「以 720p 重启」这类降级建议按钮（或 `POST /restart` 带 `resolution`）才改，且走 [026](../bug-fix/026-a-resolution-that-silently-means-best.md) 的入口归一化/拒绝。
- **暂停怎么恢复**：`restart()` 会 `self._paused.discard(job_id)`，所以暂停后点「重启」就是恢复入口；界面上**没有**单独的「继续」按钮。进度显示会归零，但文件续传。
- **条目级**：只重置那一条，其它条目的状态与产物路径不动；任务回 `queued`，计数由 `_refresh_job_counts` 重算；事件是 `item_restarted` 而不是 `job_restarted`。
- **三种容易混起来的「重启」**：① 用户点按钮 → `job_restarted` / `item_restarted`；② 改限速/重试时的运行时重新入队 → `item_requeued`（FR-10，不算用户重启，也不清空产物路径）；③ 启动时的孤儿恢复 → `jobs_recovered`（FR-47，默认 resume，`paused` 不动）。

## 按钮名改不改

**不改。** 取证结论是「重新排队 + 重跑预检 + 重新入队」，`重启` 与这个动作一致；不一致的是用户脑中「从零重下」的预期，那是**缺说明**而不是**名实不符**。改名的代价是破坏读屏标签与既有用例，收益只是换一个同样会被误解的词。

改的是四处提示的 `title`（悬停可见），文案由 [`restartSemantics.ts`](../../frontend/src/task-center/restartSemantics.ts) 一处生成：任务级、条目级、批量、换清晰度。`aria-label` 保持「重启 + 标题」不变。`换清晰度` 那一档刻意**不写**「沿用原来的清晰度」，因为它是唯一会改选项的入口 —— 四个入口共用一句会在这里说错。

## 验证

| 层 | 结论 |
| --- | --- |
| `backend/tests/test_restart_semantics.py` | 12 条：字段重置与选项保留、`.part`/成品不被删、事件只追加不删历史、`paused → queued` 且退出暂停集合、未知任务空操作、清晰度只有显式传才改（含 026 的三种非法取值 422）、条目级只动一条且事件类型不同、跨任务条目返回 `False`、**重启后真的重跑预检** |
| `frontend/src/task-center/restartSemantics.test.ts` | 9 条：结论句覆盖三组事实、四个入口都写明「不会从零重下」、任务级点明「没有继续按钮」、条目级点明只影响一条、换清晰度不承诺沿用 |
| `frontend/src/task-center/task-center-restart.test.tsx` | 6 条：四处按钮真的挂上了提示、点击仍走原接口、`aria-label` 没被长提示顶替 |
| 全量 | 后端 **1369 passed**（54 个测试文件）、前端 **32 文件 / 391 passed**、`tsc --noEmit` 退出 0 |

> 文档里的每条行为断言都由上面的后端用例钉住：先改 `test_restart_semantics.py`，再改手册与按钮文案，避免「文档写一套、实现是另一套」。

## 遗留

- 悬停提示在触屏上不可见；任务中心没有常驻的说明浮层。若将来要补，按 FR-18 用 `HelpPopover` 挂在「重启」旁边，不要再把文案复制一份。
- 「进度归零但文件在续传」仍是反直觉的一点：界面从 0% 起跳，由首个 yt-dlp 进度回调纠正。提示已说明，但进度条本身没有「续传中」的标记 —— 这属于新的展示能力，不在本条范围。
- 重启不会重新检查磁盘预算之外的其它前置（cookies 有效性、代理等由预检/下载自己报错）。
