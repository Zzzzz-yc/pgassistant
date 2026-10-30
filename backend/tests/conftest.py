"""pytest 公共夹具。

后端以 `app.*` 形式导入（与 uvicorn --app-dir backend 的解析一致），
所以先把 backend/ 放进 sys.path。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import deps  # noqa: E402
from app.db import migrate  # noqa: E402
from app.db.connection import connect  # noqa: E402

TEST_BASE = "http://127.0.0.1:8000"


def _seed(db_path: Path) -> None:
    """一库一条资料：够 taxonomy / resources 接口返回 200 即可。"""
    migrate.ensure_schema(db_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO resources
                   (slug, title, summary, body_md, body_tokens, stack, category, level,
                    type, duration_min, file_path, content_hash, created_at, updated_at)
                   VALUES ('cs-basics/array-linkedlist/array', '数组', '数组是线性结构',
                           '正文', '数 组', 'cs-basics', 'array-linkedlist', 'beginner',
                           'article', 6, 'content/x.md', 'hash', '2026-10-30', '2026-10-30')"""
            )
            rid = conn.execute("SELECT id FROM resources").fetchone()["id"]
            conn.execute(
                "INSERT INTO resources_fts (rowid, title_tokens, summary_tokens,"
                " body_tokens, tags_tokens) VALUES (?,?,?,?,?)",
                (rid, "数 组", "数 组", "数 组", ""),
            )
            conn.execute(
                "INSERT INTO resource_tags (resource_id, tag) VALUES (?, '数组')", (rid,)
            )
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "data" / "app.db"
    _seed(path)
    monkeypatch.setattr(deps, "DB_PATH", path)
    return path


@pytest.fixture()
def client(db_path):
    with TestClient(main.app, base_url=TEST_BASE) as c:
        yield c


@pytest.fixture()
def token(client) -> str:
    """用页面里注入的 token，而不是直接读全局变量——顺带验证注入链路。"""
    page = client.get("/")
    assert page.status_code == 200
    marker = 'name="x-app-token" content="'
    start = page.text.index(marker) + len(marker)
    return page.text[start:page.text.index('"', start)]
