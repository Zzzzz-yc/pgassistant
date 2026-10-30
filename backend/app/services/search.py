"""检索（需求文档 §6.3 / §6.5）。

三级降级：全词 AND → 主词 AND + 其余 OR → 全词 OR（且必须命中主词）。
比「命中一个无关词就返回」更保守，避免 kafka 这类真·缺失的查询返回噪音。
"""
from . import tokenizer

Q = chr(34)


def _quote(t):
    return Q + t.replace(Q, Q + Q) + Q


def _expr(tokens, op):
    return (" %s " % op).join(_quote(t) for t in tokens)


def _filters(stack, level, rtype, category, include_overview):
    sql, args = "", []
    if stack:
        sql += " AND r.stack = ?"
        args.append(stack)
    if category:
        sql += " AND r.category = ?"
        args.append(category)
    if level:
        sql += " AND r.level = ?"
        args.append(level)
    if rtype:
        sql += " AND r.type = ?"
        args.append(rtype)
    if not include_overview:
        sql += " AND r.type <> 'overview'"
    return sql, args


def _snippet(text, tokens, width=90):
    if not text:
        return ""
    low = text.lower()
    best = None
    for t in tokens:
        i = low.find(t.lower())
        if i >= 0 and (best is None or i < best[0]):
            best = (i, len(t))
    if best is None:
        return text[:width]
    i, ln = best
    start = max(0, i - width // 3)
    end = min(len(text), i + ln + width)
    seg = text[start:end].replace("\n", " ")
    if start > 0:
        seg = "…" + seg
    if end < len(text):
        seg = seg + "…"
    for t in tokens:
        seg = seg.replace(t, "[" + t + "]")
    return seg


def _core_token(tokens):
    """主词：最长的那个，通常是最具体的概念。"""
    return max(tokens, key=len)


SQL = """
    SELECT r.id, r.slug, r.title, r.summary, r.stack, r.category, r.level, r.type,
           r.duration_min, r.author, r.license,
           resources_fts.body_tokens AS body_tokens,
           (SELECT GROUP_CONCAT(tag, ' ') FROM resource_tags t WHERE t.resource_id = r.id) AS tags,
           bm25(resources_fts, 10.0, 5.0, 1.0, 6.0) AS rank
    FROM resources_fts
    JOIN resources r ON r.id = resources_fts.rowid
    WHERE resources_fts MATCH ?
      AND r.is_deleted = 0
"""


def search(conn, q, stack=None, level=None, rtype=None, category=None,
           limit=10, include_overview=False):
    tokens = tokenizer.query_tokens(q)
    if not tokens:
        return {"items": [], "total": 0, "mode": "empty", "tokens": []}

    filt, fargs = _filters(stack, level, rtype, category, include_overview)
    core = _core_token(tokens)

    plans = []
    single = len(tokens) == 1
    # 先只在标题与标签里找，命中就用这批（避免正文里代码块把词频拉爆）
    plans.append(("FIELD", "{title_tokens tags_tokens} : " + _quote(_core_token(tokens))))
    if len(tokens) > 1:
        plans.append(("AND", _expr(tokens, "AND")))
        others = [t for t in tokens if t != core]
        plans.append(("AND+CORE", _quote(core) + " AND (" + _expr(others, "OR") + ")"))
    plans.append(("OR", _expr(tokens, "OR")))

    rows, mode = [], ("SINGLE" if single else "AND")
    for name, expr in plans:
        try:
            rows = conn.execute(SQL + filt + " ORDER BY rank LIMIT ?",
                                 [expr] + fargs + [limit * 5]).fetchall()
        except Exception as exc:
            return {"items": [], "total": 0, "mode": "error", "tokens": tokens, "error": str(exc)}
        if name == "OR" and rows:
            # OR 至少要命中主词，否则宁可不返回
            rows = [r for r in rows if core.lower() in (r["body_tokens"] or "").lower()
                    or core.lower() in (r["tags"] or "").lower()
                    or core.lower() in r["title"].lower()
                    or core.lower() in r["summary"].lower()]
        if rows:
            mode = "SINGLE" if single else name
            break

    if not rows:
        return {"items": [], "total": 0, "mode": "miss", "tokens": tokens}

    def hit_score(r):
        t = core.lower()
        return (1 if t in r["title"].lower() else 0,
                1 if t in (r["tags"] or "").lower() else 0)

    rows = sorted(rows, key=lambda r: (-hit_score(r)[0], -hit_score(r)[1], r["rank"]))

    ranks = [abs(r["rank"]) for r in rows]
    hi = max(ranks) or 1.0
    items = []
    for r in rows:
        items.append({
            "slug": r["slug"],
            "title": r["title"],
            "snippet": _snippet(r["summary"] or r["title"], tokens),
            "stack": r["stack"],
            "category": r["category"],
            "level": r["level"],
            "type": r["type"],
            "duration_min": r["duration_min"],
            "author": r["author"],
            "license": r["license"],
            "score": round(abs(r["rank"]) / hi, 4),
        })

    return {"items": items[:limit], "total": len(items), "mode": mode, "tokens": tokens}
