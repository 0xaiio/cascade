# 011 - 平铺模块按功能分包：`backend/app` 从 46 个平铺文件到 7 个能力包

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-28 18:33 +08:00 |
| 实施时间 | 2026-10-28 18:36 ~ 19:05 +08:00 |
| 依据 | 用户要求：定位 `backend/app` 下所有平铺 `.py`，按功能重构进子目录，保持引用正确、测试全绿，并同步文档/导入/配置 |
| 起点 commit | `7542629`（main） |
| 本轮提交 | 见文末「提交与回滚」 |
| 结论 | **完成**。52 个文件移入 7 个功能包，`main.py` / `__main__.py` 留在包根；导入、测试、脚本、文档、UML 全部同步；后端全量 1223 passed（另一次 1222 passed + 1 条**既有**时序抖动，已单独修复），分层校验、行锚、契约与文档检查全绿 |

---

## 1. 计划

### 1.1 现状取证

`backend/app` 下 **46 个** `.py` 与 `routers/` 平铺在同一层（53 个模块 + 9 个 `__init__.py`，
合计 62 个文件、约 12000 行）。文件名前缀已经承担了分类职责：`bili_*` 10 个、`job_*`/`ytdlp_*`/下载相关 16 个、
`api_*` 2 个，其余是代理 / cookies / 端口 / 路径这类本机边界。**分类信息在文件名里，目录里没有** ——
新增模块只能靠自觉起对前缀，`ls app/` 看不出谁是「上传」谁是「下载」。

既有约束（这些让「移文件」不等于「改目录」）：

1. `scripts/check_layers.py` 拿 `docs/design.md` 的模块职责矩阵当**单一来源**，逐个模块比对层号；
   移动后矩阵的 46 条链接必须跟着改，否则「全部模块未登记」。
2. `scripts/check_doc_anchors.py` 校验文档里的 `#Lnn` 行锚；`docs.py check` 校验本地链接与 UML SVG 字节一致。
3. `backend/tests/test_finally_guards.py` 用 `APP_DIR.glob("*.py")`（**非递归**）扫源码，并断言
   `{"job_manager.py", "main.py"} <= names` —— 它自己写了一条守卫，正是防「扫了个空也通过」。
4. `backend/app/config.py` 用 `Path(__file__).resolve().parents[2]` 算仓库根。

### 1.2 边界：本轮**不做**的事

1. **不改任何文件名**。只挪目录。理由：`ai/` 下 30+ 份记录、`docs/` 下 15 份文档、UML 标签、测试文件名
   全部按 basename 引用；改名会把这批引用从「路径变了」升级成「名字也变了」，收益（去掉 `bili_` 前缀）
   抵不上风险。代价是 `bilibili/bili_manager.py` 里包名与前缀重复 —— 这条是**有意保留**的。
2. **不拆任何大文件**。`job_manager.py`（1635 行）、`ytdlp_service.py`（1206 行）仍是一整个模块，
   拆分是独立议题（[refactor.md](refactor.md) §4.1 的候选清单）。
3. **不动前端源码逻辑**。只改 3 处注释里的后端路径（含一个 Markdown 链接）。
4. **不改 `ai/bug-fix/` 等历史记录里的数字与命令**，只把路径跟到新位置（见 §5 第 3 条）。
5. **不给 `app/` 加顶层包装层**（不做 `app/src/` 之类）。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 相对导入层级算错 → 运行期才炸 | 用脚本按「旧模块键 → 新模块键」重算 `level`（不是手改），再对 62 个文件逐个 `importlib.import_module` 冒烟 |
| 文档 `#Lnn` 行锚整批漂移 | **改写时刻意不增删行**：只替换 `from … import …` 的前缀，一条 import 还是一条。实测行锚 0 漂移 |
| `parents[2]` 这类基于文件深度的路径假设 | 逐个搜 `__file__`（全仓 11 处），唯一受影响的是 `config.py`，改成 `parents[3]` |
| 源码扫描型测试静默失效 | `test_finally_guards` 的**非递归 glob** 改成 `rglob`，并把断言换成相对路径（`download/job_manager.py`），让目录归属也被钉住 |
| 分层校验「全绿但没看东西」 | 两次注入式取证：造一条 L5→L2 的 import、造一个未登记模块，都必须 EXIT=1 并精确报出（§4.2） |

---

## 2. 实施方案

### 2.1 目录（按功能，不按层）

```
backend/app/
├── main.py  __main__.py       # 装配根 + 进程入口（留包根：uvicorn app.main:app / python -m app）
├── api/                       # HTTP 契约层：api_context、api_support、routers/（8 个资源）
├── contracts/                 # 契约：config、models、schemas
├── download/                  # YouTube 下载域：队列/恢复/读模型、yt-dlp 边界、清晰度与字幕决策、产物路径
├── bilibili/                  # B站 投稿域：队列、biliup 边界、会员接口、元数据与文案、CC 字幕、HTTP 接缝
├── host/                      # 本机宿主边界：代理、连通性、浏览器 cookies、进程环境、端口、系统打开
├── infra/                     # 基础设施：数据库引擎、日志、进程内事件总线
└── common/                    # 纯工具：paths、log_safety
```

分配依据是「这段代码服务于哪块能力」，**不是层号**：`bilibili/bili_http.py` 是 L4、`infra/events.py` 是 L2。
层号继续只写在矩阵里，`check_layers.py` 按模块键查表 —— 两种切法因此可以并存而不打架。

`main.py` / `__main__.py` 留在包根是有理由的：`uvicorn app.main:app`、`python -m app`、`init/start.*`
都依赖这条导入路径，把它们挪进 `api/` 只是把入口路径变得更长。

### 2.2 搬迁与导入改写（机械部分）

1. `git mv` 移动 52 个文件（含 `routers/__init__.py` 与 8 个路由模块），新增 7 个包的 `__init__.py`（各一句职责）。
2. 用脚本按映射重算**包内相对导入**：`from .config import` → `from ..contracts.config import`、
   `from .. import api_support`（`routers/` → `api/routers/` 后仍是同一层）保持不变。
   多行 import（`from x import (\n a,\n b,\n)`）只改首行前缀，缩进与成员原样。
3. 包外**绝对引用**批量改写：`from app.X import`、`import app.X as m`、以及 `monkeypatch.setattr("app.X.attr")`
   这类**字符串里的模块路径** —— 最后这批最容易漏，测试会在运行期报 `AttributeError: module has no attribute`
   而不是导入错误。
4. 4 个路径假设点修复：`contracts/config.py` 的 `parents[3]`、`test_finally_guards` 的递归扫描、3 处源码注释里的
   `app/xxx.py`、前端 3 处注释与链接。

行数不变 ⇒ 文档行锚不用重算（§4.3 用它当判据）。

### 2.3 校验脚本升级（`scripts/check_layers.py`）

三处必须改，否则校验会「假绿」：

1. **模块键从链接目标取**，不从链接文字猜：链接文字是 `[job_manager.py](…/download/job_manager.py#L328)`，
   文字与路径不再相等。同时把「层号」的匹配从「格子里出现 `L\d`」收紧成「格子**开头**是 `L\d`」——
   否则新增的目录说明表（`| [api/](…) | HTTP 契约层 | … |`）会被误读成层登记。
2. **相对导入按 `level` 折算**：模块进了子包，「上一层」不再等于「包根」。
   `from ..download.job_manager import`（level 2）必须解析成 `download/job_manager`。
   旧实现只取 `node.module`，多级相对导入会全部解析错。
3. **层号回退按最近的已登记祖先**（`api/routers/jobs` → `api/routers`），不是只取第一段。

---

## 3. 实施情况

| 项 | 值 |
| --- | --- |
| 移动文件 | 52（`git status`：24 R + 28 RM，rename 检测保留历史） |
| 新增 `__init__.py` | 7（`api` / `contracts` / `download` / `bilibili` / `host` / `infra` / `common`） |
| 改写相对导入 | 28 个文件 |
| 改写包外绝对引用 | 48 个文件（`backend/tests` 43 + `scripts` 5） |
| 文档路径同步 | 38 份（`docs/` 12 + `ai/` 26） |
| UML 重渲染 | 3 张（`module-dependencies` / `component-overview` / `four-plus-one-development-view`） |

---

## 4. 验证

### 4.1 测试（[refactor.md](refactor.md) §5 第 3 步）

| 项 | 基线（`7542629`） | 重构后（两次全量） |
| --- | --- | --- |
| 后端全量 | 1222 passed / **1 failed**（`test_upload_timestamps_carry_a_timezone`） | 第一次 **1223 passed**；第二次 **1222 passed / 1 failed**（**同一条**） |
| 命令 | `../.venv/Scripts/python.exe -m pytest -q --basetemp=tmp_pytest/run_baseline_20261028` | 同参数换 `run_refactor_a` / `run_final_20261028` |

**这条红不是本轮引入的**，判据三条：

1. 它在我动手**之前**的全量跑里就红了（基线那条，工作区干净）；
2. 单跑通过，整个 `tests/test_api_bilibili.py` 连跑两次 **1 红 1 绿** —— 时序竞争，与目录结构无关；
3. 根因查实：用例用**真的** `BiliManager`，`POST /api/bilibili/uploads` 之后 worker 仍在跑，
   它收尾时写 `finished_at`；用例紧接着手工写入固定时刻，谁的写在后谁生效。
   夹具里本来就有 [`wait_until_finished`](../tests/test_api_bilibili.py#L139)，这条用例漏了调用。

修复单独成一个提交（[bug-fix/025](../bug-fix/028-upload-timestamp-test-races-the-worker.md)），不混进本轮。

### 4.2 取证：校验脚本抓得住（不是「恰好绿」）

```
# 注入 L5→L2 的反向依赖（common/paths.py 里 from ..download.job_manager import JobManager）
✗ common/paths.py（L5 纯工具）import 了 download/job_manager.py（L2 编排）—— 依赖方向朝上
分层校验失败：1 处   EXIT=1

# 注入未登记模块（backend/app/download/_probe.py）
✗ download/_probe.py 没有登记在 docs/design.md 的模块职责矩阵里
分层校验失败：1 处   EXIT=1
```

两处注入都在还原后复跑为通过 —— 说明升级后的脚本既能解析多级相对导入，也仍然要求逐个模块登记。

### 4.3 其它门槛

| 检查 | 结果 |
| --- | --- |
| `python scripts/check_doc_anchors.py` | **没有漂移**（219 处「待人工复核」属正常项，标签是文件名或散文） |
| `python scripts/docs.py check` | 通过（本地链接有效，15 张 UML 的 SVG 与 `.puml` 字节一致） |
| `python scripts/check_api_contract.py` | 通过（`types.ts` 35 接口、`openapi.yaml` 76 条目） |
| `python scripts/check_layers.py` | 通过（46 个已登记模块 / 53 个模块） |
| 导入冒烟 | 62 个文件逐个 `import_module`，0 失败 |
| 守卫扫描范围 | `test_finally_guards` 由 46 个文件扩到 **62** 个（含子包） |

---

## 5. 未覆盖 / 如实说明

1. **`module-dependencies.puml` 当时缺 4 个模块**（`orphan_recovery` / `safe_delete` / `subtitle_plan` / `bili_subtitle`）。
   本轮只改标签（加目录前缀）与说明文字，没有顺手补节点与边 —— 补边会改图的结构，属另一件事。
   **已在下一轮补掉**：[012](012-make-the-dependency-diagram-true.md) 补齐 6 个组件（上面这 4 个，再加 `dev_server` 与 `__main__`）
   与 30 条缺边、删掉 10 条过期边，并加了 `scripts/check_diagram_edges.py` 这个闸门。
2. **UML 变宽约 8%**（`module-dependencies` 4098→4436px，`component-overview` 4782→5155px，
   `four-plus-one-development-view` 6363→6911px）。原因是标签从 `job_manager.py` 变成 `download/job_manager.py`，
   高度未变。可读性靠缩放，不换行 —— PlantUML 的 `\n` 会把目录与文件名拆成两行，反而更难扫。
   （012 补边后 `module-dependencies` 进一步到 4969×1384，那部分变化归 012。）
3. **`ai/` 历史记录里的路径已跟到新位置，行数与命令保持当时的值**。例如 `refactor.md` §2 基线快照里
   `backend/app/download/ytdlp_service.py` 与「取证命令 `ls backend/app/*.py | wc -l`」并存 ——
   前者是当前可点的链接，后者是当时的事实。历史记录不重写结论。
4. **`ai/` 里的行锚不受 `check_doc_anchors.py` 保护**（脚本只扫 `docs/` 与 `README.md`）。本轮 52 个文件
   行数未变，因此那些锚事实上仍有效，但没有机器判据。
5. **前端注释里的一个锚点顺带修正**：`SettingsPanel.tsx` 里 `YTDL_ARIA2C_ENABLED` 的链接原来指向
   `config.py#L61`（落在注释块上），实际定义在 L68，已改。这是本轮唯一一处与「移目录」无关的改动。
6. **前端只做「注释级」改动，但测试照跑**：`npx vitest run --environment jsdom --no-file-parallelism`
   → **21 files / 286 tests passed**（必须加 `--no-file-parallelism`，本机并行跑 vitest 会静默丢文件）。
   `npx tsc --noEmit` 退出码 0（vitest 全绿 ≠ tsc 过，两件都跑）；`npm run build` 未跑 —— 本轮没有改前端构建产物与逻辑。

---

## 6. 风险与回滚

- **回滚**：`git revert <commit>` 一次即可（移动是纯 rename，revert 会原样还原路径）。
- **残留风险**：任何**动态**拼接的模块字符串（`importlib.import_module("app." + name)`）不会被这轮机械改写覆盖。
  本仓库当前没有这种写法（`grep import_module` 无命中，`importlib` 只用于 `importlib.metadata.version` 查版本），但未来新增时要留意。
- **新增模块的纪律**：放进哪个包看「服务于哪块能力」，登记进 `docs/design.md` 矩阵才能过 `check_layers.py`。

---

## 7. 关联

- [refactor.md](refactor.md)：重构总纲与判定准则
- [002-backend-api-layer.md](002-backend-api-layer.md)：上一轮把 `main.py` 拆成 `routers/` + 支撑模块
- [007-ci-and-layers.md](007-ci-and-layers.md)：`check_layers.py` 的由来
- `docs/design.md` §目录结构（按功能分包）：本轮新增的目录说明与两条硬约定（包内一律相对导入、目录不是层的别名）

---

## 8. 提交与回滚

提交：见 `git log --oneline -- ai/refactor/011-*.md`（记录与改动同一次提交）。
回滚：`git revert <commit>`。
