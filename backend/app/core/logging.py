"""日志配置（需求文档 §12.3）：控制台文本 + `data/logs/app.log`（JSON Lines，5MB × 5 轮转）。

为什么需要这一层：

- §12.3 要求日志**落盘**可回溯，5xx 的 `hint` 直接写「日志在 data/logs/app.log」；
- 落盘用 **JSON Lines**（一行一条 JSON）：堆栈里的换行会被 `json.dumps` 转义，不会把一条
  记录切成多行；用 `jq`/脚本按 `request_id` 过滤也方便；
- 控制台保留原有文本格式（`ts LEVEL logger [request_id]: msg`），终端可读性优先。

安全（§13.1 / §12.3「日志中无密钥」）：

- 一次性令牌每进程一枚、**不落盘**（§13.7）。但启动时会打一行 `X-App-Token=…`：
  打到控制台无所谓，写进文件就成了持久化。所以调用方把已知密钥通过 `secrets=` 传进来，
  文件 handler 会把它们替换成 `***`（只影响文件，控制台保持原样）。
- prompt / 完整对话 / api_key 由调用方保证不打日志；这里只兜「已知密钥」这一层。

测试（RULES §7「测试不得污染 data/」）：pytest 进程内调用且**没有显式给目录**时，
只装控制台 handler，不创建 `data/logs/`。要验文件行为的用例显式传 `log_dir`
（见 `tests/test_logging.py`）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..middleware.request_id import RequestIDLogFilter
from .config import SETTINGS

#: backend/app/core/logging.py -> backend/app/core -> backend -> 项目根
PROJECT = Path(__file__).resolve().parents[3]

LOG_DIR_ENV = "PG_LOG_DIR"
LOG_FILE_NAME = "app.log"
DEFAULT_LOG_DIR = PROJECT / "data" / "logs"

#: §12.3：单文件 5MB，保留 5 份（app.log + app.log.1..5）
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

#: 控制台沿用 M-1 的文本格式（`%(request_id)s` 由 RequestIDLogFilter 补）
CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"

#: 本模块装过的 handler 打这个标记，重复配置时只替换自己的
_HANDLER_TAG = "_pgassistant_handler"


def _iso_utc(timestamp: float) -> str:
    """UTC ISO8601，毫秒精度，`Z` 结尾（与 API 的时间口径一致）。"""
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class JsonLinesFormatter(logging.Formatter):
    """一行一个 JSON 对象：`ts` / `level` / `logger` / `message`（+ 可选 `request_id` / `exc`）。"""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "***")
        return text

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None)
        if request_id and request_id != "-":
            payload["request_id"] = str(request_id)

        if record.exc_info:
            # 堆栈也是日志的一部分；同样过一遍脱敏，避免异常消息里带出密钥
            payload["exc"] = self._redact(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _resolve_log_file(log_dir) -> Path | None:
    """算日志文件路径；测试进程且未显式指定目录时返回 None（不落盘）。"""
    explicit = log_dir is not None or bool(os.environ.get(LOG_DIR_ENV))
    if log_dir is not None:
        directory = Path(log_dir)
    else:
        directory = Path(os.environ.get(LOG_DIR_ENV) or DEFAULT_LOG_DIR)

    if not explicit and "pytest" in sys.modules:
        return None
    return directory / LOG_FILE_NAME


def configure_logging(
    log_dir=None,
    *,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
    level: int | None = None,
    secrets: tuple[str, ...] = (),
) -> Path | None:
    """配置 root logger，返回日志文件路径（未落盘时返回 None）。

    - 幂等：重复调用只替换本模块装过的 handler，不会叠加；
    - `log_dir` 不传时用 `PG_LOG_DIR`，再退回 `data/logs`；
    - `secrets` 里的字符串只会在文件里被替换成 `***`。
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()

    log_level = level if level is not None else getattr(logging, SETTINGS.log_level, logging.INFO)
    root.setLevel(log_level)

    request_id_filter = RequestIDLogFilter()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    console.addFilter(request_id_filter)
    setattr(console, _HANDLER_TAG, True)
    root.addHandler(console)

    log_file = _resolve_log_file(log_dir)
    if log_file is None:
        return None

    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLinesFormatter(tuple(secrets)))
    file_handler.addFilter(request_id_filter)
    setattr(file_handler, _HANDLER_TAG, True)
    root.addHandler(file_handler)

    return log_file
