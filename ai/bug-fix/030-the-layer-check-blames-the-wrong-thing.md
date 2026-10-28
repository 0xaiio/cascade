# 030 · 分层校验把「反向依赖」报成「模块没登记」

| 项 | 值 |
| --- | --- |
| 发现时间 | 2026-10-28 20:0x +08:00（给 [011](../refactor/011-flat-modules-into-feature-packages.md) 改过的护栏做注入取证时） |
| 修复时间 | 2026-10-28 20:3x +08:00 |
| 起点 commit | `281ada7` |
| 结论 | **已修**。`scripts/check_layers.py` 此前**零测试**；现在 15 条，还原修复点后 5 条红 |

---

## 1. 问题

`check_layers.py` 是依赖方向的唯一闸门：它从 `docs/design.md` 的模块矩阵取层号，
只允许「层 n import 层 ≥ n」。往一个 L3 模块里塞一条 `from ..api import api_support`
（目标 L0），期望它报「依赖方向朝上」。实际报的是：

```
✗ download/ytdlp_formats.py 里的 `from .api` 指向未登记的模块
```

它确实红了 —— 但**红错了原因**。照着这句话去修，正确动作是「把 `api` 加进矩阵」；
加完之后反向依赖依然在，而且从此**再也不会被发现**（`api` 有了层号，方向判据才会生效，
但如果被登记成 L0 之外的层，或者干脆登记成「包」而子模块各归各层，方向就混过去了）。

## 2. 原因

`imports_of` 对 `from X import a, b` 只记模块部分，**把导入名丢了**：

```python
if node.module:
    targets.add("/".join([*base, *node.module.split(".")]))   # `from ..api import api_support` → `api`
```

`from ..api import api_support` 里 `api_support` 是**子模块**，真实依赖是 `api/api_support`；
`from ..contracts import config` 里 `config` 也是子模块。但同一种写法也用于**符号**
（`from ..api_support import helper`），只看语法分不出来。

于是解析结果停在父包上。`api` 这个键从未在矩阵登记（矩阵登记的是 `api/api_context`、
`api/api_support`、`api/routers` 这些），所以主循环走到 `layer_of("api")` 得到 `None`，
先撞上「未登记」那条分支 —— 方向判据根本没机会执行。

## 3. 修法

`imports_of` 多收一个 `known`（真实存在的模块键集合），并新增
`_prefer_existing_module`：只有当 `X/a` 确实是个模块时，才把依赖细化到它；
否则保持 `X`。`main()` 传 `set(modules)`。

这样「子模块」与「符号」用**存在性**分开，而不是猜语法。同一条注入现在的输出：

```
✗ download/ytdlp_formats.py（L3 领域）import 了 api/api_support.py（L0 入口）—— 依赖方向朝上
```

## 4. 为什么它值得单独记一条

护栏被放宽时不会有人发现 —— **校验脚本说「通过」的样子和真的通过一模一样**。
重构 011 重写了它三处（模块键从链接目标取、相对导入按 `level` 折算、层号回退到已登记祖先），
而它此前**没有任何测试**。所以这条记录真正的产出不只是那一行诊断，还有 `tests/test_check_layers.py`：
把「矩阵解析」「层号回退」「相对导入折算」「闸门会失败」四件事钉住。

顺带确认了一件事（原以为会是个洞）：`layer_of` 会回退到最近的已登记祖先包，
所以理论上「包内新增模块」会静默继承该层的层号。**实测不成立** —— `common/`、`download/`
这些包本身没有登记（矩阵里那两行在「目录说明表」里，被「层号必须在格子开头」这条判据
正确滤掉了），所以包内新增模块仍然会报「没有登记」。唯一被整体登记的包是 `api/routers`，
它的成员本来就同层。

## 5. 验证

**A/B**：把 `scripts/check_layers.py` 还原成修复前，新用例 **5 红 10 绿**；修复后 15 全绿。
其中 3 条红是 `imports_of` 多了一个参数导致的 `TypeError`（信号较弱，如实记下），
真正咬住缺陷的是这两条：

- `test_a_submodule_import_is_attributed_to_the_submodule`（`api` vs `api/api_support`）
- `test_a_reverse_dependency_is_reported_as_a_direction_problem`（断言输出里**有**「依赖方向朝上」、
  **没有**「未登记的模块」）

**注入取证**（改真实模块，跑完即 `git checkout` 还原）：

```
往 backend/app/download/ytdlp_formats.py（L3）追加 `from ..api import api_support`
修复前：✗ download/ytdlp_formats.py 里的 `from .api` 指向未登记的模块
修复后：✗ download/ytdlp_formats.py（L3 领域）import 了 api/api_support.py（L0 入口）—— 依赖方向朝上
```

另有两条注入确认护栏整体仍然咬人：`common/` 下新增空模块 → 「没有登记」；
`api/routers` 之外的新模块同样被抓住。

## 6. 未覆盖

`imports_of` 只认相对导入。包内一律写相对导入是**约定**（文档在 `design.md`），
绝对导入（`from app.api import …`）绕开这条校验 —— 这是有意留的边界，测试里也钉住了
（`test_absolute_imports_are_not_checked`），不是漏测。
