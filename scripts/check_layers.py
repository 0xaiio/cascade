"""校验模块依赖方向符合 `docs/design.md` 的「模块职责矩阵」。

分层与模块清单的**单一来源**是 `docs/design.md` 的那张矩阵表 —— 本脚本解析它，所以
「新增了模块但忘了登记」也会被抓住（这正是那张表存在的意义：它是架构的机器可读表达）。

规则（[refactor.md §3](../../ai/refactor/refactor.md#3-判定准则) 第 2 条）：

    L0 入口 → L1 契约 → L2 编排 → L3 领域 → L4 基础设施 → L5 纯工具

箭头只向下 —— 层 n 的模块只能 import 层 m ≥ n 的模块。
**唯一例外是 L1 契约**：`config` / `schemas` / `models` 是各层共用的接口定义，
按矩阵自己的说法是「声明…」，被任何层 import 都是正常的。

用法：

    python scripts/check_layers.py          # 校验
    python scripts/check_layers.py --list   # 顺便列出每个模块的层
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"
DESIGN_DOC = REPO_ROOT / "docs" / "design.md"

LAYER_NAMES = {0: "L0 入口", 1: "L1 契约", 2: "L2 编排", 3: "L3 领域", 4: "L4 基础设施", 5: "L5 纯工具"}
CONTRACT_LAYER = 1


def parse_layers(design_text: str) -> dict[str, int]:
    """从模块职责矩阵取「模块键 → 层号」。

    矩阵行形如：`| [main.py](../backend/app/main.py#L39) | L0 入口 | 职责 | 不负责 |`
    一格里可能有多个链接（`[paths.py](…) / [log_safety.py](…)`），所以按链接逐个取。
    键是**相对 `backend/app` 的路径**（模块按功能分了子包：`download/job_manager`、
    `bilibili/bili_upload` ……），所以键从链接目标里取，不是从链接文字里猜。
    """
    layers: dict[str, int] = {}
    for line in design_text.splitlines():
        if not line.startswith("| ["):
            continue
        cells = line.split("|")
        if len(cells) < 5:
            continue
        # 层号必须**在格子开头**（`L2 编排`）。放宽成「格子里出现 L\d」会让
        # 目录说明表这类散文格子被误认成层登记 —— 那样未登记的模块会静默继承一个层。
        match = re.match(r"\s*L(\d)\s+\S", cells[2])
        if match is None:
            continue
        layer = int(match.group(1))
        for key in module_keys(cells[1]):
            layers[key] = layer
    return layers


def module_keys(cell: str) -> list[str]:
    """把一格里指向 `backend/app` 的链接换成模块键。

    键取自**链接目标**而不是链接文字：文字是给人看的（`[main.py](…)`、`[routers/](…)`），
    目标才是真实路径。包指向 `__init__.py`，去掉 `/__init__` 后就是包键（`api/routers`）。
    """
    keys: list[str] = []
    for target in re.findall(r"\]\([^)\s]*?/backend/app/([^)#\s]+)", cell):
        key = target.rstrip("/")
        if key.endswith(".py"):
            key = key[: -len(".py")]
        if key.endswith("/__init__"):
            key = key[: -len("/__init__")]
        keys.append(key)
    return keys


def actual_modules() -> dict[str, Path]:
    """`backend/app` 下所有模块，键是相对 `backend/app` 的无后缀路径（如 `api/routers/jobs`）。"""
    modules: dict[str, Path] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        modules[path.relative_to(APP_DIR).with_suffix("").as_posix()] = path
    return modules


def layer_of(key: str, layers: dict[str, int]) -> int | None:
    """查一个模块的层；没独立登记时逐级回退到**最近的已登记祖先包**。

    回退按段数从长到短找（`api/routers/jobs` → `api/routers`），不是只取第一段：
    模块现在活在功能包下面，只取第一段的话 `api/routers/jobs` 会去找 `api` 而永远落空。
    """
    if key in layers:
        return layers[key]
    parts = key.split("/")
    for depth in range(len(parts) - 1, 0, -1):
        ancestor = "/".join(parts[:depth])
        if ancestor in layers:
            return layers[ancestor]
    return None


def imports_of(path: Path, known: set[str] | None = None) -> set[str]:
    """模块里的包内相对 import（`from ..x import …` / `from . import x`），解析成模块键。

    要按 `level` 折算：模块搬到 `download/` 之后，「上一层」不再等于「包根」。
    只认相对导入是有意的 —— 包内一律写相对导入（绝对导入绕开了这条校验），
    这正是文档里「依赖方向」那条规则能被机器检查的前提。

    `known` 是「真实存在的模块键」集合，用来区分 `from ..api import api_support` 里
    `api_support` 是**子模块**还是**符号**：前者应记成 `api/api_support`，后者只能记成 `api`。
    不分清的话，`from ..api import api_support` 会被记成 `api` —— 而 `api` 从未在矩阵里登记，
    于是「依赖方向朝上」被报成「指向未登记的模块」，把人引去改文档而不是改代码。
    """
    relative = path.relative_to(APP_DIR).with_suffix("")
    package = relative.as_posix().split("/")
    if path.name != "__init__.py":
        package = package[:-1]

    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        base = package[: len(package) - (node.level - 1)]
        if node.module:
            key = "/".join([*base, *node.module.split(".")])
            targets.add(_prefer_existing_module(key, [alias.name for alias in node.names], known))
        else:
            for alias in node.names:
                targets.add("/".join([*base, alias.name]))
    return targets


def _prefer_existing_module(key: str, names: list[str], known: set[str] | None) -> str:
    """`from ..pkg import thing` 里 `thing` 确实是模块时，把依赖细化到 `pkg/thing`。"""
    if not known:
        return key
    for name in names:
        candidate = f"{key}/{name}"
        if candidate in known:
            return candidate
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="列出每个模块的层")
    args = parser.parse_args(argv)

    layers = parse_layers(DESIGN_DOC.read_text(encoding="utf-8"))
    if not layers:
        # 矩阵表格格式一变（列序、标题写法），解析就会得到空表；此时「全部未登记」的错误信息
        # 会误导人以为是代码的问题。宁可明确报「解析失败」。
        print(f"无法从 {DESIGN_DOC} 解析出任何模块：矩阵表格格式可能变了。")
        return 1

    modules = actual_modules()
    known = set(modules)
    problems: list[str] = []

    for key, path in sorted(modules.items()):
        source_layer = layer_of(key, layers)
        if source_layer is None:
            problems.append(f"{key}.py 没有登记在 docs/design.md 的模块职责矩阵里")
            continue
        for target in sorted(imports_of(path, known)):
            target_layer = layer_of(target, layers)
            if target_layer is None:
                problems.append(f"{key}.py 里的 `from .{target.replace('/', '.')}` 指向未登记的模块")
                continue
            if target_layer < source_layer and target_layer != CONTRACT_LAYER:
                problems.append(
                    f"{key}.py（{LAYER_NAMES[source_layer]}）import 了 {target}.py"
                    f"（{LAYER_NAMES[target_layer]}）—— 依赖方向朝上"
                )

    if args.list:
        for key in sorted(modules, key=lambda k: (layer_of(k, layers) if layer_of(k, layers) is not None else 99, k)):
            layer = layer_of(key, layers)
            print(f"    {LAYER_NAMES[layer] if layer is not None else '?? ?'}  {key}.py")
        print("")

    print(f"解析 docs/design.md 得到 {len(layers)} 个已登记模块；backend/app 下 {len(modules)} 个模块。")

    if problems:
        print("")
        for item in problems:
            print(f"  ✗ {item}")
        print("")
        print(f"分层校验失败：{len(problems)} 处")
        return 1

    print("分层校验通过：依赖方向一致，且没有未登记的模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
