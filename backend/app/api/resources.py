"""资料接口（需求文档 §11；`API.md` §3.3 / §3.4 / §3.5）。

M2 在 M1 的五组只读接口上做**加法式**扩展（`API.md` §6 的施工义务清单 1–5、7）：

| # | 扩展 | 落点 |
|---|---|---|
| 1 | `took_ms` / `index_version` | `list_resources`（`time.perf_counter()` 差值） |
| 2 | `runnable` 从逗号串 → `list[str]` | `services/search_ext.summary_row()` 一处转换 |
| 3 | `tags` / `cover_path` / `source` | `summary_row()` + 一次 `GROUP_CONCAT` 子查询（无 N+1） |
| 4 | `source` / `q` / `sort` 查询参数 | `list_resources` 的签名与 `ORDER_BY` |
| 5 | 详情 `toc` / `prev` / `next` | `_toc()` / `_siblings()` |
| 7 | 详情 `media_base` | `_media_base()`（D24，前端不得由 slug 推导） |

`id` 仍然**不出现在列表响应里**（§5.1：前端不得使用内部主键），详情保留。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api.deps import get_conn
from ..core import errors
from ..core.config import SETTINGS
from ..services import tokenizer
from ..services.search_ext import SUMMARY_COLUMNS, related as related_rows, summary_row

router = APIRouter()

Level = Literal["beginner", "intermediate", "advanced"]
ResourceType = Literal[
    "article", "video", "course", "book", "docs", "exercise", "summary", "overview"
]
ResourceSource = Literal["repo", "private"]
ResourceSort = Literal["default", "title", "duration_min", "updated_at"]

#: `default` = stack, category, title（API.md §3.3 表）；其余按字段排，最后都拿 slug 兜底，
#: 保证翻页稳定（同一排序键下不会跨页重复或漏条）。
ORDER_BY = {
    "default": "r.stack, r.category, r.title, r.slug",
    "title": "r.title, r.slug",
    # NULL 放最后：没有时长的条目不该霸占列表头部
    "duration_min": "r.duration_min IS NULL, r.duration_min, r.title, r.slug",
    "updated_at": "r.updated_at DESC, r.slug",
}


def _like_escape(text: str) -> str:
    """把 LIKE 通配符转义掉，用户输入的 `%` / `_` 不该当通配符。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _q_clause(q: str | None) -> tuple[str, list]:
    """`q` 的标题/摘要模糊匹配，复用分词（§3.3 表）。

    分词后按词 OR：搜「闭包 作用域」能命中只写了「闭包」的标题。词被停用词规则清空时
    退回原文整串匹配，避免“用户输入了却什么都没过滤”。
    """
    if not q:
        return "", []
    tokens = tokenizer.query_tokens(q) or [q]
    terms, args = [], []
    for token in tokens:
        pattern = f"%{_like_escape(token)}%"
        terms.append("(r.title LIKE ? ESCAPE '\\' OR r.summary LIKE ? ESCAPE '\\')")
        args.extend([pattern, pattern])
    return " AND (" + " OR ".join(terms) + ")", args


def _index_version(conn) -> str | None:
    row = conn.execute("SELECT content_version FROM index_state WHERE id = 1").fetchone()
    return row["content_version"] if row else None


@router.get("/resources", tags=["resources"], summary="资料分页列表")
def list_resources(
    stack: str | None = Query(None, min_length=1, max_length=64),
    category: str | None = Query(None, min_length=1, max_length=64),
    level: Level | None = None,
    type: ResourceType | None = None,
    source: ResourceSource | None = None,
    q: str | None = Query(None, min_length=1, max_length=100),
    page: int = Query(1, ge=1),
    size: int = Query(24, ge=1, le=100),
    sort: ResourceSort = "default",
    conn=Depends(get_conn),
):
    started = time.perf_counter()

    where, args = "", []
    for field, value in (
        ("stack", stack),
        ("category", category),
        ("level", level),
        ("type", type),
        ("source", source),
    ):
        if value:
            where += " AND r.%s = ?" % field
            args.append(value)

    q_sql, q_args = _q_clause(q)
    where += q_sql
    args.extend(q_args)

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM resources r WHERE r.is_deleted = 0" + where, args
    ).fetchone()["c"]

    rows = conn.execute(
        "SELECT " + SUMMARY_COLUMNS + " FROM resources r WHERE r.is_deleted = 0"
        + where + " ORDER BY " + ORDER_BY[sort] + " LIMIT ? OFFSET ?",
        args + [size, (page - 1) * size],
    ).fetchall()

    took_ms = int((time.perf_counter() - started) * 1000)
    return {
        "items": [summary_row(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
        "took_ms": max(took_ms, 0),
        "index_version": _index_version(conn),
    }


# ------------------------------------------------------------------ 详情辅助


def _heading_text(raw: str) -> str:
    """去掉标题里的行内 Markdown 标记，保留纯文本（与 `MarkdownView` 的 `textOf` 对齐）。"""
    text = raw.strip()
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)   # 图片 → alt
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)    # 链接 → 文本
    text = text.replace("`", "")
    for marker in ("**", "__", "~~", "*", "_"):
        text = text.replace(marker, "")
    return text.strip()


def _anchor_for(text: str) -> str:
    """与 `MarkdownView` 的 `slugify` 同规则：小写、空白转 `-`、只留字母数字（含中文）。

    两边算法一致，`toc[].anchor` 才和正文注入的 `id` 对得上（§3.4）。
    """
    slug = re.sub(r"\s+", "-", text.strip().lower())
    # 与 JS 的 `[^\p{L}\p{N}-]` 等价：`str.isalnum()` 对中文为 True、对 `_` / `.` 为 False
    slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-")
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "section"


def _toc(body_md: str) -> list[dict]:
    """从正文抽 h2/h3，**按文档顺序**（顺序必须与 `MarkdownView` 注入 id 的顺序一致）。

    代码围栏（``` / ~~~）内的 `#` 注释不是标题，必须跳过——否则算法章节里的
    `# 初始化数组` 会被当成目录项。
    """
    pattern = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")
    nodes: list[dict] = []
    fence: str | None = None
    for raw_line in (body_md or "").splitlines():
        line = raw_line.strip()
        if fence:
            if line.startswith(fence):
                fence = None
            continue
        marker = line[:3]
        if marker.startswith("```") or marker.startswith("~~~"):
            fence = marker
            continue
        match = pattern.match(line)
        if not match:
            continue
        text = _heading_text(match.group(2))
        if not text:
            continue
        level = len(match.group(1))
        nodes.append({"level": level, "text": text, "anchor": _anchor_for(text)})
    return nodes


def _media_base(file_path: str | None) -> str:
    """md 文件所在目录的 content 根相对路径（D24 / §3.4）。

    依据 `file_path`（形如 `content/frontend/react/state.md`）而不是 slug：
    slug 的层数由 front-matter 决定，目录结构变了两者就不再相等。
    """
    if not file_path:
        return ""
    parts = Path(file_path.replace("\\", "/")).parts
    root_name = Path(SETTINGS.content_dir).name
    if parts and parts[0] == root_name:
        parts = parts[1:]
    if not parts:
        return ""
    directory = Path(*parts).parent.as_posix()
    return "" if directory == "." else directory


def _siblings(conn, row) -> tuple[dict | None, dict | None]:
    """同 `stack + category` 按 `file_path` 顺序的上一节 / 下一节（§3.4 表）。"""
    rows = conn.execute(
        """SELECT slug, title, file_path FROM resources
           WHERE is_deleted = 0 AND stack = ? AND category = ?
           ORDER BY file_path, slug""",
        (row["stack"], row["category"]),
    ).fetchall()
    for index, candidate in enumerate(rows):
        if candidate["slug"] != row["slug"]:
            continue
        prev = rows[index - 1] if index > 0 else None
        nxt = rows[index + 1] if index + 1 < len(rows) else None
        return (
            {"slug": prev["slug"], "title": prev["title"]} if prev else None,
            {"slug": nxt["slug"], "title": nxt["title"]} if nxt else None,
        )
    return None, None


def _load_resource(conn, slug: str):
    """取一条未下架的资料，含 `tags_raw`；不存在就抛 404 信封（§3.4）。"""
    row = conn.execute(
        """SELECT r.*, (SELECT GROUP_CONCAT(tag, char(31)) FROM (
                            SELECT tag FROM resource_tags WHERE resource_id = r.id
                            ORDER BY rowid
                        )) AS tags_raw
           FROM resources r WHERE r.slug = ? AND r.is_deleted = 0""",
        (slug,),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=errors.error_payload(
                errors.RESOURCE_NOT_FOUND,
                "资料不存在或已下架",
                detail={"slug": slug},
                hint="返回资料库重新挑选",
            ),
        )
    return row


@router.get("/resources/{slug:path}/related", tags=["resources"], summary="相关推荐")
def get_related(
    slug: str,
    limit: int = Query(6, ge=1, le=6),
    conn=Depends(get_conn),
):
    _load_resource(conn, slug)
    items = related_rows(conn, slug, limit)
    return {
        "items": items,
        "total": len(items),
        # M2 只做“同分类”召回；语义近邻是 M5 的能力，结果页不得因此报错（§7.9）
        "semantic_available": False,
    }


# 必须**最后**注册：`{slug:path}` 是贪婪匹配，先注册会把 `/related` 吞进 slug，
# 当成一次详情查询（slug 含 `/` 是合法值，路由层不会报错，只会静默走错分支）。
@router.get("/resources/{slug:path}", tags=["resources"], summary="资料详情")
def get_resource(slug: str, conn=Depends(get_conn)):
    row = _load_resource(conn, slug)
    item = summary_row(row, with_id=True)
    item.update({
        "body_md": row["body_md"],
        "file_path": row["file_path"],
        "content_hash": row["content_hash"],
        "is_deleted": row["is_deleted"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "toc": _toc(row["body_md"]),
        "media_base": _media_base(row["file_path"]),
    })
    prev, nxt = _siblings(conn, row)
    item["prev"] = prev
    item["next"] = nxt
    return item
