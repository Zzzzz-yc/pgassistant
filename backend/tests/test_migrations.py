"""§5.2 迁移框架的回归测试。

验收对应需求文档 §16：「旧版本 data/app.db 启动新版本 → 自动迁移成功且留下备份」。

版本基线（2026-10-30 起）：最新 schema = **v2**。
v2 由 `migrations/0002_soft_delete.py` 落地（给 plans / plan_items 补 is_deleted，§8.1 软删除）；
`schema_v1.sql` 仍是 v1 的历史快照，新库路径 = v1 建表 + 0002 升级。
下面凡是断言版本的数值都跟 v2 对齐；再新增迁移时**只改数值、不删用例、不放宽安全断言**。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.db import migrate
from app.db.connection import connect

SCHEMA_V1 = migrate.MIGRATIONS_DIR.parent / "schema_v1.sql"
LATEST = 2


def _tables(db_path) -> set[str]:
    conn = connect(db_path)
    try:
        return {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def _version(db_path) -> int:
    conn = connect(db_path)
    try:
        return migrate.current_version(conn)
    finally:
        conn.close()


def _make_legacy_db(db_path) -> None:
    """复刻 M1 的老库：schema_v1 已建好，但 user_version 还是 0。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_V1.read_text(encoding="utf-8"))
        conn.execute(
            """INSERT INTO resources
               (slug, title, summary, body_md, stack, category, level, type,
                file_path, content_hash, created_at, updated_at)
               VALUES ('old/one', '旧条目', '', '正文', 'cs-basics', 'misc',
                       'beginner', 'article', 'content/old.md', 'h', 'x', 'x')"""
        )
        conn.commit()
    finally:
        conn.close()
    assert _version(db_path) == 0


# ------------------------------------------------------------------ 新建库
def test_fresh_db_gets_schema_v1(tmp_path):
    """新库从 v0 一路升到最新版（v1 建表 + 0002 升级）。"""
    db = tmp_path / "data" / "app.db"
    result = migrate.ensure_schema(db)

    assert result["applied"] == [1, 2]
    assert result["from"] == 0 and result["to"] == LATEST
    assert result["backup"] is None            # 新建库没什么可备份
    assert _version(db) == LATEST

    tables = _tables(db)
    for expected in ("resources", "plans", "plan_items", "index_state", "resources_fts"):
        assert expected in tables


def test_second_run_is_a_no_op(tmp_path):
    db = tmp_path / "data" / "app.db"
    migrate.ensure_schema(db)

    again = migrate.ensure_schema(db)
    assert again["applied"] == []
    assert again["to"] == LATEST
    assert again["backup"] is None


# ------------------------------------------- v2：软删除列与幂等（0002）
def test_v1_db_upgrades_to_v2_with_soft_delete_columns(tmp_path):
    """M1 时期的 v1 库（user_version=1）升到 v2：补列、建索引、留备份。"""
    db = tmp_path / "data" / "app.db"
    _make_legacy_db(db)                       # 表结构 = v1
    conn = connect(db)
    try:
        conn.execute("PRAGMA user_version = 1")   # 但版本已经是 v1（M1 发布态）
        conn.commit()
    finally:
        conn.close()

    result = migrate.ensure_schema(db)

    assert result["from"] == 1 and result["to"] == LATEST
    assert result["applied"] == [2]           # 只该跑 0002
    assert result["backup"] is not None       # 结构性变更必须留备份
    assert Path(result["backup"]).exists()
    assert _version(db) == LATEST

    conn = connect(db)
    try:
        for table in ("plans", "plan_items"):
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
            assert "is_deleted" in cols
            row = conn.execute("SELECT is_deleted FROM %s LIMIT 0" % table).description
            assert row is not None
        indexes = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert {"idx_plans_deleted", "idx_items_deleted"} <= indexes
        # 老数据不能被这次迁移弄丢或改成"已删除"
        assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1
    finally:
        conn.close()


def test_0002_is_idempotent_when_applied_twice(tmp_path):
    """手工重跑 0002 不应报错，也不应重复加列/建索引（幂等）。"""
    db = tmp_path / "data" / "app.db"
    migrate.ensure_schema(db)

    module = [m for m in migrate.discover() if m.version == 2][0].load()
    conn = connect(db)
    try:
        module.apply(conn)                    # 第二次
        module.apply(conn)                    # 第三次
        conn.commit()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan_items)")}
    finally:
        conn.close()

    assert "is_deleted" in cols
    assert _version(db) == LATEST


# --------------------------------------------------- 老库自动迁移 + 留备份
def test_legacy_db_migrates_and_keeps_backup(tmp_path):
    db = tmp_path / "data" / "app.db"
    _make_legacy_db(db)

    result = migrate.ensure_schema(db)

    assert result["from"] == 0 and result["to"] == LATEST
    assert result["applied"] == [1, 2]
    assert result["backup"] is not None
    backup = db.parent / "backups" / Path(result["backup"]).name
    assert backup.exists()
    assert backup.parent == db.parent / "backups"

    # 备份里必须还是迁移前的样子（含那条旧数据、user_version=0）
    old = sqlite3.connect(str(backup))
    try:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 0
        assert old.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1
    finally:
        old.close()

    # 迁移后数据不丢
    conn = connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1
    finally:
        conn.close()


def test_backup_pruning_keeps_latest(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(5):
        path = backup_dir / ("app-before-v0-2026010%d-000000.db" % i)
        path.write_bytes(b"x")
        os.utime(path, (1_000_000 + i, 1_000_000 + i))

    removed = migrate.prune_backups(backup_dir, "app-before-v*-*.db", keep=3)

    assert len(removed) == 2
    assert len(list(backup_dir.glob("app-before-v*-*.db"))) == 3


# --------------------------------------------------------------- 拒绝降级
def test_db_newer_than_program_is_refused(tmp_path):
    db = tmp_path / "data" / "app.db"
    migrate.ensure_schema(db)
    conn = connect(db)
    try:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(migrate.SchemaTooNewError):
        migrate.ensure_schema(db)
    assert _version(db) == 99                  # 一个字都不许改


# --------------------------------------------------------- 失败回滚不留半成品
def test_failed_migration_rolls_back(tmp_path):
    db = tmp_path / "data" / "app.db"
    migrate.ensure_schema(db)

    boom = tmp_path / "0003_boom.py"
    boom.write_text(
        "def apply(conn):\n"
        "    conn.execute('CREATE TABLE should_not_survive (id INTEGER)')\n"
        "    raise RuntimeError('故意失败')\n",
        encoding="utf-8",
    )
    migrations = migrate.discover() + [migrate.Migration(3, "boom", boom)]

    with pytest.raises(migrate.MigrationError) as excinfo:
        migrate.ensure_schema(db, migrations=migrations, make_backup=False)

    assert "回滚" in str(excinfo.value)
    assert _version(db) == LATEST
    assert "should_not_survive" not in _tables(db)


def test_migration_without_apply_is_rejected(tmp_path):
    db = tmp_path / "data" / "app.db"
    migrate.ensure_schema(db)
    empty = tmp_path / "0003_empty.py"
    empty.write_text("VERSION = 3\n", encoding="utf-8")

    with pytest.raises(migrate.MigrationError):
        migrate.ensure_schema(
            db,
            migrations=migrate.discover() + [migrate.Migration(3, "empty", empty)],
            make_backup=False,
        )


# ------------------------------------------------------------------ 工具函数
def test_split_statements_handles_schema():
    from app.db.sqlscript import split_statements

    statements = split_statements(SCHEMA_V1.read_text(encoding="utf-8"))
    assert len(statements) > 10
    assert all(s.rstrip().endswith(";") for s in statements)
    assert any("CREATE VIRTUAL TABLE" in s for s in statements)


def test_describe_reports_status(tmp_path):
    db = tmp_path / "data" / "app.db"
    assert migrate.describe(db)["exists"] is False

    migrate.ensure_schema(db)
    info = migrate.describe(db)
    assert info["exists"] is True
    assert info["version"] == LATEST
    assert info["target"] == LATEST
