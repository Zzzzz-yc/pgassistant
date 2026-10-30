"""前端静态托管与 SPA 回退（前端骨架 M0）。

覆盖四件事：
1. 有 `frontend/dist` 时 `/` 返回构建产物，且 token meta 仍被中间件注入（§13.7）；
2. `/assets/*` 由静态挂载出，且同样不经过 `/api` 鉴权；
3. `/api/*` 的未知路径返回 **JSON 信封**，不被 SPA 的 HTML 回退吞掉；
4. `/docs`、`/openapi.json` 保持可用；无 dist 时退回 M0 状态页且客户端路由 404。

**不依赖真实构建产物**：全部用 tmp_path 造最小 dist（index.html + assets 一个 js）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api import pages

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>pgassistant dist fixture</title>
  </head>
  <body>
    <div id="root" data-testid="spa-root"></div>
    <script type="module" src="/assets/app-abc123.js"></script>
  </body>
</html>
"""
ASSET_NAME = "app-abc123.js"
ASSET_BODY = "console.log('pgassistant-asset-ok');\n"

TOKEN_META = re.compile(r'<meta name="x-app-token" content="([^"]+)"')
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


@pytest.fixture()
def dist(tmp_path, monkeypatch) -> Path:
    """造一个最小 dist，并把 pages.DIST_DIR 指过去（测试不碰真实 frontend/dist）。"""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8", newline="\n")
    (root / "assets" / ASSET_NAME).write_text(ASSET_BODY, encoding="utf-8", newline="\n")
    monkeypatch.setattr(pages, "DIST_DIR", root)
    return root


def test_root_serves_dist_index_with_token_meta(client, dist):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert 'data-testid="spa-root"' in resp.text, "返回的不是 dist/index.html"
    assert resp.headers.get("cache-control") == "no-store"

    matched = TOKEN_META.search(resp.text)
    assert matched, "HTML 里没有注入 <meta name=\"x-app-token\">"
    assert matched.group(1).strip()


def test_health_is_200_without_token(client, dist):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_ready"] is True


def test_unknown_api_returns_json_envelope_not_html(client, dist, token):
    # 带令牌：路由不存在 → 404 信封（不是 SPA 的 HTML）
    resp = client.get("/api/nonexistent", headers={"X-App-Token": token})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "<!doctype" not in resp.text.lower(), "SPA 回退把接口 404 吞成 HTML 了"
    body = resp.json()
    assert isinstance(body.get("code"), str) and CODE_PATTERN.match(body["code"])
    assert body["code"] == "NOT_FOUND"
    assert body.get("message")

    # 不带令牌：仍是 JSON 信封（403 TOKEN_MISSING），也不能是 HTML
    denied = client.get("/api/nonexistent")
    assert denied.status_code == 403
    assert denied.headers["content-type"].startswith("application/json")
    assert denied.json()["code"] == "TOKEN_MISSING"


def test_docs_and_openapi_still_available(client, dist):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert docs.headers["content-type"].startswith("text/html")
    assert "swagger" in docs.text.lower()

    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"].lower() == "pgassistant"


def test_spa_fallback_serves_index_for_client_routes(client, dist):
    for path in ("/library", "/library/cs-basics/sorting/quick-sort",
                 "/plans/1/items/frontend/javascript/js-closures", "/nope"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert 'data-testid="spa-root"' in resp.text, path
        assert TOKEN_META.search(resp.text), path


def test_assets_are_served(client, dist):
    resp = client.get(f"/assets/{ASSET_NAME}")
    assert resp.status_code == 200
    assert "pgassistant-asset-ok" in resp.text
    assert resp.headers["content-type"].split(";")[0] in {
        "text/javascript",
        "application/javascript",
    }


def test_without_dist_root_is_placeholder_and_routes_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "DIST_DIR", tmp_path / "no-dist")

    root = client.get("/")
    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert "M0 状态页" in root.text
    assert TOKEN_META.search(root.text), "占位页也必须注入 token"

    missing = client.get("/library")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")
    assert missing.json()["code"] == "NOT_FOUND"
