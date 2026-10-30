"""计划模板接口（M3 推定新增；契约 API.md §3.15、D10）。

只读：模板是 `content/_meta/templates/*.yml` 里的数据，前端不能直接读
`content/`，所以补这两个端点给"套用模板（0 token）"路径取数。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from ..core import errors
from ..services import plan_templates, plan_validate

router = APIRouter()


@router.get("/plan-templates")
def list_templates():
    items = plan_templates.list_templates()
    return {"items": items, "total": len(items)}


@router.get("/plan-templates/{template_id}")
def get_template(template_id: str = Path(..., min_length=1, max_length=64)):
    try:
        return plan_templates.load_template(template_id)
    except plan_templates.TemplateNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=errors.error_payload(
                "PLAN_TEMPLATE_NOT_FOUND",
                "模板不存在",
                detail={"template_id": exc.template_id},
                hint="不选模板、直接填表单走 AI 生成",
            ),
        ) from exc
    except plan_validate.PlanInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=errors.error_payload(
                "PLAN_INVALID",
                "模板未通过校验",
                detail={"errors": exc.errors},
                hint="换一个模板，或不选模板直接生成",
            ),
        ) from exc
