"""检索接口（需求文档 §11；`API.md` §3.6 / §3.7）。

`GET` 是 M1 已实现的端点，`POST` 是 M2 新增的命令面板入口（DECISIONS D1）：
两者**语义与响应结构完全一致**，只是参数位置不同，因此共用 `_run()`。
零结果（`total == 0`）一律写 `search_misses`（§9.9），GET 与 POST 都写。

M5 起 `_run()` 走 `services/search_semantic.py` 的混合排序（§6.5）：
配了 embedding 且向量可用时按 `0.65*bm25 + 0.35*cos` 合并；否则**静默降级纯 FTS**
（§6.6：不报 `NO_EMBEDDING` 错，只把 `semantic_available` / `notice` 告诉前端）。
"""
import time

from fastapi import APIRouter, Depends, Query

from ..api.deps import get_conn
from ..schemas.models import SearchRequest
from ..services import search_semantic
from ..services.search import record_miss

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
    result = search_semantic.hybrid_search(
        conn, q, stack=stack, level=level, rtype=rtype,
        category=category, limit=limit, include_overview=include_overview,
    )
    took_ms = max(int((time.perf_counter() - started) * 1000), 0)
    # `semantic` 的三态：None=没配（不提示）/ False=配了但降级（提示）/ True=用了混合排序
    semantic = result.get("semantic")

    payload = {
        "query": q,
        "mode": result["mode"],
        "tokens": result["tokens"],
        "total": result["total"],
        "items": result["items"],
        "took_ms": took_ms,
        "semantic_available": semantic,
        "notice": search_semantic.notice(conn) if semantic is False else None,
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
