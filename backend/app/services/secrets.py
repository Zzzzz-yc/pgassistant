"""密钥存储（需求文档 §13.8 / DECISIONS D11 + D26 / `MODULES.md` §2.5）。

三级口径（`api_key` 永不进 `settings` 表、不进 `data/config.json`）：

1. **首选 keyring**：Windows 凭据管理器 / macOS Keychain，同机其它进程读不到；
2. **降级 `data/llm_key`**：单行、0600（`data/` 由 `.gitignore` 覆盖），
   embedding 目前复用同一文件（D26），拆分时只改 `FILE_NAMES` 并登记 `data/embedding_key`；
3. **环境变量**在调用方（`settings_service`）先读，本模块只管"存储"这一层。

**日志红线**：本模块任何日志都不出现密钥明文，只记 `name` 与后端级别（`keyring` / `file`）。
`mask_secret` 是唯一允许把密钥形状带到响应里的地方（`sk-****1234`）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..core import config as core_config

log = logging.getLogger("pgassistant.secrets")

#: keyring 里的服务名（`service` + `username` 共同定位一条凭据）
SERVICE = "pgassistant"

LLM_KEY = "llm_api_key"
EMBEDDING_KEY = "embedding_api_key"

#: 降级文件（相对 `data/`）。D26：embedding 暂时复用 `llm_key` 同一个文件。
FILE_NAMES: dict[str, str] = {
    LLM_KEY: "llm_key",
    EMBEDDING_KEY: "llm_key",
}

#: 0600：Windows 上 chmod 只能去掉只读位，真正的隔离靠目录 ACL（README 已说明）
FILE_MODE = 0o600


class SecretUnavailable(RuntimeError):
    """keyring 与 0600 文件都写不进去（→ 500 `INTERNAL`，detail 说明已降级）。"""


# ------------------------------------------------------------------ 路径
def _data_dir() -> Path:
    """`data/` 目录。**每次调用读模块属性**，测试可以把 SETTINGS 换到 tmp。"""
    return Path(core_config.SETTINGS.data_dir)


def secret_path(name: str) -> Path:
    return _data_dir() / FILE_NAMES.get(name, name)


# ------------------------------------------------------------------ keyring
def _keyring():
    """返回可用的 keyring 模块；未安装 / 无可用后端时返回 None（**不抛异常**）。

    `keyring.get_keyring()` 在没有凭据服务时返回 `fail.Keyring`（priority = 0），
    这里按 priority 判掉它；Linux 无桌面环境的 `null` 后端同理。
    """
    try:
        import keyring
    except Exception:                              # ImportError 或后端初始化异常
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    if backend is None:
        return None
    module = (type(backend).__module__ or "").lower()
    name = type(backend).__name__.lower()
    if any(token in module for token in ("fail", "null")) or "fail" in name:
        return None
    try:
        if float(getattr(backend, "priority", 1)) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    return keyring


def backend_in_use() -> str:
    """当前实际后端：`"keyring"` 或 `"file"`（`GET /api/settings` 的 `secret_backend`）。"""
    return "keyring" if _keyring() is not None else "file"


# ------------------------------------------------------------------ 读写
def _write_file(name: str, value: str) -> Path:
    path = secret_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # 先写临时文件再原子替换：写一半断电不会留下半截密钥
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")
    try:
        os.chmod(tmp, FILE_MODE)
    except OSError:                                # Windows 上部分文件系统不支持
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass
    return path


def store_secret(name: str, value: str) -> str:
    """存密钥，返回实际用的后端（`"keyring"` | `"file"`）。

    keyring 抛任何异常都只是降级到文件存储（不把异常抛给设置页）；
    文件也写不了才抛 `SecretUnavailable`。
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("密钥不能为空")

    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(SERVICE, name, value)
            log.info("event=secret.store backend=keyring name=%s", name)
            return "keyring"
        except Exception as exc:                   # 后端坏了：降级，不拦保存
            log.warning("event=secret.store backend=keyring failed name=%s error=%s",
                        name, type(exc).__name__)

    try:
        _write_file(name, value)
    except OSError as exc:
        log.warning("event=secret.store backend=file failed name=%s error=%s",
                    name, type(exc).__name__)
        raise SecretUnavailable("系统凭据库与本地密钥文件都不可用") from exc
    log.info("event=secret.store backend=file name=%s", name)
    return "file"


def load_secret(name: str) -> str | None:
    """读密钥：keyring 优先，取不到再读 0600 文件；都没有返回 None。

    环境变量优先级更高，但由 `settings_service` 先读 env，再调这里。
    """
    ring = _keyring()
    if ring is not None:
        try:
            value = ring.get_password(SERVICE, name)
        except Exception as exc:
            log.warning("event=secret.load backend=keyring failed name=%s error=%s",
                        name, type(exc).__name__)
            value = None
        if value:
            return value

    try:
        path = secret_path(name)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            return value or None
    except OSError as exc:
        log.warning("event=secret.load backend=file failed name=%s error=%s",
                    name, type(exc).__name__)
    return None


def mask_secret(value: str | None) -> str | None:
    """`sk-****1234` 形状的掩码；`None` 原样返回 `None`（没配就是 null，不是空串）。"""
    if not value:
        return None
    value = str(value)
    if len(value) < 8:
        return "****"
    return value[:3] + "****" + value[-4:]


def delete_secret(name: str) -> None:
    """删除密钥（keyring 与文件都尝试，任何一步失败只记日志）。"""
    ring = _keyring()
    if ring is not None:
        try:
            ring.delete_password(SERVICE, name)
            log.info("event=secret.delete backend=keyring name=%s", name)
        except Exception:                          # 不存在 / 后端不支持：忽略
            pass

    try:
        path = secret_path(name)
        if path.is_file():
            path.unlink()
            log.info("event=secret.delete backend=file name=%s", name)
    except OSError as exc:
        log.warning("event=secret.delete backend=file failed name=%s error=%s",
                    name, type(exc).__name__)
