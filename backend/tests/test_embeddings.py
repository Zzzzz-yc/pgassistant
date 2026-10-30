"""M5 语义检索测试（需求文档 §6.4 / §6.5 / §6.6，`MODULES.md` §2.2 / §2.5）。

**不真调外部 embedding**：向量由测试直接写进 `embeddings` 表（假向量），
生成路径用假的 `LLMClient`（只实现 `embed` / `aclose`），查询向量用 monkeypatch。

覆盖：

- float32 BLOB 打包 / 解包、矩阵归一化、余弦阈值（<0.25 丢弃）；
- `build_all` 按批 32 条、写 `embeddings` + `index_state.model_fingerprint`、换模型全量重建；
- `available()` 的三条件（配置 + 指纹 + 有向量）；
- 混合排序 `0.65*bm25_norm + 0.35*cos_norm`、语义独有条目的合并；
- 降级：未配置 → `semantic_available=None`；配了但没向量 → `False` + `notice`，**不报错**。
"""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import deps
from app.core import config as core_config
from app.db import migrate
from app.db.connection import connect
from app.services import embeddings, secrets, search, search_semantic, tokenizer
from app.services.llm_client import LLMConfig

BASE = "http://127.0.0.1:8000"
ENV_NAMES = (
    "PG_EMBEDDING_BASE_URL", "PG_EMBEDDING_MODEL", "PG_EMBEDDING_API_KEY",
    "PG_LLM_BASE_URL", "PG_LLM_MODEL", "PG_LLM_API_KEY",
)
EMB_ENV = {
    "PG_EMBEDDING_BASE_URL": "https://emb.test/v1",
    "PG_EMBEDDING_MODEL": "fake-embedding",
    "PG_EMBEDDING_API_KEY": "sk-embed-test",
}

#: (slug, title, tokens, level, type)
SEED = [
    ("a/closure", "闭包", "闭包 作用域", "beginner", "article"),
    ("a/scope", "闭包作用域链", "闭包 链 变量", "intermediate", "article"),
    ("a/unrelated", "数组", "数组 链表 内存", "beginner", "article"),
    ("a/functional", "函数式编程", "函数式 高阶 组合", "advanced", "docs"),
]


def _seed(db_path):
    migrate.ensure_schema(db_path)
    conn = connect(db_path)
    try:
        with conn:
            for slug, title, tokens, level, rtype in SEED:
                cur = conn.execute(
                    """INSERT INTO resources
                       (slug, title, summary, body_md, body_tokens, stack, category,
                        level, type, duration_min, file_path, content_hash,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (slug, title, title, "正文", tokens, "cs-basics", "misc",
                     level, rtype, 10, "content/x.md", "hash-" + slug,
                     "2026-10-30", "2026-10-30"),
                )
                conn.execute(
                    """INSERT INTO resources_fts
                       (rowid, title_tokens, summary_tokens, body_tokens, tags_tokens)
                       VALUES (?,?,?,?,?)""",
                    (cur.lastrowid, tokenizer.index_text(title),
                     tokenizer.index_text(title),
                     tokenizer.index_text(tokens), ""),
                )
            conn.execute(
                """INSERT INTO index_state (id, content_version, last_indexed_at, file_count)
                   VALUES (1, 'v1', '2026-10-30T00:00:00+00:00', ?)""",
                (len(SEED),),
            )
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = data / "app.db"
    _seed(db)
    settings = core_config.Settings(content_dir=tmp_path / "content", db_path=db)
    monkeypatch.setattr(core_config, "SETTINGS", settings)
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(secrets, "_keyring", lambda: None)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    embeddings.invalidate_cache()
    return SimpleNamespace(data_dir=data, db_path=db)


@pytest.fixture()
def conn(env):
    connection = connect(env.db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def client(env):
    with TestClient(main.app, base_url=BASE) as instance:
        yield instance


@pytest.fixture()
def auth(client) -> dict:
    page = client.get("/")
    marker = 'name="x-app-token" content="'
    start = page.text.index(marker) + len(marker)
    return {"X-App-Token": page.text[start:page.text.index('"', start)]}


# ------------------------------------------------------------------ 工具
def _id(conn, slug: str) -> int:
    return conn.execute("SELECT id FROM resources WHERE slug = ?", (slug,)).fetchone()["id"]


def _put_vector(conn, slug: str, vec, *, model: str = "fake-embedding") -> None:
    conn.execute(
        """INSERT OR REPLACE INTO embeddings (resource_id, model, dim, vec, created_at)
           VALUES (?,?,?,?,?)""",
        (_id(conn, slug), model, len(vec), embeddings.pack_vector(vec),
         "2026-10-30T00:00:00+00:00"),
    )
    conn.commit()
    embeddings.invalidate_cache()


def _mark_fingerprint(conn) -> None:
    conn.execute("UPDATE index_state SET model_fingerprint = ? WHERE id = 1",
                 (embeddings.fingerprint(conn),))
    conn.commit()


def _enable_embedding(monkeypatch) -> None:
    for name, value in EMB_ENV.items():
        monkeypatch.setenv(name, value)


class FakeLLM:
    """假 embedding 客户端：记录每次请求的条数，返回确定性向量。"""

    def __init__(self, config):
        self.config = config
        self.batches: list[int] = []

    async def embed(self, texts, *, timeout: float = 30.0):
        self.batches.append(len(texts))
        return [[1.0, float(index)] for index, _ in enumerate(texts)]

    async def aclose(self):
        return None


# ------------------------------------------------------------- 打包 / 矩阵
def test_pack_and_unpack_float32():
    blob = embeddings.pack_vector([0.5, -1.25, 3.0])
    assert len(blob) == 3 * 4
    assert embeddings.unpack_vector(blob) == pytest.approx([0.5, -1.25, 3.0])


def test_load_matrix_normalizes_rows(conn):
    _put_vector(conn, "a/closure", [3.0, 4.0])          # 长度 5 → 归一化成 [0.6, 0.8]
    matrix, slugs = embeddings.load_matrix(conn)

    assert slugs == ["a/closure"]
    row = matrix[0]
    values = list(row) if not hasattr(row, "tolist") else row.tolist()
    assert values == pytest.approx([0.6, 0.8], rel=1e-6)
    assert math.isclose(sum(value * value for value in values), 1.0, rel_tol=1e-6)


def test_top_k_drops_below_threshold_and_sorts(conn):
    _put_vector(conn, "a/closure", [1.0, 0.0])          # cos 1.0
    _put_vector(conn, "a/scope", [0.6, 0.8])            # cos 0.6
    _put_vector(conn, "a/unrelated", [0.2, 0.98])       # cos 0.2 → 丢弃

    hits = embeddings.top_k(conn, [1.0, 0.0], k=10, min_cos=0.25)
    slugs = [slug for slug, _cos in hits]

    assert slugs == ["a/closure", "a/scope"]
    assert all(cos >= 0.25 for _slug, cos in hits)
    assert hits[0][1] == pytest.approx(1.0, rel=1e-6)


# ------------------------------------------------------------- 生成向量
def _bulk_resources(conn, count: int) -> None:
    with conn:
        for index in range(count):
            conn.execute(
                """INSERT INTO resources
                   (slug, title, summary, body_md, body_tokens, stack, category, level,
                    type, duration_min, file_path, content_hash, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("bulk/%03d" % index, "批量 %d" % index, "", "正文", "批量", "cs-basics",
                 "misc", "beginner", "article", 5, "content/bulk.md", "h%d" % index,
                 "2026-10-30", "2026-10-30"),
            )


def test_build_all_batches_by_32_and_sets_fingerprint(conn, env, monkeypatch):
    _enable_embedding(monkeypatch)
    _bulk_resources(conn, 40)

    client = FakeLLM(LLMConfig(base_url="https://emb.test/v1", api_key="k",
                               model="fake-embedding"))
    progress: list[tuple[int, int]] = []
    written = asyncio.run(
        embeddings.build_all(conn, client, progress=lambda d, t: progress.append((d, t)))
    )

    assert written == 44                            # 种子 4 + 批量 40
    assert client.batches == [32, 12]               # 按批 32 条
    assert progress[-1] == (44, 44)

    rows = conn.execute("SELECT COUNT(*) AS n, MIN(dim) AS dim FROM embeddings").fetchone()
    assert rows["n"] == 44
    assert rows["dim"] == 2

    stored = conn.execute("SELECT model_fingerprint FROM index_state WHERE id = 1").fetchone()
    assert stored["model_fingerprint"] == embeddings.fingerprint(conn)
    assert embeddings.available(conn) is True


def test_build_all_rebuilds_when_model_changes(conn, env, monkeypatch):
    _enable_embedding(monkeypatch)
    client = FakeLLM(LLMConfig(base_url="https://emb.test/v1", api_key="k",
                               model="fake-embedding"))
    asyncio.run(embeddings.build_all(conn, client))

    # 换模型：指纹变化 → 先清空再全量重建
    monkeypatch.setenv("PG_EMBEDDING_MODEL", "another-embedding")
    other = FakeLLM(LLMConfig(base_url="https://emb.test/v1", api_key="k",
                              model="another-embedding"))
    written = asyncio.run(embeddings.build_all(conn, other))

    assert written == len(SEED)
    models = {row["model"] for row in conn.execute("SELECT model FROM embeddings")}
    assert models == {"another-embedding"}


# ------------------------------------------------------------------ 可用性
def test_available_requires_config_fingerprint_and_rows(conn, monkeypatch):
    assert embeddings.available(conn) is False          # 未配置

    _enable_embedding(monkeypatch)
    assert embeddings.available(conn) is False          # 配了但没向量

    _put_vector(conn, "a/closure", [1.0, 0.0])
    assert embeddings.available(conn) is False          # 指纹还没写

    _mark_fingerprint(conn)
    assert embeddings.available(conn) is True

    conn.execute("UPDATE index_state SET model_fingerprint = 'stale' WHERE id = 1")
    conn.commit()
    assert embeddings.available(conn) is False          # 指纹对不上 → 要重建


# ------------------------------------------------------------------ 混合排序
def test_hybrid_ranking_uses_weights_and_drops_low_cosine(conn, monkeypatch):
    _enable_embedding(monkeypatch)
    _mark_fingerprint(conn)
    _put_vector(conn, "a/closure", [1.0, 0.0])          # cos 1.0
    _put_vector(conn, "a/scope", [0.6, 0.8])            # cos 0.6
    _put_vector(conn, "a/unrelated", [0.2, 0.98])       # cos 0.2 → 丢弃
    _put_vector(conn, "a/functional", [0.9, 0.435])     # 语义独有命中（FTS 里没有）

    monkeypatch.setattr(search_semantic, "query_vector", lambda _conn, _text: [1.0, 0.0])

    base = search.search(conn, "闭包")
    assert [item["slug"] for item in base["items"]] == ["a/closure", "a/scope"]
    bm25 = {item["slug"]: item["score"] for item in base["items"]}

    result = search_semantic.hybrid_search(conn, "闭包")
    slugs = [item["slug"] for item in result["items"]]

    assert result["semantic"] is True
    assert "a/unrelated" not in slugs                   # 余弦 < 0.25 被丢弃
    assert "a/functional" in slugs                      # 语义独有命中被合并进来
    assert slugs[0] == "a/closure"

    by_slug = {item["slug"]: item["score"] for item in result["items"]}
    assert by_slug["a/closure"] == pytest.approx(
        round(0.65 * bm25["a/closure"] + 0.35 * 1.0, 4), abs=1e-3)
    assert by_slug["a/scope"] == pytest.approx(
        round(0.65 * bm25["a/scope"] + 0.35 * 0.6, 4), abs=1e-3)
    assert by_slug["a/functional"] == pytest.approx(round(0.35 * 0.9, 4), abs=1e-3)

    scores = [item["score"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_degrades_to_pure_fts_without_embeddings(conn, monkeypatch):
    monkeypatch.setattr(search_semantic, "query_vector", lambda _conn, _text: [1.0, 0.0])
    base = search.search(conn, "闭包")
    result = search_semantic.hybrid_search(conn, "闭包")

    assert result["semantic"] is None                   # 没配 embedding：不提示、不报错
    assert result["items"] == base["items"]


def test_hybrid_reports_false_when_configured_but_unusable(conn, monkeypatch):
    _enable_embedding(monkeypatch)                      # 配了，但没有向量 / 指纹
    result = search_semantic.hybrid_search(conn, "闭包")

    assert result["semantic"] is False
    assert search_semantic.notice(conn)                  # 降级提示
    assert result["items"]                              # 关键词结果照常返回


# ------------------------------------------------------------------ 接口层
def test_search_api_without_embedding_has_no_notice(client, auth):
    body = client.get("/api/search?q=闭包", headers=auth).json()

    assert body["total"] == 2
    assert body["semantic_available"] is None
    assert body["notice"] is None


def test_search_api_degrades_with_notice_when_configured(conn, client, auth, monkeypatch):
    _enable_embedding(monkeypatch)                      # 配了但没建向量
    monkeypatch.setattr(search_semantic, "query_vector", lambda _conn, _text: [1.0, 0.0])

    body = client.get("/api/search?q=闭包", headers=auth).json()
    assert body["semantic_available"] is False
    assert body["notice"]
    assert body["total"] == 2                           # 降级不丢结果，也不报 NO_EMBEDDING


def test_search_api_uses_semantic_when_available(conn, client, auth, monkeypatch):
    _enable_embedding(monkeypatch)
    _mark_fingerprint(conn)
    _put_vector(conn, "a/closure", [1.0, 0.0])
    _put_vector(conn, "a/functional", [0.9, 0.435])
    monkeypatch.setattr(search_semantic, "query_vector", lambda _conn, _text: [1.0, 0.0])

    body = client.get("/api/search?q=闭包&limit=5", headers=auth).json()
    assert body["semantic_available"] is True
    assert body["notice"] is None
    assert "a/functional" in [item["slug"] for item in body["items"]]
    assert 0.0 <= body["items"][0]["score"] <= 1.0
