# E-05　长时间暂停的任务没有任何提示

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §E-05。2026-11-04 落地，一项一个提交。

## 结论：只补一行状态文案，不新增通知渠道

候选池给的判据是「这是『暂停』这个动作的语义补全，不引入新的通知渠道」。完成提醒（[014](../bug-fix/014-a-dead-notify-switch.md)）有意只覆盖 `succeeded` / `failed`，`paused` 不是终态；为长暂停发 toast、响声音或加浏览器通知，会把「用户主动停住」误报成「任务出结果了」。所以落点只有一处：**任务行状态下面的一行提示**，超过 2 小时才出现，写明时长与下一步「点『重启』继续（不会从零重下）」，用暖色而非红色（不把「停着」读成「坏了」）。

## 为什么起点必须是专用状态列

| 方案 | 问题 |
| --- | --- |
| 拿 `updated_at` 当起点 | 它记的是「最后一次改了什么」。暂停之后任何一次字段写入（速度/ETA 归零、设置同步、读模型刷新、重启前的一次状态变更）都会把它推后，界面上的「已暂停 3 小时」悄悄变成「刚暂停」—— 正是本条要修的病 |
| 从 `JobEvent` 里查最后一次 `job_paused` | 事件是历史日志不是状态：补列前的老库没有这条事件、重启清空过的任务要额外判「哪一才算上一次」，且列表每行多一次查询 |
| **`Job.paused_at`（本条）** | 第一次进入 `paused` 时写入，重复点暂停不覆盖，任务级/条目级重启时清空；与状态列同一个事务落库，读模型一次取到 |

补列走 `init_db._ensure_columns` 追加 `DATETIME`（项目既有的追加式迁移，老库不必手工改表）。**补列之前已经暂停的历史任务拿不到准确起点**：读模型用 `updated_at` 兜底，但把 `paused_since_approximate` 置成 `true`，文案因此必须带「约」—— 不把猜测伪装成精确值。

## 实现

- 后端：`Job.paused_at` 专用列；`pause()` 用 `job.paused_at or utc_now()` 幂等写入；`restart()` 与 `restart_item()` 清空（下一次暂停自然拿到新起点）；[`_paused_since`](../backend/app/download/job_read_model.py) 在**非暂停态一律返回 `(None, False)`**，避免库里残留的脏值让跑完的任务继续显示「已暂停」；`JobRead` 新增 `paused_at`（`UtcDateTime`，序列化带 `Z`）与 `paused_since_approximate`。
- 前端：[`pausedSince.ts`](../frontend/src/task-center/pausedSince.ts) 是纯判定：阈值 2 小时、按整小时 / 天向下取整、近似值加「约」、时钟回拨 / 未来时间 / 坏值都不编数字；时间解析统一走 `parseServerTime`（无偏移量时按 UTC）。`JobQueue` 按**分钟**重算（不是秒表）；组件里再判一次 `status === "paused"`，防旧缓存的脏值；没有新增任何通知订阅。
- 契约三处同步：后端 `JobRead`、前端 `Job` interface / 夹具、`docs/openapi.yaml`；`check_api_contract.py` 是唯一闸门。
- 样式：`.paused-since` 暖色边框 + 底色，`overflow-wrap: anywhere` 保证窄屏可断行，并有样式源文不变式守住。

## 覆盖的边界

| 场景 | 结论 |
| --- | --- |
| 刚暂停 30 分钟 | 不显示（阈值是「手动暂停后忘了」，不是「暂停了一下」） |
| 正好 2 小时 / 3.5 小时 / 26 小时 | 分别显示「2 小时」/「3 小时」/「1 天 2 小时」，整小时向下取整 |
| 重复点「暂停」 | 起点不变；只有 `restart` 才清零 |
| 任务级 / 条目级重启 | 都清 `paused_at`，退出暂停集合，状态回 `queued` |
| 非暂停态残留 `paused_at` | 读模型与组件两侧都挡，界面不显示 |
| 补列前的历史任务 | `updated_at` 兜底 + `approximate=true`，文案带「约」 |
| 坏时间 / 未来时间 / `null` | 不编出「已暂停 -1 小时」，直接不显示 |
| 长暂停时是否发通知 | **不发**：`paused` 不在终态集合里，不进 toast 队列、不响声音 |
| SSE 状态变化 | 重启后提示消失；再次暂停后重现 |
| 老库迁移 | `init_db` 能把删掉的 `paused_at` 列补回来（`PRAGMA table_info` 实测） |

## 验证

| 层 | 结论 |
| --- | --- |
| `backend/tests/test_paused_since.py` | 9 条：起点写入且不冒充 `created_at`、重复暂停不重置、两种重启都清空、读模型给带时区的 UTC 且非暂停态置空、历史任务兜底并标近似、pause/restart 两个响应都带新字段、老库补列 |
| `frontend/src/task-center/pausedSince.test.ts` | 9 条：阈值与取整、跨天、近似文案、坏值/未来时间、无偏移量按 UTC、页面开着时随时间推进、`paused` 不在完成提醒集合里 |
| `frontend/src/task-center/task-center-paused.test.tsx` | 6 条：任务行显示时长与下一步但不发通知、短暂停不打扰、近似值带「约」、起点缺失不编数字、非暂停态脏值不显示、SSE 状态变化后消失/重现 |
| `frontend/src/styles.test.ts` | +1 条：提示可断行、无 `nowrap` / `text-overflow` |
| CDP 真实浏览器 | 390px 下提示宽 262px、文字换成两行、`scrollWidth == clientWidth == 390`（无横向滚动）、无通知浮层；注入修复前的 `nowrap + ellipsis` 后提示自身溢出 25px、页面被撑到 413px。截图在 `tmp_acceptance/e05_paused_since_narrow_*.png`，探针 `tmp_acceptance/probe_paused_since_narrow.py` |
| 全量 | 后端 **1393 passed**（56 个测试文件）、前端 **35 文件 / 415 passed**、`tsc --noEmit` 退出 0、六道文档闸门全绿 |

### 变异验证

- 把 `job.paused_at = job.paused_at or utc_now()` 换成 `utc_now()`：后端 `test_repeated_pause_does_not_reset_the_clock` 转红（1 failed / 8 passed）。
- 去掉组件里的 `job.status === "paused"` 判定：前端「非暂停态即使残留 `paused_at` 也不显示」转红（1 failed / 5 passed）。
- 恢复后 `test_paused_since.py` 9 条、前端两份新用例 15 条全绿，无变异残留。两条变异分别钉住「重复暂停不重置」与「脏值不回显」这两个真正的语义边界，而不是只证明「提示出现过」。

## 遗留

- 只在页面开着时按分钟推进；关掉页面不留任何后台提醒或定时检查 —— 这是 014 的有意边界。
- 阈值固定 2 小时、不进设置面板（避免为一个提示增加配置项）；文案按整小时向下取整，59 分钟的差别看不出来。
- 升级前就已暂停的任务只有近似起点，重启再暂停后才会变成精确值。
- 没有「长暂停自动重启」：提示只给信息，不替用户做动作。
