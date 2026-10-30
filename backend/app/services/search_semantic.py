"""语义检索（需求文档 §6.4 / §6.5 / §6.6；`MODULES.md` §2.2）。

**为什么要独立一个文件**：`services/search.py` 是资料页（M2）的文件，语义能力是 M5 的
新文件，避免两个人改同一个模块（`MODULES.md` §1.1 明确要求 `search_semantic.py`）。

**混合排序**（§6.5）：

    final = 0.65 * bm25_norm + 0.35 * cos_norm
    余弦 < 0.25 的候选直接丢弃（§6.4），不进入结果

无向量 / 未配置 embedding / 上游失败时**整体退化为纯 FTS**，并把
`semantic_available=False` + `notice` 交给 `/api/search`（§6.6：降级而不是报错，
也不返回 `NO_EMBEDDING` 错误码——那个码只留给"显式要求语义检索"的场景）。

**查询向量**默认走 `embeddings.embed_query`（异步），这里用同步包装 `query_vector`：
`/api/search` 是同步端点，FastAPI 把它放在线程池里跑，所以 `asyncio.run` 是安全的；
若将来从事件循环里调用，则改用一个短命线程跑，避免 `asyncio.run() cannot be called
from a running event loop`。测试直接 monkeypatch `query_vector`，不会出网。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from . import embeddings, llm_client, tokenizer
from .search import _snippet, search

log = logging.getLogger("pgassistant.search.semantic")

#: 混合权重（§6.5）；无向量时权重自动归一成纯 bm25
BM25_WEIGHT = 0.65
COS_WEIGHT = 0.35
#: 余弦阈值：低于它直接丢弃（§6.4）
MIN_COS = 0.25
#: 语义召回池大小（top-K）
TOP_K = 50
#: 降级提示（§6.6 / §9.9：语义搜索不可用时给一句人话）
NOTICE_DEGRADED = "语义检索暂不可用，已降级为关键词检索"
NOTICE_UNCONFIGURED = "未配置语义检索，当前为关键词检索"


# ------------------------------------------------------------------ 判定
def is_available_fast() -> bool:
    """不查库的粗判：只看"配置是否齐全"（`health` / 启动预热用）。"""
    try:
        return llm_client.load_embedding_config(None) is not None
    except Exception:
        return False


def available(conn) -> bool:
    """精确判定：配置在 + 指纹对得上 + 表里有向量。"""
    try:
        return embeddings.available(conn)
    except Exception as exc:
        log.warning("event=search.semantic.available failed error=%s", type(exc).__name__)
        return False


def notice(conn) -> str | None:
    """降级提示文案；能走语义检索时返回 None（前端不显示提示条）。"""
    if available(conn):
        return None
    try:
        configured = llm_client.load_embedding_config(conn) is not None
    except Exception:
        configured = False
    return NOTICE_DEGRADED if configured else NOTICE_UNCONFIGURED


# ------------------------------------------------------------------ 查询向量
def query_vector(conn, text: str) -> list[float] | None:
    """同步取查询向量；失败一律返回 None（降级纯 FTS）。测试可 monkeypatch 本函数。"""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is None:
        try:
            return asyncio.run(embeddings.embed_query(conn, text))
        except Exception as exc:
            log.warning("event=search.semantic.embed failed error=%s", type(exc).__name__)
            return None

    # 已在事件循环里：换一个短命线程跑（本机一次性查询，阻塞代价可接受）
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, embeddings.embed_query(conn, text))
        try:
            return future.result(timeout=30)
        except Exception as exc:
            log.warning("event=search.semantic.embed failed error=%s", type(exc).__name__)
            return None


# ------------------------------------------------------------------ 混合检索
def _hit(row, tokens, cos: float) -> dict:
    return {
        "slug": row["slug"],
        "title": row["title"],
        "snippet": _snippet(row["summary"] or row["title"], tokens),
        "stack": row["stack"],
        "category": row["category"],
        "level": row["level"],
        "type": row["type"],
        "duration_min": row["duration_min"],
        "author": row["author"],
        "license": row["license"],
        "score": round(cos, 4),
    }


def _fetch_rows(conn, slugs, *, stack=None, level=None, rtype=None,
                category=None, include_overview=False) -> dict:
    """按 slug 批量取语义独有条目的展示字段（一次查询，不做 N+1）。"""
    if not slugs:
        return {}
    placeholders = ",".join("?" for _ in slugs)
    sql = (
        "SELECT slug, title, summary, stack, category, level, type, duration_min,"
        " author, license FROM resources WHERE is_deleted = 0 AND slug IN (%s)" % placeholders
    )
    args = list(slugs)
    if stack:
        sql += " AND stack = ?"
        args.append(stack)
    if category:
        sql += " AND category = ?"
        args.append(category)
    if level:
        sql += " AND level = ?"
        args.append(level)
    if rtype:
        sql += " AND type = ?"
        args.append(rtype)
    if not include_overview:
        sql += " AND type <> 'overview'"
    try:
        return {row["slug"]: row for row in conn.execute(sql, args).fetchall()}
    except Exception:
        return {}


def hybrid_search(conn, q, stack=None, level=None, rtype=None, category=None,
                  limit=10, include_overview=False) -> dict:
    """返回结构与 `search()` **完全一致**，另外多一个 `semantic` 字段供 API 层映射：

    - `None`  = 没配 embedding，本次没走语义检索（`semantic_available=None`）
    - `False` = 配了但不可用（无向量 / 指纹不匹配 / 向量化失败）→ 降级提示
    - `True`  = 真的用了混合排序
    """
    base = search(
        conn, q, stack=stack, level=level, rtype=rtype,
        category=category, limit=limit, include_overview=include_overview,
    )
    result = dict(base)

    if not embeddings.configured(conn):
        result["semantic"] = None
        return result
    if not available(conn):
        result["semantic"] = False
        return result

    vector = query_vector(conn, q)
    if vector is None:
        result["semantic"] = False
        return result

    cos_by_slug = {}
    for slug, cos in embeddings.top_k(conn, vector, k=TOP_K, min_cos=MIN_COS):
        cos_by_slug[slug] = float(cos)

    tokens = base.get("tokens") or tokenizer.query_tokens(q)
    # 每个候选分别记住 bm25 与余弦：语义独有的条目 bm25 = 0，不能拿 cos 冒充
    scored: dict[str, dict] = {}
    for item in base.get("items") or []:
        scored[item["slug"]] = {
            "item": dict(item),
            "bm25": float(item.get("score") or 0.0),   # search() 已把 bm25 归一化到 [0,1]
            "cos": cos_by_slug.get(item["slug"], 0.0),
        }

    missing = [slug for slug in cos_by_slug if slug not in scored]
    rows = _fetch_rows(conn, missing, stack=stack, level=level, rtype=rtype,
                       category=category, include_overview=include_overview)
    for slug in missing:
        row = rows.get(slug)
        if row is None:                            # 已下架 / 被筛选条件排除
            continue
        scored[slug] = {
            "item": _hit(row, tokens, cos_by_slug[slug]),
            "bm25": 0.0,
            "cos": cos_by_slug[slug],
        }

    merged = []
    for entry in scored.values():
        item = entry["item"]
        cos = max(0.0, min(1.0, float(entry["cos"])))
        item["score"] = round(BM25_WEIGHT * entry["bm25"] + COS_WEIGHT * cos, 4)
        merged.append(item)

    merged.sort(key=lambda item: (-item["score"], item["slug"]))
    total = len(merged)
    mode = base.get("mode") or "miss"
    if mode in ("miss", "empty") and merged:
        # SearchMode 是封闭集合（含 miss/empty），语义独有命中时用 "OR" 表示"放宽后的结果"
        mode = "OR"

    result.update({
        "items": merged[:max(1, int(limit))],
        "total": total,
        "mode": mode,
        "tokens": tokens,
        "semantic": True,
    })
    return result


__all__ = [
    "BM25_WEIGHT", "COS_WEIGHT", "MIN_COS", "TOP_K", "NOTICE_DEGRADED",
    "NOTICE_UNCONFIGURED", "available", "is_available_fast", "notice",
    "query_vector", "hybrid_search",
]
