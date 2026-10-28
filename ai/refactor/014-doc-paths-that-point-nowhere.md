# 014 - 让文档里的代码路径不再指空：13 处断引分类改写 + 一道闸门

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-28 19:46 +08:00 |
| 实施时间 | 2026-10-28 19:47 ~ 20:10 +08:00 |
| 依据 | [013](013-frontend-feature-directories.md) §6 第 1 条留下的未修项：「`frontend/src/App.test.tsx` 的 13 处历史断引没修」 |
| 起点 commit | `55fb9d3` |
| 本轮提交 | 见文末「提交与回滚」 |
| 结论 | **完成**。9 处导航指称改到现址、其余保留原文并补现址；新增 `scripts/check_doc_paths.py`（含理由白名单）接进 CI。文档侧六项门槛全绿，未改任何产品代码 |

---

## 1. 问题：13 处断引，但不是 13 个同一个问题

`frontend/src/App.test.tsx`（1521 行 / 69 用例）于 `f270e7a` 被 [008](008-split-app-tests.md) 拆成 11 个文件，
[013](013-frontend-feature-directories.md) 又把这 11 个文件按功能归位到 7 个目录。文件没了，
指向它的文字还在，而**三种现存检查都看不见这种写法**：

| 检查 | 它管什么 | 为什么漏掉本案 |
| --- | --- | --- |
| `scripts/docs.py check` | `[文字](相对路径)` 是不是断链 | 这 13 处是**行内代码**，不是链接 |
| `scripts/check_doc_anchors.py` | `[标签](../x.py#Lnn)` 的行锚有没有漂 | 同样只认**链接**，且要求带 `#L` |
| `scripts/check_diagram_edges.py` | `.puml` 的边集与 import | 只管图，不管散文 |

`git grep` 一查就露：`docs/`、`ai/`、`README.md` 里指向不存在文件的引用共 **18 处 / 4 个路径**
（`frontend/src/App.test.tsx` 15 处、`backend/app/config.py`、`backend/app/download/_probe.py`、
`frontend/src/types.ts` 各 1 处）。013 报的 13 处是「只认 `frontend/src/` 前缀」的口径；
把不带前缀的裸 `App.test.tsx`（`ai/ui/*` 的小标题、`ai/perf`、`ai/plan`）也算上还要多 4 处。

## 2. 判据：导航指称 vs 事件记录

13 处不能一起改。有的改成现址就对了，有的改了就变成假话 —— 判据是**读这句话的人要不要按它去找东西**：

| 类型 | 特征 | 处置 |
| --- | --- | --- |
| **导航指称** | 「这条断言在哪」「这个 fixture 要改哪个」—— 读者会去找 | **改到现址**（原文件名是主语时，保留原名 + 补现址） |
| **事件记录** | 「删除 X」「`git show HEAD:X`」「当时的基线是 1521 行」—— 写的就是那一刻的状态 | **保留原文**，改一个字就假 |

这条判据不是本轮新造的，[008](008-split-app-tests.md) §7 当时就是这么定的（「保留原文 —— 那几条写的是当时的事实」）。
本轮补的是它缺的那一半：**保留原文的地方，要给一个能走到的现址**，否则读者只有考古一条路。
008 §7 的表格现在是这条政策的单一来源。

## 3. 逐条处置

### 3.1 改到现址（9 处）

| 位置 | 原指称 | 现址 | 判定依据 |
| --- | --- | --- | --- |
| `ai/ui/001` 头部「范围」 | `frontend/src/App.test.tsx` | `frontend/src/styles.test.ts` | 那一轮加的版面不变式，008 已原文搬入该文件 |
| `ai/ui/001` 「新增断言」标题 | `App.test.tsx` → `keeps layout out of the file-button base class` | 同上（`styles.test.ts:17`） | 用例名一字未变，按名可搜 |
| `ai/ui/002` 头部「范围」 | 同上 | 同上 | 同上 |
| `ai/ui/002` 「新增断言」标题 | `App.test.tsx` → `keeps the fixed-width side column able to wrap its text` | 同上（`styles.test.ts:36`） | 同上 |
| `ai/bug-fix/006` 验证表 | 「`frontend/src/App.test.tsx`（+2 例）」 | `frontend/src/settings/settings.test.tsx` | 2 例分别是 `autosaves the proxy…`、`shows which proxy is actually in effect` |
| `ai/bug-fix/007` 验证表 | 「（+9 例）」 | 4 个文件：`settings/settings.test.tsx` 3 例、`settings/diagnostics.test.tsx` 2 例、`cookies/cookies.test.tsx` 3 例、`shared/ui/help-notes.test.tsx` 1 例 | 逐例点名，含「三种获取方式」实为 help-notes 那一条 |
| `ai/perf/PLAN.md` 风险行 | `frontend/src/App.test.tsx:331` | `settings/settings.test.tsx` 的 `autosaves concurrency without a save settings button` | 取的是 `getByLabelText("并发（同时下载的视频数；单个视频无效）")` |
| `ai/perf/PLAN.md` §6.2 | `App.test.tsx` | 同上 | 计划项已执行，断言在此 |
| `ai/plan/plan.md` Test Plan | `App.test.tsx` 的 analyze fixture | `frontend/src/download-request/analyzer.test.tsx` | 解析面板整套用例现居此处 |

顺手查出一件旧事：`PLAN.md` 写的 `App.test.tsx:331` **当时就是错的** —— 那条并发标签断言在旧文件的
第 419 行（331 行落在 cookies 导入用例中间）。这也是新闸门按**路径**而不是按行号判的原因：
行号会漂，路径至少有个明确的存在性判据。

### 3.2 保留原文（事件记录）

`ai/refactor/008` 内部的 17 行、`ai/refactor/001` 的 4 行、`ai/refactor/010` 的 3 行、
`refactor.md` 的基线表与 §4.1 第 5 项（2 行）、`013` 对缺陷本身的描述（4 行）—— 它们要么是**被删对象**，
要么是**当时跑的命令**，要么是**当时记录的基线**。改了以后「删除一个不存在的文件」这种句子就没法读了。

对这 13 处没有逐条加注：`008` 全篇讲的就是这次拆分，读者不会误以为 `App.test.tsx` 还在；
而 `001`/`010` 里的那几行说的都是「未拆分」「1521 行」，与文件名同时出现，语义自洽。

### 3.3 另外 3 个路径

| 路径 | 处置 |
| --- | --- |
| `backend/app/config.py`（011 §1.1 第 4 条） | 保留 —— 它写的是**移动前**的状态，同一节紧接着给出 `contracts/config.py` 的改法 |
| `backend/app/download/_probe.py`（011 §4） | 保留 —— 注入取证的临时模块，从未提交，本来就该不存在 |
| `frontend/src/types.ts`（013 的配置表） | 保留 —— 它是 before → after 的左边，右边是 `contracts/types.ts` |

## 4. 方案：把「路径存不存在」变成一条可执行的检查

`scripts/check_doc_paths.py`：扫 `docs/**/*.md` + `ai/**/*.md` + `README.md`，凡出现
`frontend/src/…`、`backend/app/…`、`backend/tests/…`、`scripts/…` 的路径，必须在磁盘上存在。

三个设计取舍：

1. **按路径白名单，不按行号**。行号会漂（本轮就抓到一处：`PLAN.md` 的 `:331` 早就不准了），
   而且每改一行文档就要维护一次白名单。`ALLOWED` 的键是路径、值是理由 —— **没写理由的条目不算数**。
2. **只扫四个会搬家的代码树**。不扫 `docs/` 自己的 `.md`（那是 `docs.py check` 的活）、不扫
   `tmp_acceptance/`（gitignore 里的临时产物）。
3. **白名单失效会告警**：登记的路径如果已经没有任何引用，脚本打 `!` 提示删条目 ——
   否则这份名单会慢慢变成一堆早就不存在的例外。

不做的事：**不自动改文档**。一处悬空要么该改现址、要么该登记成历史，脚本分不清（§2 的判据是语义的）。

## 5. 验证

| 项 | 结果 |
| --- | --- |
| `python scripts/check_doc_paths.py` | **通过**：91 份文档 / 360 处代码路径引用（含本记录），0 处未登记悬空；4 个路径登记为历史指称（写成本记录之后实测：`App.test.tsx`×22、另三个各 ×3 —— 含本记录自己的引用） |
| 注入取证 ①：`ai/_tmp_probe.md` 写两个不存在的路径 | 报 2 处 `✗`，**EXIT=1**；删掉后 EXIT=0 |
| 注入取证 ②：往 `ALLOWED` 塞一个存在的路径 | 打 `! 白名单里的 frontend/src/main.tsx 已经没有任何引用` |
| `python scripts/docs.py check` | 通过 |
| `python scripts/check_doc_anchors.py` | 通过（**0 漂移 / 222 处待复核**；013 之后是 219，本轮新增的 3 条链接都指向文件名而非符号名，按设计进复核列） |
| `python scripts/check_layers.py` / `check_api_contract.py` / `check_diagram_edges.py` | 通过 |
| CI | `ci.yml` 后端 job 加一步「文档里的代码路径」，YAML 解析通过（9 → 10 步） |

## 6. 未覆盖 / 如实说明

1. **`ai/` 里的普通链接仍然没有检查器。** `docs.py check` 的扫描范围只有 `docs/`，所以
   `ai/**/*.md` 里 `[文字](../x.md)` 这种**文档之间**的链接断了没人报（[010](010-baseline-sync-and-hash-backfill.md)
   记过一次人工扫链、修掉 9 处断链）。本轮只覆盖「指向代码的路径」，没有扩到「指向文档的链接」——
   那需要先定 `ai/` 的链接基准（现在有 `../refactor/x.md`、`../../ai/...` 两种写法混用）。
2. **不带目录前缀的裸文件名不在判据内。** `job_manager.py` 这种写法在 `ai/` 里很常见（记录里第一次提到后
   就简称），机检分不清它是「指某个模块」还是「指当时那个文件名」。
3. **`docs/` 里的行内代码路径同样被本脚本覆盖**，但它们同时也是 `docs.py check` 的链接扫描范围 ——
   两条检查在链接上重叠，在行内代码上互补，不算冗余。

## 7. 风险与回滚

- 风险：新闸门可能把「合理的历史指称」判成错，挡住提交。缓解：`ALLOWED` 是路径级白名单，
  误报只需加一条带理由的条目，不必改文档。
- 回滚：`git revert <commit>`。脚本与 CI 步骤一起撤；文档改动是纯文字，价值独立于脚本。

## 8. 关联

- 上游：[013](013-frontend-feature-directories.md) §6 第 1 条；政策来源 [008](008-split-app-tests.md) §7。
- 同类： [012](012-make-the-dependency-diagram-true.md)（依赖图从「看着像」变成「可机检」）——
  本轮做的是同一件事的另一个面：**文档与代码树的一致性**。

## 9. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 | `a2d66db` |
| 改动面 | 6 份 `ai/` 记录、1 份 `docs/` 文档、`ci.yml`、新增脚本 1 个 |
| 产品代码 | **零改动**（未跑测试；`check_doc_paths.py` 只读文档与文件名） |
