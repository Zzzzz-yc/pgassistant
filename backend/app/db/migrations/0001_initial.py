"""schema v1：14 张表 + FTS5 虚表（需求文档 §5 / §5.3）。

原有 M1 的 schema_v1.sql 全部语句都是 CREATE ... IF NOT EXISTS，
所以对「老库（user_version=0，但表已建好）」也是安全的：只补 user_version。
"""
from __future__ import annotations

from pathlib import Path

VERSION = 1

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schema_v1.sql"


def apply(conn) -> None:
    from app.db.sqlscript import split_statements

    script = SCHEMA_FILE.read_text(encoding="utf-8")
    for statement in split_statements(script):
        head = statement.lstrip().upper()
        if head.startswith("PRAGMA"):
            # journal_mode / foreign_keys 是连接级设置，由 connection.connect() 负责；
            # 在事务里改 journal_mode 会直接报错
            continue
        conn.execute(statement)
