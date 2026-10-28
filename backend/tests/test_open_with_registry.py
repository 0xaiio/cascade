"""自带「打开方式」对话框的候选来源：注册表 → 可执行文件路径。

这一层此前**没有任何直接用例**：`test_system_open.py` 里那条「清单 = 系统登记 + 本机编辑器」
把 `_registered_handlers` 整个换成了假的，于是被测函数从没被执行过 —— 而它内部有两个
来源恒定为空（`name.isdigit()` 判断反了），界面上表现为「清单里只有编辑器，没有你平时
用来打开这类文件的那个程序」。见 `ai/bug-fix/029`。

测试不去碰真实注册表：`winreg` 在函数内 `import`，所以替换 `sys.modules["winreg"]` 就够了，
这也让「键名是 `a`/`b`/`MRUList`」这种**实测形态**能被固定下来，而不是靠本机装了什么。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.host import system_open


class _FakeKey:
    def __init__(self, registry: FakeRegistry, root: str, sub_key: str) -> None:
        self.registry = registry
        self.root = root
        self.sub_key = sub_key

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class FakeRegistry:
    """`winreg` 的替身：只实现本模块用到的四个入口。"""

    HKEY_CLASSES_ROOT = "HKEY_CLASSES_ROOT"
    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    HKEY_LOCAL_MACHINE = "HKEY_LOCAL_MACHINE"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], list[tuple[str, object, int]]] = {}
        self.defaults: dict[tuple[str, str], str] = {}
        self.subkeys: dict[tuple[str, str], list[str]] = {}

    # --- 造数据 -----------------------------------------------------------
    def set_values(self, root: str, sub_key: str, rows: list[tuple[str, object, int]]) -> None:
        self.values[(root, sub_key)] = rows

    def set_default(self, root: str, sub_key: str, value: str) -> None:
        self.defaults[(root, sub_key)] = value

    def set_subkeys(self, root: str, sub_key: str, names: list[str]) -> None:
        self.subkeys[(root, sub_key)] = names

    # --- winreg 接口 ------------------------------------------------------
    def OpenKey(self, root: str | _FakeKey, sub_key: str) -> _FakeKey:  # noqa: N802 - 对齐 winreg
        """`root` 既可能是根键常量，也可能是**已打开的键**（`App Paths` 那段就是这么用的）。"""
        if isinstance(root, _FakeKey):
            root_name, full = root.root, root.sub_key + "\\" + sub_key
        else:
            root_name, full = root, sub_key
        key = (root_name, full)
        if key in self.values or key in self.defaults or key in self.subkeys:
            return _FakeKey(self, root_name, full)
        raise OSError(2, "系统找不到指定的文件。")

    def EnumValue(self, key: _FakeKey, index: int) -> tuple[str, object, int]:  # noqa: N802
        rows = self.values.get((key.root, key.sub_key), [])
        if index >= len(rows):
            raise OSError(259, "没有更多数据。")
        return rows[index]

    def EnumKey(self, key: _FakeKey, index: int) -> str:  # noqa: N802
        names = self.subkeys.get((key.root, key.sub_key), [])
        if index >= len(names):
            raise OSError(259, "没有更多数据。")
        return names[index]

    def QueryValue(self, key: _FakeKey, _name: str | None) -> str:  # noqa: N802
        return self.defaults.get((key.root, key.sub_key), "")


FILEEXTS_MD = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.md\OpenWithList"
HKCR_MD = r".md\OpenWithList"
HKCR_MD_PROGIDS = r".md\OpenWithProgids"
APP_PATHS_HKLM = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    fake = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(system_open.os, "name", "nt")
    return fake


@pytest.fixture
def exe(tmp_path: Path):
    def make(name: str) -> Path:
        target = tmp_path / name
        target.write_text("MZ", encoding="utf-8")
        return target

    return make


# --------------------------------------------------------------------------
# OpenWithList：键名是 a/b/c，不是数字
# --------------------------------------------------------------------------


def test_open_with_list_entries_are_read_and_the_mru_record_is_skipped(
    registry: FakeRegistry, exe
) -> None:
    """`a` 是程序项、`MRUList` 只是它们的顺序 —— 两者都不能当成「程序」。

    修复前这里返回 `[]`：过滤条件写的是 `name.isdigit()`，而真实键名一个都不是数字。
    """
    code = exe("Code.exe")
    registry.set_values(
        registry.HKEY_CURRENT_USER,
        FILEEXTS_MD,
        [("a", str(code), 1), ("MRUList", "a", 1)],
    )

    assert system_open._registered_handlers(".md") == [("Code", str(code))]


def test_the_hkcr_open_with_list_is_read_too(registry: FakeRegistry, exe) -> None:
    r"""`HKCR\<ext>\OpenWithList` 与 HKCU 那两个键同形，同样会丢条目。"""
    notepad = exe("notepad.exe")
    registry.set_values(registry.HKEY_CLASSES_ROOT, HKCR_MD, [("a", str(notepad), 1), ("b", str(exe("Other.exe")), 1)])

    assert [name for name, _ in system_open._registered_handlers(".md")] == ["notepad", "Other"]


def test_a_bare_exe_name_is_resolved_through_path(registry: FakeRegistry, monkeypatch, tmp_path: Path) -> None:
    """实测取值常是裸 exe 名（`Everything.exe`）——`Path(...).is_file()` 对它是假。"""
    tools = tmp_path / "tools"
    tools.mkdir()
    everything = tools / "Everything.exe"
    everything.write_text("MZ", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tools))
    registry.set_values(registry.HKEY_CURRENT_USER, FILEEXTS_MD, [("a", "Everything.exe", 1)])

    assert system_open._registered_handlers(".md") == [("Everything", str(everything))]


def test_a_bare_exe_name_falls_back_to_app_paths(registry: FakeRegistry, monkeypatch, exe) -> None:
    """`PATH` 里没有时还有第二来源：`App Paths` 的默认值。"""
    qq = exe("QQ.exe")
    monkeypatch.setattr(system_open.shutil, "which", lambda _name: None)
    registry.set_default(registry.HKEY_LOCAL_MACHINE, rf"{APP_PATHS_HKLM}\QQ.exe", f'"{qq}"')
    registry.set_values(registry.HKEY_CURRENT_USER, FILEEXTS_MD, [("a", "QQ.exe", 1)])

    assert system_open._registered_handlers(".md") == [("QQ", str(qq))]


def test_an_appx_package_identifier_is_dropped(registry: FakeRegistry) -> None:
    """`…!App` 是包标识，不能按路径启动 —— 列进清单只会让用户点一个打不开的条目。"""
    registry.set_values(
        registry.HKEY_CURRENT_USER,
        FILEEXTS_MD,
        [("a", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", 1)],
    )

    assert system_open._registered_handlers(".md") == []


def test_an_uninstalled_program_is_dropped(registry: FakeRegistry) -> None:
    registry.set_values(registry.HKEY_CURRENT_USER, FILEEXTS_MD, [("a", r"C:\Gone\Nope.exe", 1)])

    assert system_open._registered_handlers(".md") == []


def test_a_missing_sub_key_is_not_an_error(registry: FakeRegistry) -> None:
    """三个来源都可能不存在（本机 `.srt` 就没有 OpenWithProgids）。"""
    assert system_open._registered_handlers(".srt") == []


# --------------------------------------------------------------------------
# OpenWithProgids：显示名与命令解析
# --------------------------------------------------------------------------


def test_a_progid_uses_its_registered_display_name(registry: FakeRegistry, exe) -> None:
    code = exe("Code.exe")
    registry.set_values(registry.HKEY_CLASSES_ROOT, HKCR_MD_PROGIDS, [("VSCode.md", b"", 0)])
    registry.set_default(registry.HKEY_CLASSES_ROOT, "VSCode.md", "Markdown 源文件")
    registry.set_default(registry.HKEY_CLASSES_ROOT, r"VSCode.md\shell\open\command", f'"{code}" "%1"')

    assert system_open._registered_handlers(".md") == [("Markdown 源文件", str(code))]


def test_a_progid_without_a_display_name_falls_back_to_the_exe_name(registry: FakeRegistry, exe) -> None:
    """`Toolbox.Air.b9908000-346e-…` 这种没登记显示名，印出来就是一串 GUID。"""
    air = exe("Air.exe")
    progid = "Toolbox.Air.b9908000-346e-4c93-ac4a-3f3799308eae"
    registry.set_values(registry.HKEY_CLASSES_ROOT, HKCR_MD_PROGIDS, [(progid, b"", 0)])
    registry.set_default(registry.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command", f'"{air}" "%1"')

    assert system_open._registered_handlers(".md") == [("Air", str(air))]


def test_a_progid_whose_command_is_empty_is_dropped(registry: FakeRegistry) -> None:
    """本机 `.mp4` 的三个 AppX ProgID 命令都是空串 —— 以前会产出 `(progid, '')` 这种垃圾行。"""
    progid = "AppXcezf6bjsrpbyaqwyjdehhb46y5e5mm3a"
    registry.set_values(registry.HKEY_CLASSES_ROOT, r".mp4\OpenWithProgids", [(progid, b"", 0)])
    registry.set_default(registry.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command", "")

    assert system_open._registered_handlers(".mp4") == []


def test_an_extension_without_a_dot_is_accepted(registry: FakeRegistry, exe) -> None:
    notepad = exe("notepad.exe")
    registry.set_values(registry.HKEY_CLASSES_ROOT, HKCR_MD, [("a", str(notepad), 1)])

    assert system_open._registered_handlers("md") == [("notepad", str(notepad))]


# --------------------------------------------------------------------------
# 取值解析与「标识 vs 名字」
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_suffix"),
    [
        ('"C:\\Program Files\\App\\app.exe"', "app.exe"),
        ("C:\\Program Files\\App\\app.exe", "app.exe"),
        ("  C:\\Program Files\\App\\app.exe  ", "app.exe"),
    ],
)
def test_resolve_executable_accepts_a_path_with_or_without_quotes(
    tmp_path: Path, value: str, expected_suffix: str
) -> None:
    """真实的路径要存在才被认；这里用临时目录拼一个「带空格 + 带引号」的形态。"""
    spaced = tmp_path / "Program Files" / "App"
    spaced.mkdir(parents=True)
    target = spaced / "app.exe"
    target.write_text("MZ", encoding="utf-8")

    raw = value.replace("C:\\Program Files\\App\\app.exe", str(target))

    assert system_open._resolve_executable(raw) == target


@pytest.mark.parametrize("value", ["", "   ", '"   "', "C:\\Gone\\app.exe", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"])
def test_resolve_executable_rejects_what_cannot_be_launched(value: str) -> None:
    assert system_open._resolve_executable(value) is None


@pytest.mark.parametrize(
    ("text", "machine_id"),
    [
        ("AppX4ztfk9wxr86nxmzzq47px0nh0e58b8fw", True),
        ("Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", True),
        ("Toolbox.Air.b9908000-346e-4c93-ac4a-3f3799308eae", True),
        ("Markdown 源文件", False),
        ("记事本", False),
        ("Visual Studio Code", False),
        ("", False),
    ],
)
def test_machine_ids_are_not_shown_as_names(text: str, machine_id: bool) -> None:
    assert system_open._looks_like_machine_id(text) is machine_id


# --------------------------------------------------------------------------
# 候选清单的合并与去重
# --------------------------------------------------------------------------


def test_program_candidates_replace_a_machine_id_label_with_the_exe_name(
    registry: FakeRegistry, monkeypatch, exe
) -> None:
    air = exe("Air.exe")
    registry.set_values(registry.HKEY_CLASSES_ROOT, HKCR_MD_PROGIDS, [("AppXabcd", b"", 0)])
    registry.set_default(registry.HKEY_CLASSES_ROOT, r"AppXabcd\shell\open\command", f'"{air}" "%1"')
    monkeypatch.setattr(system_open, "_editor_candidates", lambda: [])

    assert [item.name for item in system_open._program_candidates(".md")] == ["Air"]


def test_program_candidates_keep_the_first_of_two_identical_paths(registry: FakeRegistry, monkeypatch, exe) -> None:
    code = exe("Code.exe")
    registry.set_values(registry.HKEY_CURRENT_USER, FILEEXTS_MD, [("a", str(code), 1)])
    # 同一个程序在编辑器清单里又出现一次（名字不同、路径相同）。
    monkeypatch.setattr(system_open, "_editor_candidates", lambda: [("Visual Studio Code", str(code))])

    assert [item.name for item in system_open._program_candidates(".md")] == ["Code"]


def test_program_candidates_collapse_the_same_name_twice(registry: FakeRegistry, monkeypatch, tmp_path: Path) -> None:
    """记事本同时命中 System32 与 WindowsApps 的别名时只留一个。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    first = a / "notepad.exe"
    second = b / "notepad.exe"
    for target in (first, second):
        target.write_text("MZ", encoding="utf-8")
    registry.set_values(registry.HKEY_CURRENT_USER, FILEEXTS_MD, [("a", str(first), 1), ("b", str(second), 1)])
    monkeypatch.setattr(system_open, "_editor_candidates", lambda: [])

    assert [str(item.executable) for item in system_open._program_candidates(".md")] == [str(first)]


# --------------------------------------------------------------------------
# 编辑器来源
# --------------------------------------------------------------------------


def test_editor_candidates_use_the_configured_locations_and_app_paths(
    registry: FakeRegistry, monkeypatch, exe
) -> None:
    clion = exe("clion64.exe")
    code = exe("Code.exe")
    monkeypatch.setattr(system_open, "_EDITOR_LOCATIONS", (("CLion", (str(clion),)),))
    registry.set_subkeys(registry.HKEY_LOCAL_MACHINE, APP_PATHS_HKLM, ["code.exe", "unrelated.exe"])
    registry.set_default(registry.HKEY_LOCAL_MACHINE, rf"{APP_PATHS_HKLM}\code.exe", f'"{code}"')

    assert system_open._editor_candidates() == [("CLion", str(clion)), ("Visual Studio Code", str(code))]


def test_editor_candidates_ignore_patterns_that_match_nothing(registry: FakeRegistry, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(system_open, "_EDITOR_LOCATIONS", (("Ghost", (str(tmp_path / "ghost.exe"),)),))

    assert system_open._editor_candidates() == []


def test_expand_pattern_resolves_variables_and_wildcards(monkeypatch, tmp_path: Path) -> None:
    deep = tmp_path / "Vim" / "vim90"
    deep.mkdir(parents=True)
    target = deep / "gvim.exe"
    target.write_text("MZ", encoding="utf-8")
    monkeypatch.setenv("YTDL_TEST_ROOT", str(tmp_path))

    assert system_open._expand_pattern(r"%YTDL_TEST_ROOT%\Vim\vim*\gvim.exe") == [target]
    assert system_open._expand_pattern(r"%YTDL_TEST_ROOT%\Vim\vim98\gvim.exe") == []
    assert system_open._expand_pattern(str(target)) == [target]


def test_program_files_dir_is_none_when_no_variable_points_at_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(name, raising=False)

    assert system_open._program_files_dir() is None
