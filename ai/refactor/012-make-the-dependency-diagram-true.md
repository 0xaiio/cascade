# 012 - 让依赖图说真话：补 6 个模块、改 40 条边，并把它纳入机器校验

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-28 19:18 +08:00 |
| 实施时间 | 2026-10-28 19:20 ~ 19:45 +08:00 |
| 依据 | [011](011-flat-modules-into-feature-packages.md) §5 第 1 条留下的缺口，用户要求「补上」 |
| 起点 commit | `0be7b2e` |
| 本轮提交 | 见文末「提交与回滚」 |
| 结论 | **完成**。`module-dependencies.puml` 从「46 组件 / 105 边、缺 5 个模块」变成「46 组件 / 125 边，与代码逐边一致」；新增 `scripts/check_diagram_edges.py`（纯标准库）并接进 CI |

---

## 1. 计划

### 1.1 起点：不只是「少四个点」

011 的结论里只写了「缺 4 个模块」。真正动手前先算了一次差集（图的边集 vs `backend/app` 的
相对 import，按 `level` 折算），结果比预期差：

| 项 | 数量 | 例子 |
| --- | --- | --- |
| 图上有、代码里已不存在的边 | **10** | `Manager --> Stall` / `Manager --> Advice` / `Manager --> Fallback` / `Manager --> Paths` / `Main --> CookieHealth` / `Main --> Connectivity` / `Main --> ReadModel` / `Main --> Schemas` / `DB --> Models` / `Broker --> Schemas` |
| 代码里有、图上没有的边 | **30** | `Manager --> Recovery` / `Service --> SubtitlePlan` / `BiliSubtitle --> BiliApi` / 一批 `X --> Schemas` |
| 图上没画的模块 | **6** | `orphan_recovery` / `safe_delete` / `subtitle_plan` / `bili_subtitle`，**外加** `host/dev_server` 与 `__main__` |

那 10 条过期边是历史的沉积：`job_manager` 在 [003](003-resolution-decisions.md)（降级决策）、
[004](004-artifact-paths.md)（路径）、[005](005-safe_delete.md)（删除判定）里被拆小，
职责搬进了新模块，**图的箭头没跟着搬**。`Manager --> Stall` 尤其能说明问题：停滞判定现在由
`ytdlp_service` 调 `stall_guard`，`job_manager` 一行都不 import 它 —— 图上那条箭头会让人去
`job_manager` 里找一个不存在的调用。

### 1.2 边界：本轮**不做**的事

1. **不改任何代码**（`backend/app/**` 一行未动）。这一轮只让图跟着代码走，不改代码去迁就图。
2. **不拆图**。125 条边确实稠密，可拆成「L0-L2」与「L3-L5」两张 —— 但那是文档结构调整，另开一轮。
3. **不推广到其余 14 张 UML**。`component-overview` / `four-plus-one-development-view` 里也有模块标签，
   但它们表达的是「组件全景」而不是「逐条依赖」，没有可机检的判据。
4. **不改 `docs.py check` 的语义**：它守的是「SVG 与 `.puml` 同源」，本轮新增的脚本守「`.puml` 与代码同构」，
   两件事分开。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 手写 40 条边 → 手抖、少一条或多一条 | 边集**由脚本从代码生成**，不是手改；生成后再跑一次差集自证为空 |
| 补全后图不可读 | 渲染三个布局变体实测比对（§3.2），保留 `ortho` |
| 新脚本「永远绿」 | 三次注入取证：删一条真边、加一条假边、加一个未画模块，都必须 EXIT=1 并精确报出（§4.2） |
| 新脚本进 CI 后误报（`api/routers/*` 聚合节点） | 聚合规则写进脚本且只有这一条；8 个路由模块都映射到同一个别名 |

---

## 2. 实施方案

### 2.1 图（`docs/diagrams/module-dependencies.puml`）

- 补 6 个组件：`__main__.py`（L0）、`host/dev_server.py`（L0）、`download/orphan_recovery.py`（L2）、
  `download/subtitle_plan.py` / `download/safe_delete.py` / `bilibili/bili_subtitle.py`（L3）。
  标签沿用 011 的约定 `[<相对路径>\n<一句话>] as <别名>`。
- 边集按代码重生成：保留原有「按源分组」的行序（组内旧目标在前、新目标按组件声明序追加），
  生成 125 条边，删掉 10 条过期边。
- 图内说明补一段：这张图是**被校验**的，`api/routers/*` 是唯一的聚合节点。

### 2.2 闸门（`scripts/check_diagram_edges.py`，129 行）

判据只有一条：`{(源, 目标)}` 两个集合相等。三类差异都算错：

1. `图里缺边：X --> Y（代码里有这条 import）`
2. `图里多边：X --> Y（代码里已经没有这条 import）`
3. `X.py 没有出现在依赖图上（补一个组件，标签里写它的相对路径）`

**为什么它必须存在**：`docs.py check` 只比「SVG 与 `.puml` 是否同源」。两份**一起**过期时它照样通过 ——
这正是本轮之前的状态：`module-dependencies.svg` 与 `.puml` 完全一致，而 `.puml` 与代码差 40 条边。

### 2.3 接线

- `docs/development.md` 的「结构性检查」表：三个 → **四个**，补 `check_diagram_edges.py` 一行（含「什么时候必须跑」）。
- `.github/workflows/ci.yml`：后端 job 加一步，job 名与头部注释同步改成「测试 / 分层 / 契约 / 依赖图 / 锚点」。
  该脚本是纯标准库，不碰 CI「不引入新依赖」那条约定。

---

## 3. 实施情况

### 3.1 数字

| 项 | 之前 | 之后 |
| --- | --- | --- |
| 组件 | 40 | **46** |
| 边 | 105（其中 10 条是假的） | **125**（与代码逐条一致） |
| SVG 尺寸 | 4436×1334 | 4969×1384 |

### 3.2 布局：试了三个变体才定

| 变体 | 尺寸 | 结论 |
| --- | --- | --- |
| `skinparam linetype ortho`（保留） | 4969×1384 | 边沿直角走，能逐段跟；稠密但仍可追溯 |
| 默认（polyline） | 6268×1464 | 弃。曲线横跨整幅，几乎无法判断哪条线接到哪 |
| `left to right direction` | 2050×4779 | 弃。虽窄，但长曲线更多，整幅变成一团 |

结论写进本轮记录而不是图里：**再稠密就该拆图，而不是继续调布线参数**。

---

## 4. 验证

### 4.1 门槛

| 检查 | 结果 |
| --- | --- |
| `python scripts/check_diagram_edges.py` | 通过：46 组件 / 125 边 ↔ 125 边 |
| `python scripts/docs.py check` | 通过（SVG 已重渲染，与 `.puml` 字节一致） |
| `python scripts/check_layers.py` | 通过（代码未动） |
| `python scripts/check_doc_anchors.py` | 无漂移 |
| CI 配置 | `yaml.safe_load` 解析通过；后端 job 8 步 |

### 4.2 取证：闸门抓得住

```
# ① 删掉一条真边（Service --> SubtitlePlan）
✗ 图里缺边：Service --> SubtitlePlan（代码里有这条 import）

# ② 再加一条假边（Formats --> Manager）
✗ 图里多边：Formats --> Manager（代码里已经没有这条 import）

# ③ 放一个没画进图里的模块（download/_probe.py）
✗ download/_probe.py 没有出现在依赖图上（补一个组件，标签里写它的相对路径）
```

三次都是 EXIT=1，还原后复跑通过。

---

## 5. 未覆盖 / 如实说明

1. **125 条边的图注定是「参考图」而不是「一眼看懂图」**。4969px 宽，在文档站点里要缩放或横滚。
   真正的下一步是**按层拆成两张**（L0-L2 一张、L3-L5 一张），本轮没做。
2. **只校验「有没有这条边」，不校验语义**。`Broker --> Schemas : JSON payloads` 这条旧边被删掉，
   是因为 `infra/events.py` 确实不 import `schemas` —— 但它「事件载荷是 JSON」这个事实仍成立，
   只是不该用一条依赖边表达。这类「想表达事实但判据不成立」的情形，脚本管不了，只能人来判断。
3. **`component-overview.puml` 与 `four-plus-one-development-view.puml` 没有同样的闸门**。
   它们也在 011 里被改过标签，但它们画的是组件全景，没有「边 = import」这条语义。
4. **没在新机器 / CI runner 上跑过**。本机 Python 3.12 下通过；脚本只用 `ast` / `re` / `pathlib`，
   跨平台风险低，但仍属未取证。

---

## 6. 风险与回滚

- 回滚：`git revert <commit>`。图与脚本一起撤，CI 里多出的那一步也随之消失，不留悬空引用。
- 残留风险：以后新增模块时，`check_diagram_edges.py` 会**先红**（「没有出现在依赖图上」），
  这是设计意图 —— 与 `check_layers.py` 的「没登记进矩阵」同一套纪律：新模块必须同时在两处登记。

---

## 7. 关联

- [011](011-flat-modules-into-feature-packages.md)：留下这个缺口的那一轮
- [003](003-resolution-decisions.md) / [004](004-artifact-paths.md) / [005](005-safe_delete.md)：把 `job_manager` 拆小、
  却没有回填箭头的三轮（10 条过期边的来源）
- [007](007-ci-and-layers.md)：`check_layers.py` 与本轮 `check_diagram_edges.py` 是同一套思路（把「文档说的」与「代码做的」对齐成机器判据）

---

## 8. 提交与回滚

提交：见 `git log --oneline -- ai/refactor/012-*.md`（记录与改动同一次提交）。
回滚：`git revert <commit>`。
