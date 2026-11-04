"""`python -m app` —— 本地启动入口。

比裸 `python -m uvicorn app.main:app` 多做一件小事，但那件小事正好是本机每次都会踩的：
**启动前把端口问题说清楚**。

裸 uvicorn 在端口被占时只给一句

    [Errno 13] error while attempting to bind on address ('127.0.0.1', 8000):
    [winerror 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。

看不出被谁占了，也看不出下一步该敲什么（本机 8000 常年是 IncrediBuild 的 `Manager.exe`）。
细节见 [ai/bug-fix/009](../../ai/bug-fix/009-local-dev-port-is-occupied-and-unconfigurable.md)。

用法：

    python -m app                  # 127.0.0.1:8000，端口可用 .env 里的 YTDL_API_PORT 覆盖
    python -m app --port 8010      # 临时换端口
    python -m app --reload         # 前端热更新开发模式
    python -m app --auto-port      # 端口被占时自动往后找一个可用的，而不是报错退出
"""

from __future__ import annotations

import argparse
import os
import sys

from .contracts.config import get_settings
from .host.dev_server import describe_occupant, find_available_port, port_available
from .host.instance_lock import InstanceLock, InstanceLockError

_AUTO_PORT_ATTEMPTS = 20


def _resolve_port(host: str, port: int, *, auto_port: bool) -> int:
    """端口能绑就返回它；不能绑就打印「谁占了 + 下一步」后退出，或自动往后找一个。"""
    if port_available(host, port):
        return port

    occupant = describe_occupant(port)
    suffix = f"，占用者：{occupant.describe()}" if occupant else ""
    print(f"端口 {port} 无法绑定{suffix}。", file=sys.stderr)

    if not auto_port:
        print("下一步（任选一条）：", file=sys.stderr)
        print(f"  1) 换个端口启动：       python -m app --port {port + 1}", file=sys.stderr)
        print(f"  2) 写进仓库根 .env：    YTDL_API_PORT={port + 1}   （前后端会自动一致）", file=sys.stderr)
        print(f"  3) 看是谁占着：         netstat -ano -p tcp | findstr :{port}", file=sys.stderr)
        print("  也可以直接加 --auto-port，让本命令自己往后找一个可用端口。", file=sys.stderr)
        raise SystemExit(2)

    chosen = find_available_port(host, port + 1, attempts=_AUTO_PORT_ATTEMPTS)
    if chosen is None:
        print(
            f"从 {port + 1} 起连续 {_AUTO_PORT_ATTEMPTS} 个端口都不可用，请显式指定 --port。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(
        f"已自动改用 {chosen}；前端开发模式请设 YTDL_API_PORT={chosen}（或写进仓库根 .env）。",
        file=sys.stderr,
    )
    return chosen


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="启动本机 API 服务（默认只监听 127.0.0.1，供单用户使用）。",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=settings.api_port,
        help=f"监听端口，默认 {settings.api_port}（可用仓库根 .env 的 YTDL_API_PORT 覆盖）",
    )
    parser.add_argument("--reload", action="store_true", help="源码变更自动重启（前端热更新开发模式用）")
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="端口被占用时自动往后找一个可用端口，而不是报错退出",
    )
    args = parser.parse_args(argv)

    # **先取单实例锁，再谈端口**：`--auto-port` 换的只是监听端口，库与 .part 还是同一份 ——
    # 让它绕过锁，等于给「两个进程同时恢复、同时写同一个 .part」开了正门。
    lock = InstanceLock(settings.data_dir)
    try:
        lock.acquire(note=f"pid={os.getpid()}")
    except InstanceLockError as exc:
        print(f"{exc}。", file=sys.stderr)
        print(
            "两个实例同时跑会同时恢复同一批孤儿任务、往同一个 .part 写，产物会坏，而界面上看不到任何报错。",
            file=sys.stderr,
        )
        print("下一步（任选一条）：", file=sys.stderr)
        print("  1) 找到并关掉那个实例（锁文件里写着它的 pid）", file=sys.stderr)
        print(
            "  2) 让这一份用另一个数据目录："
            + r"YTDL_DATA_DIR=D:\cascade-data-2"
            + "（两份各用各的库与产物）",
            file=sys.stderr,
        )
        print("  3) 确认那个进程已经死了：删掉锁文件后重试（操作系统锁会随进程退出自动释放）", file=sys.stderr)
        raise SystemExit(2)

    try:
        port = _resolve_port(args.host, args.port, auto_port=args.auto_port)
        lock.update_note(f"pid={os.getpid()} port={port}")
        print(f"服务地址：http://{args.host}:{port}")

        # 延迟导入：--help 与端口预检失败这两条路径不必付 uvicorn 的导入开销。
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=port, reload=args.reload)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
