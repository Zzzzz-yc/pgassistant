"""M5 索引端点测试（`API.md` §3.22 / §3.23，需求文档 §6.1 / §6.2）。

覆盖：202 + `task_id`、`/api/index/status` 轮询到完成、重建幂等、1 次 / 5 分钟限流、
进行中 409 `INDEX_BUILDING`、未知 `task_id` 404 `TASK_NOT_FOUND`、`/api/health` 的
`index_version` 与 `index_files`。

**不碰 `data/app.db`**：库与内容目录都指向 `tmp_path`（`PG_DB` 之外还改 SETTINGS，
因为内容目录也来自 SETTINGS）。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import deps
from app.core import config as core_config
from app.db import migrate
from app.db.connection import connect
from app.services import embeddings, index_task, secrets

BASE = "http://127.0.0.1:8000"
ORIGIN = {"Origin": BASE}
ENV_NAMES = (
    "PG_LLM_API_KEY", "PG_LLM_BASE_URL", "PG_LLM_MODEL", "PG_EMBEDDING_API_KEY",
    "PG_EMBEDDING_BASE_URL", "PG_EMBEDDING_MODEL",
)

PAGES = {
    "arrays.md": (
        "---\ntitle: 数组\nslug: cs-basics/array/array\nlevel: beginner\n"
        "type: article\nstack: cs-basics\ncategory: array\ntags: [数组]\n---\n\n"
        "数组是连续内存里的同类型元素集合。\n"
    ),
    "closures.md": (
        "---\ntitle: 闭包\nslug: frontend/javascript/closures\nlevel: intermediate\n"
        "type: article\nstack: frontend\ncategory: javascript\ntags: [闭包]\n---\n\n"
        "闭包让函数记住它定义时的作用域。\n"
    ),
}


def _write_content(content_dir):
    content_dir.mkdir(parents=True, exist_ok=True)
    for name, text in PAGES.items():
        (content_dir / name).write_text(text, encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = data / "app.db"
    content = tmp_path / "content"
    migrate.ensure_schema(db)
    _write_content(content)

    settings = core_config.Settings(content_dir=content, db_path=db)
    monkeypatch.setattr(core_config, "SETTINGS", settings)
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(secrets, "_keyring", lambda: None)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    index_task.reset_limits()
    embeddings.invalidate_cache()
    return SimpleNamespace(data_dir=data, db_path=db, content_dir=content)


@pytest.fixture()
def client(env):
    with TestClient(main.app, base_url=BASE) as instance:
        yield instance


@pytest.fixture()
def token(client) -> str:
    page = client.get("/")
    marker = 'name="x-app-token" content="'
    start = page.text.index(marker) + len(marker)
    return page.text[start:page.text.index('"', start)]


@pytest.fixture()
def auth(token) -> dict:
    return {"X-App-Token": token}


def _post_rebuild(client, auth, body=None):
    return client.post("/api/index/rebuild", json=body or {},
                       headers={**auth, **ORIGIN})


def _wait_done(client, auth, task_id, timeout=20.0):
    """轮询 `/api/index/status`，直到 state 不是 running（契约里完成态是 idle + 100%）。"""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/api/index/status?task_id={task_id}", headers=auth).json()
        if last["state"] != "running":
            return last
        time.sleep(0.02)
    raise AssertionError(f"索引任务超时未完成：{last}")


# ------------------------------------------------------------- 重建 + 轮询
def test_rebuild_returns_task_and_status_reaches_idle(client, auth, env):
    response = _post_rebuild(client, auth)
    assert response.status_code == 202

    body = response.json()
    task_id = body["task_id"]
    assert body["accepted"] is True
    assert task_id.startswith("idx-")
    assert body["status"]["task_id"] == task_id
    assert body["status"]["state"] in ("running", "idle")
    assert body["status"]["mode"] == "full"

    final = _wait_done(client, auth, task_id)
    assert final["state"] == "idle"                 # IndexRunState 没有 done（schemas 封闭集合）
    assert final["percent"] == 100.0
    assert final["files_total"] == 2
    assert final["files_done"] == 2
    assert final["content_version"]
    assert final["error"] is None

    conn = connect(env.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM resources").fetchone()["n"]
    finally:
        conn.close()
    assert count == 2


def test_rebuild_is_idempotent(client, auth, env):
    first = _post_rebuild(client, auth).json()
    first_status = _wait_done(client, auth, first["task_id"])

    index_task.reset_limits()                       # 换一次重建机会（限流是 5 分钟）
    second = _post_rebuild(client, auth).json()
    second_status = _wait_done(client, auth, second["task_id"])

    assert second_status["state"] == "idle"
    assert second_status["content_version"] == first_status["content_version"]
    assert second_status["files_total"] == first_status["files_total"] == 2

    conn = connect(env.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM resources").fetchone()["n"]
        rows = conn.execute("SELECT file_count FROM index_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    assert count == 2                                # 幂等：没有重复行
    assert rows["file_count"] == 2


def test_incremental_mode_is_accepted(client, auth):
    body = _post_rebuild(client, auth, {"mode": "incremental"}).json()
    assert body["status"]["mode"] == "incremental"
    assert _wait_done(client, auth, body["task_id"])["state"] == "idle"


def test_rebuild_is_rate_limited(client, auth):
    body = _post_rebuild(client, auth).json()
    _wait_done(client, auth, body["task_id"])

    blocked = _post_rebuild(client, auth)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.json()["detail"]["retry_after"] > 0


def test_rebuild_conflict_while_running(client, auth, monkeypatch):
    def slow_index(*_args, **_kwargs):
        time.sleep(0.4)
        return {"skipped": False, "files": 2, "version": "v-slow", "db": "x", "migration": {}}

    monkeypatch.setattr(index_task.indexer, "index_all", slow_index)

    first = _post_rebuild(client, auth)
    assert first.status_code == 202
    second = _post_rebuild(client, auth)

    assert second.status_code == 409
    assert second.json()["code"] == "INDEX_BUILDING"
    assert second.json()["detail"]["task_id"] == first.json()["task_id"]
    assert "percent" in second.json()["detail"]

    assert index_task.wait_until_idle(timeout=10.0)


# ---------------------------------------------------------------- status
def test_status_task_not_found(client, auth):
    response = client.get("/api/index/status?task_id=idx-nope", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_status_without_task_returns_idle_shape(client, auth):
    body = client.get("/api/index/status", headers=auth).json()
    assert body["state"] in ("idle", "running", "failed")
    assert body["files_total"] >= 0
    assert "content_version" in body


def test_status_requires_token(client):
    assert client.get("/api/index/status").status_code == 403


# ----------------------------------------------------------------- health
def test_health_exposes_index_version_after_rebuild(client, auth, env):
    body = _post_rebuild(client, auth).json()
    _wait_done(client, auth, body["task_id"])

    health = client.get("/api/health").json()
    conn = connect(env.db_path)
    try:
        stored = conn.execute(
            "SELECT content_version FROM index_state WHERE id = 1"
        ).fetchone()["content_version"]
    finally:
        conn.close()

    assert health["index_version"] == stored
    assert health["index"]["files"] == 2
    assert health["embedding_configured"] is False
