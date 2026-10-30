"""§13.7 三道防线的回归测试。

验收对应需求文档 §16「安全与韧性」：
- 伪造 Host → 403
- 跨站 POST → 403
- 不带 X-App-Token → 403
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main

HOST_127 = "http://127.0.0.1:8000"
HOST_LOCALHOST = "http://localhost:8000"


def _code(response) -> str:
    return response.json()["code"]


# --------------------------------------------------------------- 第一道：Host
def test_forged_host_is_rejected(client, token):
    forged = TestClient(main.app, base_url="http://evil.example.com")
    resp = forged.get("/api/taxonomy", headers={"X-App-Token": token})
    assert resp.status_code == 403
    assert _code(resp) == "BAD_HOST"
    assert "x-app-token" not in resp.text          # 非法 Host 连 token 都不给


def test_colon_less_localhost_is_allowed(client, token):
    local = TestClient(main.app, base_url="http://localhost")
    resp = local.get("/api/taxonomy", headers={"X-App-Token": token})
    assert resp.status_code == 200


def test_dns_rebinding_host_with_port_is_rejected(client, token):
    forged = TestClient(main.app, base_url="http://127.0.0.1.evil.example:8000")
    assert forged.get("/api/taxonomy", headers={"X-App-Token": token}).status_code == 403


# ------------------------------------------------------- 第二道：Origin / Sec-Fetch-Site
def test_cross_site_post_is_rejected(client, token):
    resp = client.post(
        "/api/plans",
        headers={"X-App-Token": token, "Origin": "http://evil.example.com"},
        json={},
    )
    assert resp.status_code == 403
    assert _code(resp) == "BAD_ORIGIN"


def test_write_without_origin_is_rejected(client, token):
    resp = client.post("/api/plans", headers={"X-App-Token": token}, json={})
    assert resp.status_code == 403
    assert _code(resp) == "MISSING_ORIGIN"


def test_write_with_same_origin_passes_guard(client, token):
    """过了中间件就够了：空 body 走到路由后应是 422（校验失败），而不是 403。"""
    resp = client.post(
        "/api/plans",
        headers={"X-App-Token": token, "Origin": HOST_127},
        json={},
    )
    assert resp.status_code in (400, 422)          # 400 兼容 LLM/业务前置校验的分支
    assert _code(resp) not in {"BAD_HOST", "BAD_ORIGIN", "MISSING_ORIGIN", "CROSS_SITE",
                               "TOKEN_MISSING", "TOKEN_INVALID"}


def test_cross_site_fetch_metadata_is_rejected(client, token):
    resp = client.get(
        "/api/taxonomy",
        headers={"X-App-Token": token, "Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403
    assert _code(resp) == "CROSS_SITE"


def test_same_origin_fetch_metadata_is_allowed(client, token):
    resp = client.get(
        "/api/taxonomy",
        headers={"X-App-Token": token, "Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code == 200


def test_dev_origin_is_allowed(client, token):
    """Vite dev server 直连 8000 时 Origin 是 5173，Sec-Fetch-Site 是 cross-site。"""
    resp = client.get(
        "/api/taxonomy",
        headers={
            "X-App-Token": token,
            "Origin": "http://localhost:5173",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert resp.status_code == 200


# --------------------------------------------------------- 第三道：一次性令牌
def test_missing_token_is_rejected(client):
    resp = client.get("/api/taxonomy")
    assert resp.status_code == 403
    assert _code(resp) == "TOKEN_MISSING"


def test_wrong_token_is_rejected(client):
    resp = client.get("/api/taxonomy", headers={"X-App-Token": "not-the-token"})
    assert resp.status_code == 403
    assert _code(resp) == "TOKEN_INVALID"


def test_health_needs_no_token(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_ready"] is True
    # 最新 schema 版本：v1 建表 + 0002 软删除升级（migrations/0002_soft_delete.py）
    assert body["schema_version"] == 2


def test_valid_token_reads_api(client, token):
    resp = client.get("/api/taxonomy", headers={"X-App-Token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["stacks"][0]["stack"] == "cs-basics"


# --------------------------------------------------------------- token 注入
def test_token_injected_into_html(client, token):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert 'name="x-app-token"' in resp.text
    assert token in resp.text
    # 注入后 content-length 必须重算，否则浏览器按旧长度截断
    assert int(resp.headers["content-length"]) == len(resp.content)


def test_token_not_injected_twice(client):
    resp = client.get("/")
    assert resp.text.count('<meta name="x-app-token"') == 1


def test_html_has_security_headers(client):
    resp = client.get("/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"


# ----------------------------------------------------------------- 错误信封
def test_error_envelope_shape(client):
    resp = client.get("/api/taxonomy")
    body = resp.json()
    assert resp.status_code == 403
    assert set(body) >= {"code", "message"}
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["cache-control"] == "no-store"


def test_resource_not_found_envelope(client, token):
    resp = client.get("/api/resources/no/such/slug", headers={"X-App-Token": token})
    assert resp.status_code == 404
    assert _code(resp) == "RESOURCE_NOT_FOUND"
