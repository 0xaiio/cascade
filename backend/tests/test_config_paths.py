"""仓库根与默认目录：纯移动重构的静默陷阱。

`ai/refactor/011` 把 `app/` 平铺模块拆进 7 个包，`contracts/config.py` 从
`backend/app/config.py` 变成了 `backend/app/contracts/config.py` —— 深了一层，
`REPO_ROOT` 必须从 `parents[2]` 改成 `parents[3]`。改错的后果不是导入失败，
而是**所有默认目录悄悄挪到 `backend/data`、`backend/downloads`**：应用照常启动、
数据库照常创建（一个空的新库），旧任务像是全丢了。

`test_dev_server.py` 里那条 `env_file == REPO_ROOT / ".env"` 挡不住这个 ——
`env_file` 本来就是用 `REPO_ROOT` 定义出来的，等于自证。所以这里从**外部事实**判：
仓库根下必须有 `backend/app/main.py` 与 `frontend/package.json`。
"""

from __future__ import annotations

from pathlib import Path

from app.contracts.config import REPO_ROOT, AppSettings


def test_repo_root_is_the_repository_not_the_backend_directory() -> None:
    assert (REPO_ROOT / "backend" / "app" / "main.py").is_file()
    assert (REPO_ROOT / "frontend" / "package.json").is_file()
    assert REPO_ROOT.name != "backend"
    assert REPO_ROOT.name != "app"


def test_default_directories_hang_off_the_repository_root() -> None:
    """默认值由 `REPO_ROOT` 派生 —— 这条钉的是「派生关系」，不是某个绝对路径。"""
    settings = AppSettings()

    assert settings.data_dir == REPO_ROOT / "data"
    assert settings.download_dir == REPO_ROOT / "downloads"
    assert settings.database_path == REPO_ROOT / "data" / "app.sqlite3"


def test_the_env_file_is_absolute_so_it_does_not_depend_on_the_working_directory() -> None:
    """文档让用户 `cd backend` 再启动。相对路径会按工作目录解析，仓库根的 `.env` 就读不到。"""
    env_file = Path(AppSettings.model_config["env_file"])

    assert env_file.is_absolute()
    assert env_file == REPO_ROOT / ".env"
