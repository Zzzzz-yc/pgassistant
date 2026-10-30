"""把 .sql 脚本切成可逐条执行的语句。

不能用 executescript()：它会先隐式 COMMIT，迁移就失去事务性（§5.2 铁律 3）。
sqlite3.complete_statement() 会正确处理字符串里的分号与未闭合的引号。
"""
from __future__ import annotations

import sqlite3


def split_statements(script: str) -> list[str]:
    out: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            text = buf.strip()
            if text:
                out.append(text)
            buf = ""
    leftover = buf.strip()
    if leftover:
        out.append(leftover)
    return out
