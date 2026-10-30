"""SQLite 连接（WAL + dict 行）。

一请求一连接：SQLite 打开开销在微秒级，换来的是「不会跨线程复用连接」。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    # 逻辑外键而非物理外键：资料下架不能级联删用户计划（§5.3）
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn
