"""分类树（需求文档 §4.6）。从库里实际的分类聚合，保证不出现空分类。"""
from fastapi import APIRouter, Depends

from ..api.deps import get_conn

router = APIRouter()


@router.get("/taxonomy")
def api_taxonomy(conn=Depends(get_conn)):
    rows = conn.execute("""
        SELECT stack, category, COUNT(*) AS n
        FROM resources
        WHERE is_deleted = 0
        GROUP BY stack, category
        ORDER BY stack, category
    """).fetchall()

    stacks = {}
    for r in rows:
        node = stacks.setdefault(r["stack"], {"stack": r["stack"], "total": 0, "categories": []})
        node["categories"].append({"category": r["category"], "count": r["n"]})
        node["total"] += r["n"]

    levels = [r["level"] for r in conn.execute(
        "SELECT level, COUNT(*) n FROM resources WHERE is_deleted = 0 GROUP BY level ORDER BY n DESC")]
    types = [r["type"] for r in conn.execute(
        "SELECT type, COUNT(*) n FROM resources WHERE is_deleted = 0 GROUP BY type ORDER BY n DESC")]

    return {
        "stacks": sorted(stacks.values(), key=lambda x: -x["total"]),
        "levels": sorted(set(levels)),
        "types": sorted(set(types)),
        "total": sum(s["total"] for s in stacks.values()),
    }
