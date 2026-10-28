"""`scripts/check_layers.py` 自己的用例。

它是**依赖方向的唯一闸门**（矩阵 → 层号 → 只准朝下），而 2026-10-28 的目录重构
（`ai/refactor/011`）重写过它一次：模块键改成从链接目标取、相对导入按 `level` 折算、
层号回退到最近的已登记祖先。闸门被放宽时不会有人发现 —— 校验脚本说「通过」的样子
和真的通过一模一样。所以这里钉住三件事：解析、回退、以及**诊断说的是哪一类问题**。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load_check_layers():
    spec = importlib.util.spec_from_file_location("check_layers_under_test", SCRIPTS / "check_layers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_layers = _load_check_layers()


# --------------------------------------------------------------------------
# 矩阵解析
# --------------------------------------------------------------------------


def test_layers_come_from_the_cell_that_starts_with_the_layer() -> None:
    text = "| [job_manager.py](../backend/app/download/job_manager.py#L43) | L2 编排 | 队列。 | 不做降级。 |"

    assert check_layers.parse_layers(text) == {"download/job_manager": 2}


def test_a_directory_table_row_is_not_a_layer_registration() -> None:
    """目录说明表的第三格是职责文字。若放宽成「格子里出现 L\\d」，这类行会被当成层登记，
    未登记的模块就会静默继承一个层 —— 这正是加「格子开头」这条判据的原因。"""
    text = (
        "| [common/](../backend/app/common/__init__.py) | 纯工具：`paths`、`log_safety`。 | 无项目内依赖 |\n"
        "| [download/](../backend/app/download/__init__.py) | 下载。 | 见 L3 领域 |\n"
    )

    assert check_layers.parse_layers(text) == {}


def test_a_row_with_several_links_registers_every_module() -> None:
    text = (
        "| [paths.py](../backend/app/common/paths.py#L7) / [log_safety.py](../backend/app/common/log_safety.py#L20)"
        " | L5 纯工具 | 文件名安全化。 | 不读设置。 |"
    )

    assert check_layers.parse_layers(text) == {"common/paths": 5, "common/log_safety": 5}


def test_a_package_link_registers_the_package_key() -> None:
    text = "| [routers/](../backend/app/api/routers/__init__.py#L21) | L0 入口 | 路由。 | 不含业务。 |"

    assert check_layers.parse_layers(text) == {"api/routers": 0}


def test_links_outside_backend_app_are_ignored() -> None:
    """矩阵里还有指向 `frontend/src` 的行，它们不是后端模块。"""
    text = "| [App.tsx](../frontend/src/App.tsx) | 编排根 | 装配。 | 不含业务。 |"

    assert check_layers.parse_layers(text) == {}


# --------------------------------------------------------------------------
# 层号回退
# --------------------------------------------------------------------------


def test_an_unregistered_module_inherits_its_nearest_registered_ancestor() -> None:
    """`api/routers/jobs` 没单独登记，要能落到 `api/routers` 那一行；只取第一段会落空。"""
    layers = {"api/routers": 0}

    assert check_layers.layer_of("api/routers/jobs", layers) == 0


def test_a_module_outside_every_registered_package_has_no_layer() -> None:
    assert check_layers.layer_of("download/new_thing", {"download/job_manager": 2}) is None


# --------------------------------------------------------------------------
# 相对导入的解析
# --------------------------------------------------------------------------


def test_a_submodule_import_is_attributed_to_the_submodule(monkeypatch, tmp_path: Path) -> None:
    """`from ..api import api_support` 记成 `api/api_support`。

    记成 `api` 的话，`api` 从未在矩阵登记，于是「依赖方向朝上」会被报成
    「指向未登记的模块」—— 那是叫人去改文档，而问题在代码。
    """
    monkeypatch.setattr(check_layers, "APP_DIR", tmp_path)
    module = tmp_path / "download" / "job.py"
    module.parent.mkdir()
    module.write_text("from ..api import api_support\n", encoding="utf-8")

    assert check_layers.imports_of(module, {"api/api_support"}) == {"api/api_support"}


def test_a_symbol_import_stays_at_the_module_that_holds_it(monkeypatch, tmp_path: Path) -> None:
    """`from ..contracts import config` 里的 `config` 是模块；`from ..api_support import helper`
    里的 `helper` 是符号。后者不该被当成模块 `api/api_support/helper`。"""
    monkeypatch.setattr(check_layers, "APP_DIR", tmp_path)
    module = tmp_path / "download" / "job.py"
    module.parent.mkdir()
    module.write_text("from ..api.api_support import helper\n", encoding="utf-8")

    assert check_layers.imports_of(module, {"api/api_support"}) == {"api/api_support"}


def test_without_the_known_set_the_parent_module_is_reported(monkeypatch, tmp_path: Path) -> None:
    """没有 `known` 时无法分辨子模块与符号，只能保守地记成父模块。"""
    monkeypatch.setattr(check_layers, "APP_DIR", tmp_path)
    module = tmp_path / "download" / "job.py"
    module.parent.mkdir()
    module.write_text("from ..api import api_support\n", encoding="utf-8")

    assert check_layers.imports_of(module) == {"api"}


def test_three_levels_up_resolves_to_the_package_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(check_layers, "APP_DIR", tmp_path)
    module = tmp_path / "download" / "sub" / "deep.py"
    module.parent.mkdir(parents=True)
    module.write_text("from ...common import paths\n", encoding="utf-8")

    assert check_layers.imports_of(module, {"common/paths"}) == {"common/paths"}


def test_absolute_imports_are_not_checked(monkeypatch, tmp_path: Path) -> None:
    """包内一律写相对导入；绝对导入绕开这条校验，是刻意留的边界（测试自己就这么写）。"""
    monkeypatch.setattr(check_layers, "APP_DIR", tmp_path)
    module = tmp_path / "download" / "job.py"
    module.parent.mkdir()
    module.write_text("from app.api import api_support\n", encoding="utf-8")

    assert check_layers.imports_of(module, set()) == set()


# --------------------------------------------------------------------------
# 整条闸门
# --------------------------------------------------------------------------


def test_the_repository_itself_passes() -> None:
    """当前仓库必须过闸 —— 与 CI 跑的是同一条命令。"""
    assert check_layers.main([]) == 0


def _gates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sources: dict[str, str], matrix: str) -> None:
    app = tmp_path / "app"
    for relative, text in sources.items():
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    app.mkdir(exist_ok=True)
    doc = tmp_path / "design.md"
    doc.write_text(matrix, encoding="utf-8")
    monkeypatch.setattr(check_layers, "APP_DIR", app)
    monkeypatch.setattr(check_layers, "DESIGN_DOC", doc)


def test_an_unregistered_module_makes_the_gate_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`common/` 这个包本身没有登记（矩阵里只有它下面的两个文件），所以新模块会被抓住。"""
    _gates(
        monkeypatch,
        tmp_path,
        {"common/paths.py": "X = 1\n", "common/brand_new.py": "VALUE = 1\n"},
        "| [paths.py](../backend/app/common/paths.py#L7) | L5 纯工具 | 文件名安全化。 | 不读设置。 |\n",
    )

    assert check_layers.main([]) == 1
    assert "common/brand_new.py 没有登记" in capsys.readouterr().out


def test_a_reverse_dependency_is_reported_as_a_direction_problem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """这条钉的是**诊断的类别**：L3 依赖 L0 是「方向朝上」，不是「模块没登记」。

    修复前 `from ..api import support` 被记成模块键 `api`（丢掉了 `support`），
    于是报的是「指向未登记的模块」—— 照着它去补文档，反向依赖就被永久掩盖了。
    """
    _gates(
        monkeypatch,
        tmp_path,
        {"api/support.py": "X = 1\n", "download/job.py": "from ..api import support\n"},
        "| [support.py](../backend/app/api/support.py#L1) | L0 入口 | 支撑。 | 不含业务。 |\n"
        "| [job.py](../backend/app/download/job.py#L1) | L3 领域 | 编排。 | 不写库。 |\n",
    )

    assert check_layers.main([]) == 1
    out = capsys.readouterr().out
    assert "依赖方向朝上" in out
    assert "未登记的模块" not in out

