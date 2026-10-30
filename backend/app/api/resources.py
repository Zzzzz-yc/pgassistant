"""资料接口（需求文档 §11）。"""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..api.deps import get_conn

router = APIRouter()

LIST_SQL = """
    SELECT slug, title, summary, stack, category, level, type, duration_min,
           author, license, source_url, runnable, lang
    FROM resources
    WHERE is_deleted = 0
"""


@router.get("/resources")
def list_resources(
    stack: str | None = None,
    category: str | None = None,
    level: str | None = None,
    type: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(24, ge=1, le=100),
    conn=Depends(get_conn),
):
    where, args = "", []
    for field, value in (("stack", stack), ("category", category),
                         ("level", level), ("type", type)):
        if value:
            where += " AND %s = ?" % field
            args.append(value)

    total = conn.execute("SELECT COUNT(*) c FROM resources WHERE is_deleted = 0" + where, args).fetchone()["c"]
    rows = conn.execute(
        LIST_SQL + where + " ORDER BY stack, category, title LIMIT ? OFFSET ?",
        args + [size, (page - 1) * size],
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/resources/{slug:path}")
def get_resource(slug: str, conn=Depends(get_conn)):
    row = conn.execute(
        "SELECT * FROM resources WHERE slug = ? AND is_deleted = 0", (slug,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND"})

    item = dict(row)
    item.pop("body_tokens", None)
    tags = [r["tag"] for r in conn.execute(
        "SELECT tag FROM resource_tags WHERE resource_id = ?", (row["id"],)
    )]
    item["tags"] = tags
    item["runnable"] = [x for x in (row["runnable"] or "").split(",") if x]
    return item
