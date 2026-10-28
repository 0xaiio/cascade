# 013 - 前端按功能分目录：19 个组件 + 21 个测试从两个平铺桶里拆出来

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-28 19:26 +08:00 |
| 实施时间 | 2026-10-28 19:27 ~ 19:45 +08:00 |
| 依据 | 用户要求：分析 `*.test.tsx` / `components/*.tsx` 未按功能组织的问题，按功能重排，并与后端已完成的 [011](011-flat-modules-into-feature-packages.md) 对齐 |
| 起点 commit | `1a64b23` |
| 本轮提交 | 见文末「提交与回滚」 |
| 结论 | **完成**。50 个文件重排进 7 个功能目录 + `contracts/` + `shared/`；**21 文件 / 286 用例**在重构前后逐数相同，`tsc` 与 `vite build` 通过，五个 Python 门槛全绿 |

---

## 1. 问题：两个平铺桶，"谁测谁"看不出来

重构前的 `frontend/src` 是**两个平铺桶**：

- `components/`：19 个 `.tsx`，从 `Toggle.tsx`（41 行，无业务）到 `JobQueue.tsx`（665 行）挤在一起；
- `src/` 根：21 个 `*.test.tsx|ts` 与 12 个纯模块（`api` / `formatting` / `biliStatus` / `types` …）混放。

三处具体代价：

1. **文件名不再回答归属**。`bili-upload.test.tsx` 测的是 B站 上传，`task-center-files.test.tsx` 测的是任务中心，
   但它们在同一个目录里与 `styles.test.ts`、`layout.test.tsx` 平级 —— 想知道「改 `BiliUploadDialog` 要跑哪些用例」，
   只能逐个打开文件看它 import 了什么。
2. **一个功能的改动横跨两个目录**。改 B站 上传平均要碰 `components/Bili*.tsx`（6 个）+ `src/bili*.ts`（4 个）
   + `src/bili-*.test.tsx`（2 个），三个方向、两个桶。
3. **基础件与业务件混在一起**。`Toggle.tsx` 没有业务判断、被一个功能用；`JobQueue.tsx` 有 665 行业务编排。
   两者同一个目录，评审时无法用目录做「这一层允许依赖谁」的判断。

判据：**目录应当回答「服务于谁」，不回答「是组件还是测试」** —— 这正是后端 011 定下的那条。

## 2. 边界：本轮**不做**的事

1. **不改文件名、不拆任何组件**。`JobQueue.tsx` 仍是 665 行一整个；拆分是另一个议题（[refactor.md](refactor.md) §4.1 的候选）。
2. **不改任何业务逻辑**。除 import 说明符与一处夹具注释外，`.tsx` 的 JSX、`.ts` 的判定一行未动。
3. **不动 `styles.css`**。它是全局单表（45KB、按类名组织），拆它要配套改 19 个组件的 class 名，属独立一轮。
4. **不动 `src/test/`**。夹具是"只有一份"的共享物，位置本来就对。
5. **不给前端造一个 `check_layers.py`**。前端的依赖关系是「App 装配、功能目录互不反向」，可从 import 直接看出来；
   造一个只覆盖 59 个文件、还要维护例外表的闸门，收益不抵维护成本（见 §5 第 2 条）。

## 3. 方案

### 3.1 目录（与后端 1:1 对齐）

| 目录 | 内容 | 组件 / 模块 / 测试 |
| --- | --- | --- |
| `src/`（根） | `App.tsx`（编排根）、`main.tsx`（入口）、`styles.css`、`layout.test.tsx`、`styles.test.ts`、`vite-env.d.ts` | 2 / 1 / 2 |
| `contracts/` | `types.ts` —— 线上契约，与后端 `contracts/schemas.py` 一一对应 | 0 / 1 / 0 |
| `shared/` | `api`（HTTP 边界 + 超时）、`errors`、`formatting`、`quality`、`subtitles` | 0 / 5 / 3 |
| `shared/ui/` | `HelpPopover`、`Toggle`、`StatusPill` —— 无业务判断的基础件 | 3 / 0 / 1 |
| `download-request/` | `UrlAnalyzer`、`AnalysisPanel`、`DownloadOptionsPanel`、`SearchableLanguageSelect` | 4 / 0 / 1 |
| `cookies/` | `CookieSection`、`cookieLock` | 1 / 1 / 1 |
| `task-center/` | `JobQueue`、`SubtitleOpenButton` | 2 / 0 / 4 |
| `settings/` | `SettingsPanel`、`ProxySection` | 2 / 0 / 2 |
| `bilibili/` | 6 个上传相关组件 + `biliForm` / `biliStatus` / `biliProgress` / `biliHealth` | 6 / 4 / 6 |
| `notifications/` | `notifications`、`CompletionToasts` | 1 / 1 / 1 |
| `test/` | `appFixtures`、`appHarness`、`cssRules`、`setup` | 0 / 4 / 0 |

`App.tsx` / `main.tsx` 留根，理由与后端 `main.py` 相同：`index.html` → `/src/main.tsx` → `./App` 这条入口路径
是外部约定（`vite` 的 `index.html` 里写死了 `/src/main.tsx`），挪进子目录只会把它变长。
`layout.test.tsx`（App 版面与文案不变式）也跟着编排根留根 —— 它测的是 `App` 自己的版面契约，不属于任何单个功能。

### 3.2 两条约定（写进 `docs/design.md`）

1. **测试跟着它测的能力走，不跟它 `render` 谁走**。多数 `*.test.tsx` 都 `render(<App/>)`（有意的集成口径），
   但归属看断言描述的是谁的行为：`resolution-fallback.test.tsx` 断言的是任务卡上的「以 720p 重启」，
   所以进 `task-center/`，尽管它的断言里也出现分辨率。
2. **只有「一份」的东西才进 `shared/`**：被两个以上功能目录 import 才算共享。`HelpPopover` 被
   cookies / settings / download-request 三处使用 → 进 `shared/ui/`；`biliStatus` 只服务 B站 上传 → 留在 `bilibili/`，
   不提前"抽象"。

### 3.3 机械部分

`git mv` 移动 50 个文件 + 脚本重算相对导入（**扩展名无关**的查表：说明符多半不带 `.tsx`，映射键带）。
只替换引号里的路径字符串 ⇒ 每个文件行数不变 ⇒ 文档里 `#Lnn` 行锚不需要重算。

---

## 4. 实施

| 项 | 值 |
| --- | --- |
| 移动文件 | 50（`git status`：12 R + 38 RM） |
| 改写相对导入 | 41 个文件 |
| 文档 / UML | 27 个文件：10 份 `docs/` 文档、15 份 `ai/` 记录、2 张 `.puml`（后者同时重渲染了 SVG） |
| 配置 | `scripts/check_api_contract.py` 的 `frontend/src/types.ts` → `contracts/types.ts`（含一处按路径段拼的常量） |
| 一处注释 | `test/appHarness.ts` 顶部的用法示例 `./test/appHarness` → `../test/appHarness`（用例搬进功能目录后深了一层） |

一次做错的记录（如实写）：第一版脚本用**带扩展名的映射键**查表，而说明符 `./components/JobQueue` 不带扩展名
⇒ 10 个"原地不动"的文件（`App.tsx` / `main.tsx` / `test/*`）的 import 没被改写，`tsc` 立刻报一串
`TS2307: Cannot find module './components/AnalysisPanel'`（连带一批 `TS7006`，那只是拿不到类型后的连锁反应）。我先写了个"只修解析不到的说明符"的补丁，
结果那 10 个文件的相对深度算错一层（`../types` 从 `bilibili/` 出发指向 `src/types`，已不存在）。
**最终把整个前端退回 HEAD（反搬 + 按索引还原）重做一遍**，而不是继续打补丁 —— 两套路径并存时，
补丁只会把状态搅得更难判断。

## 5. 验证

### 5.1 测试与构建（[refactor.md](refactor.md) §5 第 3 步）

| 项 | 基线（`1a64b23`） | 重构后（两次） |
| --- | --- | --- |
| 前端测试 | **21 文件 / 286 用例** 全绿 | **21 文件 / 286 用例** 全绿 ×2 |
| `tsc --noEmit` | 0 | 0 |
| `vite build` | — | 通过（`index.html` + 24.83 kB CSS + 247.03 kB JS） |

**文件数与用例数必须逐数相等**：本机并行跑 vitest 会静默丢文件（`--no-file-parallelism` 之外还丢过 15/172），
所以「文件数变少但全绿」按红灯处理。本轮三次跑都是 21/286。

### 5.2 仓库门槛

| 检查 | 结果 |
| --- | --- |
| `python scripts/docs.py check` | 通过（本地链接 + 15 张 UML 的 SVG 与源一致） |
| `python scripts/check_doc_anchors.py` | **没有漂移**（行数未变；219 处"待人工复核"属正常项） |
| `python scripts/check_api_contract.py` | 通过（35 接口 + 76 条目；脚本已改读 `contracts/types.ts`） |
| `python scripts/check_layers.py` | 通过（后端未动） |
| `python scripts/check_diagram_edges.py` | 通过（后端未动） |

### 5.3 文档路径全量扫描

对 `docs/**/*.md` + `ai/**/*.md` + `README.md` 里所有 `frontend/src/...` 引用逐个检查文件是否存在：

```
指向不存在文件的引用：13 处，全部是 frontend/src/App.test.tsx
```

那 13 处是 [008](008-split-app-tests.md) 把 `App.test.tsx` 拆成 11 个文件时的遗留断引，**本轮之前就存在**
（不是路径替换能修的，需要逐条改写成新的文件名），见 §6 第 1 条。

> 2026-10-28 已全部处理：9 处「导航指称」改到现址，其余「事件记录」保留原文（判据见
> [008](008-split-app-tests.md) §7）；另加 `scripts/check_doc_paths.py` 防复发。
> 见 [014](014-doc-paths-that-point-nowhere.md)。

## 6. 未覆盖 / 如实说明

1. **`frontend/src/App.test.tsx` 的 13 处历史断引**——本轮只登记、没修。它指向一个 2026-10-02 就已经拆掉的文件，
   修它要逐条判断"当时那句话该指向拆分后的哪个文件"，与本轮主题无关。
   **已在下一轮修掉**：[014](014-doc-paths-that-point-nowhere.md) 按「导航指称 / 事件记录」分两类处理，
   并把这类断引变成一条可执行的检查。
2. **没有对「功能目录之间的依赖方向」加机器校验**。当前跨功能 import 只有三条：
   `download-request → cookies`（解析面板里嵌 cookies 区）、`task-center → bilibili`（任务行上的上传入口与明细）、
   `settings → bilibili`（设置里的登录区）。三条都是"父界面嵌子功能区"，方向单向、无环。
   要机检就得再写一个 `check_frontend_layers.py` 并维护例外表 —— 59 个文件的规模下，成本高于收益。
3. **`component-overview.puml` 只把标签补上了目录前缀**，没有像后端那样做边集机检：那张图画的是"全景"，
   不表达逐条 import，没有可机检的判据。
4. **没跑浏览器级验收（CDP 探针）**。本轮零逻辑改动、测试与构建全绿，`styles.css` 未动；
   视觉上不会有差异，但"没有量过像素"这件事如实写在这里。
5. **`styles.css` 仍是单表**（45KB）。它按类名组织、被 19 个组件共用，拆分要先定命名约定（见 §2 第 3 条）。

## 7. 风险与回滚

- 回滚：`git revert <commit>`。移动是纯 rename，revert 会原样还原路径与 import。
- 残留风险：新增组件时的纪律变成两条 —— 放进**功能目录**（不是 `components/`），**测试跟着功能走**；
  若两个以上功能要用，才进 `shared/`。这条已写进 `docs/design.md` 的「前端组件边界」。

## 8. 关联

- [011](011-flat-modules-into-feature-packages.md)：后端做过的同一件事（46 个平铺模块 → 7 个包），本轮对齐它
- [008](008-split-app-tests.md)：把 1500 行的 `App.test.tsx` 拆成 11 个按功能面的文件 —— 本轮是把这 11 个
  连同后来新增的用例**归到功能目录**，两步合起来才是"按功能组织"
- [001](001-frontend-view-layer.md)：展示层从 `App.tsx` 外移到 `components/` 的那一轮（现在这些组件再按功能分组）

---

## 9. 提交与回滚

提交：见 `git log --oneline -- ai/refactor/013-*.md`（记录与改动同一次提交）。
回滚：`git revert <commit>`。
