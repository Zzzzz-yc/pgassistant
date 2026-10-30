"""计划服务（M3）：三层结构 CRUD、进度、标题解析。

契约：`docs/contracts/MODULES.md` §2.3（签名与省缺规则）、`API.md` §3.8–3.14、
`DB.md` §6–§8。服务层只抛领域异常，不构造 HTTP 响应；映射在 `api/plans.py`。

七条省缺规则（验收逐条查）：
1. 每个写函数在自己的 `with conn:` 事务内完成；
2. `version` 校验先于任何写入；
3. `order_index` 为 None → `max + 1000`（空集合 1000，取 max 时过滤软删除）；
4. 拖拽取相邻中值；中值不足 1 或与兄弟重复时整段按 1000 重排；
5. 阶段硬删除连带条目，`affected_item_ids` 列全；
6. 条目软删除：保留行，但从计划树的 `items[]` 里消失；
7. 所有读计划树的函数一律 `WHERE is_deleted = 0`。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..schemas.models import (
    ItemCreate,
    ItemPatch,
    ItemRead,
    PlanCreate,
    PlanItemStatus,
    PlanMutationResult,
    PlanPatch,
    PlanProgress,
    PlanRead,
    PlanStatus,
    PlanSummary,
    StageCreate,
    StagePatch,
    StageProgress,
    StageRead,
)
from . import plan_validate
from .indexer import now_iso

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ 异常
class PlanNotFound(Exception):
    """计划不存在或已软删除（→ 404 PLAN_NOT_FOUND）。"""

    def __init__(self, plan_id: int) -> None:
        self.plan_id = plan_id
        super().__init__("plan %s not found" % plan_id)


class StageNotFound(Exception):
    """阶段不存在（或所属计划已软删除）→ 404 STAGE_NOT_FOUND。"""

    def __init__(self, stage_id: int) -> None:
        self.stage_id = stage_id
        super().__init__("stage %s not found" % stage_id)


class ItemNotFound(Exception):
    """条目不存在或已软删除 → 404 ITEM_NOT_FOUND。"""

    def __init__(self, item_id: int) -> None:
        self.item_id = item_id
        super().__init__("item %s not found" % item_id)


class VersionConflict(Exception):
    """乐观锁版本不匹配（→ 409 VERSION_CONFLICT）。`current` 是最新实体。"""

    def __init__(self, current, expected_version: int, actual_version: int) -> None:
        self.current = current
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            "version conflict: expected %s, actual %s"
            % (expected_version, actual_version)
        )


# ------------------------------------------------------------------ 读
def list_plans(conn, status: str | None = None) -> list[PlanSummary]:
    """计划列表。

    `WHERE is_deleted = 0` 与 `status` 过滤正交（先滤删除，再滤状态）；
    计划数量是个位数（D12），不分页。
    """
    if status in (None, "", "all"):
        rows = conn.execute(
            "SELECT * FROM plans WHERE is_deleted = 0 ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM plans WHERE is_deleted = 0 AND status = ?"
            " ORDER BY updated_at DESC, id DESC",
            (status,),
        ).fetchall()

    summaries: list[PlanSummary] = []
    for row in rows:
        plan = _build_plan(conn, row)
        progress = plan.progress
        if progress is not None:
            progress = progress.model_copy(update={"stages": []})
        summaries.append(
            PlanSummary(
                id=plan.id,
                title=plan.title,
                goal=plan.goal,
                start_level=plan.start_level,
                weekly_hours=plan.weekly_hours,
                horizon_weeks=plan.horizon_weeks,
                status=plan.status,
                origin=plan.origin,
                version=plan.version,
                updated_at=plan.updated_at,
                is_deleted=0,
                progress=progress,
            )
        )
    return summaries


def active_plan_id(conn) -> int | None:
    """唯一 active 计划的 id；没有则 None。软删除的计划永不返回。"""
    row = conn.execute(
        "SELECT id FROM plans WHERE is_deleted = 0 AND status = 'active'"
        " ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def get_plan(conn, plan_id: int) -> PlanRead:
    """计划详情：三层结构 + 进度 + 实时标题。已软删除 → PlanNotFound。"""
    row = _fetch_plan_row(conn, plan_id)
    return _build_plan(conn, row)


# ------------------------------------------------------------------ 计划写
def create_plan(conn, payload: PlanCreate) -> PlanRead:
    """保存计划（模板 / AI / 手动三条路径共用）。

    落库前先过 `plan_validate`（白名单、条目数、总时长、难度顺序）；
    白名单从 `resources` 表实时取（`is_deleted = 0`）。
    """
    allowed, level_by_slug, duration_by_slug = _resource_meta(conn)
    draft = payload.draft
    plan_validate.fill_estimates(draft, duration_by_slug)
    cleaned, warnings = plan_validate.validate_draft(
        draft,
        allowed_slugs=allowed,
        level_by_slug=level_by_slug,
        target_minutes=draft.weeks * draft.weekly_hours * 60.0,
    )
    # 白名单丢弃、估时缺失都只进 warnings（§7.6），不拦保存；但必须留痕，
    # 否则"AI 编了个不存在的 slug"会被静默吞掉，排查时无从下手。
    for warning in warnings:
        log.warning("计划草稿校验: %s", warning)

    now = now_iso()
    status = PlanStatus.ACTIVE.value if payload.set_active else PlanStatus.ARCHIVED.value
    with conn:
        if payload.set_active:
            _archive_other_active(conn, keep_id=None, now=now)
        cursor = conn.execute(
            "INSERT INTO plans (title, goal, start_level, weekly_hours, horizon_weeks,"
            " status, origin, version, created_at, updated_at, is_deleted)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0)",
            (
                cleaned.title,
                cleaned.goal,
                payload.start_level,
                cleaned.weekly_hours,
                cleaned.weeks,
                status,
                payload.origin,
                now,
                now,
            ),
        )
        plan_id = int(cursor.lastrowid)
        for stage_index, stage in enumerate(cleaned.stages, start=1):
            cursor = conn.execute(
                "INSERT INTO plan_stages (plan_id, title, description, weeks, order_index)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    plan_id,
                    stage.title,
                    stage.description,
                    stage.weeks,
                    stage_index * _ORDER_STEP,
                ),
            )
            stage_id = int(cursor.lastrowid)
            for item_index, item in enumerate(stage.items, start=1):
                conn.execute(
                    "INSERT INTO plan_items (stage_id, title, resource_slug, note,"
                    " est_minutes, order_index, status, depends_on, completed_at,"
                    " version, is_deleted)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'todo', NULL, NULL, 1, 0)",
                    (
                        stage_id,
                        item.title,
                        item.resource_slug,
                        item.note,
                        item.est_minutes,
                        item_index * _ORDER_STEP,
                    ),
                )
    row = _fetch_plan_row(conn, plan_id)
    return _build_plan(conn, row)


def patch_plan(conn, plan_id: int, patch: PlanPatch) -> PlanRead:
    """改名 / 归档 / 激活。`version` = plans.version，先校验后写。"""
    now = now_iso()
    with conn:
        row = _fetch_plan_row(conn, plan_id)
        _check_plan_version(conn, row, patch.version)

        updates: list[str] = []
        params: list[Any] = []
        if patch.title is not None:
            updates.append("title = ?")
            params.append(patch.title)
        if patch.goal is not None:
            updates.append("goal = ?")
            params.append(patch.goal)

        effective_status = _effective_status(patch)
        if effective_status == PlanStatus.ACTIVE.value:
            # 唯一 active：同一事务里先归档其它计划（§8.1）
            _archive_other_active(conn, keep_id=plan_id, now=now)
        if effective_status is not None:
            updates.append("status = ?")
            params.append(effective_status)

        updates.extend(["version = version + 1", "updated_at = ?"])
        params.append(now)
        params.append(plan_id)
        conn.execute(
            "UPDATE plans SET %s WHERE id = ?" % ", ".join(updates), params
        )
    return _build_plan(conn, _fetch_plan_row(conn, plan_id))


def delete_plan(conn, plan_id: int, version: int) -> PlanRead:
    """软删除计划：只置 `is_deleted = 1` 并 +1 版本，不动阶段/条目/打勾历史。"""
    now = now_iso()
    with conn:
        row = _fetch_plan_row(conn, plan_id)
        _check_plan_version(conn, row, version)
        conn.execute(
            "UPDATE plans SET is_deleted = 1, version = version + 1, updated_at = ?"
            " WHERE id = ?",
            (now, plan_id),
        )
    deleted = _fetch_plan_row(conn, plan_id, include_deleted=True)
    return _plan_read_base(deleted, stages=[], progress=None)


def restore_plan(conn, plan_id: int, version: int) -> PlanRead:
    """撤销软删除。**预留**：§11 未定义端点，本期不接线（仅供脚本/测试调用）。"""
    now = now_iso()
    with conn:
        row = _fetch_plan_row(conn, plan_id, include_deleted=True)
        actual = int(row["version"])
        if actual != version:
            raise VersionConflict(_plan_read_base(row, stages=[], progress=None),
                                  version, actual)
        conn.execute(
            "UPDATE plans SET is_deleted = 0, version = version + 1, updated_at = ?"
            " WHERE id = ?",
            (now, plan_id),
        )
    return _build_plan(conn, _fetch_plan_row(conn, plan_id))


# ------------------------------------------------------------------ 阶段写
def add_stage(conn, plan_id: int, payload: StageCreate) -> PlanMutationResult:
    """新增阶段。`order_index=None` → 追加 `max + 1000`。"""
    now = now_iso()
    stage_id: int
    with conn:
        plan_row = _fetch_plan_row(conn, plan_id)
        _check_plan_version(conn, plan_row, payload.version)
        order = payload.order_index
        if order is None:
            order = _next_stage_order(conn, plan_id)
        cursor = conn.execute(
            "INSERT INTO plan_stages (plan_id, title, description, weeks, order_index)"
            " VALUES (?, ?, ?, ?, ?)",
            (plan_id, payload.title, payload.description, payload.weeks, order),
        )
        stage_id = int(cursor.lastrowid)
        _touch_plan(conn, plan_id, now)
        if payload.order_index is not None:
            _normalize_stage_orders(conn, plan_id)
    return PlanMutationResult(
        plan=_build_plan(conn, _fetch_plan_row(conn, plan_id)),
        affected_stage_ids=[stage_id],
        affected_item_ids=[],
    )


def patch_stage(conn, stage_id: int, patch: StagePatch) -> PlanMutationResult:
    """阶段改名 / 改周数 / 拖拽排序。`version` = **所属计划**的 version。"""
    now = now_iso()
    stage_row = _fetch_stage_row(conn, stage_id)
    plan_id = int(stage_row["plan_id"])
    with conn:
        plan_row = _fetch_plan_row(conn, plan_id, missing=StageNotFound(stage_id))
        _check_plan_version(conn, plan_row, patch.version)

        updates: list[str] = []
        params: list[Any] = []
        for field in ("title", "description", "weeks"):
            value = getattr(patch, field)
            if value is not None:
                updates.append("%s = ?" % field)
                params.append(value)
        if patch.order_index is not None:
            updates.append("order_index = ?")
            params.append(patch.order_index)
        if updates:
            params.append(stage_id)
            conn.execute(
                "UPDATE plan_stages SET %s WHERE id = ?" % ", ".join(updates), params
            )
        _touch_plan(conn, plan_id, now)
        if patch.order_index is not None:
            _normalize_stage_orders(conn, plan_id)
    return PlanMutationResult(
        plan=_build_plan(conn, _fetch_plan_row(conn, plan_id)),
        affected_stage_ids=[stage_id],
        affected_item_ids=[],
    )


def delete_stage(conn, stage_id: int, version: int) -> PlanMutationResult:
    """阶段硬删除，连带其下条目（含已软删除的历史条目）。"""
    now = now_iso()
    stage_row = _fetch_stage_row(conn, stage_id)
    plan_id = int(stage_row["plan_id"])
    with conn:
        plan_row = _fetch_plan_row(conn, plan_id, missing=StageNotFound(stage_id))
        _check_plan_version(conn, plan_row, version)
        item_rows = conn.execute(
            "SELECT id FROM plan_items WHERE stage_id = ? ORDER BY id", (stage_id,)
        ).fetchall()
        item_ids = [int(row["id"]) for row in item_rows]
        conn.execute("DELETE FROM plan_items WHERE stage_id = ?", (stage_id,))
        conn.execute("DELETE FROM plan_stages WHERE id = ?", (stage_id,))
        _touch_plan(conn, plan_id, now)
    return PlanMutationResult(
        plan=_build_plan(conn, _fetch_plan_row(conn, plan_id)),
        affected_stage_ids=[stage_id],
        affected_item_ids=item_ids,
    )


# ------------------------------------------------------------------ 条目写
def add_item(conn, payload: ItemCreate) -> PlanMutationResult:
    """新增条目。`stage_id` 必须真实存在且其计划未删除。"""
    now = now_iso()
    item_id: int
    stage_row = _fetch_stage_row(conn, payload.stage_id)
    plan_id = int(stage_row["plan_id"])
    with conn:
        plan_row = _fetch_plan_row(conn, plan_id, missing=StageNotFound(payload.stage_id))
        _check_plan_version(conn, plan_row, payload.version)
        order = payload.order_index
        if order is None:
            order = _next_item_order(conn, payload.stage_id)
        cursor = conn.execute(
            "INSERT INTO plan_items (stage_id, title, resource_slug, note, est_minutes,"
            " order_index, status, depends_on, completed_at, version, is_deleted)"
            " VALUES (?, ?, ?, ?, ?, ?, 'todo', NULL, NULL, 1, 0)",
            (
                payload.stage_id,
                payload.title,
                payload.resource_slug,
                payload.note,
                payload.est_minutes,
                order,
            ),
        )
        item_id = int(cursor.lastrowid)
        _touch_plan(conn, plan_id, now)
        if payload.order_index is not None:
            _normalize_item_orders(conn, payload.stage_id)
    return PlanMutationResult(
        plan=_build_plan(conn, _fetch_plan_row(conn, plan_id)),
        affected_stage_ids=[payload.stage_id],
        affected_item_ids=[item_id],
    )


def patch_item(conn, item_id: int, patch: ItemPatch) -> PlanMutationResult:
    """改条目：状态 / 时长 / 备注 / 跨阶段 / 排序 / 换绑资料。

    `version` = **plan_items.version**（D4）。已软删除的条目一律 ItemNotFound。
    """
    now = now_iso()
    item_row = _fetch_item_row(conn, item_id)
    old_stage_id = int(item_row["stage_id"])
    stage_row = _fetch_stage_row(conn, old_stage_id, missing=ItemNotFound(item_id))
    plan_id = int(stage_row["plan_id"])

    with conn:
        plan_row = _fetch_plan_row(conn, plan_id, missing=ItemNotFound(item_id))
        actual = int(item_row["version"])
        if actual != patch.version:
            raise VersionConflict(_item_read(conn, item_row), patch.version, actual)

        updates: list[str] = []
        params: list[Any] = []
        if patch.title is not None:
            updates.append("title = ?")
            params.append(patch.title)
        if "resource_slug" in patch.model_fields_set:
            updates.append("resource_slug = ?")
            params.append(patch.resource_slug)
        if patch.note is not None:
            updates.append("note = ?")
            params.append(patch.note)
        if patch.est_minutes is not None:
            updates.append("est_minutes = ?")
            params.append(patch.est_minutes)
        if patch.status is not None:
            updates.append("status = ?")
            params.append(patch.status)
            updates.append("completed_at = ?")
            params.append(now if patch.status == PlanItemStatus.DONE.value else None)

        target_stage_id = old_stage_id
        if patch.stage_id is not None and patch.stage_id != old_stage_id:
            target = _fetch_stage_row(conn, patch.stage_id, missing=ItemNotFound(item_id))
            if int(target["plan_id"]) != plan_id:
                raise plan_validate.PlanInvalid(["目标阶段不属于同一计划，不能跨计划移动条目"])
            target_stage_id = patch.stage_id
            updates.append("stage_id = ?")
            params.append(target_stage_id)

        order_given = patch.order_index is not None
        if order_given:
            updates.append("order_index = ?")
            params.append(patch.order_index)
        elif target_stage_id != old_stage_id:
            # 跨阶段且未指定位置 → 追加到目标阶段末尾
            updates.append("order_index = ?")
            params.append(_next_item_order(conn, target_stage_id))

        updates.extend(["version = version + 1"])
        params.append(item_id)
        conn.execute(
            "UPDATE plan_items SET %s WHERE id = ?" % ", ".join(updates), params
        )
        _touch_plan(conn, plan_id, now)
        if order_given:
            _normalize_item_orders(conn, target_stage_id)

    affected_stages = [old_stage_id]
    if target_stage_id != old_stage_id:
        affected_stages.append(target_stage_id)
    return PlanMutationResult(
        plan=_build_plan(conn, _fetch_plan_row(conn, plan_id)),
        affected_stage_ids=affected_stages,
        affected_item_ids=[item_id],
    )


def delete_item(conn, item_id: int, version: int) -> PlanMutationResult:
    """条目软删除：`is_deleted = 1`，条目 version +1、计划 version +1。"""
    now = now_iso()
    item_row = _fetch_item_row(conn, item_id)
    stage_id = int(item_row["stage_id"])
    stage_row = _fetch_stage_row(conn, stage_id, missing=ItemNotFound(item_id))
    plan_id = int(stage_row["plan_id"])

    with conn:
        _fetch_plan_row(conn, plan_id, missing=ItemNotFound(item_id))
        actual = int(item_row["version"])
        if actual != version:
            raise VersionConflict(_item_read(conn, item_row), version, actual)
        conn.execute(
            "UPDATE plan_items SET is_deleted = 1, version = version + 1 WHERE id = ?",
            (item_id,),
        )
        _touch_plan(conn, plan_id, now)
    return PlanMutationResult(
        plan=_build_plan(conn, _fetch_plan_row(conn, plan_id)),
        affected_stage_ids=[stage_id],
        affected_item_ids=[item_id],
    )


# ------------------------------------------------------------------ 进度 / 标题
def compute_progress(plan: PlanRead) -> PlanProgress:
    """计划总进度**按时长加权**；`skipped` 不进分子也不进分母（§8.4）。"""
    stage_progress: list[StageProgress] = []
    done_items = todo_items = skipped_items = 0
    invested_minutes = 0
    total_minutes = 0

    for stage in plan.stages:
        active = [item for item in stage.items if item.is_deleted == 0]
        done = sum(1 for item in active if item.status == PlanItemStatus.DONE.value)
        todo = sum(1 for item in active if item.status == PlanItemStatus.TODO.value)
        skipped = sum(1 for item in active if item.status == PlanItemStatus.SKIPPED.value)
        denominator = done + todo
        percent = round(done / denominator * 100, 1) if denominator else 0.0
        stage_est = sum(
            item.est_minutes
            for item in active
            if item.status != PlanItemStatus.SKIPPED.value
        )
        stage_progress.append(
            StageProgress(
                stage_id=stage.id,
                done=done,
                todo=todo,
                skipped=skipped,
                percent=percent,
                est_minutes=stage_est,
            )
        )
        done_items += done
        todo_items += todo
        skipped_items += skipped
        invested_minutes += sum(
            item.est_minutes for item in active if item.status == PlanItemStatus.DONE.value
        )
        total_minutes += stage_est

    percent = (
        round(invested_minutes / total_minutes * 100, 1) if total_minutes else 0.0
    )
    remaining_weeks = round(plan.horizon_weeks * (1 - percent / 100), 1)
    return PlanProgress(
        percent=percent,
        done_items=done_items,
        todo_items=todo_items,
        skipped_items=skipped_items,
        total_minutes=total_minutes,
        invested_minutes=invested_minutes,
        remaining_weeks=remaining_weeks,
        stages=stage_progress,
    )


def resolve_titles(conn, items: list[ItemRead]) -> None:
    """按 `resource_slug` 批量查最新标题，填 `resolved_title` / `resource_available`。

    资料改名 → 用新标题；资料下架/删除 → `resolved_title=None`，前端回退快照并标灰。
    """
    slugs = sorted({item.resource_slug for item in items if item.resource_slug})
    found: dict[str, dict] = {}
    for start in range(0, len(slugs), 400):
        chunk = slugs[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            "SELECT slug, title, is_deleted FROM resources WHERE slug IN (%s)"
            % placeholders,
            chunk,
        ).fetchall()
        for row in rows:
            found[row["slug"]] = {"title": row["title"], "is_deleted": row["is_deleted"]}

    for item in items:
        if item.resource_slug is None:
            item.resolved_title = None
            item.resource_available = True
            continue
        live = found.get(item.resource_slug)
        if live is None or int(live["is_deleted"]) != 0:
            item.resolved_title = None
            item.resource_available = False
        else:
            item.resolved_title = live["title"]
            item.resource_available = True


# ---------------------------------------------------------------- 内部工具
_ORDER_STEP = 1000


def _fetch_plan_row(conn, plan_id: int, *, include_deleted: bool = False,
                    missing: Exception | None = None) -> sqlite3.Row:
    sql = "SELECT * FROM plans WHERE id = ?"
    params: list[Any] = [plan_id]
    if not include_deleted:
        sql += " AND is_deleted = 0"
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise missing if missing is not None else PlanNotFound(plan_id)
    return row


def _fetch_stage_row(conn, stage_id: int, *, missing: Exception | None = None) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM plan_stages WHERE id = ?", (stage_id,)
    ).fetchone()
    if row is None:
        raise missing if missing is not None else StageNotFound(stage_id)
    return row


def _fetch_item_row(conn, item_id: int, *, include_deleted: bool = False) -> sqlite3.Row:
    """按 id 取条目；默认**在 SQL 层排除软删除行**（MODULES §4.2-6）。

    已软删除的条目对写操作等于不存在：PATCH/DELETE 命中死行一律 ItemNotFound。
    需要读历史行（如审计/恢复脚本）时显式传 `include_deleted=True`。
    """
    sql = "SELECT * FROM plan_items WHERE id = ?"
    if not include_deleted:
        sql += " AND is_deleted = 0"
    row = conn.execute(sql, (item_id,)).fetchone()
    if row is None:
        raise ItemNotFound(item_id)
    return row


def _check_plan_version(conn, plan_row, expected: int) -> None:
    actual = int(plan_row["version"])
    if actual != expected:
        current = _build_plan(conn, plan_row)
        raise VersionConflict(current, expected, actual)


def _effective_status(patch: PlanPatch) -> str | None:
    """`set_active` 是 `status` 的快捷方式：True → active，False → archived。"""
    if patch.set_active is True:
        return PlanStatus.ACTIVE.value
    if patch.set_active is False:
        return PlanStatus.ARCHIVED.value
    if patch.status is not None:
        return str(patch.status)
    return None


def _archive_other_active(conn, keep_id: int | None, now: str) -> None:
    """把其它 active 计划归档；状态变了就是一次写，version 与 updated_at 一起 +1。"""
    if keep_id is None:
        conn.execute(
            "UPDATE plans SET status = 'archived', version = version + 1,"
            " updated_at = ? WHERE is_deleted = 0 AND status = 'active'",
            (now,),
        )
    else:
        conn.execute(
            "UPDATE plans SET status = 'archived', version = version + 1,"
            " updated_at = ? WHERE is_deleted = 0 AND status = 'active' AND id <> ?",
            (now, keep_id),
        )


def _touch_plan(conn, plan_id: int, now: str) -> None:
    conn.execute(
        "UPDATE plans SET version = version + 1, updated_at = ? WHERE id = ?",
        (now, plan_id),
    )


def _next_stage_order(conn, plan_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(order_index) AS m FROM plan_stages WHERE plan_id = ?", (plan_id,)
    ).fetchone()
    return (int(row["m"]) if row and row["m"] is not None else 0) + _ORDER_STEP


def _next_item_order(conn, stage_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(order_index) AS m FROM plan_items"
        " WHERE stage_id = ? AND is_deleted = 0",
        (stage_id,),
    ).fetchone()
    return (int(row["m"]) if row and row["m"] is not None else 0) + _ORDER_STEP


def _normalize_stage_orders(conn, plan_id: int) -> None:
    rows = conn.execute(
        "SELECT id, order_index FROM plan_stages WHERE plan_id = ?"
        " ORDER BY order_index, id",
        (plan_id,),
    ).fetchall()
    if not _needs_renumber(rows):
        return
    for index, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE plan_stages SET order_index = ? WHERE id = ?",
            (index * _ORDER_STEP, row["id"]),
        )


def _normalize_item_orders(conn, stage_id: int) -> None:
    rows = conn.execute(
        "SELECT id, order_index FROM plan_items"
        " WHERE stage_id = ? AND is_deleted = 0 ORDER BY order_index, id",
        (stage_id,),
    ).fetchall()
    if not _needs_renumber(rows):
        return
    for index, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE plan_items SET order_index = ? WHERE id = ?",
            (index * _ORDER_STEP, row["id"]),
        )


def _needs_renumber(rows: list[sqlite3.Row]) -> bool:
    """中值不足 1 或与兄弟重复 → 整段重排（规则 4）。"""
    seen: set[int] = set()
    for row in rows:
        value = int(row["order_index"])
        if value < 1 or value in seen:
            return True
        seen.add(value)
    return False


def _item_read(conn, row) -> ItemRead:
    item = ItemRead(
        id=int(row["id"]),
        stage_id=int(row["stage_id"]),
        title=row["title"],
        resource_slug=row["resource_slug"],
        note=row["note"],
        est_minutes=int(row["est_minutes"]),
        order_index=int(row["order_index"]),
        status=row["status"],
        depends_on=_parse_depends_on(row["depends_on"]),
        completed_at=row["completed_at"],
        version=int(row["version"]),
        is_deleted=int(row["is_deleted"]),
    )
    resolve_titles(conn, [item])
    return item


def _parse_depends_on(value) -> list[int] | None:
    if value is None or value == "":
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return [int(item) for item in parsed if isinstance(item, int)]


def _plan_read_base(row, *, stages, progress) -> PlanRead:
    return PlanRead(
        id=int(row["id"]),
        title=row["title"],
        goal=row["goal"],
        start_level=row["start_level"],
        weekly_hours=int(row["weekly_hours"]),
        horizon_weeks=int(row["horizon_weeks"]),
        status=row["status"],
        origin=row["origin"],
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_deleted=int(row["is_deleted"]),
        stages=stages,
        progress=progress,
    )


def _build_plan(conn, plan_row) -> PlanRead:
    stage_rows = conn.execute(
        "SELECT * FROM plan_stages WHERE plan_id = ? ORDER BY order_index, id",
        (plan_row["id"],),
    ).fetchall()

    stages: list[StageRead] = []
    for stage_row in stage_rows:
        item_rows = conn.execute(
            "SELECT * FROM plan_items WHERE stage_id = ? AND is_deleted = 0"
            " ORDER BY order_index, id",
            (stage_row["id"],),
        ).fetchall()
        items = [_item_read(conn, row) for row in item_rows]
        stages.append(
            StageRead(
                id=int(stage_row["id"]),
                plan_id=int(stage_row["plan_id"]),
                title=stage_row["title"],
                description=stage_row["description"],
                weeks=int(stage_row["weeks"]),
                order_index=int(stage_row["order_index"]),
                items=items,
                progress=None,
            )
        )

    plan = _plan_read_base(plan_row, stages=stages, progress=None)
    progress = compute_progress(plan)
    by_stage = {entry.stage_id: entry for entry in progress.stages}
    for stage in plan.stages:
        stage.progress = by_stage.get(stage.id)
    plan.progress = progress
    return plan


def _resource_meta(conn) -> tuple[set[str], dict[str, str], dict[str, int]]:
    """白名单 / 难度 / 资料时长，全部只取未下架资料。"""
    allowed: set[str] = set()
    levels: dict[str, str] = {}
    durations: dict[str, int] = {}
    for row in conn.execute(
        "SELECT slug, level, duration_min FROM resources WHERE is_deleted = 0"
    ):
        slug = row["slug"]
        allowed.add(slug)
        levels[slug] = row["level"]
        if row["duration_min"] is not None:
            durations[slug] = int(row["duration_min"])
    return allowed, levels, durations
