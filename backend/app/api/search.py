"""检索接口（需求文档 §11；`API.md` §3.6 / §3.7）。

`GET` 是 M1 已实现的端点，`POST` 是 M2 新增的命令面板入口（DECISIONS D1）：
两者**语义与响应结构完全一致**，只是参数位置不同，因此共用 `_run()`。
零结果（`total == 0`）一律写 `search_misses`（§9.9），GET 与 POST 都写。
"""
import time

from fastapi import APIRouter, Depends, Query

from ..api.deps import get_conn
from ..schemas.models import SearchRequest
from ..services.search import record_miss, search

router = APIRouter()


def _filters(stack, category, level, rtype, include_overview) -> dict:
    """写 `search_misses` 用的筛选快照；未传的项不出现（不是 null）。"""
    payload = {}
    for key, value in (
        ("stack", stack),
        ("category", category),
        ("level", level),
        ("type", rtype),
    ):
        if value:
            payload[key] = value
    if include_overview:
        payload["include_overview"] = True
    return payload


def _run(conn, q, stack, category, level, rtype, limit, include_overview) -> dict:
    """GET 与 POST 的唯一实现：一次检索 + 耗时 + 零结果记账。"""
    started = time.perf_counter()
    result = search(
        conn, q, stack=stack, level=level, rtype=rtype,
        category=category, limit=limit, include_overview=include_overview,
    )
    took_ms = max(int((time.perf_counter() - started) * 1000), 0)

    payload = {
        "query": q,
        "mode": result["mode"],
        "tokens": result["tokens"],
        "total": result["total"],
        "items": result["items"],
        "took_ms": took_ms,
        # M2 没有语义检索（M5 才接）：null = 本次没走向量，不提示、不报错（§6.6 / §7.9）
        "semantic_available": None,
        "notice": None,
    }
    if result["total"] == 0:
        record_miss(conn, q, _filters(stack, category, level, rtype, include_overview))
    return payload


@router.get("/search", tags=["search"], summary="关键词检索")
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
    return _run(conn, q, stack, category, level, type, limit, include_overview)


@router.post("/search", tags=["search"], summary="关键词检索（JSON body，命令面板）")
def api_search_post(body: SearchRequest, conn=Depends(get_conn)):
    """`extra="forbid"`：拼错的字段名直接 422，不静默丢弃（§1.3）。"""
    return _run(
        conn,
        body.q,
        body.stack,
        body.category,
        body.level,
        body.type,
        body.limit,
        body.include_overview,
    )
