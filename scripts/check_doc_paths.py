"""校验 `docs/` 与 `ai/` 里提到的代码路径在磁盘上真的存在。

为什么需要它：文档里的路径是**手写**的，而代码树会搬家。2026-10-02 把 1521 行的
`frontend/src/App.test.tsx` 拆成 11 个文件之后，全仓留下 13 处指向它的引用：
`ai/ui/001` 说「新增断言在 `App.test.tsx`」、`ai/bug-fix/006` 的验证表把 2 个用例记在
那个文件名下。读者按它去找，只会落到空处。

现有两个检查都看不见这种写法：

- `scripts/check_doc_anchors.py` 只看 `[标签](../x.py#Lnn)` —— **带行锚的链接**；
- `scripts/docs.py check` 只看 `[文字](路径)` 是否断链 —— **链接**。

而 `ai/` 记录里最常用的是「行内代码里的一个裸路径」，两者都不管。2026-10-28 实测：
这类悬空引用 18 处，涉及 4 个路径。

判据：`docs/**/*.md` + `ai/**/*.md` + `README.md` 里出现的
`frontend/src/…`、`backend/app/…`、`backend/tests/…`、`scripts/…` 路径，必须存在于磁盘。

例外分三类，登记在 `ALLOWED` 里，每条写明理由：

1. **被删除 / 被移动的那个对象**。写「删除 `frontend/src/App.test.tsx`」时，它按定义不存在。
2. **当时的命令与基线**。`git show HEAD:…` 是历史命令，改了就成了假的。
3. **before → after 的对照**。左边那个路径要陈述的正是「它已经不在了」。

不做的事：**不自动改文档**。一处悬空要么该改成现址、要么该登记成历史，脚本分不清 ——
判据是「读这句话的人要不要按它去找东西」：导航指称加现址，事件记录保留原文
（`ai/refactor/008-split-app-tests.md` §7 是这条政策的单一来源）。

    python scripts/check_doc_paths.py    # 校验；有悬空引用时退出码 1
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("docs", "ai")
EXTRA_FILES = ("README.md",)
# 会搬家的代码树。`docs/` 自己的 `.md` 不在其中：那些由 `scripts/docs.py check` 管。
CODE_ROOTS = ("frontend/src", "backend/app", "backend/tests", "scripts")
PATH = re.compile(
    r"(?<![\w/.-])(?:" + "|".join(re.escape(root) for root in CODE_ROOTS) + r")/[A-Za-z0-9_./-]+\.(?:tsx?|py)"
)
TRAILING = ".,;:)`'\"、；）"

# 确属历史指称、允许悬空的路径。**键是路径，值是理由** —— 没写理由的条目不算数。
ALLOWED = {
    "frontend/src/App.test.tsx": (
        "2026-10-02 拆成 11 个文件（ai/refactor/008）。现存的提及要么同行给了现址，"
        "要么是删除清单、当时的命令、当时的基线"
    ),
    "backend/app/config.py": "011 移动前的路径，同一节随即给出 `contracts/config.py`",
    "backend/app/download/_probe.py": "011 的注入取证用临时模块，从未提交",
    "frontend/src/types.ts": "013 的 before → after 对照（→ `contracts/types.ts`）",
}


def markdown_files() -> list[Path]:
    files = [path for name in SCAN_DIRS for path in sorted((REPO_ROOT / name).rglob("*.md"))]
    files.extend(REPO_ROOT / name for name in EXTRA_FILES)
    return [path for path in files if path.is_file()]


def main() -> int:
    offenders: list[str] = []
    allowed_hits: dict[str, int] = {path: 0 for path in ALLOWED}
    total = 0

    for path in markdown_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in PATH.finditer(line):
                total += 1
                target = match.group(0).rstrip(TRAILING)
                if (REPO_ROOT / target).is_file():
                    continue
                if target in ALLOWED:
                    allowed_hits[target] += 1
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} → {target}")

    print(f"扫了 {len(markdown_files())} 份文档，{total} 处代码路径引用。")
    if offenders:
        print("")
        for item in offenders:
            print(f"  ✗ {item}")
        print("")
        print(
            f"文档路径校验失败：{len(offenders)} 处悬空。改到现址，"
            f"或在 scripts/check_doc_paths.py 的 ALLOWED 里登记理由（它确实是历史指称吗？）。"
        )
        return 1

    # 白名单条目失效不会让文档出错，但会让这份名单慢慢变成噪音（列着一堆早就不存在的例外）。
    stale = [path for path, hits in allowed_hits.items() if hits == 0]
    print(
        "历史指称（已登记）："
        + "、".join(f"`{path}`×{hits}" for path, hits in allowed_hits.items() if hits)
    )
    if stale:
        print("")
        for path in stale:
            print(f"  ! 白名单里的 `{path}` 已经没有任何引用，可以从 ALLOWED 里删掉。")
    print("文档路径校验通过：没有未登记的悬空引用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
