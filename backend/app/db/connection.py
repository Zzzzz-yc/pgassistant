"""SQLite 连接（WAL + dict 行）。

一请求一连接：SQLite 打开开销在微秒级，换来的是「不会跨线程复用连接」。

**check_same_thread=False 是必须的**：`api/deps.py::get_conn` 是同步生成器依赖，
FastAPI 会把 `__enter__` / endpoint / `__exit__` 分派到线程池里**可能不同**的线程
（同一请求内仍然串行，不存在并发共用同一条连接）。保持默认 True 时，浏览器并发
首屏就会 500：`sqlite3.ProgrammingError: SQLite objects created in a thread can only
be used in that same thread`（回归用例：`tests/test_api_concurrency.py`）。

**temp_store = MEMORY 是 M5 补的**（`PRAGMA` 只是连接级设置，签名不变）：

1. FTS5 的整表 `DELETE` / 重建、`VACUUM` 之类的操作会申请**临时库**；默认
   `temp_store = FILE` 时它落在操作系统的临时目录里，若该目录对进程不可写
   （受限运行环境、被沙箱重定向、磁盘 ACL 收紧），SQLite 直接报
   `unable to open database file`，索引重建与 `POST /api/index/rebuild` 全线失败；
2. 顺手少一处隐私泄漏点：FTS 的临时文件里是**正文切词结果**，留在进程内存里不落盘。

本地单用户库只有几百篇资料，内存里的临时排序代价可以忽略。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 见模块 docstring：跨线程只是"换线程用同一条连接"，不是并发共享
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    # 见模块 docstring：FTS5 重建不再依赖操作系统临时目录
    conn.execute("PRAGMA temp_store = MEMORY")
    # 逻辑外键而非物理外键：资料下架不能级联删用户计划（§5.3）
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn
