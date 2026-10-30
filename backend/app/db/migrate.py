"""schema 迁移框架（需求文档 §5.2）。

四条铁律的落点：
1. 脚本只增不改 —— 按文件名 0001_ / 0002_ 顺序发现，已发布的文件不再编辑；
2. 迁移前 wal_checkpoint(TRUNCATE) + 全量备份到 data/backups/，保留最近 3 份；
3. 整个迁移在一个事务里，失败 rollback 并抛 MigrationError（调用方退出码非 0）；
4. user_version 高于程序里的最大版本 → 拒绝启动（SchemaTooNewError）。

不用 Alembic：单机 SQLite 用不上多分支与 autogenerate（§5.2）。
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import util as importlib_util
from pathlib import Path

from .connection import connect

log = logging.getLogger("pgassistant.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_BACKUP_KEEP = 3
DAILY_BACKUP_KEEP = 7


class MigrationError(RuntimeError):
    """迁移失败：结构已回滚，需要人看一眼备份。"""


class SchemaTooNewError(RuntimeError):
    """库比程序新（用户降级了），按 §5.2 铁律 4 拒绝启动。"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    def load(self):
        spec = importlib_util.spec_from_file_location(
            "pgassistant_migration_%04d" % self.version, self.path
        )
        if spec is None or spec.loader is None:
            raise MigrationError("无法加载迁移脚本: %s" % self.path)
        module = importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def discover() -> list[Migration]:
    """扫描 migrations/ 下的 000x_*.py，按版本号排序。"""
    items: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.py")):
        try:
            version = int(path.name[:4])
        except ValueError:                       # 命名不合规的文件直接忽略
            log.warning("跳过不合规的迁移文件: %s", path.name)
            continue
        items.append(Migration(version, path.stem[5:], path))

    versions = [m.version for m in items]
    if len(set(versions)) != len(versions):
        raise MigrationError("迁移版本号重复: %s" % versions)
    items.sort(key=lambda m: m.version)
    return items


def target_version(migrations: list[Migration] | None = None) -> int:
    items = discover() if migrations is None else list(migrations)
    return items[-1].version if items else 0


def current_version(conn) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def wal_checkpoint(conn) -> None:
    """备份前把 WAL 并回主库，否则拷出来的文件缺最近的事务。"""
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    except sqlite3.DatabaseError as exc:          # 只读库/锁冲突时退化为直接拷贝
        log.warning("wal_checkpoint 失败（继续备份）: %s", exc)


def backup_database(db_path, backup_dir, tag: str = "manual") -> Path | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / ("app-%s-%s.db" % (tag, stamp))
    shutil.copy2(db_path, target)
    return target


def prune_backups(backup_dir, pattern: str, keep: int) -> list[Path]:
    """按修改时间保留最近 keep 份，其余的删掉（§5.2 备份保留策略）。"""
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    files = sorted(
        backup_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old in files[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError as exc:
            log.warning("清理旧备份失败 %s: %s", old, exc)
    return removed


def daily_backup(db_path, backup_dir, keep: int = DAILY_BACKUP_KEEP) -> Path | None:
    """每日首次启动留一份全量备份，一天只做一次。

    文件名固定为 app-daily-YYYYMMDD.db：靠「同名已存在」判定今天做过，
    不要在这里再拼时间戳，否则每次启动都会多一份。
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.exists():
        return None
    target = backup_dir / ("app-daily-%s.db" % datetime.now().strftime("%Y%m%d"))
    if target.exists():
        return None

    conn = connect(db_path)
    try:
        wal_checkpoint(conn)
    finally:
        conn.close()
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, target)
    prune_backups(backup_dir, "app-daily-*.db", keep)
    return target


def ensure_schema(db_path, backup_dir=None, migrations: list[Migration] | None = None,
                  make_backup: bool = True) -> dict:
    """把库升到程序期望的版本；幂等，已经是目标版本时什么都不做。

    返回 {from, to, applied, backup, existed}，供 bootstrap/启动日志打印。
    """
    db_path = Path(db_path)
    items = discover() if migrations is None else sorted(migrations, key=lambda m: m.version)
    target = target_version(items)
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent / "backups"

    existed = db_path.exists() and db_path.stat().st_size > 0
    conn = connect(db_path)
    try:
        version = current_version(conn)
        if version > target:
            raise SchemaTooNewError(
                "数据库 schema v%d 高于程序支持的 v%d；"
                "请升级程序，或从 data/backups/ 恢复旧库（§5.2 铁律 4）" % (version, target)
            )
        pending = [m for m in items if m.version > version]
        if not pending:
            return {
                "from": version, "to": version, "applied": [],
                "backup": None, "existed": existed,
            }

        backup_path = None
        if make_backup and existed:
            wal_checkpoint(conn)
            backup_path = backup_database(db_path, backup_dir, tag="before-v%d" % version)
            prune_backups(backup_dir, "app-before-v*-*.db", MIGRATION_BACKUP_KEEP)
            log.info("迁移前备份: %s", backup_path)

        applied: list[int] = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            for migration in pending:
                module = migration.load()
                apply_fn = getattr(module, "apply", None)
                if not callable(apply_fn):
                    raise MigrationError(
                        "迁移 %s 缺少 apply(conn)" % migration.path.name
                    )
                apply_fn(conn)
                # PRAGMA 不支持参数绑定；version 来自文件名解析出的 int，无注入面
                conn.execute("PRAGMA user_version = %d" % int(migration.version))
                applied.append(migration.version)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            hint = ("迁移失败，结构已回滚。迁移前备份: %s" % backup_path
                    if backup_path else "迁移失败，结构已回滚（本次是新建库，无备份）")
            raise MigrationError("%s；原因: %s" % (hint, exc)) from exc

        final = current_version(conn)
        if final != applied[-1]:
            raise MigrationError(
                "迁移后 user_version=%d，期望 %d" % (final, applied[-1])
            )
        return {
            "from": version, "to": final, "applied": applied,
            "backup": str(backup_path) if backup_path else None,
            "existed": existed,
        }
    finally:
        conn.close()


def describe(db_path) -> dict:
    """给 CLI 用的只读状态：库在不在、当前版本、程序版本。"""
    db_path = Path(db_path)
    target = target_version()
    if not db_path.exists():
        return {"db": str(db_path), "exists": False, "version": None, "target": target}
    conn = connect(db_path)
    try:
        return {
            "db": str(db_path), "exists": True,
            "version": current_version(conn), "target": target,
        }
    finally:
        conn.close()
