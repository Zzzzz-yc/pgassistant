"""集中配置（需求文档 §3.4 配置分层）。

只读环境变量（PG_*），不读 .env 文件——少一个依赖，也少一处泄密点。
优先级：环境变量 > 内置默认值。用户级配置与密钥走 data/（§13.8），不在这里。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# backend/app/core/config.py -> backend/app -> backend -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# 开发态 Vite dev server；生产态同源部署不需要跨源（§13.4 / §13.7）
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = _env(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    # 相对路径按项目根解析，避免从别的目录启动时找不到 content/ 与 data/
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    content_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "content")
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "app.db")
    log_level: str = "INFO"
    dev_origins: tuple[str, ...] = DEV_ORIGINS

    @property
    def backup_dir(self) -> Path:
        return self.db_path.parent / "backups"

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent


def load_settings() -> Settings:
    return Settings(
        host=_env("PG_HOST", DEFAULT_HOST),
        port=_env_int("PG_PORT", DEFAULT_PORT),
        content_dir=_env_path("PG_CONTENT_DIR", PROJECT_ROOT / "content"),
        db_path=_env_path("PG_DB", PROJECT_ROOT / "data" / "app.db"),
        log_level=_env("PG_LOG_LEVEL", "INFO").upper(),
        dev_origins=tuple(
            o.strip() for o in _env("PG_DEV_ORIGINS", ",".join(DEV_ORIGINS)).split(",") if o.strip()
        ),
    )


SETTINGS = load_settings()
