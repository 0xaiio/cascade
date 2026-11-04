# E-01　下载前的规模预估与磁盘空间预检

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §E-01。2026-11-04 落地，一项一个提交。

## 结论

**先给一条「提示」，再给两次「判定」**，且两次判定共用同一个函数（`download/disk_budget.py` 的 `assess`）：

| 时机 | 估值来源 | 能否阻断 |
| --- | --- | --- |
| `POST /api/analyze` | `formats[].filesize` × 条目数，按**设置默认清晰度**挑格式 | 否，只挂 `disk_budget` 提示 |
| `POST /api/jobs` | 同一算法，按**本次请求里的 `options`** 重算 | 是，`409` 且不写库 |
| 每条视频开始前 | `prepare_download` 给这一条的**真实** `filesize` | 是，该条 `failed`、不启动 yt-dlp |

三档结论 `ok` / `insufficient` / `unknown`：**只有「两个数字都读到了且确实不够」才阻断**。平台没给大小（合集用 `extract_flat` 解析时的常态）、分区读不出来、`subtitles_only`、空选择，一律只提示。

## 为什么需要第三次

候选池原文写「预检数据已经在手，只差一次比较」，实测要修正这句判断：预检（`prepare_download`）发生在**每条视频被 worker 领到之后**，建任务时手上只有解析结果。而 flat playlist 的条目既没有 `formats` 也没有 `filesize` —— 恰恰是「批量下载撑爆磁盘」这个原始痛点最常见的场景，建任务这一层估不出来。

所以逐条开始前必须再核一次：此时 `item.total_bytes` 已被 `_apply_download_preparation` 写入这一条的真实大小。用「平均时长 × 猜一个码率」把建任务的估值补出来是**把提示伪装成预估**，与「界面里的数字必须能回答一个问题」冲突，不做。

## 实现要点

- `download/disk_budget.py`（新增，L3 领域）：估值、量取、三档判定、文案都在一处。`free_space_bytes()` 会**向上找最近的存在的祖先** —— playlist 的同名子目录在建任务时还没创建；`disk_usage` 抛 `OSError` 返回 `None`（读不到 ≠ 不够）。
- 安全余量固定 512 MiB（合并后的文件、字幕、缩略图、元数据，以及接近写满时的写放大），**不是可配置项**，避免在设置里再加一个「改完不生效」的快照（[032](../bug-fix/032-auto-retry-settings-need-a-restart.md) 的教训）。
- `DiskBudgetRead`（`contracts/schemas.py`）挂在 `AnalyzeResponse.disk_budget`；`message` 由后端拼好，前端原样显示、只按 `status` 配色，**不在浏览器里重算数字**。
- `_guard_item_disk_budget` 在 `_prepare_download` 之后、下载之前调用。`options.skip_existing` 且 `discover_existing_output_path()` 命中已有成品时跳过校验 —— 那一轮不写大文件，按满盘判失败是误伤。
- 失败文案带预计量、可用量、差额与「清理分区 / 换下载目录 / 重启这一条」，并明说已有文件不会被删（验收口径：新增失败路径必须给可执行的下一步）。

## 验证

| 层 | 用例 | 结论 |
| --- | --- | --- |
| 纯判定 | `backend/tests/test_disk_budget.py`（21 条） | 量级单位、祖先上溯、`OSError` 归 `unknown`、估值六条来源、正好相等 / 少 1 字节 / 五种 `unknown` 边界 |
| 接口 + 生命周期 | `backend/tests/test_disk_budget_lifecycle.py`（11 条） | 409 不写库、按请求清晰度复核、满盘时 `failed` 且 `downloads == []`、腾出空间后同一条 `failed → succeeded`、`skip_existing` 不误伤、`paused` 不被唤醒 |
| 前端 | `frontend/src/download-request/disk-budget.test.tsx`（5 条） | 三档提示、旧后端兼容、409 回执保留数字且请求确实发出 |
| 闸门 | `check_api_contract.py` / `check_layers.py` / `check_doc_anchors.py --fix` | 契约 38 接口 + 74 条目一致；新模块已登记进 `docs/design.md` 的 L3 矩阵；89 处行锚重算 |
| 全量 | `pytest -q` / `npm test` / `npx tsc --noEmit` | 2026-11-04 实测：后端 **1357 passed**（53 个测试文件）、前端 **28 文件 / 347 passed**、`tsc --noEmit` 退出 0 |

**承重验证**：把 `_guard_item_disk_budget` 的调用去掉，逐条那组用例里「满盘失败 / `downloads == []` / 腾出空间后恢复」三条会绿着过去又失去意义（它们依赖真实的大小与可用空间对比），因此断言同时钉住 `item.total_bytes == 400 MiB` 与 `service.downloads == []`，不是只看状态字符串。

## 遗留与边界

- 解析提示按默认清晰度估，与提交时实际选项可能不同；文案只说「预计」，阻断一律以 `POST /api/jobs` 为准。
- 直接调 API 绕过前端也会被 409 挡住（判定在服务端），但**没有逃生阀**：用户认为预估过于保守时，只能换下载目录或调低清晰度。这是「宁可拦住也不撑爆磁盘」的有意取舍。
- 只检查**目标分区**的可用空间；两个不同 `data/` 指向同一批下载目录时不互相感知（与 [036](../bug-fix/036-two-instances-write-the-same-part.md) 同形状的边界）。
- 空间在下载过程中被别的程序吃掉仍可能产生半截 `.part`；本条只管「开始前」，不做事中配额预留。
