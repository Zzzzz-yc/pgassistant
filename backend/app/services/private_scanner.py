"""私有资料目录扫描（需求文档 §4.4 / §6.2 / §10；`API.md` §3.24）。

与 `content/` 共用同一套解析（`services/indexer.build_record`）与同一套 diff 语义
（§6.2：新增 / 修改 / 删除），差别只有三处：

1. **根不同**：默认 `data/private/`，可由 `settings.private_dir` 覆盖，但 resolve 后
   **必须仍在 `data/private` 内**（§13.3-1），越界 → `PathForbidden`（403 `PATH_FORBIDDEN`）；
2. **slug 命名空间**：强制 `private/` 前缀（`MODULES.md` §4.5-3），`source='private'`；
3. **删除软删**：文件没了 → `resources.is_deleted = 1`，**FTS 行保留**（§6.2 原文），
   查询侧靠 `is_deleted = 0` 过滤，计划条目不会变成死链。

只在手动触发时执行（`POST /api/private/rescan`），不做文件监听（§4.4）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ..core import config as core_config
from ..schemas.models import PrivateRescanResponse
from . import frontmatter, indexer, tokenizer

log = logging.getLogger("pgassistant.private")

PRIVATE_SOURCE = "private"
PRIVATE_SLUG_PREFIX = "private/"
SETTING_KEY = "private_dir"
REPORT_KEY = "last_private_scan"
MAX_ERRORS = 50

#: slug 里不允许出现的字符（空白、控制字符、Windows 保留符号、`?#%`）
_BAD_SLUG_CHARS = re.compile(r"[\s\x00-\x1f\\?#%]+")


class PathForbidden(Exception):
    """私有路径越界（`../`、符号链接逃逸、指向 data/private 之外）→ 403 `PATH_FORBIDDEN`。"""


class PrivateDirInvalid(Exception):
    """目录不存在 / 不可读 / 不是绝对路径 → 400 `PRIVATE_DIR_INVALID`。

    契约 `MODULES.md` §2.5 只登记了 `PathForbidden`；400 这一侧必须有独立的异常，
    否则「越界」与「不存在」会被压成同一个状态码（API.md §3.20 要求区分）。
    """


# ------------------------------------------------------------------ 路径
def _data_dir() -> Path:
    return Path(core_config.SETTINGS.data_dir)


def private_root_default() -> Path:
    """默认根 `data/private`（不 resolve，方便比较）。"""
    return _data_dir() / "private"


def _resolved_default_root() -> Path:
    return private_root_default().resolve()


def resolve_private_root(raw: str | None, *, require_absolute: bool = True) -> Path:
    """把配置里的 `private_dir` 解析成绝对路径；不合法按异常类型区分 400 / 403。

    - 空 → 默认 `data/private`；
    - 相对路径 → `PrivateDirInvalid`（API.md §3.20 要求"绝对路径"）；
    - resolve 后不在 `data/private` 内 → `PathForbidden`；
    - 不存在 / 不可读 → `PrivateDirInvalid`。

    **`require_absolute=False` 只给读路径用**（`rescan`）。原因：`settings.private_dir`
    落库与 `GET /api/settings` 回显的是**项目根相对**形式（`MODULES.md` §2.5 的
    `"data/private"`，且不泄露服务器绝对路径，§13.1-3），而 `PUT` 收的是绝对路径
    （`API.md` §3.20）。若读路径也强制绝对路径，"设置页保存 → 重扫"就会立刻 400。
    写路径（`settings_service.write_settings`）保持 `require_absolute=True`。
    """
    base = _resolved_default_root()
    text = (raw or "").strip()
    if not text:
        return base

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        if require_absolute:
            raise PrivateDirInvalid("私有资料目录必须是绝对路径")
        # 回落：把相对形式按项目根解析（与 core/config.py 对 PG_* 路径的口径一致）
        candidate = Path(core_config.PROJECT_ROOT) / candidate

    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise PathForbidden("私有资料目录必须在 data/private 内")
    if not resolved.is_dir():
        raise PrivateDirInvalid("私有资料目录不存在")
    if not os.access(resolved, os.R_OK):
        raise PrivateDirInvalid("私有资料目录不可读")
    return resolved


def display_root(root: Path) -> str:
    """对外只回显项目根相对路径（不回显服务器绝对路径，§13.1-3）。

    根不在项目内时（测试 / 特殊部署把 `PG_DB` 指到别处）**退回绝对 posix 路径**：
    这样落库的值仍然能被 `resolve_private_root` 读回来，`PUT → rescan` 不会断链。
    """
    project = Path(core_config.PROJECT_ROOT).resolve()
    resolved = Path(root).resolve()
    try:
        return resolved.relative_to(project).as_posix()
    except ValueError:
        return resolved.as_posix()


# ------------------------------------------------------------------ slug
def _safe_segment(segment: str) -> str:
    segment = _BAD_SLUG_CHARS.sub("-", segment).strip()
    segment = segment.strip(".")
    return segment or "untitled"


def private_slug(rel_path: str, meta: dict) -> str:
    """私有 slug：`private/` + 相对路径（或 front-matter 的 slug），并清洗非法段。"""
    raw = str(meta.get("slug") or "").strip() or frontmatter.slug_from_path(rel_path)
    raw = raw.replace("\\", "/")
    segments = [_safe_segment(part) for part in raw.split("/") if part not in ("", ".", "..")]
    body = "/".join(segments) or "untitled"
    if body.startswith(PRIVATE_SLUG_PREFIX):
        body = body[len(PRIVATE_SLUG_PREFIX):]
    return PRIVATE_SLUG_PREFIX + body


# ------------------------------------------------------------------ 扫描
def _iter_markdown(root: Path):
    for path in sorted(root.rglob("*.md")):
        if path.name.lower().startswith("credits"):
            continue
        yield path


def _read_setting(conn, key: str):
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except Exception:                              # 库/表不可用：当作没配
        return None
    if not row:
        return None
    raw = row["value"] if hasattr(row, "keys") else row[0]
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _put_setting(conn, key: str, value, ts: str) -> None:
    conn.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                          updated_at = excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), ts),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_rel(path: Path) -> str:
    project = Path(core_config.PROJECT_ROOT).resolve()
    try:
        return path.resolve().relative_to(project).as_posix()
    except ValueError:
        return path.name


def _record_for(path: Path, root: Path) -> dict:
    """读一个私有文件：先要求 front-matter 有 title，再复用 indexer 的解析。"""
    raw = path.read_text(encoding="utf-8")
    meta, _body = frontmatter.parse(raw)
    rel_from_root = path.relative_to(root).as_posix()
    if not str(meta.get("title") or "").strip():
        raise ValueError(f"{rel_from_root}: front-matter 缺 title")

    record = indexer.build_record(path, root)
    record["slug"] = private_slug(rel_from_root, meta)
    # content_files.path 口径是"相对项目根"（DB.md §11），私有文件写 data/private/…
    record["file_path"] = _project_rel(path)
    record["rel"] = record["file_path"]
    return record


def _body_tokens(record: dict) -> str:
    return tokenizer.index_text(indexer.strip_fence_lang(record["body_md"][:8000]))


def _upsert_fts(conn, resource_id: int, record: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO resources_fts
           (rowid, title_tokens, summary_tokens, body_tokens, tags_tokens)
           VALUES (?,?,?,?,?)""",
        (
            resource_id,
            tokenizer.index_text(record["title"]),
            tokenizer.index_text(record["summary"]),
            tokenizer.index_text(record["body_md"][:8000]),
            tokenizer.index_text(" ".join(record["tags"])),
        ),
    )


def _replace_tags(conn, resource_id: int, tags) -> None:
    conn.execute("DELETE FROM resource_tags WHERE resource_id = ?", (resource_id,))
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO resource_tags (resource_id, tag) VALUES (?,?)",
            (resource_id, tag),
        )


def _upsert_content_file(conn, record: dict) -> None:
    conn.execute(
        """INSERT INTO content_files (path, mtime, size, hash, resource_slug)
           VALUES (?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             mtime = excluded.mtime, size = excluded.size,
             hash = excluded.hash, resource_slug = excluded.resource_slug""",
        (record["file_path"], record["mtime"], record["size"],
         record["content_hash"], record["slug"]),
    )


INSERT_SQL = """
    INSERT INTO resources
      (slug, title, summary, body_md, body_tokens, stack, category, level, type,
       duration_min, url, cover_path, source, file_path, content_hash,
       author, license, source_url, runnable, lang, is_deleted, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)
"""

UPDATE_SQL = """
    UPDATE resources SET
      slug = ?, title = ?, summary = ?, body_md = ?, body_tokens = ?, stack = ?,
      category = ?, level = ?, type = ?, duration_min = ?, url = ?, cover_path = ?,
      source = ?, file_path = ?, content_hash = ?, author = ?, license = ?,
      source_url = ?, runnable = ?, lang = ?, is_deleted = 0, updated_at = ?
    WHERE id = ?
"""


def _insert_row(conn, record: dict, ts: str) -> int:
    cur = conn.execute(
        INSERT_SQL,
        (
            record["slug"], record["title"], record["summary"], record["body_md"],
            _body_tokens(record), record["stack"], record["category"], record["level"],
            record["type"], record["duration_min"], record["url"], record["cover_path"],
            PRIVATE_SOURCE, record["file_path"], record["content_hash"],
            record["author"], record["license"], record["source_url"],
            record["runnable"], record["lang"], ts, ts,
        ),
    )
    return int(cur.lastrowid)


def _update_row(conn, resource_id: int, record: dict, ts: str) -> None:
    conn.execute(
        UPDATE_SQL,
        (
            record["slug"], record["title"], record["summary"], record["body_md"],
            _body_tokens(record), record["stack"], record["category"], record["level"],
            record["type"], record["duration_min"], record["url"], record["cover_path"],
            PRIVATE_SOURCE, record["file_path"], record["content_hash"],
            record["author"], record["license"], record["source_url"],
            record["runnable"], record["lang"], ts, resource_id,
        ),
    )


def rescan(conn, root: Path | None = None) -> PrivateRescanResponse:
    """重扫私有目录，返回新增 / 更新 / 删除计数（`API.md` §3.24）。

    单文件失败只进 `errors`，不中断整批；整批写在一个事务里。
    """
    started = time.perf_counter()
    if root is None:
        # 读路径：库里存的是项目根相对形式（见 display_root 与 resolve_private_root）
        root = resolve_private_root(_read_setting(conn, SETTING_KEY), require_absolute=False)
    root = Path(root)

    default_root = _resolved_default_root()
    if not root.is_dir():
        if root.resolve() == default_root:
            root.mkdir(parents=True, exist_ok=True)     # 首次扫描自动建目录
        else:
            raise PrivateDirInvalid("私有资料目录不存在")

    records: list[dict] = []
    errors: list[str] = []
    scanned = 0
    for path in _iter_markdown(root):
        scanned += 1
        try:
            records.append(_record_for(path, root))
        except Exception as exc:                      # 单个文件坏掉不影响整批
            message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            if len(errors) < MAX_ERRORS:
                errors.append(message[:200])

    ts = _now_iso()
    added = updated = removed = skipped = 0
    seen_paths: set[str] = set()

    with conn:
        existing = {
            row["file_path"]: dict(row)
            for row in conn.execute(
                "SELECT id, slug, file_path, content_hash, is_deleted"
                " FROM resources WHERE source = ?",
                (PRIVATE_SOURCE,),
            )
        }
        by_slug = {row["slug"]: row for row in existing.values()}

        for record in records:
            seen_paths.add(record["file_path"])
            row = existing.get(record["file_path"])
            target = None
            if row is not None:
                target = row
            elif record["slug"] in by_slug:           # 文件改名：同 slug 的老行
                target = by_slug[record["slug"]]

            try:
                if target is None:
                    resource_id = _insert_row(conn, record, ts)
                    added += 1
                elif target["content_hash"] == record["content_hash"] \
                        and target.get("slug") == record["slug"] \
                        and not target.get("is_deleted"):
                    skipped += 1
                    _upsert_content_file(conn, record)
                    continue
                else:
                    # 内容变了 / 改名 / 之前被软删（文件又回来了）都走更新，
                    # 更新会把 is_deleted 置回 0
                    _update_row(conn, target["id"], record, ts)
                    resource_id = target["id"]
                    updated += 1
            except Exception as exc:                  # slug 冲突等：记错误，继续下一份
                if len(errors) < MAX_ERRORS:
                    errors.append(f"{record['file_path']}: {type(exc).__name__}")
                continue

            _upsert_fts(conn, resource_id, record)
            _replace_tags(conn, resource_id, record["tags"])
            _upsert_content_file(conn, record)

        for row in existing.values():
            if row["file_path"] in seen_paths or row.get("is_deleted"):
                continue                              # 已软删的不要重复计数
            conn.execute(
                "UPDATE resources SET is_deleted = 1, updated_at = ? WHERE id = ?",
                (ts, row["id"]),
            )
            conn.execute("DELETE FROM content_files WHERE path = ?", (row["file_path"],))
            removed += 1

        _put_setting(conn, REPORT_KEY, {
            "scanned": scanned,
            "added": added,
            "updated": updated,
            "removed": removed,
            "at": ts,
        }, ts)

    took_ms = max(int((time.perf_counter() - started) * 1000), 0)
    log.info(
        "event=private.rescan scanned=%d added=%d updated=%d removed=%d skipped=%d errors=%d",
        scanned, added, updated, removed, skipped, len(errors),
    )
    return PrivateRescanResponse(
        root=display_root(root),
        scanned=scanned,
        added=added,
        updated=updated,
        removed=removed,
        skipped=skipped,
        errors=errors,
        took_ms=took_ms,
    )


def last_report(conn) -> dict | None:
    """最近一次扫描结果（`settings.last_private_scan`，§10）。"""
    value = _read_setting(conn, REPORT_KEY)
    return value if isinstance(value, dict) else None
