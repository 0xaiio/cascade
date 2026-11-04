# E-02　任务与条目的筛选 / 搜索

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §E-02。2026-11-04 落地，一项一个提交。**纯前端**，不改 API 契约。

## 结论

任务中心标题下加一排控件：状态下拉 + 标题搜索。判定抽成 [`jobFilter.ts`](../../frontend/src/task-center/jobFilter.ts) 的纯函数 `filterJobs`，`JobQueue` 只把返回值渲染出来。

| 档位 | 状态 |
| --- | --- |
| 全部状态 | 不收窄（**未来新增的未知状态也不会被悄悄丢掉**） |
| 在跑 | `queued` + `running` |
| 已暂停 / 失败 / 完成 / 已取消 | `paused` / `failed` / `succeeded` / `cancelled` |

六档合起来覆盖 `JobStatus` 的全部取值，有专门的用例钉住这一点。

## 三条判据

1. **筛选只作用于展示层。** `App` 的全量 `jobs` 还要供完成提醒的「状态跃迁」比对（[014](../bug-fix/014-a-dead-notify-switch.md)）、B站 徽标与选中集合清理使用；在 `App` 里 `setJobs` 成过滤结果会让「一进来就已跑完的历史任务」被反复播报。所以只派生一份 `visibleJobs`，批量操作仍按 `selectedJobIds`（全量 id）执行；被隐藏但仍选中的任务会在批量工具条里写明「其中 N 个当前被筛选隐藏」。
2. **关键字是普通子串，不是正则。** 匹配任务标题 / 链接，或任一条目的标题 / 链接；`toLocaleLowerCase` 后 `includes`，`[` `*` 这类符号既不抛异常也不意外全中。
3. **命中条目标题不裁剪 `job.items`。** 只把整个任务显示出来并记进 `itemMatchJobIds`，供 UI 自动展开合集（用户手动折叠过则以手动优先）。若把条目列表也过滤掉，用户会以为其它条目不存在，批量删除的范围就说不清了。

## 生命周期与边界

- SSE 每推一帧，`App` 重拉 `/api/jobs` → `filterJobs` 用新的 `jobs` 重算：选着「在跑」时刚跑完的任务会自己离开列表。有集成用例专门推一帧验证。
- 无结果时给空态（共 M 个、已隐藏 K 个）与「清空筛选」；计数徽标在筛选时变成「显示 N / 共 M 个任务」，无筛选时**保持**原来的「N 个任务」（既有版面断言依赖它）。
- 空任务列表：筛选条仍在，徽标「暂无任务」，不显示「没有符合条件的任务」这种假空态。

## 验证

| 层 | 结论 |
| --- | --- |
| 纯判定 `jobFilter.test.ts` | 17 条：六档状态集合、未知状态、大小写 / 空白、任务与条目链接、条目命中不裁剪、正则元字符、组合筛选、空列表与计数、缺字段不抛 |
| 集成 `task-center-filter.test.tsx` | 11 条：控件接线、空态与清空、条目标题命中自动展开、**SSE 状态变化后重算**、批量选择不受改写、空列表、特殊字符不崩 |
| 样式不变式 `styles.test.ts` | +1 条：筛选条 `flex-wrap: wrap`、查询列非零 `flex-basis`、空态 `overflow-wrap: anywhere` |
| CDP 真实浏览器 | `tmp_acceptance/probe_job_filter_narrow.py`：390px 视口下筛选条 320px、输入框 **294px**、`document.scrollWidth == clientWidth == 390`（无横向滚动）；注入修复前的 `flex-wrap:nowrap + flex:1 1 0` 后输入框只剩 **156px**（Δ=138px），证明差异确实来自这两条声明。截图 `e02_job_filter_narrow_{after,prefix}.png` |
| 全量 | `npm test` **30 文件 / 376 passed**、`npx tsc --noEmit` 退出 0；后端未改动，全量 1357 passed 保持不变 |

## 遗留

- 筛选条件不持久化（刷新回到「全部 + 空关键字」）。这是「筛选是展示层」的直接后果；若将来要持久化，得单独定义「恢复筛选后用户以为任务丢了」的提示，不能只塞进 localStorage。
- 状态档位是本地常量表，后端若新增状态，「全部」档仍能看到，但不会有对应的独立档位。纯函数测试会在 `JobStatus` 扩展时提示维护者补档（当前断言是集合相等）。
- 只测了 390px 与桌面 jsdom；681–1023px 的临界宽度未单独量（与 [ui/011](../ui/011-narrow-job-title-squeezed.md) 同一条遗留）。
