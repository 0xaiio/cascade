"""`bili_bin`：外部可执行文件的探测、命令组装、进度解析与执行。

这一层是整条上传链路上唯一依赖**本机环境**的部分，所以它的用例有两类价值：

1. **把 Windows 特有的坑钉死**：winget 在未开开发者模式时留下 0 字节空壳、而它恰好在
   PATH 里优先级最高。`is_pe` 就是为它写的 —— 少了这一层，`shutil.which` 会成功地
   返回一个 `CreateProcess` 拒绝执行的文件，报错信息（「不是有效的 Win32 应用」）
   与「没装 biliup」完全不同，用户按提示去装一遍也修不好。
2. **把「命令里必须有什么」写进断言**：`--submit web` 少一个词，投稿会以「cookies
   里没有 access_token」失败；`--copyright 1` 少了会让稿件被当成转载。

执行类用例（`run_command`）会真的起一个子进程 —— 它是唯一无法用替身覆盖的接缝，
因为它本身就是替身的实现。代价限制在 1 秒级（超时下限就是 1 秒）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.bili_bin import (
    DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    CommandResult,
    _first_valid,
    build_upload_argv,
    biliup_version,
    detect_broken_stub,
    extract_cover_frame,
    find_biliup,
    is_pe,
    parse_progress,
    repair_broken_stub,
    run_command,
    upload,
)
from app.bili_meta import UploadMetadata
from app.bili_policy import TIMEOUT_MARKER

# ---------------------------------------------------------------- 夹具
# 一份真实 PE 的开头两字节就是 MZ；`is_pe` 只读这两个字节，所以不必造一个完整可执行文件。
PE_BYTES = b"MZ" + b"\x90" * 62


def write_pe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PE_BYTES)
    return path


def write_stub(path: Path) -> Path:
    """winget 留下的那种空壳：文件存在、长度为 0。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def make_meta(**overrides) -> UploadMetadata:
    base = {
        "title": "演示标题",
        "tid": "122",
        "tags": "编程,教程",
        "desc": "一段简介",
    }
    base.update(overrides)
    return UploadMetadata(**base)


@pytest.fixture(autouse=True)
def _no_host_biliup(monkeypatch):
    """**别让「开发机上装没装 biliup」影响断言。**

    `detect_broken_stub` 的第一步是用 `find_biliup` 在本机 PATH / winget 包目录里找一个
    真货。开发机上恰好装了 biliup 时它会返回那个 11 MB 的真二进制，于是「应该用我造的
    替身来修」变成「用本机那个来修」—— 断言跟着开发机走，换台机器就翻车。

    这里改的是 `app.bili_bin` 模块里对 `find_biliup` 的引用（也就是 `detect_broken_stub`
    实际会看到的那一个）；本文件顶层 `from app.bili_bin import find_biliup` 拿到的仍是原函数，
    所以 `test_find_biliup_*` 那几条**不受影响**，它们各自显式打了 `shutil.which` 与包目录的桩。
    """
    monkeypatch.setattr("app.bili_bin.find_biliup", lambda explicit=None: None)


# ---------------------------------------------------------------- is_pe
def test_is_pe_accepts_a_real_header_and_rejects_a_zero_byte_stub(tmp_path):
    assert is_pe(write_pe(tmp_path / "biliup.exe")) is True
    assert is_pe(write_stub(tmp_path / "stub.exe")) is False


def test_is_pe_rejects_a_missing_file_instead_of_raising(tmp_path):
    """探测路径会去摸一堆可能不存在的候选；抛异常等于让整个体检崩掉。"""
    assert is_pe(tmp_path / "nope.exe") is False


def test_is_pe_rejects_a_file_that_only_starts_with_the_letter_m(tmp_path):
    """只读两个字节，所以 `MZ` 必须**两字节都对** —— 一个以 `M` 开头的文本文件不算。"""
    path = tmp_path / "not-a-pe.exe"
    path.write_bytes(b"M, this is text")
    assert is_pe(path) is False


# ---------------------------------------------------------------- 优先级
def test_first_valid_returns_the_first_usable_candidate(tmp_path):
    """顺序即优先级：显式配置 > PATH > 包目录。"""
    first = write_pe(tmp_path / "first.exe")
    second = write_pe(tmp_path / "second.exe")
    assert _first_valid([str(first), str(second)]) == str(first)


def test_first_valid_skips_empty_entries_and_non_pe_files(tmp_path):
    real = write_pe(tmp_path / "real.exe")
    stub = write_stub(tmp_path / "stub.exe")
    assert _first_valid([None, "", str(stub), str(real)]) == str(real)


def test_first_valid_returns_none_when_nothing_is_usable(tmp_path):
    assert _first_valid([None, str(write_stub(tmp_path / "stub.exe"))]) is None


def test_find_biliup_prefers_the_explicit_path(monkeypatch, tmp_path):
    explicit = write_pe(tmp_path / "explicit.exe")
    monkeypatch.setattr("app.bili_bin.shutil.which", lambda name: str(write_pe(tmp_path / "path.exe")))
    assert find_biliup(str(explicit)) == str(explicit)


def test_find_biliup_refuses_an_explicit_path_that_is_a_stub(monkeypatch, tmp_path):
    """显式配置了但指向空壳时返回 `None`，**不是**原样返回 —— 否则调用方会拿着一个
    必然执行失败的文件去投稿，错误发生在几分钟后的子进程里，比在这里说清楚贵得多。"""
    stub = write_stub(tmp_path / "stub.exe")
    monkeypatch.setattr("app.bili_bin.shutil.which", lambda name: None)
    assert find_biliup(str(stub)) is None


def test_find_biliup_falls_back_to_path_then_package_dir(monkeypatch, tmp_path):
    on_path = write_pe(tmp_path / "path" / "biliup.exe")
    monkeypatch.setattr(
        "app.bili_bin.shutil.which",
        lambda name: str(on_path) if name == "biliup" else None,
    )
    monkeypatch.setattr("app.bili_bin.WINGET_PACKAGE_GLOB", str(tmp_path / "empty"))
    assert find_biliup() == str(on_path)


def test_find_biliup_skips_a_stub_on_path_and_falls_through_to_the_package(monkeypatch, tmp_path):
    """**这就是那个坑的回归用例**：PATH 上有空壳时必须继续往下找，而不是返回它。"""
    stub = write_stub(tmp_path / "links" / "biliup.exe")
    in_package = write_pe(tmp_path / "packages" / "ForgQi.biliup-rs_abc" / "biliup.exe")
    monkeypatch.setattr("app.bili_bin.shutil.which", lambda name: str(stub))
    monkeypatch.setattr("app.bili_bin.WINGET_PACKAGE_GLOB", str(tmp_path / "packages"))
    assert find_biliup() == str(in_package)


def test_find_biliup_returns_none_when_nothing_usable_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("app.bili_bin.shutil.which", lambda name: None)
    monkeypatch.setattr("app.bili_bin.WINGET_PACKAGE_GLOB", str(tmp_path / "empty"))
    assert find_biliup() is None


# ---------------------------------------------------------------- 空壳修复
def test_detect_broken_stub_finds_the_stub_and_its_replacement(tmp_path):
    link = write_stub(tmp_path / "links" / "biliup.exe")
    real = write_pe(tmp_path / "packages" / "ForgQi.biliup-rs_x" / "biliup.exe")
    found_link, found_real = detect_broken_stub(None, link_path=link, package_dir=str(tmp_path / "packages"))
    assert found_link == str(link)
    assert found_real == str(real)


def test_detect_broken_stub_ignores_a_healthy_link(tmp_path):
    """判据收得很窄：真正的 PE 就不是空壳，不做任何事。"""
    link = write_pe(tmp_path / "links" / "biliup.exe")
    real = write_pe(tmp_path / "packages" / "ForgQi.biliup-rs_x" / "biliup.exe")
    assert detect_broken_stub(None, link_path=link, package_dir=str(tmp_path / "packages")) == (None, None)


def test_detect_broken_stub_ignores_a_missing_link(tmp_path):
    assert detect_broken_stub(None, link_path=tmp_path / "nope.exe", package_dir=str(tmp_path)) == (None, None)


def test_detect_broken_stub_reports_nothing_when_no_replacement_exists(tmp_path):
    """找不到真货就不要报告「这里坏了」—— 报了也没法修，只会让体检多一条无解的问题。"""
    link = write_stub(tmp_path / "links" / "biliup.exe")
    assert detect_broken_stub(None, link_path=link, package_dir=str(tmp_path / "empty")) == (None, None)


def test_detect_broken_stub_refuses_to_treat_the_link_as_its_own_replacement(tmp_path):
    """`glob` 在「空壳自己就躺在 winget 包目录里」这种布局下会把链接**自己**找出来；
    拿它覆盖它等于没修，必须排除。"""
    stub = write_stub(tmp_path / "ForgQi.biliup-rs_win-x64" / "biliup.exe")
    assert detect_broken_stub(None, link_path=stub, package_dir=str(tmp_path)) == (None, None)


def test_repair_broken_stub_overwrites_and_explains_what_happened(tmp_path):
    link = write_stub(tmp_path / "links" / "biliup.exe")
    write_pe(tmp_path / "packages" / "ForgQi.biliup-rs_x" / "biliup.exe")
    message = repair_broken_stub(None, link_path=link, package_dir=str(tmp_path / "packages"))
    assert message is not None
    assert "已用" in message and "覆盖修复" in message
    # 原位覆盖之后，那个路径上就是一个可用的 PE 了。
    assert is_pe(link) is True
    assert link.stat().st_size == len(PE_BYTES)


def test_repair_broken_stub_is_a_noop_on_a_healthy_install(tmp_path):
    link = write_pe(tmp_path / "links" / "biliup.exe")
    assert repair_broken_stub(None, link_path=link, package_dir=str(tmp_path / "packages")) is None


# ---------------------------------------------------------------- 版本探测
def test_biliup_version_returns_only_the_first_line():
    def runner(argv, timeout, on_line):
        assert argv[1] == "--version"
        return CommandResult(0, "biliup 0.2.4\nextra noise\n", "")

    assert biliup_version("biliup.exe", runner=runner) == "biliup 0.2.4"


def test_biliup_version_falls_back_to_stderr():
    """有些程序把版本写到 stderr；两条流都看一遍比挑一条更省事。"""
    assert biliup_version("b", runner=lambda a, t, c: CommandResult(0, "", "v0.2.4\n")) == "v0.2.4"


def test_biliup_version_returns_none_instead_of_raising():
    """版本查不到不影响投稿，所以这里必须吞掉异常。"""

    def boom(argv, timeout, on_line):
        raise OSError("找不到文件")

    assert biliup_version("b", runner=boom) is None


def test_biliup_version_returns_none_for_empty_output():
    assert biliup_version("b", runner=lambda a, t, c: CommandResult(0, "", "")) is None


# ---------------------------------------------------------------- 命令组装
def test_build_upload_argv_always_asks_for_web_submit(tmp_path):
    """**本模块最贵的一条**：`--submit web` 缺席时，cookie 文件登录拿不到 access_token，
    投稿会在几分钟后以一句与「提交方式」毫不相干的报错失败。"""
    argv = build_upload_argv("biliup.exe", "cookies.json", "v.mp4", make_meta())
    assert "--submit" in argv
    assert argv[argv.index("--submit") + 1] == "web"


def test_build_upload_argv_declares_original_content(tmp_path):
    """`--copyright 1` = 自制。少了它，稿件会被当成转载。"""
    argv = build_upload_argv("biliup.exe", "c.json", "v.mp4", make_meta())
    assert argv[argv.index("--copyright") + 1] == "1"


def test_build_upload_argv_carries_every_field_from_the_metadata():
    meta = make_meta(title="标题甲", tid="122", tags="a,b", desc="简介乙")
    argv = build_upload_argv("b", "c.json", "v.mp4", meta)
    for flag, value in (("--title", "标题甲"), ("--tid", "122"), ("--tag", "a,b"), ("--desc", "简介乙")):
        assert argv[argv.index(flag) + 1] == value


def test_build_upload_argv_omits_the_cover_when_the_file_is_absent(tmp_path):
    """封面是可选项：路径给了但文件不在时**不能**把参数带上去，
    否则 biliup 会因为一个不存在的文件而整条投稿失败。"""
    argv = build_upload_argv("b", "c.json", "v.mp4", make_meta(), cover_path=tmp_path / "missing.jpg")
    assert "--cover" not in argv


def test_build_upload_argv_includes_the_cover_when_the_file_exists(tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff")
    argv = build_upload_argv("b", "c.json", "v.mp4", make_meta(), cover_path=cover)
    assert argv[argv.index("--cover") + 1] == str(cover)


def test_build_upload_argv_keeps_the_cookie_and_video_positional_shape():
    """`-u <cookie> upload <video>` 这个形状是 biliup 的固定接口，顺序不能动。"""
    argv = build_upload_argv("biliup.exe", "c.json", "v.mp4", make_meta())
    assert argv[:2] == ["biliup.exe", "-u"]
    assert argv[3] == "upload"
    assert argv[4] == "v.mp4"


def test_upload_passes_the_argv_to_the_runner_and_returns_its_result(tmp_path):
    seen: list[list[str]] = []

    def runner(argv, timeout, on_line):
        seen.append(list(argv))
        return CommandResult(0, "APP接口投稿成功", "")

    result = upload("b", "c.json", "v.mp4", make_meta(), runner=runner)
    assert result.ok is True
    assert seen[0][seen[0].index("--submit") + 1] == "web"


def test_upload_default_timeout_is_generous_enough_for_a_real_upload():
    """一次投稿可能几分钟；超时只是兜住「卡死不返回」，不该误杀正常上传。"""
    assert DEFAULT_UPLOAD_TIMEOUT_SECONDS >= 600


# ---------------------------------------------------------------- 进度解析
def test_parse_progress_is_monotonic_and_never_goes_backwards():
    """biliup 的输出不是结构化的，会出现重复行、乱序行。**进度只能前进**，
    否则界面上的进度条会来回跳，看起来像卡住了。"""
    current = 0.0
    for line in ("start pre_upload", "Upload completed", "APP接口投稿成功", "start pre_upload", '"code": 0'):
        new = parse_progress(line, current)
        assert new >= current
        current = new
    assert current == pytest.approx(0.95)


def test_parse_progress_ignores_lines_it_does_not_recognise():
    assert parse_progress("随便一行日志", 0.4) == pytest.approx(0.4)


def test_parse_progress_never_exceeds_one():
    assert parse_progress("APP接口投稿成功", 0.99) <= 1.0


# ---------------------------------------------------------------- 执行
def test_run_command_streams_lines_to_the_callback():
    """逐行回调是「上传中还活着」的唯一来源，必须有内容。"""
    seen: list[str] = []
    script = "print('line one'); print('line two')"
    result = run_command([sys.executable, "-c", script], 30.0, seen.append)
    assert result.ok is True
    assert seen == ["line one", "line two"]


def test_run_command_merges_stderr_into_stdout():
    """biliup 把进度写 stdout、错误写 stderr，**顺序有意义** —— 合并成一条流才不会错位。"""
    script = "import sys; print('out'); sys.stderr.write('err\\n')"
    result = run_command([sys.executable, "-c", script], 30.0, None)
    assert "out" in result.stdout
    assert "err" in result.stdout
    assert result.stderr == ""


def test_run_command_records_a_timeout_marker_and_kills_the_process():
    """超时必须留下**我们自己**给的可识别标记：`classify_failure` 靠它把本地超时
    排在所有上游字样之前。没有标记时，一句同时含 `read timeout` 的日志会被误判成网络问题。"""
    script = "import time; time.sleep(30)"
    result = run_command([sys.executable, "-c", script], 1.0, None)
    assert TIMEOUT_MARKER in result.stderr
    assert "1s" in result.stderr


def test_run_command_survives_a_callback_that_raises():
    """回调是进度上报，它坏掉不该把上传本身弄失败。"""
    calls: list[str] = []

    def bad_sink(line: str) -> None:
        calls.append(line)
        raise ValueError("callback is broken")

    result = run_command([sys.executable, "-c", "print('still fine')"], 30.0, bad_sink)
    assert result.ok is True
    assert calls == ["still fine"]


# ---------------------------------------------------------------- 抽封面
def test_extract_cover_frame_returns_none_when_ffmpeg_fails(tmp_path):
    """封面是可选项：抽不出来就放弃封面，**不能让整次投稿失败**。"""

    def runner(argv, timeout, on_line):
        return CommandResult(1, "", "ffmpeg: No such file")

    assert extract_cover_frame("ffmpeg", tmp_path / "v.mp4", tmp_path / "cover.jpg", runner=runner) is None


def test_extract_cover_frame_returns_none_when_the_runner_raises(tmp_path):
    """连 ffmpeg 都起不来时（`FileNotFoundError`）同样降级。"""

    def boom(argv, timeout, on_line):
        raise FileNotFoundError("ffmpeg")

    assert extract_cover_frame("ffmpeg", tmp_path / "v.mp4", tmp_path / "cover.jpg", runner=boom) is None


def test_extract_cover_frame_returns_none_for_a_zero_byte_output(tmp_path):
    """ffmpeg 偶尔会「成功」地写出一个 0 字节文件。把它当封面等于投一张打不开的图。"""

    def runner(argv, timeout, on_line):
        Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[-1]).write_bytes(b"")
        return CommandResult(0, "", "")

    assert extract_cover_frame("ffmpeg", tmp_path / "v.mp4", tmp_path / "cover.jpg", runner=runner) is None


def test_extract_cover_frame_returns_the_path_of_a_real_frame(tmp_path):
    out = tmp_path / "frames" / "cover.jpg"

    def runner(argv, timeout, on_line):
        target = Path(argv[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
        return CommandResult(0, "", "")

    assert extract_cover_frame("ffmpeg", tmp_path / "v.mp4", out, runner=runner) == out


def test_extract_cover_frame_seeks_past_the_first_frame(tmp_path):
    """抽第 1 秒而不是第 0 秒：第 0 帧常常是黑场或片头卡，当封面等于没有封面。"""
    seen: list[list[str]] = []

    def runner(argv, timeout, on_line):
        seen.append(list(argv))
        return CommandResult(0, "", "")

    extract_cover_frame("ffmpeg", tmp_path / "v.mp4", tmp_path / "cover.jpg", at_seconds=1.5, runner=runner)
    argv = seen[0]
    assert argv[argv.index("-ss") + 1] == "1.5"
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert argv[0] == "ffmpeg"
    # `-y` 必须有：默认会交互式询问是否覆盖，无头环境下等于卡死。
    assert "-y" in argv
