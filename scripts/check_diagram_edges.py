"""校验 `docs/diagrams/module-dependencies.puml` 的**边集**与 `backend/app` 的实际 import 一致。

为什么需要它：这张图是设计文档里「依赖方向」那一节唯一的图，但它是**手写**的。
模块拆小了、import 改了，图不会自己跟着变 —— 2026-10-28 实测：图上有 10 条边在代码里
已经不存在（`job_manager` 拆分后留下的旧箭头），代码里有 30 条边图上没有（含 5 个
模块根本没画）。而 `scripts/docs.py check` 只比 SVG 与 `.puml` 是否同源，
**它守的是「图有没有重新渲染」，不是「图对不对」** —— 两份一起过期时它照样通过。

判据只有一条：`{(源, 目标)}` 两个集合必须相等。

    python scripts/check_diagram_edges.py    # 校验；有差异时退出码 1

约定（改图时按这三条办）：

1. 每个模块一个组件，标签写成 `[<相对路径>\\n<一句话>] as <别名>`。
   **路径是判据**，别名只是图上的名字 —— 脚本按路径把图和模块对上，不靠别名猜。
2. `api/routers/*` 是**唯一的聚合节点**：8 个路由模块共用一个组件，因为它们的边
   （`→ api_context` / `api_support` / …）逐条画出来只会把图压塌，而这一层「路由是叶子」
   的事实用一条边就表达完了。
3. 同模块内部的 import 不画（`src == dst`）。

不做的事：不自动改图。差异要么是图的错，要么是代码的错，脚本分不清 —— 交给人判断。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"
DIAGRAM = REPO_ROOT / "docs" / "diagrams" / "module-dependencies.puml"

COMPONENT = re.compile(r"\[([^\]]+?)\\n[^\]]*\] as (\w+)")
EDGE = re.compile(r"^(\w+) --> (\w+)", re.MULTILINE)
AGGREGATE = "api/routers/*"
AGGREGATE_KEY = "api/routers"


def components(text: str) -> dict[str, str]:
    """组件路径 → 别名。路径形如 `download/job_manager.py`（相对 `backend/app`）。"""
    return {match.group(1): match.group(2) for match in COMPONENT.finditer(text)}


def alias_of_module(key: str, component_map: dict[str, str]) -> str | None:
    """模块键（`download/job_manager`）→ 图上别名；`api/routers/*` 的成员都归到聚合节点。"""
    for path, alias in component_map.items():
        if path == AGGREGATE and (key == AGGREGATE_KEY or key.startswith(AGGREGATE_KEY + "/")):
            return alias
        if not path.endswith("/*") and path[:-3] == key:
            return alias
    return None


def imports_of(path: Path) -> set[str]:
    """模块里的包内相对 import，按 `level` 折算成模块键（与 `check_layers.py` 同一套算法）。"""
    relative = path.relative_to(APP_DIR).with_suffix("")
    package = relative.as_posix().split("/")
    if path.name != "__init__.py":
        package = package[:-1]

    targets: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        base = package[: len(package) - (node.level - 1)]
        if node.module:
            targets.add("/".join([*base, *node.module.split(".")]))
        else:
            for alias in node.names:
                targets.add("/".join([*base, alias.name]))
    return targets


def code_edges(component_map: dict[str, str]) -> tuple[set[tuple[str, str]], list[str]]:
    edges: set[tuple[str, str]] = set()
    problems: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        key = path.relative_to(APP_DIR).with_suffix("").as_posix()
        source = alias_of_module(key, component_map)
        if source is None:
            problems.append(f"{key}.py 没有出现在依赖图上（补一个组件，标签里写它的相对路径）")
            continue
        for target_key in imports_of(path):
            target = alias_of_module(target_key, component_map)
            if target is None:
                problems.append(
                    f"{key}.py 里的 `from {target_key.replace('/', '.')}` 指向图上没有的模块"
                )
                continue
            if target != source:
                edges.add((source, target))
    return edges, problems


def main() -> int:
    component_map = components(DIAGRAM.read_text(encoding="utf-8"))
    if not component_map:
        # 组件写法的约定一变（`[x\n…] as Y` 之外的形式），解析会得到空表；
        # 那时「代码里的边全都不在图上了」这种报错会误导人，宁可明确说解析失败。
        print(f"无法从 {DIAGRAM} 解析出任何组件：标签格式可能变了（应为 `[路径\\n说明] as 别名`）。")
        return 1

    expected, problems = code_edges(component_map)
    actual = set(EDGE.findall(DIAGRAM.read_text(encoding="utf-8")))

    for source, target in sorted(expected - actual):
        problems.append(f"图里缺边：{source} --> {target}（代码里有这条 import）")
    for source, target in sorted(actual - expected):
        problems.append(f"图里多边：{source} --> {target}（代码里已经没有这条 import）")

    print(f"图上 {len(component_map)} 个组件、{len(actual)} 条边；代码侧 {len(expected)} 条边。")
    if problems:
        print("")
        for item in problems:
            print(f"  ✗ {item}")
        print("")
        print(f"依赖图校验失败：{len(problems)} 处")
        return 1

    print("依赖图校验通过：边集与 backend/app 的实际 import 一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
