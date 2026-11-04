"""单实例锁（[ai/plan/brainstorming.md](../ai/plan/brainstorming.md) D-05）。

它保护的是**唯一会损坏数据**的那条路径：两个进程同时恢复同一批孤儿条目、
同时往同一个 `.part` 写。界面上看不到任何报错，坏的是产物。

四条判据：
1. 同一个 `data/` 第二个实例取不到锁，且异常里带着持有者的 pid；
2. 释放之后能再取（不是一次性的）；
3. **残留的锁文件本身不阻挡** —— 这条区分「文件锁」与「存在即占用」；
4. 两个不同的 `data/` 各取各的（用户开两份是合法用法）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.host.instance_lock import LOCK_FILE_NAME, InstanceLock, InstanceLockError, read_lock_note, read_lock_note


@pytest.fixture(autouse=True)
def _fresh_settings():
    """`get_settings()` 是 `lru_cache` 的：不清缓存，后一条用例会读到前一条的 `data_dir`。

    这不是测试的洁癖 —— 缓存是全局的，而本文件每条用例都用 `YTDL_DATA_DIR` 指到自己的临时目录。
    """
    from app.contracts.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_second_instance_on_the_same_data_dir_is_refused(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path)
    first.acquire(note="pid=4242 port=8010")
    try:
        second = InstanceLock(tmp_path)
        with pytest.raises(InstanceLockError) as excinfo:
            second.acquire()
        # 消息要能让下一个人照着做：谁的锁、在哪。
        assert str(tmp_path / LOCK_FILE_NAME) in str(excinfo.value)
        assert "pid=4242 port=8010" in str(excinfo.value)
        assert second.held is False
    finally:
        first.release()


def test_the_lock_file_is_never_empty(tmp_path: Path) -> None:
    """取锁之前必须先写出第 0 个字节。

    实测（2026-11-04）：Windows 的 `_locking` 对**空文件**锁 1 字节，锁的是一个长度为 0 的
    区间 —— 第二个进程照样能起来。单元测试里两个句柄没暴露它，真实双进程才暴露：
    第一个 `python -m app` 起来之后，第二个还能绑到另一个端口继续跑。
    """
    lock = InstanceLock(tmp_path)
    lock.acquire()
    try:
        assert (tmp_path / LOCK_FILE_NAME).stat().st_size >= 1
    finally:
        lock.release()


def test_releasing_lets_the_next_instance_in(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path)
    first.acquire()
    first.release()
    assert first.held is False

    second = InstanceLock(tmp_path)
    second.acquire()  # 不抛就是通过
    second.release()


def test_a_leftover_lock_file_does_not_block(tmp_path: Path) -> None:
    """强杀进程会留下锁文件。**文件存在不等于被占用** —— 这正是不能用标记文件的地方。"""
    (tmp_path / LOCK_FILE_NAME).write_text("pid=999999 port=8000\n", encoding="utf-8")

    lock = InstanceLock(tmp_path)
    lock.acquire()
    # 上一个持有者写的内容被本次覆盖（第 0 字节是锁，备注从第 1 字节起）。
    assert f"pid={os.getpid()}" in read_lock_note(tmp_path / LOCK_FILE_NAME)
    lock.release()


def test_two_data_dirs_can_run_at_the_same_time(tmp_path: Path) -> None:
    """锁跟着 `data/` 走：用户开两份、各用各的数据目录是合法的。"""
    left = InstanceLock(tmp_path / "left")
    right = InstanceLock(tmp_path / "right")
    left.acquire()
    right.acquire()  # 不抛就是通过
    left.release()
    right.release()


def test_main_refuses_to_start_when_another_instance_holds_the_lock(tmp_path: Path, monkeypatch) -> None:
    """入口层：锁被占时**退出 2**，并且绝不启动 uvicorn（否则锁就白加了）。"""
    from app import __main__ as entry

    monkeypatch.setenv("YTDL_DATA_DIR", str(tmp_path))
    holder = InstanceLock(tmp_path)
    holder.acquire(note="pid=777 port=8011")

    started: list[tuple] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: started.append((args, kwargs)))

    try:
        with pytest.raises(SystemExit) as excinfo:
            entry.main(["--port", "8123"])
        assert excinfo.value.code == 2
        assert started == []
    finally:
        holder.release()


def test_main_writes_the_port_and_releases_on_exit(tmp_path: Path, monkeypatch) -> None:
    """正常启动时：锁文件里留下 pid 与端口；`uvicorn.run` 返回后锁被释放。"""
    from app import __main__ as entry

    monkeypatch.setenv("YTDL_DATA_DIR", str(tmp_path))
    from app.contracts.config import get_settings

    assert get_settings().data_dir == tmp_path, "前提：本用例靠 YTDL_DATA_DIR 把数据目录指到临时目录"
    monkeypatch.setattr(entry, "_resolve_port", lambda host, port, *, auto_port: 8123)
    seen: list[str] = []

    def fake_run(*args, **kwargs):
        # 运行期间锁必须还被持有，且文件里写着端口 —— 这是排查「谁占着」的唯一线索。
        seen.append(read_lock_note(tmp_path / LOCK_FILE_NAME))

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert entry.main([]) == 0
    assert "port=8123" in seen[0]
    assert f"pid={os.getpid()}" in seen[0]

    # 退出后锁已释放：再取一次应当成功。
    again = InstanceLock(tmp_path)
    again.acquire()
    again.release()
