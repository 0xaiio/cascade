# E-04　建完任务才发现选错：克隆一个新任务

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §E-04。2026-11-04 落地，一项一个提交。

## 结论：取「克隆」，不做 `PATCH`

| | 克隆（本条） | `PATCH /api/jobs/{id}/options`（未取） |
| --- | --- | --- |
| 改动面 | 新增一条路由 + 复用既有写库路径 | 要处理 `queued` 之外的状态、预检时序、事件与历史 |
| 选项校验 | 覆盖项在 `CloneJobRequest` 请求层就归一化/拒绝，业务层再整份重建 | 若用 `model_copy` 改原任务而不经过请求模型，校验要另挡一处 |
| 数据 | 原任务、它的事件与 B站 投稿记录一个字段都不动 | 改原任务会丢掉「当时选了什么」的痕迹 |

候选池给的判据是「预检之后才有信息」：预检会告诉你「降到 1080p」，但那时选项已经固化。克隆用同一个链接 + 改过的选项建一个新任务，旧任务留着当历史；已有成品由 `skip_existing` 命中，不会重下。

## 实现

- `POST /api/jobs/{job_id}/clone`（`routers/jobs.py`）：读源 `Job.options_json` → `DownloadOptions.model_validate_json` → 与请求里的覆盖项合并后**整份** `model_validate` → 重新解析源 URL → 写库 → `enqueue`。返回 `201 JobRead`。
- `CloneJobRequest` 只暴露 `resolution` / `mode` 两项，`None` = 沿用原设置。前端只送改过的字段，**不把 `DownloadOptions()` 的出厂默认当成原设置**。
- **026 的校验位置要如实说**：清晰度写法由请求体里的 `Resolution` 归一化 / 拒绝（`hd` / `1080` / `1440p60` → 422），业务层随后用 `DownloadOptions.model_validate({**original.model_dump(), **overrides})` 整份重建。整份重建是**防御性的单一判据**（保证写库拿到完整有效对象），不是「唯一能挡住 026 的地方」—— 见下面的变异验证。
- `api_support.create_job_records()` 是建任务与克隆**共用**的写库路径：选条目、磁盘预算预检、409 阻断、写 `Job` + N 个 `JobItem` 只在一处。克隆因此绕不过 E-01 的 409。
- 新任务写一条 `job_cloned` 事件（含 `source_job_id`、新 resolution / mode）；`enqueue` 照旧发 `job_queued`。源任务的历史事件不复制。
- 前端 `CloneJobPanel` 挂在任务行内（不是弹窗），两个下拉第一项都是「沿用原设置」，说明写明「原任务、下载历史与 B站 投稿记录都不变」以及 `skip_existing` 的条件性命中。回执**就近显示在面板内**，不上升为页面级红条（FR-55）；`onSubmit` 的类型是 `Promise<string | null>`，不是 `void asyncFn`（ai/ui/007 的教训）。

## 覆盖的状态与边界

| 场景 | 结论 |
| --- | --- |
| 源任务六种状态（queued/running/paused/failed/succeeded/cancelled） | 都能克隆，新任务一律 `queued`；已终态的四种源状态保持不变。`queued`/`running` 会被启动时的孤儿恢复（FR-47）认领，测试**不**对这两档断言源状态（否则整包必红、单跑必绿） |
| `resolution: " 720P "` | 归一化成 `720p` |
| `resolution: hd / 1080 / 1440p60`、`mode: audio_only` | `422`，且库里不留半个任务 |
| 源 `options_json` 损坏 | `409` +「请新建一个任务」 |
| 源任务不存在 | `404` |
| 重新解析失败 | `400` 带原始原因 |
| 可用空间不足 | `409`，与直接建任务同一条判据 |
| 条目范围 | 沿用原 `playlist_items`（如 `[2]` 只建第 2 条） |

## 验证

| 层 | 结论 |
| --- | --- |
| `backend/tests/test_clone_job.py` | 15 条：正常克隆与字段沿用、只改两项、**请求模型才是 026 的校验入口**、归一化与三种非法值、六种源状态、404 / 坏 JSON / 解析失败 / 409 磁盘预算、事件留痕不复制源历史 |
| `frontend/src/task-center/task-center-clone.test.tsx` | 7 条：面板接线与说明、「沿用」时 body 为 `{}`、只送改过的两项、成功后新任务进列表且面板收起、409 就近显示、取消不发请求、**「创建中…」加载态** |
| `frontend/src/styles.test.ts` | +1 条：面板长文案可断行、两个下拉非零 `flex-basis` |
| 契约 / 图 | `check_api_contract.py` 76 条目一致（新路径 + `CloneJobRequest`）；依赖图新增 `ApiSupport --> Manager`、移除已不存在的 `Routers --> DiskBudget`，SVG 重渲染 |
| 全量 | 后端 **1384 passed**（55 个测试文件）、前端 **33 文件 / 399 passed**、`tsc --noEmit` 退出 0 |

### 变异验证（一处被证伪的初始论断）

把业务层的整份重建临时换成 `original.model_copy(update=overrides)`，`test_clone_job.py` **14 条仍然全绿**。原因：026 的归一化/拒绝发生在 `CloneJobRequest` 的字段校验上，覆盖项到达业务层时已经是合法值；`model_copy` 在这里并没有绕过任何校验。

据此把初稿里「不用 `model_copy` 否则 026 会漏进来」的说法**改掉**：当前设计下它是错的。保留整份 `model_validate` 的真实理由是「写库拿到的一定是完整有效对象，不依赖调用方已经校验过」这一防御性判据；`PATCH` 方案的风险则在于它可能绕过请求模型直接改库。恢复实现后，补了第 15 条用例 `test_the_request_model_is_where_resolution_is_validated`，直接钉住「校验发生在 `CloneJobRequest`」，复跑 15 条全绿。这条变异本身也说明：**只测「非法值被拒」证明不了校验发生在哪一层**。

## 遗留

- 只开放了清晰度与下载模式两项。字幕语言、来源、限速、重试等仍要删任务重建（或先用克隆再改设置）；这是「一次可交付」的有意收缩。
- 克隆会**重新解析一次** URL：合集条目在两次之间发生变化时，新任务按当前解析结果建条目（条目序号仍按原 `playlist_items` 过滤）。这是「不信任前端传回的元数据」的既有口径，不是缓存复用。
- 新任务与原任务的 playlist 落在**同名子目录**，`skip_existing` 因此能命中；若原任务关着该选项，新任务也会沿用「关」，此时会真的重下并产生新文件。面板文案已写明这一条件是「如果原任务开着」。
- 没有「克隆时复制 B站 投稿记录」的选项：投稿记录属于原任务，复制会造成重复投稿。
