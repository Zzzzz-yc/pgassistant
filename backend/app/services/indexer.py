"""内容导入（需求文档 §6.1 bootstrap / §6.2 增量）。

对外只暴露 index_all()：幂等、事务化、失败回滚保留旧库。
"""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import re

from . import frontmatter, tokenizer
from ..db import migrate
from ..db.connection import connect

FENCE_LANG = re.compile(r"```[A-Za-z0-9+#_-]*")


def strip_fence_lang(body):
    """去掉代码围栏后的语言标记，否则每段 Java 代码都会贡献一次 java 词频。"""
    return FENCE_LANG.sub(" ", body)

TYPE_SET = {"article", "video", "course", "book", "docs", "exercise", "summary", "overview"}
LEVEL_SET = {"beginner", "intermediate", "advanced"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iter_markdown(content_dir):
    root = Path(content_dir)
    for p in sorted(root.rglob("*.md")):
        if p.name.lower().startswith("credits"):
            continue
        yield p


def estimate_minutes(body, meta):
    if meta.get("duration_min"):
        try:
            return max(1, min(600, int(meta["duration_min"])))
        except ValueError:
            pass
    cjk = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff")
    return max(5, min(120, round(cjk / 400)))


def build_record(path, content_dir):
    raw = path.read_text(encoding="utf-8")
    meta, body = frontmatter.parse(raw)
    rel = path.relative_to(Path(content_dir).parent).as_posix()
    slug = str(meta.get("slug") or frontmatter.slug_from_path(rel)).strip()

    level = str(meta.get("level", "beginner")).strip()
    if level not in LEVEL_SET:
        level = "beginner"
    rtype = str(meta.get("type", "article")).strip()
    if rtype not in TYPE_SET:
        rtype = "article"

    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = [t for t in tags if t]

    runnable = meta.get("runnable") or []
    if isinstance(runnable, str):
        runnable = [runnable]

    return {
        "slug": slug,
        "title": str(meta.get("title", path.stem)).strip(),
        "summary": str(meta.get("summary", "")).strip(),
        "body_md": body.strip(),
        "stack": str(meta.get("stack", "cs-basics")).strip(),
        "category": str(meta.get("category", "misc")).strip(),
        "level": level,
        "type": rtype,
        "duration_min": estimate_minutes(body, meta),
        "url": meta.get("url") or None,
        "cover_path": meta.get("cover") or None,
        "author": str(meta.get("author", "")).strip(),
        "license": str(meta.get("license", "")).strip(),
        "source_url": meta.get("source_url") or None,
        "runnable": ",".join(runnable) if runnable else None,
        "lang": str(meta.get("lang", "zh")).strip(),
        "tags": tags,
        "rel": rel,
        "file_path": rel,
        "content_hash": sha256(raw),
        "mtime": int(path.stat().st_mtime),
        "size": path.stat().st_size,
    }


def index_all(db_path, content_dir, rebuild=False, verbose=False):
    # 建表与升级统一走迁移框架（§5.2）：老库在这里自动升级并留下备份
    migration = migrate.ensure_schema(db_path)
    conn = connect(db_path)

    records = [build_record(p, content_dir) for p in iter_markdown(content_dir)]
    version = sha256("\n".join(sorted(r["rel"] + ":" + r["content_hash"] for r in records)))

    row = conn.execute("SELECT content_version FROM index_state WHERE id = 1").fetchone()
    if row and row["content_version"] == version and not rebuild:
        conn.close()
        return {
            "skipped": True,
            "files": len(records),
            "version": version,
            "db": str(db_path),
            "migration": migration,
        }

    tokenizer.add_words(
        [w for r in records for w in r["tags"]]
        + [r["title"] for r in records]
    )

    ts = now_iso()
    with conn:
        conn.execute("DELETE FROM resources_fts")
        conn.execute("DELETE FROM resource_tags")
        conn.execute("DELETE FROM resources")
        conn.execute("DELETE FROM content_files")

        for r in records:
            cur = conn.execute(
                """INSERT INTO resources
                   (slug, title, summary, body_md, body_tokens, stack, category, level, type,
                    duration_min, url, cover_path, source, file_path, content_hash,
                    author, license, source_url, runnable, lang, is_deleted, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                (
                    r["slug"], r["title"], r["summary"], r["body_md"],
                    tokenizer.index_text(strip_fence_lang(r["body_md"][:8000])),
                    r["stack"], r["category"], r["level"], r["type"],
                    r["duration_min"], r["url"], r["cover_path"], "repo",
                    r["file_path"], r["content_hash"], r["author"], r["license"],
                    r["source_url"], r["runnable"], r["lang"], ts, ts,
                ),
            )
            rid = cur.lastrowid

            conn.execute(
                """INSERT INTO resources_fts
                   (rowid, title_tokens, summary_tokens, body_tokens, tags_tokens)
                   VALUES (?,?,?,?,?)""",
                (
                    rid,
                    tokenizer.index_text(r["title"]),
                    tokenizer.index_text(r["summary"]),
                    tokenizer.index_text(r["body_md"][:8000]),
                    tokenizer.index_text(" ".join(r["tags"])),
                ),
            )
            for tag in r["tags"]:
                conn.execute(
                    "INSERT OR IGNORE INTO resource_tags (resource_id, tag) VALUES (?,?)",
                    (rid, tag),
                )
            conn.execute(
                """INSERT INTO content_files (path, mtime, size, hash, resource_slug)
                   VALUES (?,?,?,?,?)""",
                (r["rel"], r["mtime"], r["size"], r["content_hash"], r["slug"]),
            )

        conn.execute(
            """INSERT INTO index_state (id, content_version, last_indexed_at, file_count)
               VALUES (1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 content_version = excluded.content_version,
                 last_indexed_at = excluded.last_indexed_at,
                 file_count      = excluded.file_count""",
            (version, ts, len(records)),
        )

    conn.close()
    return {
        "skipped": False,
        "files": len(records),
        "version": version,
        "db": str(db_path),
        "indexed_at": ts,
        "migration": migration,
    }
