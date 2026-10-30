"""日志落盘（需求文档 §12.3）：JSON Lines + 5MB × 5 轮转 + 密钥不落盘。

覆盖四件事：
1. `configure_logging(tmp_path)` 之后每条日志是**合法 JSON**，字段是 ts/level/logger/message；
2. 有 request_id 时写成 `request_id` 键，没有请求上下文时不写这个键；
3. 把 `max_bytes` 调小，能滚出 `app.log.1`；
4. `secrets=` 传进去的字符串在文件里被替换成 `***`（令牌不落盘，§13.7）。

另外钉一条约束：pytest 进程里**默认不往 data/ 写日志**（RULES §7「测试不得污染 data/」）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.core.logging import configure_logging


def _flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def _json_lines(path: Path) -> list[dict]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


@pytest.fixture(autouse=True)
def _clean_logging(tmp_path):
    """每个用例结束后收尾：关掉文件 handler（Windows 上不关会锁住 tmp 文件），只留控制台。"""
    yield
    configure_logging()


def test_configure_logging_writes_json_lines(tmp_path):
    log_file = configure_logging(tmp_path, level=logging.INFO)
    assert log_file == tmp_path / "app.log"
    assert log_file.is_file()

    logging.getLogger("pgassistant.test.json").info("hello %s", "json")
    _flush_handlers()

    lines = _json_lines(log_file)
    assert lines, "日志文件是空的"
    payload = lines[-1]
    assert payload["level"] == "INFO"
    assert payload["logger"] == "pgassistant.test.json"
    assert payload["message"] == "hello json"
    assert payload["ts"].endswith("Z") and "T" in payload["ts"]
    # 没有请求上下文时不该出现 request_id 键（过滤器补的是 "-"，格式化器会丢掉）
    assert "request_id" not in payload


def test_request_id_is_written_when_available(tmp_path):
    log_file = configure_logging(tmp_path, level=logging.INFO)
    logging.getLogger("pgassistant.test.rid").info("带着请求 id 的一条", extra={"request_id": "abc123def456"})
    _flush_handlers()

    payload = _json_lines(log_file)[-1]
    assert payload["request_id"] == "abc123def456"


def test_rotation_creates_numbered_backup(tmp_path):
    log_file = configure_logging(tmp_path, level=logging.INFO, max_bytes=256, backup_count=2)
    logger = logging.getLogger("pgassistant.test.rotate")
    for index in range(50):
        logger.info("rotation line %03d — padding padding padding padding", index)
    _flush_handlers()

    assert log_file.is_file()
    assert (tmp_path / "app.log.1").is_file(), "超过 max_bytes 后应滚出 app.log.1"
    # 滚过去的文件同样是 JSON Lines
    assert all("level" in item for item in _json_lines(tmp_path / "app.log.1"))


def test_secrets_are_redacted_in_file(tmp_path):
    secret = "SUPER-SECRET-TOKEN-1234567890"
    log_file = configure_logging(tmp_path, level=logging.INFO, secrets=(secret,))
    logging.getLogger("pgassistant.test.secret").info("X-App-Token=%s（随进程重启更换）", secret)
    _flush_handlers()

    text = log_file.read_text(encoding="utf-8")
    assert secret not in text
    assert "***" in text


def test_pytest_default_does_not_write_into_data():
    """没给 log_dir 时（pytest 进程内）只装控制台，返回 None，绝不创建 data/logs。"""
    assert configure_logging(level=logging.INFO) is None
