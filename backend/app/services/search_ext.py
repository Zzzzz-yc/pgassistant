"""资料行序列化与相关推荐（M2 资料页的“同模块扩展”）。

为什么单独一个文件而不是塞进 `services/search.py`：

- `services/search.py` 是 M1 冻结模块，签名只能加法式扩展（`MODULES.md` §2.2）；
- 列表接口（`api/resources.py`）与相关推荐（`services/search.py::related`）要用**同一份**
  `ResourceSummary` 序列化逻辑，放这里两边都能 import，且不会形成
  `services → api` 的反向依赖。

`services/search.py` 仍然暴露契约要求的 `related(conn, slug, limit=6)`，实现委托到这里。
"""
from __future__ import annotations

TAG_SEP = "\x1f"

#: 列表与相关推荐共用的列集合。`tags` 用子查询一次取回（无 N+1；`char(31)` 作分隔符，
#: 标签文本里不会出现这个字符，比逗号安全）。
#: 内层 `ORDER BY rowid` 不能省：`resource_tags` 上有 `UNIQUE(resource_id, tag)` 索引，
#: 直接聚合会按**标签字典序**扫索引，front-matter 里的书写顺序就丢了（卡片要按原序展示）。
SUMMARY_COLUMNS = """
    r.slug, r.title, r.summary, r.stack, r.category, r.level, r.type,
    r.duration_min, r.author, r.license, r.source_url, r.url, r.cover_path,
    r.runnable, r.lang, r.source,
    (SELECT GROUP_CONCAT(tag, char(31)) FROM (
        SELECT tag FROM resource_tags WHERE resource_id = r.id ORDER BY rowid
     )) AS tags_raw
"""


def split_tags(raw: str | None) -> list[str]:
    """`GROUP_CONCAT` 结果 → 标签数组。空 / 无标签 → `[]`（不是 `null`）。"""
    if not raw:
        return []
    return [tag for tag in raw.split(TAG_SEP) if tag]


def split_runnable(raw: str | None) -> list[str] | None:
    """M1 是 TEXT 列原样透出（`"python,javascript"`），契约要求数组（D7 / API.md §6-2）。"""
    if not raw:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def summary_row(row, *, with_id: bool = False) -> dict:
    """一条 `ResourceSummary`。列表与相关推荐都**不带 `id`**（§5.1：前端不得用内部主键）。"""
    item = {
        "slug": row["slug"],
        "title": row["title"],
        "summary": row["summary"],
        "stack": row["stack"],
        "category": row["category"],
        "level": row["level"],
        "type": row["type"],
        "duration_min": row["duration_min"],
        "author": row["author"],
        "license": row["license"],
        "source_url": row["source_url"],
        "url": row["url"],
        "cover_path": row["cover_path"],
        "tags": split_tags(row["tags_raw"]),
        "runnable": split_runnable(row["runnable"]),
        "lang": row["lang"],
        "source": row["source"] or "repo",
    }
    if with_id:
        item["id"] = row["id"]
    return item


def related(conn, slug: str, limit: int = 6) -> list[dict]:
    """同分类的相关推荐，最多 6 条（§11 / `API.md` §3.5）。

    - slug 不存在 → `[]`（404 由 API 层在调用前判定，契约见 `MODULES.md` §2.2）；
    - 排除自身与 `type=overview`（章节导读进去就是空页，§6.5）；
    - M2 只有“同分类”这一路召回，`semantic_available` 由 API 层给 `false`。
    """
    row = conn.execute(
        "SELECT id, stack, category FROM resources WHERE slug = ? AND is_deleted = 0",
        (slug,),
    ).fetchone()
    if not row:
        return []

    rows = conn.execute(
        "SELECT " + SUMMARY_COLUMNS + """ FROM resources r
           WHERE r.is_deleted = 0 AND r.type <> 'overview'
             AND r.slug <> ? AND r.stack = ? AND r.category = ?
           ORDER BY r.file_path, r.slug LIMIT ?""",
        (slug, row["stack"], row["category"], max(0, limit)),
    ).fetchall()
    return [summary_row(item) for item in rows]
