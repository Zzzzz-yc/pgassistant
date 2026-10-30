#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""security_selfcheck.py — M-1 鉴权与迁移的现场自检。

不启动服务、不写项目 data/：用 TestClient 跑一遍真实 app，
再用临时目录演一遍「老库自动迁移」。
验收口径见需求文档 §16「安全与韧性（v1.1 新增）」。

用法:
  python backend/scripts/security_selfcheck.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT / ".deps"))

from fastapi.testclient import TestClient                        # noqa: E402

from app import main                                             # noqa: E402
from app.api import deps                                         # noqa: E402
from app.db import migrate                                       # noqa: E402

BASE = "http://127.0.0.1:8000"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, note: str = "") -> None:
    results.append((name, ok, note))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, "  " + note if note else ""))


def code_of(response) -> str:
    try:
        return response.json().get("code", "")
    except ValueError:
        return ""


def page_token(client: TestClient) -> str:
    page = client.get("/")
    marker = 'name="x-app-token" content="'
    start = page.text.index(marker) + len(marker)
    return page.text[start:page.text.index('"', start)]


def check_security(db_ready: bool) -> None:
    with TestClient(main.app, base_url=BASE) as client:
        token = page_token(client)
        check("页面注入了 meta token", len(token) >= 32, "长度 %d" % len(token))

        resp = client.get("/api/health")
        check("/api/health 免 token", resp.status_code == 200, "状态 %d" % resp.status_code)

        resp = client.get("/api/taxonomy")
        check("不带 X-App-Token 被拒", resp.status_code == 403 and code_of(resp) == "TOKEN_MISSING",
              "%d %s" % (resp.status_code, code_of(resp)))

        resp = client.get("/api/taxonomy", headers={"X-App-Token": token})
        expected = 200 if db_ready else 503
        check("带 token 可读接口", resp.status_code == expected,
              "%d（期望 %d）" % (resp.status_code, expected))

        forged = TestClient(main.app, base_url="http://evil.example.com")
        resp = forged.get("/api/taxonomy", headers={"X-App-Token": token})
        check("伪造 Host 返回 403", resp.status_code == 403 and code_of(resp) == "BAD_HOST",
              "%d %s" % (resp.status_code, code_of(resp)))

        resp = client.post("/api/plans",
                           headers={"X-App-Token": token, "Origin": "http://evil.example.com"},
                           json={})
        check("跨站 POST 返回 403", resp.status_code == 403 and code_of(resp) == "BAD_ORIGIN",
              "%d %s" % (resp.status_code, code_of(resp)))

        resp = client.post("/api/plans", headers={"X-App-Token": token}, json={})
        check("写操作缺 Origin 返回 403",
              resp.status_code == 403 and code_of(resp) == "MISSING_ORIGIN",
              "%d %s" % (resp.status_code, code_of(resp)))

        resp = client.get("/api/taxonomy",
                          headers={"X-App-Token": token, "Sec-Fetch-Site": "cross-site"})
        check("跨站 GET（Sec-Fetch-Site）返回 403",
              resp.status_code == 403 and code_of(resp) == "CROSS_SITE",
              "%d %s" % (resp.status_code, code_of(resp)))


def check_migration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "data" / "app.db"
        db.parent.mkdir(parents=True)
        schema = migrate.MIGRATIONS_DIR.parent / "schema_v1.sql"

        old = sqlite3.connect(str(db))
        try:
            old.executescript(schema.read_text(encoding="utf-8"))
            old.commit()
        finally:
            old.close()

        result = migrate.ensure_schema(db)
        backup = result.get("backup")
        check("老库（user_version=0）自动迁移", result["applied"] == [1] and result["to"] == 1,
              "v%s → v%s" % (result["from"], result["to"]))
        check("迁移前留下备份", bool(backup) and Path(backup).exists(),
              str(backup) if backup else "-")

        again = migrate.ensure_schema(db)
        check("再次启动不重复迁移", again["applied"] == [] and again["backup"] is None, "幂等")

        conn = sqlite3.connect(str(db))
        try:
            conn.execute("PRAGMA user_version = 99")
            conn.commit()
        finally:
            conn.close()
        try:
            migrate.ensure_schema(db)
            refused = False
        except migrate.SchemaTooNewError:
            refused = True
        check("库比程序新时拒绝启动", refused)


def main_check() -> int:
    db_ready = deps.DB_PATH.exists()
    print("M-1 自检（%s）" % ("数据库已就绪" if db_ready else "数据库未建，相关项按 503 判"))
    check_security(db_ready)
    check_migration()
    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print("未通过 %d 项：%s" % (len(failed), "；".join(failed)))
        return 1
    print("全部通过（%d 项）" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main_check())
