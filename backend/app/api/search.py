"""检索接口（需求文档 §11）。"""
from fastapi import APIRouter, Depends, Query

from ..api.deps import get_conn
from ..services.search import search

router = APIRouter()


@router.get("/search")
def api_search(
    q: str = Query(..., min_length=1, max_length=100, description="查询词"),
    stack: str | None = None,
    level: str | None = None,
    category: str | None = None,
    type: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    include_overview: bool = False,
    conn=Depends(get_conn),
):
    result = search(conn, q, stack=stack, level=level, rtype=type,
                    category=category, limit=limit, include_overview=include_overview)
    return {
        "query": q,
        "mode": result["mode"],
        "tokens": result["tokens"],
        "total": result["total"],
        "items": result["items"],
    }
