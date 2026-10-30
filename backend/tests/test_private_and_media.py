"""M5 私有资料与私有媒体测试（`API.md` §3.24 / §1.10 / §3.26，需求文档 §4.4 / §13.3）。

覆盖：

- `POST /api/private/rescan` 的新增 / 更新 / 删除 / 跳过 / 错误计数；
- 私有 slug 强制 `private/` 前缀、`source='private'`、删除走软删（FTS 行保留）；
- `private_dir` 非法时的 403 `PATH_FORBIDDEN` / 400 `PRIVATE_DIR_INVALID`；
- `GET/HEAD /media/private/{path}`：200、`../` 404、扩展名白名单、405、跨站 403、
  免令牌，以及**不回落 content 根**。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import deps
from app.core import config as core_config
from app.db import migrate
from app.db.connection import connect
from app.services import embeddings, private_scanner, secrets

BASE = "http://127.0.0.1:8000"
ORIGIN = {"Origin": BASE}
ENV_NAMES = ("PG_LLM_API_KEY", "PG_EMBEDDING_API_KEY")
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"private-media"

DP_MD = """---
title: 背包问题笔记
slug: dp-intro
level: advanced
type: article
stack: private
category: notes
tags: [dp]
---

0-1 背包的状态转移与滚动数组优化。
"""

OK_MD = """---
title: 二分查找
---

二分的边界条件。
"""

BAD_MD = """---
summary: 没有 title
---

缺 front-matter title，扫描时应记一条错误。
"""


def _private_files(root):
    notes = root / "notes" / "nested"
    notes.mkdir(parents=True, exist_ok=True)
    (root / "notes" / "dp.md").write_text(DP_MD, encoding="utf-8")
    (root / "notes" / "nested" / "ok.md").write_text(OK_MD, encoding="utf-8")
    (root / "bad.md").write_text(BAD_MD, encoding="utf-8")
    (root / "img").mkdir(parents=True, exist_ok=True)
    (root / "img" / "pic.png").write_bytes(PNG_BYTES)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = data / "app.db"
    content = tmp_path / "content"
    content.mkdir(parents=True, exist_ok=True)
    (content / "img").mkdir(parents=True, exist_ok=True)
    (content / "img" / "content-only.png").write_bytes(PNG_BYTES)

    migrate.ensure_schema(db)
    _private_files(data / "private")

    settings = core_config.Settings(content_dir=content, db_path=db)
    monkeypatch.setattr(core_config, "SETTINGS", settings)
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(secrets, "_keyring", lambda: None)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    embeddings.invalidate_cache()
    return SimpleNamespace(data_dir=data, db_path=db, content_dir=content,
                           private_dir=data / "private")


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


def _rescan(client, auth):
    return client.post("/api/private/rescan", json={}, headers={**auth, **ORIGIN})


def _resources(env):
    conn = connect(env.db_path)
    try:
        return {
            row["slug"]: dict(row)
            for row in conn.execute(
                "SELECT id, slug, title, source, file_path, is_deleted FROM resources"
            )
        }
    finally:
        conn.close()


# ------------------------------------------------------------- rescan 计数
def test_rescan_adds_private_resources_with_prefix(client, auth, env):
    response = _rescan(client, auth)
    assert response.status_code == 200
    body = response.json()

    assert body["root"].endswith("data/private")
    assert body["scanned"] == 3
    assert body["added"] == 2                       # dp.md + nested/ok.md
    assert body["updated"] == 0
    assert body["removed"] == 0
    assert body["skipped"] == 0
    assert len(body["errors"]) == 1                 # bad.md 缺 title
    assert "bad.md" in body["errors"][0]
    assert isinstance(body["took_ms"], int)

    rows = _resources(env)
    private_rows = {slug: row for slug, row in rows.items() if row["source"] == "private"}
    assert set(private_rows) == {"private/dp-intro", "private/notes/nested/ok"}
    assert all(slug.startswith("private/") for slug in private_rows)
    assert private_rows["private/dp-intro"]["title"] == "背包问题笔记"
    assert private_rows["private/dp-intro"]["file_path"].endswith("private/notes/dp.md")


def test_rescan_updates_changed_file_and_skips_unchanged(client, auth, env):
    _rescan(client, auth)

    target = env.private_dir / "notes" / "dp.md"
    target.write_text(DP_MD.replace("背包问题笔记", "背包问题笔记（第二版）"), encoding="utf-8")
    body = _rescan(client, auth).json()

    assert body["scanned"] == 3
    assert body["added"] == 0
    assert body["updated"] == 1
    assert body["skipped"] == 1                     # nested/ok.md 内容没变
    assert body["removed"] == 0

    rows = _resources(env)
    assert rows["private/dp-intro"]["title"] == "背包问题笔记（第二版）"


def test_rescan_soft_deletes_missing_file_and_keeps_fts(client, auth, env):
    _rescan(client, auth)
    before = _resources(env)
    removed_slug = "private/notes/nested/ok"
    removed_id = before[removed_slug]["id"]

    (env.private_dir / "notes" / "nested" / "ok.md").unlink()
    body = _rescan(client, auth).json()

    assert body["removed"] == 1
    assert body["added"] == 0
    assert body["updated"] == 0

    rows = _resources(env)
    assert rows[removed_slug]["is_deleted"] == 1    # 软删除，行还在

    conn = connect(env.db_path)
    try:
        fts = conn.execute(
            "SELECT COUNT(*) AS n FROM resources_fts WHERE rowid = ?", (removed_id,)
        ).fetchone()
    finally:
        conn.close()
    assert fts["n"] == 1                            # §6.2：FTS 行保留

    # 再扫一次仍然 removed=0（已经删过了，不重复计数）
    again = _rescan(client, auth).json()
    assert again["removed"] == 0


def test_rescan_stores_last_report(client, auth, env):
    _rescan(client, auth)
    conn = connect(env.db_path)
    try:
        report = private_scanner.last_report(conn)
    finally:
        conn.close()

    assert report["scanned"] == 3
    assert report["added"] == 2
    assert report["at"]


# ----------------------------------------------------------- 非法私有目录
def _set_private_dir(env, value):
    conn = connect(env.db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                ("private_dir", json.dumps(value, ensure_ascii=False),
                 "2026-10-30T00:00:00+00:00"),
            )
    finally:
        conn.close()


def test_rescan_outside_private_root_is_403(client, auth, env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _set_private_dir(env, str(outside))

    response = _rescan(client, auth)
    assert response.status_code == 403
    assert response.json()["code"] == "PATH_FORBIDDEN"


def test_rescan_missing_dir_is_400(client, auth, env):
    _set_private_dir(env, str(env.data_dir / "private" / "nope"))

    response = _rescan(client, auth)
    assert response.status_code == 400
    assert response.json()["code"] == "PRIVATE_DIR_INVALID"


def test_rescan_requires_origin(client, auth):
    assert client.post("/api/private/rescan", json={},
                       headers=auth).status_code == 403


# --------------------------------------------- PUT 保存 private_dir 后重扫（回归）
def test_put_private_dir_then_rescan_round_trips(client, auth, env):
    """回归：`PUT /api/settings` 落库的是**项目根相对**形式（MODULES §2.5），
    `rescan` 必须能把它读回来——否则"设置页保存私有目录 → 立即重扫"会 400。

    修复前：`write_settings` 存 `data/private`，`rescan` 走 `resolve_private_root`
    的严格分支，把相对路径当非法输入 → 400 `PRIVATE_DIR_INVALID`。
    """
    target = env.private_dir / "notes"
    response = client.put("/api/settings", json={"private_dir": str(target)},
                          headers={**auth, **ORIGIN})
    assert response.status_code == 200
    stored = response.json()["private_dir"]
    assert not stored.startswith(("C:", "D:", "/"))      # 只回显项目根相对形式

    rescan = client.post("/api/private/rescan", json={}, headers={**auth, **ORIGIN})
    assert rescan.status_code == 200, rescan.text
    body = rescan.json()
    assert body["root"] == stored
    assert body["scanned"] == 2                          # notes/dp.md + notes/nested/ok.md
    assert body["added"] == 2


def test_display_root_keeps_out_of_project_roots_resolvable(tmp_path, monkeypatch):
    """根在项目外时 `display_root` 退回绝对路径，保证落库值仍可被读回。

    直接把 `PROJECT_ROOT` 挪到一个**不是父目录**的位置来触发回落分支
    （不能靠 tmp_path 自然落在项目外：验收跑 pytest 时 `--basetemp` 就在项目内）。
    """
    inside = tmp_path / "data" / "private"
    inside.mkdir(parents=True)
    monkeypatch.setattr(core_config, "PROJECT_ROOT", tmp_path / "not-a-parent")

    shown = private_scanner.display_root(inside)

    assert Path(shown).is_absolute()
    assert Path(shown).resolve() == inside.resolve()


# ------------------------------------------------------------- 私有媒体
def test_media_private_is_served_without_token(client, env):
    response = client.get("/media/private/img/pic.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_BYTES
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private" in response.headers["cache-control"]


def test_media_private_head_has_content_length(client):
    response = client.head("/media/private/img/pic.png")
    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(PNG_BYTES))
    assert response.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/app.db",
        "..%2f..%2fapp.db",
        "%2e%2e%2f%2e%2e%2fapp.db",
        "img/%2e%2e/%2e%2e/app.db",
    ],
)
def test_media_private_rejects_traversal(client, path):
    response = client.get("/media/private/" + path)
    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_NOT_FOUND"


def test_media_private_rejects_non_whitelisted_extension(client):
    response = client.get("/media/private/notes/dp.md")
    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_NOT_FOUND"


def test_media_private_missing_file_is_404(client, env):
    response = client.get("/media/private/img/nope.png")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "MEDIA_NOT_FOUND"
    assert body["detail"]["path"] == "img/nope.png"
    assert str(env.data_dir) not in json.dumps(body, ensure_ascii=False)


def test_media_private_does_not_fall_back_to_content_root(client, env):
    """/media/private/* 的根固定 data/private，不能回落 content/。"""
    assert (env.content_dir / "img" / "content-only.png").is_file()
    response = client.get("/media/private/img/content-only.png")
    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_NOT_FOUND"


def test_media_private_post_is_405_with_method_code(client):
    response = client.post("/media/private/img/pic.png")

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]


def test_media_private_rejects_cross_site(client):
    blocked = client.get("/media/private/img/pic.png",
                         headers={"Sec-Fetch-Site": "cross-site"})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "CROSS_SITE"

    allowed = client.get("/media/private/img/pic.png",
                         headers={"Sec-Fetch-Site": "same-origin"})
    assert allowed.status_code == 200
