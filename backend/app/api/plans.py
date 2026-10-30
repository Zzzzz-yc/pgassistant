"""计划接口（M3，需求文档 §8 / §11；契约 API.md §3.8–3.14）。

服务层异常 → HTTP 的唯一映射点（MODULES §4.3）：服务层不构造响应，
这里统一转成 `core.errors` 的同一形状。
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..api.deps import get_conn
from ..core import errors
from ..schemas.models import (
    ItemCreate,
    ItemPatch,
    ItemRead,
    PlanCreate,
    PlanPatch,
    PlanStatusFilter,
    StageCreate,
    StagePatch,
)
from ..services import plan_service

router = APIRouter()


def _http(status_code: int, code: str, message: str, detail=None, hint=None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=errors.error_payload(code, message, detail, hint),
    )


@contextmanager
def _plan_errors():
    """把领域异常翻译成契约状态码与错误码。"""
    try:
        yield
    except plan_service.PlanNotFound as exc:
        raise _http(
            404, "PLAN_NOT_FOUND", "计划不存在或已删除",
            detail={"plan_id": exc.plan_id}, hint="返回计划列表",
        ) from exc
    except plan_service.StageNotFound as exc:
        raise _http(
            404, "STAGE_NOT_FOUND", "阶段不存在或已删除",
            detail={"stage_id": exc.stage_id}, hint="刷新计划后重试",
        ) from exc
    except plan_service.ItemNotFound as exc:
        raise _http(
            404, "ITEM_NOT_FOUND", "条目不存在或已删除",
            detail={"item_id": exc.item_id}, hint="刷新计划后重试",
        ) from exc
    except plan_service.VersionConflict as exc:
        message = "这个条目已在别处更新" if isinstance(exc.current, ItemRead) \
            else "这个计划已在别处更新"
        raise _http(
            409, "VERSION_CONFLICT", message,
            detail={
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
                "current": exc.current.model_dump(mode="json"),
            },
            hint="已为你刷新，请重试刚才的操作",
        ) from exc
    except plan_service.plan_validate.PlanInvalid as exc:
        detail: dict = {"errors": exc.errors}
        if exc.suggestions:
            detail["suggestions"] = exc.suggestions
        raise _http(
            422, "PLAN_INVALID", "生成的计划未通过校验",
            detail=detail, hint="换一种描述重试，或手动从零创建计划",
        ) from exc


# ------------------------------------------------------------------ 计划
@router.get("/plans")
def list_plans(
    status: PlanStatusFilter = Query("all"),
    conn=Depends(get_conn),
):
    """计划列表（不分页，D12）：先滤软删除，再滤状态。"""
    with _plan_errors():
        items = plan_service.list_plans(conn, None if status == "all" else status)
        return {
            "items": items,
            "total": len(items),
            "active_plan_id": plan_service.active_plan_id(conn),
        }


@router.post("/plans", status_code=201)
def create_plan(payload: PlanCreate, conn=Depends(get_conn)):
    """保存计划（模板 / AI / 手动三条路径共用，§8.2 第 5 步）。"""
    with _plan_errors():
        return plan_service.create_plan(conn, payload)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int = Path(..., ge=1), conn=Depends(get_conn)):
    with _plan_errors():
        return plan_service.get_plan(conn, plan_id)


@router.patch("/plans/{plan_id}")
def patch_plan(
    payload: PlanPatch,
    plan_id: int = Path(..., ge=1),
    conn=Depends(get_conn),
):
    with _plan_errors():
        return plan_service.patch_plan(conn, plan_id, payload)


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: int = Path(..., ge=1),
    version: int = Query(..., ge=1),
    conn=Depends(get_conn),
):
    """软删除（§11）：行、阶段、条目、打勾历史全部保留。"""
    with _plan_errors():
        return plan_service.delete_plan(conn, plan_id, version)


# ------------------------------------------------------------------ 阶段
@router.post("/plans/{plan_id}/stages", status_code=201)
def add_stage(
    payload: StageCreate,
    plan_id: int = Path(..., ge=1),
    conn=Depends(get_conn),
):
    with _plan_errors():
        return plan_service.add_stage(conn, plan_id, payload)


@router.patch("/stages/{stage_id}")
def patch_stage(
    payload: StagePatch,
    stage_id: int = Path(..., ge=1),
    conn=Depends(get_conn),
):
    with _plan_errors():
        return plan_service.patch_stage(conn, stage_id, payload)


@router.delete("/stages/{stage_id}")
def delete_stage(
    stage_id: int = Path(..., ge=1),
    version: int = Query(..., ge=1),
    conn=Depends(get_conn),
):
    """硬删除阶段，连带其条目（§8.3「删除连带条目二次确认」）。"""
    with _plan_errors():
        return plan_service.delete_stage(conn, stage_id, version)


# ------------------------------------------------------------------ 条目
@router.post("/plan-items", status_code=201)
def add_item(payload: ItemCreate, conn=Depends(get_conn)):
    with _plan_errors():
        return plan_service.add_item(conn, payload)


@router.patch("/plan-items/{item_id}")
def patch_item(
    payload: ItemPatch,
    item_id: int = Path(..., ge=1),
    conn=Depends(get_conn),
):
    with _plan_errors():
        return plan_service.patch_item(conn, item_id, payload)


@router.delete("/plan-items/{item_id}")
def delete_item(
    item_id: int = Path(..., ge=1),
    version: int = Query(..., ge=1),
    conn=Depends(get_conn),
):
    """条目软删除（§8.1，保留打勾历史）。"""
    with _plan_errors():
        return plan_service.delete_item(conn, item_id, version)
