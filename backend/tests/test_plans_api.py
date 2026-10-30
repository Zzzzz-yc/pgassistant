"""计划接口（M3）的集成测试：CRUD、软删除、版本冲突、进度、模板。

全部写操作走 FastAPI 的 TestClient（连同中间件与错误信封一起验证）；
数据库是 `tmp_path` 里的临时库（conftest 的 `db_path`），不污染 `data/app.db`。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app.core import config
from app.db.connection import connect
from app.services import plan_service, plan_templates

HOST = "http://127.0.0.1:8000"
SEEDED_SLUG = "cs-basics/array-linkedlist/array"
ADVANCED_SLUG = "cs-basics/dynamic-programming/knapsack_problem"


def _headers(token: str) -> dict:
    return {"X-App-Token": token, "Origin": HOST}


def _item(title: str, slug: str | None = None, minutes: int = 40,
          note: str = "") -> dict:
    return {"title": title, "resource_slug": slug, "est_minutes": minutes, "note": note}


def _stage(title: str, weeks: int = 2, items: list[dict] | None = None) -> dict:
    return {
        "title": title,
        "weeks": weeks,
        "description": "",
        "items": items or [
            _item("%s 任务一" % title),
            _item("%s 任务二" % title),
            _item("%s 任务三" % title),
        ],
    }


def _draft(stages: list[dict] | None = None, weeks: int = 4,
           weekly_hours: int = 1) -> dict:
    return {
        "title": "测试计划",
        "goal": "验证计划模块",
        "weeks": weeks,
        "weekly_hours": weekly_hours,
        "stages": stages if stages is not None else [_stage("阶段一"), _stage("阶段二")],
    }


def _create(client, token: str, draft: dict | None = None, set_active: bool = True) -> dict:
    resp = client.post(
        "/api/plans",
        headers=_headers(token),
        json={"draft": draft or _draft(), "origin": "manual", "set_active": set_active},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get(client, token: str, plan_id: int) -> dict:
    resp = client.get("/api/plans/%d" % plan_id, headers={"X-App-Token": token})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _patch(client, token: str, path: str, body: dict, expect: int = 200):
    resp = client.patch(path, headers=_headers(token), json=body)
    assert resp.status_code == expect, resp.text
    return resp.json()


def _add_resource(db_path: Path, slug: str, level: str, minutes: int = 30) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO resources (slug, title, summary, body_md, body_tokens,"
                " stack, category, level, type, duration_min, file_path, content_hash,"
                " created_at, updated_at) VALUES (?, ?, '', '', '', 'cs-basics',"
                " 'dynamic-programming', ?, 'article', ?, 'content/x.md', 'hash',"
                " '2026-10-30', '2026-10-30')",
                (slug, "测试资料 " + slug, level, minutes),
            )
    finally:
        conn.close()


def _db_item(db_path: Path, item_id: int) -> sqlite3.Row:
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM plan_items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------- 创建 / 读取
def test_create_plan_with_stages_and_items(client, token):
    plan = _create(client, token)
    assert plan["id"] >= 1
    assert plan["version"] == 1
    assert plan["is_deleted"] == 0
    assert plan["origin"] == "manual"
    assert plan["status"] == "active"
    assert [stage["order_index"] for stage in plan["stages"]] == [1000, 2000]
    assert [item["order_index"] for item in plan["stages"][0]["items"]] == [1000, 2000, 3000]
    assert plan["progress"]["percent"] == 0.0
    assert plan["progress"]["todo_items"] == 6
    assert plan["progress"]["total_minutes"] == 240
    assert plan["progress"]["remaining_weeks"] == 4.0
    assert plan["stages"][0]["progress"]["todo"] == 3


def test_list_and_detail(client, token):
    first = _create(client, token, set_active=False)
    assert first["status"] == "archived"
    second = _create(client, token)

    listing = client.get("/api/plans", headers={"X-App-Token": token})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert body["active_plan_id"] == second["id"]
    assert {item["id"] for item in body["items"]} == {first["id"], second["id"]}

    only_active = client.get(
        "/api/plans?status=active", headers={"X-App-Token": token}
    ).json()
    assert [item["id"] for item in only_active["items"]] == [second["id"]]
    only_archived = client.get(
        "/api/plans?status=archived", headers={"X-App-Token": token}
    ).json()
    assert [item["id"] for item in only_archived["items"]] == [first["id"]]

    detail = _get(client, token, second["id"])
    assert detail["title"] == second["title"]
    assert len(detail["stages"]) == 2


def test_rename_archive_and_activate(client, token):
    first = _create(client, token)
    second = _create(client, token)                    # 新计划自动归档旧 active
    assert _get(client, token, first["id"])["status"] == "archived"
    assert _get(client, token, second["id"])["status"] == "active"

    archived = _patch(
        client, token, "/api/plans/%d" % second["id"],
        {"status": "archived", "version": second["version"]},
    )
    assert archived["status"] == "archived"
    assert archived["version"] == 2
    assert client.get("/api/plans", headers={"X-App-Token": token}).json()["active_plan_id"] is None

    # 第二个计划创建时把 first 归档，那次归档也是一次写 → first.version 1 → 2
    first_current = _get(client, token, first["id"])
    assert first_current["status"] == "archived"
    assert first_current["version"] == 2
    activated = _patch(
        client, token, "/api/plans/%d" % first["id"],
        {"set_active": True, "version": first_current["version"]},
    )
    assert activated["status"] == "active"
    assert activated["version"] == 3
    assert client.get("/api/plans", headers={"X-App-Token": token}).json()["active_plan_id"] == first["id"]


def test_archiving_other_active_bumps_version(client, token):
    first = _create(client, token)
    assert first["version"] == 1
    _create(client, token)                       # set_active=True → 归档 first
    refreshed = _get(client, token, first["id"])
    assert refreshed["status"] == "archived"
    assert refreshed["version"] == 2


# --------------------------------------------------------------- 软删除 / 409
def test_soft_deleted_plan_is_invisible(client, token):
    plan = _create(client, token)
    resp = client.delete(
        "/api/plans/%d?version=%d" % (plan["id"], plan["version"]),
        headers=_headers(token),
    )
    assert resp.status_code == 200, resp.text
    deleted = resp.json()
    assert deleted["is_deleted"] == 1
    assert deleted["version"] == 2
    assert deleted["stages"] == []
    assert deleted["progress"] is None

    assert client.get("/api/plans", headers={"X-App-Token": token}).json()["total"] == 0
    assert client.get(
        "/api/plans/%d" % plan["id"], headers={"X-App-Token": token}
    ).status_code == 404
    patched = client.patch(
        "/api/plans/%d" % plan["id"], headers=_headers(token),
        json={"title": "复活", "version": 2},
    )
    assert patched.status_code == 404
    assert patched.json()["code"] == "PLAN_NOT_FOUND"


def test_version_conflict_writes_nothing(client, token):
    plan = _create(client, token)
    resp = client.patch(
        "/api/plans/%d" % plan["id"], headers=_headers(token),
        json={"title": "冲突改名", "version": 99},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["detail"]["expected_version"] == 99
    assert body["detail"]["actual_version"] == 1
    assert body["detail"]["current"]["version"] == 1
    assert body["detail"]["current"]["title"] == plan["title"]

    unchanged = _get(client, token, plan["id"])
    assert unchanged["title"] == plan["title"]
    assert unchanged["version"] == 1

    ok = _patch(
        client, token, "/api/plans/%d" % plan["id"],
        {"title": "正常改名", "version": 1},
    )
    assert ok["title"] == "正常改名"
    assert ok["version"] == 2


def test_item_version_conflict_returns_item(client, token):
    plan = _create(client, token)
    item_id = plan["stages"][0]["items"][0]["id"]
    resp = client.patch(
        "/api/plan-items/%d" % item_id, headers=_headers(token),
        json={"status": "done", "version": 99},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["detail"]["current"]["id"] == item_id
    assert body["detail"]["current"]["version"] == 1


# --------------------------------------------------------------------- 阶段
def test_add_patch_and_drag_stage(client, token):
    plan = _create(client, token)
    created = client.post(
        "/api/plans/%d/stages" % plan["id"], headers=_headers(token),
        json={"title": "阶段三", "description": "新增", "weeks": 1,
              "order_index": None, "version": 1},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["plan"]["version"] == 2
    assert body["affected_stage_ids"]
    stage = body["plan"]["stages"][-1]
    assert stage["order_index"] == 3000
    assert body["affected_item_ids"] == []

    renamed = _patch(
        client, token, "/api/stages/%d" % stage["id"],
        {"title": "阶段三（改名）", "version": 2},
    )
    assert renamed["plan"]["version"] == 3
    assert renamed["plan"]["stages"][-1]["title"] == "阶段三（改名）"

    dragged = _patch(
        client, token, "/api/stages/%d" % stage["id"],
        {"order_index": 1500, "version": 3},
    )
    assert dragged["plan"]["version"] == 4
    orders = {item["id"]: item["order_index"] for item in dragged["plan"]["stages"]}
    assert orders[stage["id"]] == 1500
    ordered_ids = [item["id"] for item in dragged["plan"]["stages"]]
    assert ordered_ids[1] == stage["id"]          # 1500 落在 1000 与 2000 之间


def test_stage_order_collision_triggers_renumber(client, token):
    plan = _create(client, token)
    second_stage = plan["stages"][1]
    result = _patch(
        client, token, "/api/stages/%d" % second_stage["id"],
        {"order_index": 1000, "version": 1},
    )
    orders = [stage["order_index"] for stage in result["plan"]["stages"]]
    assert len(set(orders)) == len(orders)
    assert orders == [1000, 2000]


def test_delete_stage_cascades_items(client, token):
    plan = _create(client, token)
    stage = plan["stages"][0]
    item_ids = [item["id"] for item in stage["items"]]
    resp = client.delete(
        "/api/stages/%d?version=1" % stage["id"], headers=_headers(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"]["version"] == 2
    assert sorted(body["affected_item_ids"]) == sorted(item_ids)
    assert body["affected_stage_ids"] == [stage["id"]]
    remaining_ids = {s["id"] for s in body["plan"]["stages"]}
    assert stage["id"] not in remaining_ids
    assert sum(len(s["items"]) for s in body["plan"]["stages"]) == 3


# --------------------------------------------------------------------- 条目
def test_add_item_appends_and_delete_is_soft(client, token, db_path):
    plan = _create(client, token)
    stage = plan["stages"][0]
    created = client.post(
        "/api/plan-items", headers=_headers(token),
        json={"stage_id": stage["id"], "title": "新增条目",
              "resource_slug": SEEDED_SLUG, "note": "", "est_minutes": 25,
              "order_index": None, "version": 1},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["plan"]["version"] == 2
    item = body["plan"]["stages"][0]["items"][-1]
    assert item["order_index"] == 4000
    assert item["version"] == 1
    assert item["resolved_title"] == "数组"
    assert item["resource_available"] is True
    assert body["affected_item_ids"] == [item["id"]]

    deleted = client.delete(
        "/api/plan-items/%d?version=1" % item["id"], headers=_headers(token)
    )
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["affected_item_ids"] == [item["id"]]
    assert payload["plan"]["version"] == 3
    assert item["id"] not in [i["id"] for i in payload["plan"]["stages"][0]["items"]]
    row = _db_item(db_path, item["id"])
    assert row["is_deleted"] == 1
    assert row["version"] == 2


def test_patch_soft_deleted_item_is_404(client, token, db_path):
    plan = _create(client, token)
    item = plan["stages"][0]["items"][0]
    original_title = item["title"]
    deleted = client.delete(
        "/api/plan-items/%d?version=1" % item["id"], headers=_headers(token)
    )
    assert deleted.status_code == 200
    assert deleted.json()["plan"]["version"] == 2

    resp = client.patch(
        "/api/plan-items/%d" % item["id"], headers=_headers(token),
        json={"title": "死行改名", "version": 2},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "ITEM_NOT_FOUND"

    row = _db_item(db_path, item["id"])
    assert row["title"] == original_title
    assert row["version"] == 2
    assert row["is_deleted"] == 1
    assert _get(client, token, plan["id"])["version"] == 2


def test_delete_soft_deleted_item_is_404(client, token, db_path):
    plan = _create(client, token)
    item = plan["stages"][0]["items"][0]
    deleted = client.delete(
        "/api/plan-items/%d?version=1" % item["id"], headers=_headers(token)
    )
    assert deleted.status_code == 200

    resp = client.delete(
        "/api/plan-items/%d?version=2" % item["id"], headers=_headers(token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "ITEM_NOT_FOUND"

    row = _db_item(db_path, item["id"])
    assert row["version"] == 2
    assert row["is_deleted"] == 1
    assert _get(client, token, plan["id"])["version"] == 2


def test_item_move_and_status_and_progress(client, token):
    # 5 条：200 分钟 1 条 + 10 分钟 4 条，总时长 240 = 4 周 × 1 小时
    draft = _draft(stages=[
        _stage("重头戏", 2, [_item("长课程", minutes=200),
                             _item("短读一", minutes=10),
                             _item("短读二", minutes=10)]),
        _stage("收尾", 2, [_item("短读三", minutes=10),
                           _item("短读四", minutes=10)]),
    ])
    plan = _create(client, token, draft=draft)
    long_item = plan["stages"][0]["items"][0]
    short_item = plan["stages"][0]["items"][1]
    target_stage = plan["stages"][1]

    done = _patch(client, token, "/api/plan-items/%d" % long_item["id"],
                  {"status": "done", "version": 1})
    assert done["plan"]["progress"]["percent"] == 83.3       # 200 / 240，按时长加权
    assert done["plan"]["progress"]["done_items"] == 1
    assert done["plan"]["progress"]["invested_minutes"] == 200
    tree_item = next(
        i for s in done["plan"]["stages"] for i in s["items"] if i["id"] == long_item["id"]
    )
    assert tree_item["status"] == "done"
    assert tree_item["completed_at"] is not None

    skipped = _patch(client, token, "/api/plan-items/%d" % short_item["id"],
                     {"status": "skipped", "version": 1})
    progress = skipped["plan"]["progress"]
    assert progress["skipped_items"] == 1
    assert progress["total_minutes"] == 230                  # skipped 不入分母
    assert progress["percent"] == 87.0                       # 200 / 230

    moved = _patch(client, token, "/api/plan-items/%d" % long_item["id"],
                   {"stage_id": target_stage["id"], "version": 2})
    assert sorted(moved["affected_stage_ids"]) == sorted(
        [plan["stages"][0]["id"], target_stage["id"]]
    )
    target_items = next(
        s for s in moved["plan"]["stages"] if s["id"] == target_stage["id"]
    )["items"]
    assert [i["id"] for i in target_items][-1] == long_item["id"]
    assert target_items[-1]["order_index"] == 3000


def test_item_skipped_back_to_todo_clears_completed_at(client, token):
    plan = _create(client, token)
    item = plan["stages"][0]["items"][0]
    done = _patch(client, token, "/api/plan-items/%d" % item["id"],
                  {"status": "done", "version": 1})
    done_item = next(i for s in done["plan"]["stages"] for i in s["items"]
                     if i["id"] == item["id"])
    assert done_item["completed_at"] is not None
    back = _patch(client, token, "/api/plan-items/%d" % item["id"],
                  {"status": "todo", "version": 2})
    back_item = next(i for s in back["plan"]["stages"] for i in s["items"]
                     if i["id"] == item["id"])
    assert back_item["completed_at"] is None
    assert back["plan"]["progress"]["percent"] == 0.0


# --------------------------------------------------------------- 业务校验
def test_whitelist_drops_unknown_slug(client, token):
    draft = _draft(stages=[
        _stage("阶段一", 2, [_item("真实资料", SEEDED_SLUG),
                             _item("未知资料", "frontend/react/nope"),
                             _item("自定义", None)]),
        _stage("阶段二", 2, [_item("任务甲"), _item("任务乙"), _item("任务丙")]),
    ])
    plan = _create(client, token, draft=draft)
    slugs = [i["resource_slug"] for s in plan["stages"] for i in s["items"]]
    assert "frontend/react/nope" not in slugs
    assert sum(len(s["items"]) for s in plan["stages"]) == 5
    assert SEEDED_SLUG in slugs


def test_whitelist_drop_is_logged(client, token, caplog):
    draft = _draft(stages=[
        _stage("阶段一", 2, [_item("真实资料", SEEDED_SLUG),
                             _item("未知资料", "frontend/react/nope"),
                             _item("自定义", None)]),
        _stage("阶段二", 2, [_item("任务甲"), _item("任务乙"), _item("任务丙")]),
    ])
    with caplog.at_level(logging.WARNING, logger="app.services.plan_service"):
        _create(client, token, draft=draft)
    messages = [
        record.getMessage() for record in caplog.records
        if record.name == "app.services.plan_service"
    ]
    assert any(
        "frontend/react/nope" in message and "丢弃" in message
        for message in messages
    ), messages


def test_too_few_items_is_plan_invalid(client, token):
    draft = _draft(stages=[
        _stage("阶段一", 2, [_item("一"), _item("二")]),
        _stage("阶段二", 2, [_item("三"), _item("四")]),
    ])
    resp = client.post("/api/plans", headers=_headers(token), json={"draft": draft})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "PLAN_INVALID"
    assert body["detail"]["errors"]
    assert any("条目总数" in error for error in body["detail"]["errors"])


def test_duration_deviation_is_plan_invalid(client, token):
    draft = _draft(stages=[
        _stage("阶段一", 2, [_item("一", minutes=240), _item("二", minutes=240),
                             _item("三", minutes=240)]),
        _stage("阶段二", 2, [_item("四", minutes=240), _item("五", minutes=240),
                             _item("六", minutes=240)]),
    ])
    resp = client.post("/api/plans", headers=_headers(token), json={"draft": draft})
    assert resp.status_code == 422
    assert any("偏差" in error for error in resp.json()["detail"]["errors"])


def test_level_inversion_is_plan_invalid_unless_exempt(client, token, db_path):
    _add_resource(db_path, ADVANCED_SLUG, "advanced", 30)
    base_items = [_item("进阶资料", ADVANCED_SLUG), _item("自定义一"),
                  _item("自定义二")]
    draft = _draft(stages=[
        _stage("基础阶段", 2, base_items),
        _stage("进阶阶段", 2, [_item("入门资料", SEEDED_SLUG), _item("自定义三"),
                               _item("自定义四")]),
    ])
    resp = client.post("/api/plans", headers=_headers(token), json={"draft": draft})
    assert resp.status_code == 422, resp.text
    assert any("难度" in error for error in resp.json()["detail"]["errors"])

    exempt = _draft(stages=[
        _stage("基础阶段", 2, [_item("进阶资料", ADVANCED_SLUG, note="选学"),
                               _item("自定义一"), _item("自定义二")]),
        _stage("进阶阶段", 2, [_item("入门资料", SEEDED_SLUG), _item("自定义三"),
                               _item("自定义四")]),
    ])
    plan = _create(client, token, draft=exempt)
    assert plan["id"] >= 1


def test_unknown_field_is_validation_error(client, token):
    resp = client.post(
        "/api/plans", headers=_headers(token),
        json={"draft": _draft(), "unknown_field": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------- 模板
def test_template_list_and_detail(client, token):
    listing = client.get("/api/plan-templates", headers={"X-App-Token": token})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 4
    ids = {item["template_id"] for item in body["items"]}
    assert ids == {"frontend-entry", "backend-entry", "algo-basics", "interview-sprint"}
    frontend = next(item for item in body["items"] if item["template_id"] == "frontend-entry")
    assert frontend["label"]["zh"] == "前端入门"
    assert frontend["stage_count"] == 4
    assert 12 <= frontend["item_count"] <= 40

    detail = client.get(
        "/api/plan-templates/frontend-entry", headers={"X-App-Token": token}
    )
    assert detail.status_code == 200, detail.text
    draft = detail.json()
    assert len(draft["stages"]) == 4
    assert draft["weeks"] == 8

    missing = client.get(
        "/api/plan-templates/embedded-entry", headers={"X-App-Token": token}
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "PLAN_TEMPLATE_NOT_FOUND"


def test_all_templates_validate_and_slugs_exist():
    db_path = Path(config.SETTINGS.db_path)
    if not db_path.exists():
        pytest.skip("模板 slug 校验需要 data/app.db")
    assert plan_templates.load_template("algo-basics").stages
    assert plan_templates.validate_all() == []

    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        known = {
            row[0] for row in conn.execute(
                "SELECT slug FROM resources WHERE is_deleted = 0"
            )
        }
    finally:
        conn.close()
    for template_id in ("frontend-entry", "backend-entry", "algo-basics",
                        "interview-sprint"):
        draft = plan_templates.load_template(template_id)
        for stage in draft.stages:
            for item in stage.items:
                if item.resource_slug is not None:
                    assert item.resource_slug in known, (template_id, item.resource_slug)


def test_templates_meet_quality_bar():
    if not Path(config.SETTINGS.db_path).exists():
        pytest.skip("模板质量校验需要 data/app.db")
    errors = plan_templates.validate_all()
    assert errors == []
    for template_id in ("frontend-entry", "backend-entry", "algo-basics",
                        "interview-sprint"):
        draft = plan_templates.load_template(template_id)
        assert 4 <= len(draft.stages) <= 8
        for stage in draft.stages:
            assert 3 <= len(stage.items) <= 8
        total = sum(i.est_minutes for s in draft.stages for i in s.items)
        target = draft.weeks * draft.weekly_hours * 60
        assert abs(total - target) / target <= 0.30


# ----------------------------------------------------- D25：条目实时解析字段


def _resource_row(db_path, slug: str):
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT type, level, is_deleted FROM resources WHERE slug = ?", (slug,)
        ).fetchone()
    finally:
        conn.close()


def _bound_plan(client, token, slug: str = SEEDED_SLUG) -> dict:
    """两阶段各 3 条，全部绑同一个资料（满足总条目 5–40 与时长偏差 ≤30%）。"""
    stages = [
        _stage("阶段一", items=[_item("任务 %d" % i, slug) for i in range(3)]),
        _stage("阶段二", items=[_item("任务 %d" % (i + 3), slug) for i in range(3)]),
    ]
    return _create(client, token, draft=_draft(stages=stages, weeks=4, weekly_hours=1))


def test_item_resolved_type_and_level(client, token, db_path):
    """绑定了资料 → resource_type / resource_level 来自 resources 的实时值。"""
    row = _resource_row(db_path, SEEDED_SLUG)
    assert row is not None and int(row["is_deleted"]) == 0

    plan = _bound_plan(client, token)
    items = [item for stage in plan["stages"] for item in stage["items"]]
    assert len(items) == 6

    resp = client.get("/api/plans/%d" % plan["id"], headers=_headers(token))
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    for item in (i for stage in detail["stages"] for i in stage["items"]):
        assert item["resource_available"] is True
        assert item["resolved_title"] == "数组"
        assert item["resource_type"] == row["type"]
        assert item["resource_level"] == row["level"]


def test_unbound_item_has_null_type_and_level(client, token):
    """未绑定资料 → 两个字段都是 null（且 available=True，API.md §3.14.5）。"""
    plan = _create(client, token)  # 默认草稿的条目都不绑资料
    items = [item for stage in plan["stages"] for item in stage["items"]]
    assert items
    for item in items:
        assert item["resource_slug"] is None
        assert item["resource_type"] is None
        assert item["resource_level"] is None
        assert item["resource_available"] is True


def test_missing_slug_is_unavailable_with_null_type_and_level(client, token):
    """slug 不存在 → 双 null + resource_available=False（前端据此标灰并显示「资料已下架」）。"""
    plan = _create(client, token)
    stage = plan["stages"][0]
    created = client.post(
        "/api/plan-items", headers=_headers(token),
        json={"stage_id": stage["id"], "title": "幽灵条目",
              "resource_slug": "cs-basics/does-not-exist/ghost", "note": "",
              "est_minutes": 30, "order_index": None, "version": plan["version"]},
    )
    assert created.status_code == 201, created.text
    ghost = created.json()["plan"]["stages"][0]["items"][-1]
    assert ghost["resource_slug"] == "cs-basics/does-not-exist/ghost"
    assert ghost["resource_available"] is False
    assert ghost["resolved_title"] is None
    assert ghost["resource_type"] is None
    assert ghost["resource_level"] is None


def test_resolve_resources_is_one_batch_query(client, token, db_path):
    """D25：整棵计划只发一条 `resources` 查询，6 条绑定条目也不例外（禁止 N+1）。"""
    plan = _bound_plan(client, token)
    conn = connect(db_path)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        loaded = plan_service.get_plan(conn, plan["id"])
    finally:
        conn.set_trace_callback(None)
        conn.close()

    assert len(loaded.stages) == 2
    assert sum(len(stage.items) for stage in loaded.stages) == 6
    resource_selects = [
        sql for sql in statements if "FROM resources" in sql and "SELECT" in sql.upper()
    ]
    assert len(resource_selects) == 1, resource_selects
    # 一次查询覆盖全部 6 条，且字段确实被填上
    assert all(item.resource_type is not None for stage in loaded.stages
               for item in stage.items if item.resource_slug)

