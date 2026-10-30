"""数据库连接依赖（每个请求一条连接，SQLite 开销很小）。"""
from pathlib import Path

from fastapi import HTTPException

from ..core import errors
from ..core.config import SETTINGS
from ..db.connection import connect

PROJECT = Path(__file__).resolve().parents[3]
DB_PATH = SETTINGS.db_path


def get_conn():
    db_path = DB_PATH                       # 读模块属性，测试可替换
    if not db_path.exists():
        raise HTTPException(
            status_code=503,
            detail=errors.error_payload(
                errors.DB_NOT_READY,
                "数据库不存在",
                hint="先运行 python backend/scripts/bootstrap_index.py",
            ),
        )
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
