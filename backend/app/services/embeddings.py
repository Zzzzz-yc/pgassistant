"""语义检索的向量层（需求文档 §6.4 / `MODULES.md` §2.5 / `DB.md` §5）。

四件事：

1. **生成**：配置了 OpenAI 兼容 `/embeddings` 后，按批 32 条生成向量，
   写 `embeddings(resource_id, model, dim, vec, created_at)`，`vec = float32.tobytes()`；
2. **模型指纹**：`index_state.model_fingerprint` 变了（换 base_url / model / dim）→ 整表重建；
3. **内存矩阵**：`load_matrix` 把整表载入（行已 L2 归一化，点积就是余弦），带指纹级缓存；
4. **降级**：**任何一环失败都只是"不可用"**，不抛给调用方、不影响关键词检索（§6.6 / §4.8）。

numpy 是**可选**依赖（`requirements.txt` 里仍是注释状态）：装了就用 ndarray 做矩阵乘，
没装就退化成纯 Python 点积 —— 300 条 × 1024 维的规模两者都够用，缺 numpy 不能变成报错。
"""
from __future__ import annotations

import array
import hashlib
import logging
import math
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Callable

from . import llm_client

log = logging.getLogger("pgassistant.embeddings")

#: 每批条数（§6.4 明确写 32）
BATCH_SIZE = 32
#: 送进 /embeddings 的正文字符上限（标题 + 摘要已经够区分，避免烧 token）
TEXT_LIMIT = 1200

#: 内存矩阵缓存：键 = (指纹, 行数, 最新 created_at)
_CACHE: dict = {"key": None, "matrix": None, "slugs": []}


# ------------------------------------------------------------------ 配置
def _config(conn):
    """embedding 配置（env > settings 表；密钥走 keyring/文件）。读失败 → None。"""
    try:
        return llm_client.load_embedding_config(conn)
    except Exception as exc:                       # 配置读取失败不能连带检索不可用
        log.warning("event=embeddings.config failed error=%s", type(exc).__name__)
        return None


def _fingerprint_of(config) -> str:
    """模型指纹 = `base_url|model`。

    刻意**不含 `dim`**：dim 是"首次返回后回填"的提示值，若把它算进指纹，
    第一次把 dim 写回 settings 就会让指纹变化、触发一次无谓的全量重建。
    换 base_url / model 才是真正需要重建向量的场景（§6.4）。
    """
    raw = "%s|%s" % (config.base_url.rstrip("/"), config.model)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def fingerprint(conn) -> str | None:
    """当前 embedding 模型指纹；未配置返回 None。"""
    config = _config(conn)
    return _fingerprint_of(config) if config is not None else None



def _stored_fingerprint(conn) -> str | None:
    try:
        row = conn.execute(
            "SELECT model_fingerprint FROM index_state WHERE id = 1"
        ).fetchone()
        return (row["model_fingerprint"] or None) if row else None
    except sqlite3.Error:
        return None


def _row_count(conn) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error:
        return 0


def available(conn) -> bool:
    """是否可以走语义检索：**配置在 + 指纹对得上 + 表里有向量**（三者缺一不可）。"""
    config = _config(conn)
    if config is None:
        return False
    if _stored_fingerprint(conn) != _fingerprint_of(config):
        return False
    return _row_count(conn) > 0


def configured(conn) -> bool:
    """只判配置（不看向量表），给 `health.embedding_configured` 与启动预热用。"""
    return _config(conn) is not None


# ------------------------------------------------------------------ 打包
def pack_vector(vec) -> bytes:
    """`float32` 小端 BLOB（与 `numpy.float32.tobytes()` 逐字节一致）。"""
    values = array.array("f", [float(x) for x in vec])
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    return list(values)


def _normalize(vec) -> list[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
    return [float(x) / norm for x in vec]


def _numpy():
    try:
        import numpy  # noqa: F401  可选依赖，缺了就走纯 Python
        return numpy
    except Exception:
        return None


# ------------------------------------------------------------------ 生成
def _text_of(row) -> str:
    parts = [row["title"] or "", row["summary"] or "", (row["body_md"] or "")[:TEXT_LIMIT]]
    return "\n".join(part for part in parts if part).strip()


def _pending_rows(conn, model: str) -> list:
    return conn.execute(
        """SELECT r.id, r.slug, r.title, r.summary, r.body_md
           FROM resources r
           LEFT JOIN embeddings e ON e.resource_id = r.id AND e.model = ?
           WHERE r.is_deleted = 0 AND e.resource_id IS NULL
           ORDER BY r.id""",
        (model,),
    ).fetchall()


async def build_all(conn, llm, *, batch: int = BATCH_SIZE,
                    progress: Callable[[int, int], None] | None = None) -> int:
    """给所有缺失向量的资料生成 embedding，返回本次写入条数。

    换模型（指纹变化）时先清空整表再全量重建（§6.4）。任何失败都向上抛 `LLMError`，
    由后台任务的调用方兜住（设置页保存后的异步重建不能把服务拖垮）。
    """
    config = getattr(llm, "config", None)
    model = getattr(config, "model", "") or ""
    if not model:
        raise ValueError("embedding 模型未配置")
    target = _fingerprint_of(config)

    if _stored_fingerprint(conn) != target:
        with conn:
            conn.execute("DELETE FROM embeddings")
        invalidate_cache()

    rows = _pending_rows(conn, model)
    total = len(rows)
    done = 0
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for start in range(0, total, max(1, int(batch))):
        chunk = rows[start:start + max(1, int(batch))]
        vectors = await llm.embed([_text_of(row) for row in chunk])
        if len(vectors) != len(chunk):
            raise ValueError("上游返回的向量条数与请求不一致")
        with conn:
            for row, vec in zip(chunk, vectors):
                conn.execute(
                    """INSERT OR REPLACE INTO embeddings
                       (resource_id, model, dim, vec, created_at) VALUES (?,?,?,?,?)""",
                    (row["id"], model, len(vec), pack_vector(vec), ts),
                )
        done += len(chunk)
        if progress is not None:
            session = (done, total)
            try:
                progress(*session)
            except Exception:                  # 进度回调坏了不影响建向量
                pass

    with conn:
        # index_state 是单行表：没有行（还没建索引）就跳过，避免写入空的 content_version
        conn.execute(
            "UPDATE index_state SET model_fingerprint = ? WHERE id = 1", (target,)
        )
    invalidate_cache()
    log.info("event=embeddings.build total=%d written=%d model=%s", total, done, model)
    return done


async def embed_query(conn, text: str) -> list[float] | None:
    """把查询串向量化；未配置 / 上游失败都返回 None（调用方据此降级纯 FTS）。"""
    config = _config(conn)
    if config is None or not (text or "").strip():
        return None
    client = llm_client.LLMClient(config)
    try:
        vectors = await client.embed([text])
    except llm_client.LLMError as exc:
        log.warning("event=embeddings.query failed code=%s", exc.code)
        return None
    except Exception as exc:
        log.warning("event=embeddings.query failed error=%s", type(exc).__name__)
        return None
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    return _normalize(vectors[0]) if vectors else None


# ------------------------------------------------------------------ 读取
def invalidate_cache() -> None:
    _CACHE.update({"key": None, "matrix": None, "slugs": []})


def _cache_key(conn):
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(created_at) AS ts FROM embeddings"
        ).fetchone()
        return (_stored_fingerprint(conn), int(row["n"] or 0), row["ts"] or "")
    except sqlite3.Error:
        return None


def load_matrix(conn) -> tuple[object, list[str]]:
    """载入 `(矩阵, slugs)`：行已归一化，`matrix[i]` 对应 `slugs[i]`。

    返回空矩阵（`[]` 或 0×0 ndarray）表示"没有可用向量"，调用方应降级。
    """
    numpy = _numpy()
    key = _cache_key(conn)
    if key is not None and _CACHE.get("key") == key and _CACHE.get("matrix") is not None:
        return _CACHE["matrix"], list(_CACHE["slugs"])

    stored = _stored_fingerprint(conn)
    try:
        rows = conn.execute(
            """SELECT e.vec, r.slug
               FROM embeddings e JOIN resources r ON r.id = e.resource_id
               WHERE r.is_deleted = 0
               ORDER BY r.slug"""
        ).fetchall()
    except sqlite3.Error:
        return ([], []) if numpy is None else (numpy.zeros((0, 0), dtype="float32"), [])

    slugs: list[str] = []
    vectors: list[list[float]] = []
    for row in rows:
        try:
            vec = _normalize(unpack_vector(row["vec"]))
        except (ValueError, TypeError):
            continue
        vectors.append(vec)
        slugs.append(row["slug"])

    if numpy is not None:
        matrix = numpy.array(vectors, dtype="float32") if vectors else numpy.zeros((0, 0), "float32")
    else:
        matrix = vectors

    if key is not None:
        _CACHE.update({"key": key, "matrix": matrix, "slugs": slugs})
    return matrix, slugs


def top_k(conn, query_vec, *, k: int = 50, min_cos: float = 0.25) -> list[tuple[str, float]]:
    """与内存矩阵做余弦（点积）取前 k 条，**余弦 < min_cos 直接丢弃**（§6.4 阈值）。"""
    matrix, slugs = load_matrix(conn)
    if not slugs:
        return []
    query = _normalize(query_vec)
    scored: list[tuple[str, float]] = []
    for index, slug in enumerate(slugs):
        try:
            row = matrix[index]
            if hasattr(row, "dot"):
                cos = float(row.dot(query))
            else:
                cos = float(sum(a * b for a, b in zip(row, query)))
        except (TypeError, ValueError):
            continue
        if cos >= min_cos:
            scored.append((slug, cos))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:max(0, int(k))]


def warm_cache(conn) -> bool:
    """启动时把矩阵载进内存（lifespan 调用）；失败只记日志。"""
    try:
        matrix, slugs = load_matrix(conn)
        return bool(slugs)
    except Exception as exc:
        log.warning("event=embeddings.warm failed error=%s", type(exc).__name__)
        return False


def matrix_dim(matrix) -> int:
    """测试与日志用：矩阵维度（空矩阵返回 0）。"""
    shape = getattr(matrix, "shape", None)
    if shape:
        return int(shape[1]) if len(shape) > 1 else 0
    return len(matrix[0]) if matrix else 0


__all__ = [
    "BATCH_SIZE", "available", "configured", "fingerprint", "build_all", "embed_query",
    "load_matrix", "top_k", "warm_cache", "invalidate_cache", "pack_vector",
    "unpack_vector", "matrix_dim",
]
