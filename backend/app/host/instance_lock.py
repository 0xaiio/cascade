"""单实例锁：**同一个 `data/` 目录只允许一个进程在跑**。

为什么需要它：库是 SQLite、产物是同一批 `.part`，而启动即恢复（[015](../ai/bug-fix/015-orphan-items-never-resume.md)）
让「两个进程」从偶发风险变成**每次双开都会发生**：两边同时把同一批孤儿条目重新排队、
同时往同一个 `.part` 写，坏的是产物，而界面上没有任何报错。

三条设计约束（对应 [ai/plan/brainstorming.md](../ai/plan/brainstorming.md) 的 D-05）：

1. **用文件锁，不用「存在即占用」的标记文件**。强制杀进程不会释放标记文件，
   于是下一次启动会被一个已经死掉的进程挡在门外；操作系统锁在进程退出时自动释放。
2. **锁跟着 `data/` 走**，不是全局单例：用户开两份、各用各的 `data/`（`YTDL_DATA_DIR`）
   是合法用法，那种情况下两份都该能起来。
3. **锁文件不删**。删掉会与「别人刚好打开同一个文件」的时序打架（经典 inode 竞态）；
   锁释放后文件留着完全无害，它的内容只是给下一个人看的「上次是谁」。

实现：Windows 用 `msvcrt.locking` 锁首字节，POSIX 用 `fcntl.flock`；
两者都是**非阻塞**的，拿不到立刻抛 `InstanceLockError`，由入口翻译成一句话并退出。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 锁文件名。放在 `data/` 里，与库、日志同级 —— 用户排查时一眼能看到。
LOCK_FILE_NAME = "app.instance.lock"

#: 锁住**第 0 个字节**，备注从第 1 个字节起写。
#:
#: 为什么要把备注挪开：Windows 的字节范围锁连**读**都禁止（`read_text()` 直接 EACCES），
#: 锁在自己的字节上就等于「持有者的 pid 谁都看不到」。挪开一个字节后，
#: 锁仍然互斥，而备注随时可读。
_NOTE_OFFSET = 1


class InstanceLockError(RuntimeError):
    """这个 `data/` 目录已经被另一个进程占着。"""

    def __init__(self, path: Path, holder: str = "") -> None:
        self.path = path
        self.holder = holder
        detail = f"，持有者：{holder}" if holder else ""
        super().__init__(f"已有一个实例在跑（锁文件 {path}{detail}）")


if os.name == "nt":  # pragma: no cover - 平台分支，两边的用例各跑一边
    import msvcrt

    def _try_lock(fd: int) -> None:
        # 锁第 0 个字节，非阻塞。
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - 见上
    import fcntl

    def _try_lock(fd: int) -> None:
        # POSIX 的 flock 锁整份文件，读不受影响 —— 备注的位置与平台无关，仍是第 1 字节起。
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def read_lock_note(path: Path) -> str:
    """读锁文件里的备注（持有者的 pid / 端口）。读不到就返回空串。

    **必须从第 1 个字节开始读**：第 0 个字节被锁着，Windows 上连读都不允许。
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return ""
    try:
        os.lseek(fd, _NOTE_OFFSET, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    finally:
        os.close(fd)


class InstanceLock:
    """一个 `data/` 目录的独占锁。`acquire()` 成功即持有，`release()` 或进程退出即释放。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self.data_dir / LOCK_FILE_NAME

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, *, note: str = "") -> None:
        """取锁。拿不到就抛 `InstanceLockError`（**不阻塞、不重试**）。

        `note` 写进锁文件给下一个人看（pid / 端口），失败时会被原样带进异常消息里。
        """
        if self._fd is not None:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # **先把第 0 个字节写出来，再锁它**。Windows 的 `_locking` 对空文件锁 1 字节
            # 等于锁了一个长度为 0 的区间 —— 测出来的表现是「第二个进程照样能起来」。
            # 这一字节同时是锁的位置与「文件非空」的标记，备注从第 1 字节起写。
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\x00")
            _try_lock(fd)
        except OSError as exc:
            holder = self._read_note()
            os.close(fd)
            raise InstanceLockError(self.path, holder) from exc
        self._fd = fd
        self._write_note(note or f"pid={os.getpid()}")

    def update_note(self, note: str) -> None:
        """补充锁文件里的信息（例如端口解析完之后）。没持锁时什么都不做。"""
        if self._fd is None:
            return
        self._write_note(note)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)
        # **不删文件**：见模块文档第 3 条。

    def _write_note(self, note: str) -> None:
        assert self._fd is not None
        data = f"{note}\n".encode("utf-8")
        os.lseek(self._fd, _NOTE_OFFSET, os.SEEK_SET)
        os.write(self._fd, data)
        # 上一次的内容可能更长，截掉尾巴（截断只由持有者做，别人没有写权限）。
        os.ftruncate(self._fd, _NOTE_OFFSET + len(data))
        os.fsync(self._fd)

    def _read_note(self) -> str:
        return read_lock_note(self.path)

    # -- 上下文管理：`with InstanceLock(dir): ...` ------------------------
    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


__all__ = ["LOCK_FILE_NAME", "InstanceLock", "InstanceLockError", "read_lock_note"]
