"""schema v2：给 `plans` 与 `plan_items` 补 `is_deleted`（软删除）。

为什么要有这一版：
- §8.1「条目软删除（保留打勾历史，避免删了又加回来进度清零）」
- §11「DELETE /api/plans/{id} 软删除」
- 而 v1 建表时这两张表都没有 `is_deleted`，只有 `resources` 有 → 规格缺口，补列而不是改需求。

写法约束（照 0001_initial.py，逐条对齐）：
1. **只增不改**：`schema_v1.sql` 是历史快照，本文件不碰它；新库路径是「v1 建表 + 0002 升级」。
2. **幂等**：先用 `PRAGMA table_info` 查列，缺了才 `ALTER TABLE`；索引用 `IF NOT EXISTS`。
   所以即使某个库被手工补过列，重跑也不会炸。
3. **不加 CHECK / 不改已有列**：SQLite 给已有表加 CHECK 要重建表，风险远大于收益；
   `is_deleted` 的取值（0/1）由应用层与 Pydantic 模型保证。
4. 事务与备份**不在这里做**：`migrate.ensure_schema` 负责 `wal_checkpoint(TRUNCATE)`、
   全量备份 `data/backups/app-before-v1-<时间戳>.db`、单事务执行、失败 `rollback` 并抛
   `MigrationError`（§5.2 四条铁律）。

回滚方式：迁移失败时框架整体回滚，结构不会留半成品；要回到 v1 就从迁移前备份恢复
`data/app.db`，再把 `PRAGMA user_version` 置回 1（或用备份文件整个替换）。
SQLite 3.35+ 支持 `DROP COLUMN`，但本项目不提供降级脚本——降级一律走备份恢复（§5.2 铁律 4）。
"""
from __future__ import annotations

VERSION = 2

# (表名, 索引名, 索引列) —— 表名与列名是常量，不存在注入面
SOFT_DELETE_TARGETS = (
    ("plans", "idx_plans_deleted", "is_deleted, status"),
    ("plan_items", "idx_items_deleted", "is_deleted, stage_id, order_index"),
)


def _columns(conn, table: str) -> set[str]:
    """PRAGMA 不支持参数绑定；table 来自上面的常量元组。"""
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def apply(conn) -> None:
    for table, index, index_columns in SOFT_DELETE_TARGETS:
        if "is_deleted" not in _columns(conn, table):
            conn.execute(
                "ALTER TABLE %s ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0" % table
            )
        # 索引放在列补齐之后：`IF NOT EXISTS` 让重复执行安全
        conn.execute(
            "CREATE INDEX IF NOT EXISTS %s ON %s(%s)" % (index, table, index_columns)
        )
