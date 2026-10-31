"""M5 设置与基础设施测试（`API.md` §3.19–§3.21、§1.5/§1.6、§5；需求文档 §9.9 / §10 / §13）。

覆盖：

- `GET/PUT /api/settings` 的掩码回读、密钥不落 `settings` 表 / `config.json`、env 覆盖；
- `POST /api/settings/test` 的探针成功 / 失败 / 未配置（**httpx 全 mock，不真调 API**）；
- `private_dir` 的 400 / 403 两档；
- 韧性：405 `METHOD_NOT_ALLOWED` + `Allow`、`X-Request-Id` 透传与 5xx 信封；
- `GET /api/taxonomy` 的 `levels` / `types` 按数量降序（D25 25.5）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import deps
from app.core import config as core_config
from app.db import migrate
from app.db.connection import connect
from app.services import embeddings, secrets, settings_service
from app.services.llm_client import LLMError, ProbeResult

BASE = "http://127.0.0.1:8000"
ORIGIN = {"Origin": BASE}
PLAIN_KEY = "sk-live-1234567890abcdef"
ENV_NAMES = (
    "PG_LLM_API_KEY", "PG_LLM_BASE_URL", "PG_LLM_MODEL", "PG_LLM_TEMPERATURE",
    "PG_LLM_MAX_TOKENS", "PG_EMBEDDING_API_KEY", "PG_EMBEDDING_BASE_URL",
    "PG_EMBEDDING_MODEL",
)

SEED = [
    # (slug, title, stack, category, level, type)
    ("cs-basics/array/array", "数组", "cs-basics", "array", "beginner", "article"),
    ("cs-basics/array/list", "链表", "cs-basics", "array", "beginner", "article"),
    ("cs-basics/tree/bst", "二叉搜索树", "cs-basics", "tree", "advanced", "article"),
    ("frontend/react/state", "React 状态", "frontend", "react", "beginner", "docs"),
]


def _seed(db_path):
    migrate.ensure_schema(db_path)
    conn = connect(db_path)
    try:
        with conn:
            for slug, title, stack, category, level, rtype in SEED:
                conn.execute(
                    """INSERT INTO resources
                       (slug, title, summary, body_md, body_tokens, stack, category,
                        level, type, duration_min, file_path, content_hash,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (slug, title, "", "正文", title, stack, category, level, rtype,
                     10, "content/x.md", "hash-" + slug, "2026-10-30", "2026-10-30"),
                )
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立 data 目录：库、密钥文件、私有目录、备份都在 tmp 里，不碰 data/app.db。"""
    data = tmp_path / "data"
    db = data / "app.db"
    _seed(db)
    settings = core_config.Settings(
        content_dir=tmp_path / "content",
        db_path=db,
    )
    monkeypatch.setattr(core_config, "SETTINGS", settings)
    monkeypatch.setattr(deps, "DB_PATH", db)
    # 绝不让测试碰真实的 Windows 凭据管理器：强制 0600 文件后端
    monkeypatch.setattr(secrets, "_keyring", lambda: None)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    embeddings.invalidate_cache()
    return SimpleNamespace(data_dir=data, db_path=db, content_dir=settings.content_dir)


@pytest.fixture()
def client(env):
    with TestClient(main.app, base_url=BASE) as instance:
        yield instance


@pytest.fixture()
def token(client) -> str:
    page = client.get("/")
    assert page.status_code == 200
    marker = 'name="x-app-token" content="'
    start = page.text.index(marker) + len(marker)
    return page.text[start:page.text.index('"', start)]


@pytest.fixture()
def auth(token) -> dict:
    return {"X-App-Token": token}


def _put(client, auth, payload):
    return client.put("/api/settings", json=payload, headers={**auth, **ORIGIN})


def _llm_payload(**over):
    payload = {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": PLAIN_KEY,
        "temperature": 0.5,
        "max_tokens": 2048,
    }
    payload.update(over)
    return {"llm": payload}


# ------------------------------------------------------------ GET / 默认值
def test_get_settings_defaults(client, auth):
    body = client.get("/api/settings", headers=auth).json()

    assert body["llm"]["base_url"] is None
    assert body["llm"]["temperature"] == 0.3
    assert body["llm"]["max_tokens"] == 4096
    assert body["llm"]["api_key_masked"] is None
    assert body["embedding"]["api_key_masked"] is None
    assert body["language"] == "zh-CN"
    assert body["code_runner_enabled"] is True
    assert body["onboarded"] is False
    assert body["secret_backend"] == "file"        # 测试里 keyring 被禁用
    assert body["llm_configured"] is False
    assert body["embedding_configured"] is False


# ------------------------------------------------------------ PUT / 掩码回读
def test_put_settings_masks_key_and_reads_back_masked(client, auth, env):
    response = _put(client, auth, _llm_payload())
    assert response.status_code == 200
    body = response.json()

    assert body["llm"]["base_url"] == "https://api.deepseek.com/v1"
    assert body["llm"]["model"] == "deepseek-chat"
    assert body["llm"]["temperature"] == 0.5
    assert body["llm"]["max_tokens"] == 2048
    assert body["llm"]["api_key_masked"] == "sk-****cdef"
    assert body["llm_configured"] is True
    assert PLAIN_KEY not in response.text

    # 再读一次：仍然只有掩码（写入后立刻读也不回显明文，D11-3）
    again = client.get("/api/settings", headers=auth)
    assert again.json()["llm"]["api_key_masked"] == "sk-****cdef"
    assert PLAIN_KEY not in again.text

    # 密钥落在 0600 文件里，且是单行
    key_file = env.data_dir / "llm_key"
    assert key_file.read_text(encoding="utf-8").strip() == PLAIN_KEY
    assert key_file.read_text(encoding="utf-8").count("\n") == 1


def test_put_settings_never_writes_key_into_settings_or_config(client, auth, env):
    assert _put(client, auth, _llm_payload()).status_code == 200

    conn = connect(env.db_path)
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()

    keys = {row["key"] for row in rows}
    assert "llm.base_url" in keys
    assert all("api_key" not in key for key in keys)
    assert all(PLAIN_KEY not in row["value"] for row in rows)
    assert not (env.data_dir / "config.json").exists()
    assert not (env.data_dir / "secrets.json").exists()


def test_put_without_api_key_keeps_previous_secret(client, auth, env):
    assert _put(client, auth, _llm_payload()).status_code == 200

    body = _put(client, auth, {"llm": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-reasoner",
        "temperature": 0.2,
        "max_tokens": 512,
    }}).json()

    assert body["llm"]["model"] == "deepseek-reasoner"
    assert body["llm"]["api_key_masked"] == "sk-****cdef"     # 不传 = 保持原值
    assert (env.data_dir / "llm_key").read_text(encoding="utf-8").strip() == PLAIN_KEY


def test_put_partial_update_keeps_other_groups(client, auth):
    _put(client, auth, _llm_payload())
    body = _put(client, auth, {"language": "en-US"}).json()

    assert body["language"] == "en-US"
    assert body["llm"]["model"] == "deepseek-chat"            # 未提供的组保持原值
    assert body["code_runner_enabled"] is True


def test_put_rejects_extra_field(client, auth):
    response = _put(client, auth, {"llm": {"base_url": "https://x.dev", "model": "m",
                                           "nope": 1}})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_put_rejects_bad_url(client, auth):
    response = _put(client, auth, {"llm": {"base_url": "ftp://x", "model": "m"}})
    assert response.status_code == 422


# ------------------------------------------------------------ env 覆盖
def test_env_overrides_settings_table(client, auth, monkeypatch):
    assert _put(client, auth, _llm_payload()).status_code == 200

    monkeypatch.setenv("PG_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("PG_LLM_MODEL", "env-model")
    body = client.get("/api/settings", headers=auth).json()

    assert body["llm"]["base_url"] == "https://env.example/v1"
    assert body["llm"]["model"] == "env-model"


def test_put_does_not_write_env(client, auth, monkeypatch):
    monkeypatch.setenv("PG_LLM_MODEL", "from-env")
    assert _put(client, auth, _llm_payload(model="from-settings")).status_code == 200

    import os
    assert os.environ["PG_LLM_MODEL"] == "from-env"           # env 是只读覆盖
    assert client.get("/api/settings", headers=auth).json()["llm"]["model"] == "from-env"


# ------------------------------------------------------------ private_dir 校验
def test_private_dir_relative_is_400(client, auth):
    response = _put(client, auth, {"private_dir": "data/private"})
    assert response.status_code == 400
    assert response.json()["code"] == "PRIVATE_DIR_INVALID"


def test_private_dir_outside_is_403(client, auth, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    response = _put(client, auth, {"private_dir": str(outside)})
    assert response.status_code == 403
    assert response.json()["code"] == "PATH_FORBIDDEN"


def test_private_dir_valid_is_stored(client, auth, env):
    inside = env.data_dir / "private" / "notes"
    inside.mkdir(parents=True)
    body = _put(client, auth, {"private_dir": str(inside)}).json()

    assert body["private_dir"].endswith("private/notes")      # 只回显相对路径
    assert str(env.data_dir) not in json.dumps(body, ensure_ascii=False)


# ------------------------------------------------------------ 探针
class FakeClient:
    """假 LLMClient：只实现 probe / embed / aclose（**不出网**）。"""

    ok = True
    error_code = None
    dim = 4
    last_config = None

    def __init__(self, config, **_kwargs):
        self.config = config
        FakeClient.last_config = config

    async def probe(self, *, timeout: float = 10.0):
        if self.ok:
            return ProbeResult(True, 12, self.config.model, "连接正常", None)
        return ProbeResult(False, 8, None, "密钥无效或已过期", self.error_code or "LLM_401")

    async def embed(self, texts, *, timeout: float = 30.0):
        if not self.ok:
            raise LLMError("上游拒绝", status_code=401)
        return [[0.1] * self.dim for _ in texts]

    async def aclose(self):
        return None


@pytest.fixture()
def fake_llm(monkeypatch):
    FakeClient.ok = True
    FakeClient.error_code = None
    FakeClient.last_config = None
    monkeypatch.setattr(settings_service, "LLMClient", FakeClient)
    return FakeClient


def test_probe_llm_success(client, auth, fake_llm):
    _put(client, auth, _llm_payload())
    body = client.post("/api/settings/test", json={"kind": "llm"},
                       headers={**auth, **ORIGIN}).json()

    assert body == {"ok": True, "kind": "llm", "latency_ms": 12, "model": "deepseek-chat",
                    "message": "连接正常", "error_code": None}
    assert PLAIN_KEY not in json.dumps(body)


def test_probe_llm_failure_is_200(client, auth, fake_llm):
    fake_llm.ok = False
    _put(client, auth, _llm_payload())
    response = client.post("/api/settings/test", json={"kind": "llm"},
                           headers={**auth, **ORIGIN})

    assert response.status_code == 200                        # 业务失败不是 HTTP 错误
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "LLM_401"
    assert body["model"] is None


def test_probe_llm_not_configured(client, auth, fake_llm):
    body = client.post("/api/settings/test", json={"kind": "llm"},
                       headers={**auth, **ORIGIN}).json()
    assert body["ok"] is False
    assert body["error_code"] == "LLM_NOT_CONFIGURED"


def test_probe_with_override_does_not_persist(client, auth, fake_llm):
    response = client.post(
        "/api/settings/test",
        json={"kind": "llm", "llm": {
            "base_url": "https://probe.example/v1", "model": "probe-model",
            "api_key": "sk-probe-0000111122223333",
        }},
        headers={**auth, **ORIGIN},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    settings = client.get("/api/settings", headers=auth).json()
    assert settings["llm"]["base_url"] is None                # 探针不落库
    assert settings["llm"]["model"] is None
    assert fake_llm.last_config.base_url == "https://probe.example/v1"


def test_probe_embedding_unconfigured(client, auth, fake_llm):
    body = client.post("/api/settings/test", json={"kind": "embedding"},
                       headers={**auth, **ORIGIN}).json()
    assert body["ok"] is False
    assert body["error_code"] == "NO_EMBEDDING"


def test_probe_embedding_infers_dim(client, auth, fake_llm):
    response = client.post(
        "/api/settings/test",
        json={"kind": "embedding", "embedding": {
            "base_url": "https://emb.example/v1", "model": "text-embedding-3-small",
            "api_key": "sk-embed-0000111122223333",
        }},
        headers={**auth, **ORIGIN},
    )
    body = response.json()
    assert body["ok"] is True
    assert body["kind"] == "embedding"
    assert body["model"] == "text-embedding-3-small"

    # dim 空则由首次返回推断（MODULES §2.5）
    settings = client.get("/api/settings", headers=auth).json()
    assert settings["embedding"]["dim"] == 4


def test_settings_test_missing_kind_is_422(client, auth):
    response = client.post("/api/settings/test", json={}, headers={**auth, **ORIGIN})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ 韧性
def test_method_not_allowed_has_allow_and_code(client, auth):
    response = client.put("/api/health")

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]
    assert response.json()["message"]


def test_method_not_allowed_for_post_only_endpoint(client, auth):
    """`POST /api/settings/test` 用 GET 访问 → 405，不是 404，也不是 422。"""
    response = client.get("/api/settings/test", headers=auth)
    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "POST" in response.headers["allow"]


def test_unknown_api_path_is_still_404(client, auth):
    response = client.get("/api/no-such-endpoint", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_request_id_is_echoed_on_every_response(client, auth):
    response = client.get("/api/health", headers={"X-Request-Id": "req-abc-123"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-abc-123"

    denied = client.get("/api/settings")                       # 403 也要带（最外层中间件）
    assert denied.status_code == 403
    assert denied.headers["x-request-id"]


def test_request_id_is_generated_when_missing(client):
    first = client.get("/api/health")
    second = client.get("/api/health")
    assert first.headers["x-request-id"]
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_unsafe_inbound_request_id_is_replaced(client):
    response = client.get("/api/health", headers={"X-Request-Id": "bad\nvalue"})
    assert response.headers["x-request-id"] != "bad\nvalue"


def test_5xx_envelope_has_request_id(env, auth, monkeypatch):
    """未捕获异常 → 500 `INTERNAL`，顶层与 detail 都带 request_id，响应头同值。"""

    def boom(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(settings_service, "read_settings", boom)
    with TestClient(main.app, base_url=BASE, raise_server_exceptions=False) as raw:
        response = raw.get("/api/settings", headers=auth)

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL"
    assert body["request_id"]
    assert body["detail"]["request_id"] == body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


# ------------------------------------------------------------ taxonomy 排序
def test_taxonomy_levels_and_types_are_count_desc(client, auth):
    body = client.get("/api/taxonomy", headers=auth).json()

    # article 3 条 > docs 1 条（字典序会把 docs 排前面）
    assert body["types"] == ["article", "docs"]
    # beginner 3 条 > advanced 1 条（字典序会把 advanced 排前面）
    assert body["levels"] == ["beginner", "advanced"]
