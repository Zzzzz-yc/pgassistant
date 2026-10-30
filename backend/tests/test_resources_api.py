"""资料页 M2 后端验收测试（`API.md` §6 的 9 处差异 + §3.5 / §3.7 / §3.26）。

覆盖范围（逐条对着契约，不测"看起来对不对"）：

- §6-1 `took_ms` / `index_version`
- §6-2 `runnable` 是数组
- §6-3 `tags` / `cover_path` / `source`（列表也算，不只详情）
- §6-4 `source` / `q` / `sort` 查询参数
- §6-5 详情 `toc` / `prev` / `next`
- §6-7 `taxonomy.stacks[].label`（M0 建 `_meta/` 后 M2 补）
- §3.5 `related`：同分类优先、排除自身与 overview、上限 6
- §3.7 `POST /api/search`：与 GET 同响应，零结果写 `search_misses`
- §3.26 `/media/*`：免令牌、Host、跨站 403、路径/扩展名/符号链接、响应头、404 信封

`conftest.py` 是冻结文件（`MODULES.md` §1.1），所以这里自带夹具；模块级 `client` /
`token` 覆盖 conftest 的同名夹具，只影响本文件。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import deps, media as media_api, taxonomy as taxonomy_api
from app.db import migrate
from app.db.connection import connect

BASE = "http://127.0.0.1:8000"

#: 种子数据：(slug, title, summary, stack, category, level, type, duration, file_path,
#:             runnable, cover, source, tags)
SEED = [
    (
        "frontend/javascript/js-closures", "JavaScript 闭包", "闭包是保护私有变量的机制",
        "frontend", "javascript", "beginner", "article", 12,
        "content/frontend/javascript/js-closures.md", "python,javascript",
        "frontend/javascript/closures.webp", "repo", ["闭包", "作用域"],
    ),
    (
        "frontend/javascript/js-scope", "作用域与词法环境", "作用域链决定变量可见性",
        "frontend", "javascript", "beginner", "article", 5,
        "content/frontend/javascript/js-scope.md", None, None, "repo", ["作用域"],
    ),
    (
        "frontend/javascript/js-overview", "JavaScript 章节导读", "本章导读",
        "frontend", "javascript", "beginner", "overview", 3,
        "content/frontend/javascript/js-overview.md", None, None, "repo", [],
    ),
    (
        "frontend/react/state", "React 状态管理", "useState 与 useReducer",
        "frontend", "react", "intermediate", "docs", 20,
        "content/frontend/react/state.md", "javascript", None, "repo", ["react"],
    ),
    (
        "private/notes/dp", "我的 DP 笔记", "背包问题手记",
        "private", "notes", "advanced", "article", None,
        "content/private/notes/dp.md", None, None, "private", ["dp"],
    ),
]

#: 正文里同时含：正常 h2、代码围栏里的 `# 注释`（不算标题）、h3、带行内标记的标题。
BODY = """## 闭包是什么

正文段落。

```python
# 这不是标题
def f():
    pass
```

### 行内 `code` 与 **粗体** 标题

| 列 | 列 |
|---|---|
| 1 | 2 |

## 结尾
"""


def _seed(db_path: Path) -> None:
    migrate.ensure_schema(db_path)
    conn = connect(db_path)
    try:
        with conn:
            for index, row in enumerate(SEED, start=1):
                (slug, title, summary, stack, category, level, rtype, duration,
                 file_path, runnable, cover, source, tags) = row
                conn.execute(
                    """INSERT INTO resources
                       (slug, title, summary, body_md, body_tokens, stack, category, level,
                        type, duration_min, url, cover_path, source, file_path,
                        content_hash, author, license, source_url, runnable, lang,
                        is_deleted, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                    (
                        slug, title, summary, BODY, title, stack, category, level, rtype,
                        duration, None, cover, source, file_path, "hash-%d" % index,
                        "krahets", "CC-BY-4.0", "https://example.com", runnable,
                        "zh", "2026-10-30", "2026-10-30",
                    ),
                )
                rid = conn.execute(
                    "SELECT id FROM resources WHERE slug = ?", (slug,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT INTO resources_fts (rowid, title_tokens, summary_tokens,"
                    " body_tokens, tags_tokens) VALUES (?,?,?,?,?)",
                    (rid, title, summary, title, " ".join(tags)),
                )
                for tag in tags:
                    conn.execute(
                        "INSERT INTO resource_tags (resource_id, tag) VALUES (?, ?)",
                        (rid, tag),
                    )
            conn.execute(
                "INSERT INTO index_state (id, content_version, last_indexed_at,"
                " file_count) VALUES (1, 'v-test-1', '2026-10-30', ?)",
                (len(SEED),),
            )
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "data" / "app.db"
    _seed(path)
    monkeypatch.setattr(deps, "DB_PATH", path)
    return path


@pytest.fixture()
def client(db_path) -> TestClient:
    with TestClient(main.app, base_url=BASE) as instance:
        yield instance


@pytest.fixture()
def token(client) -> str:
    """用页面里注入的 token，顺带验证注入链路仍然通（与 conftest 口径一致）。"""
    page = client.get("/")
    assert page.status_code == 200
    marker = 'name="x-app-token" content="'
    start = page.text.index(marker) + len(marker)
    return page.text[start:page.text.index('"', start)]


@pytest.fixture()
def auth(token) -> dict:
    return {"X-App-Token": token}


@pytest.fixture()
def origin() -> dict:
    """写方法（POST）按 §1.2 必须带同源 Origin。"""
    return {"Origin": BASE}


# ------------------------------------------------------- §6-1 / §6-2 / §6-3

def test_list_returns_took_ms_and_index_version(client, auth):
    """§6-1：两个字段都要返回（§9.5 要显示“共 N 篇 · 用时 87ms”）。"""
    body = client.get("/api/resources?size=3", headers=auth).json()

    assert isinstance(body["took_ms"], int) and body["took_ms"] >= 0
    assert body["index_version"] == "v-test-1"
    assert body["page"] == 1 and body["size"] == 3
    assert len(body["items"]) == 3 and body["total"] == len(SEED)


def test_list_runnable_is_array(client, auth):
    """§6-2：逗号串 → 数组（D7）；没有可运行语言时是 null，不是空串。"""
    items = client.get("/api/resources?size=100", headers=auth).json()["items"]
    by_slug = {item["slug"]: item for item in items}

    assert by_slug["frontend/javascript/js-closures"]["runnable"] == ["python", "javascript"]
    assert by_slug["frontend/react/state"]["runnable"] == ["javascript"]
    assert by_slug["frontend/javascript/js-scope"]["runnable"] is None


def test_list_returns_tags_cover_path_and_source(client, auth):
    """§6-3：卡片要的标签 / 封面 / 私有角标；`id` 仍然不返回（§5.1）。"""
    items = client.get("/api/resources?size=100", headers=auth).json()["items"]
    by_slug = {item["slug"]: item for item in items}

    closure = by_slug["frontend/javascript/js-closures"]
    assert closure["tags"] == ["闭包", "作用域"]
    assert closure["cover_path"] == "frontend/javascript/closures.webp"
    assert closure["source"] == "repo"

    private = by_slug["private/notes/dp"]
    assert private["source"] == "private"
    assert private["cover_path"] is None
    assert private["tags"] == ["dp"]

    for item in items:
        assert "id" not in item, "列表不得返回内部主键（§5.1）"
        assert "body_md" not in item


# ------------------------------------------------------------- §6-4 查询参数

def test_filter_by_source(client, auth):
    body = client.get("/api/resources?source=private", headers=auth).json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "private/notes/dp"


def test_invalid_source_is_422(client, auth):
    response = client.get("/api/resources?source=usb", headers=auth)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_q_matches_title_and_summary(client, auth):
    """`q` 复用分词：命中标题，也命中摘要。"""
    by_title = client.get("/api/resources?q=闭包", headers=auth).json()
    assert [item["slug"] for item in by_title["items"]] == ["frontend/javascript/js-closures"]

    by_summary = client.get("/api/resources?q=词法环境", headers=auth).json()
    assert [item["slug"] for item in by_summary["items"]] == ["frontend/javascript/js-scope"]

    none = client.get("/api/resources?q=kafka", headers=auth).json()
    assert none["total"] == 0 and none["items"] == []


def test_q_like_wildcards_are_escaped(client, auth):
    """用户输入的 `%` 不该变成通配符（否则搜 `%` 会把整库捞出来）。"""
    body = client.get("/api/resources?q=%25", headers=auth).json()
    assert body["total"] == 0


def test_sort_duration_min(client, auth):
    """`sort=duration_min`：升序，且 null 的条目排在最后。"""
    items = client.get("/api/resources?size=100&sort=duration_min", headers=auth).json()["items"]
    durations = [item["duration_min"] for item in items]
    non_null = [value for value in durations if value is not None]
    assert non_null == sorted(non_null)
    assert durations[: len(non_null)] == non_null          # null 全在尾部


def test_sort_title_and_updated_at_are_accepted(client, auth):
    for sort in ("title", "updated_at", "default"):
        response = client.get(f"/api/resources?sort={sort}", headers=auth)
        assert response.status_code == 200, sort


def test_invalid_sort_is_422(client, auth):
    assert client.get("/api/resources?sort=views", headers=auth).status_code == 422


# ------------------------------------------- §6-5 / §6-7 详情与 media_base

def test_detail_toc_prev_next_and_media_base(client, auth):
    """§6-5 + §6-7 + D24：目录跳过代码围栏、上下节按 file_path 顺序、media_base 是目录。"""
    body = client.get("/api/resources/frontend/javascript/js-closures", headers=auth).json()

    # toc：只认 h2/h3，且代码块里的 `# 这不是标题` 不能进来
    texts = [node["text"] for node in body["toc"]]
    assert texts == ["闭包是什么", "行内 code 与 粗体 标题", "结尾"]
    assert [node["level"] for node in body["toc"]] == [2, 3, 2]
    assert body["toc"][0]["anchor"] == "闭包是什么"
    # 行内标记去掉后仍是非空锚点；空白与标点不会留在锚点里
    for node in body["toc"]:
        assert node["anchor"] and " " not in node["anchor"]

    # prev / next：同 stack+category 按 file_path 排序（js-closures < js-overview < js-scope）
    assert body["prev"] is None
    assert body["next"] == {"slug": "frontend/javascript/js-overview", "title": "JavaScript 章节导读"}

    # media_base：md 所在目录的 content 根相对路径，且等于 <stack>/<category>
    assert body["media_base"] == "frontend/javascript"


def test_detail_middle_item_has_both_siblings(client, auth):
    body = client.get("/api/resources/frontend/javascript/js-overview", headers=auth).json()
    assert body["prev"]["slug"] == "frontend/javascript/js-closures"
    assert body["next"]["slug"] == "frontend/javascript/js-scope"


def test_detail_last_item_has_no_next(client, auth):
    body = client.get("/api/resources/frontend/javascript/js-scope", headers=auth).json()
    assert body["next"] is None
    assert body["prev"]["slug"] == "frontend/javascript/js-overview"


def test_detail_is_deleted_resource_is_404(client, auth, db_path):
    """§3.4：已下架（is_deleted=1）与不存在一样回 404，不用 410。"""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE resources SET is_deleted = 1 WHERE slug = ?",
                ("frontend/react/state",),
            )
    finally:
        conn.close()

    response = client.get("/api/resources/frontend/react/state", headers=auth)
    assert response.status_code == 404
    envelope = response.json()
    assert envelope["code"] == "RESOURCE_NOT_FOUND"
    assert envelope["detail"] == {"slug": "frontend/react/state"}
    assert envelope["hint"] == "返回资料库重新挑选"


def test_media_base_empty_for_root_level_file(client, auth):
    """`media_base` 为空串 = 按 content 根解析（D24-24.2.2），不能变成 `.` 或 `/`。"""
    from app.api.resources import _media_base

    assert _media_base("content/readme.md") == ""
    assert _media_base("content/frontend/react/state.md") == "frontend/react"
    assert _media_base(None) == ""


# ------------------------------------------------------------- §3.5 related

def test_related_excludes_self_and_overview(client, auth):
    body = client.get("/api/resources/frontend/javascript/js-closures/related", headers=auth).json()

    slugs = [item["slug"] for item in body["items"]]
    assert "frontend/javascript/js-closures" not in slugs      # 不含自身
    assert "frontend/javascript/js-overview" not in slugs      # 排除 overview
    assert slugs == ["frontend/javascript/js-scope"]           # 只召回同分类
    assert body["total"] == 1
    assert body["semantic_available"] is False
    assert "id" not in body["items"][0]


def test_related_respects_limit_and_cap(client, auth):
    """上限 6（§11）：`limit` 大于 6 → 422（Query 约束），等于 1 时只回 1 条。"""
    assert client.get(
        "/api/resources/frontend/javascript/js-closures/related?limit=1", headers=auth
    ).status_code == 200
    assert client.get(
        "/api/resources/frontend/javascript/js-closures/related?limit=7", headers=auth
    ).status_code == 422

    body = client.get(
        "/api/resources/frontend/javascript/js-closures/related", headers=auth
    ).json()
    assert len(body["items"]) <= 6


def test_related_returns_at_most_six(client, auth, db_path):
    """真的塞满 8 条同分类资料，确认服务端自己截断到 6（不是靠调用方传 limit）。"""
    conn = connect(db_path)
    try:
        with conn:
            for index in range(8):
                conn.execute(
                    """INSERT INTO resources
                       (slug, title, summary, body_md, stack, category, level, type,
                        duration_min, file_path, content_hash, created_at, updated_at)
                       VALUES (?,?,'s','b','frontend','react','beginner','article',
                               5,?,'h','2026-10-30','2026-10-30')""",
                    (
                        "frontend/react/extra-%d" % index,
                        "extra %d" % index,
                        "content/frontend/react/extra-%d.md" % index,
                    ),
                )
    finally:
        conn.close()

    body = client.get("/api/resources/frontend/react/state/related", headers=auth).json()
    assert len(body["items"]) == 6
    assert body["total"] == 6


def test_related_unknown_slug_is_404(client, auth):
    response = client.get("/api/resources/no/such/slug/related", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


# -------------------------------------------------------------- §6-7 label

def test_taxonomy_label_from_meta(client, auth):
    body = client.get("/api/taxonomy", headers=auth).json()
    by_stack = {node["stack"]: node for node in body["stacks"]}

    assert by_stack["frontend"]["label"] == {"zh": "前端", "en": "Frontend"}
    assert by_stack["private"]["label"] is None           # 未登记 → null，不报错
    assert body["levels"] and body["types"]


def test_taxonomy_label_supports_block_style(tmp_path, monkeypatch):
    """`label` 两种写法都要认：流式 `{zh: …, en: …}` 与块式缩进。"""
    meta = tmp_path / "_meta"
    meta.mkdir()
    path = meta / "taxonomy.yml"
    path.write_text(
        "stacks:\n"
        "  frontend:\n"
        "    label: { zh: 前端, en: Frontend }\n"
        "    categories: [javascript]\n"
        "  backend:\n"
        "    label:\n"
        "      zh: 后端\n"
        "      en: Backend\n"
        "    categories: [http]\n"
        "  broken:\n"
        "    label: { zh: 只有中文 }\n"
        "levels: [beginner]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: path)

    labels = taxonomy_api.load_labels()
    assert labels == {
        "frontend": {"zh": "前端", "en": "Frontend"},
        "backend": {"zh": "后端", "en": "Backend"},
    }


def test_taxonomy_label_degrades_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: tmp_path / "nope.yml")
    assert taxonomy_api.load_labels() == {}


# ---------------------------------------------------- §3.7 POST 搜索 + 记账

def test_post_search_matches_get(client, auth, origin):
    get_body = client.get("/api/search?q=闭包&limit=5", headers=auth).json()
    post_body = client.post(
        "/api/search", json={"q": "闭包", "limit": 5}, headers={**auth, **origin}
    ).json()

    assert post_body["query"] == get_body["query"] == "闭包"
    assert post_body["mode"] == get_body["mode"]
    assert [item["slug"] for item in post_body["items"]] == [
        item["slug"] for item in get_body["items"]
    ]
    assert post_body["total"] == get_body["total"]
    assert isinstance(post_body["took_ms"], int)
    assert post_body["semantic_available"] is None


def test_post_search_extra_field_is_422(client, auth, origin):
    response = client.post(
        "/api/search", json={"q": "闭包", "nope": 1}, headers={**auth, **origin}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_post_search_without_origin_is_403(client, auth):
    """写方法必须带同源 Origin（§1.2）；不带且非安全方法 → MISSING_ORIGIN。"""
    response = client.post("/api/search", json={"q": "闭包"}, headers=auth)
    assert response.status_code == 403
    assert response.json()["code"] == "MISSING_ORIGIN"


def _miss_count(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM search_misses").fetchone()["c"]
    finally:
        conn.close()


def test_zero_result_writes_search_miss_for_get_and_post(client, auth, origin, db_path):
    assert _miss_count(db_path) == 0

    get_body = client.get("/api/search?q=kafka&stack=frontend", headers=auth).json()
    assert get_body["total"] == 0 and get_body["mode"] in {"miss", "empty"}

    post_body = client.post(
        "/api/search", json={"q": "kafka", "limit": 3}, headers={**auth, **origin}
    ).json()
    assert post_body["total"] == 0

    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT query, filters FROM search_misses").fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert rows[0]["query"] == rows[1]["query"] == "kafka"
    assert json.loads(rows[0]["filters"]) == {"stack": "frontend"}
    assert rows[1]["filters"] is None


def test_hit_does_not_write_search_miss(client, auth, db_path):
    client.get("/api/search?q=闭包", headers=auth)
    assert _miss_count(db_path) == 0


def test_record_miss_never_raises_on_broken_table(db_path):
    """写失败不能影响搜索响应（`MODULES.md` §2.2：不抛异常）。"""
    from app.services.search import record_miss

    conn = connect(db_path)
    try:
        with conn:
            conn.execute("DROP TABLE search_misses")
        record_miss(conn, "kafka", {"stack": "frontend"})   # 不抛即通过
    finally:
        conn.close()


# --------------------------------------------------------- §3.26 `/media/*`

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDAT\x78\x9c\x63\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>'
MEDIA_REL = "frontend/javascript/a.assets/pic.png"


@pytest.fixture()
def content_root(tmp_path, monkeypatch) -> Path:
    """造一个 content/ 根：正常图片、SVG、大写扩展名、.md、隐藏目录、目录本身。"""
    root = tmp_path / "content"
    assets = root / "frontend" / "javascript" / "a.assets"
    assets.mkdir(parents=True)
    (assets / "pic.png").write_bytes(PNG_BYTES)
    (assets / "icon.svg").write_bytes(SVG_BYTES)
    (assets / "photo.JPG").write_bytes(PNG_BYTES)
    (root / "frontend" / "javascript" / "note.md").write_text("# 正文", encoding="utf-8")
    (root / "frontend" / "javascript" / "data.json").write_text("{}", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config.png").write_bytes(PNG_BYTES)
    monkeypatch.setattr(media_api, "_media_root", lambda: root)
    return root


def test_media_is_reachable_without_token_but_api_is_not(client, content_root):
    """D24 的核心：`/media` 无 token 200；同一进程里 `/api/*` 无 token 仍然 403。"""
    media = client.get("/media/" + MEDIA_REL)
    assert media.status_code == 200
    assert media.headers["content-type"] == "image/png"
    assert media.content == PNG_BYTES

    api = client.get("/api/resources")
    assert api.status_code == 403
    assert api.json()["code"] == "TOKEN_MISSING"


def test_media_response_headers(client, content_root):
    """§3.26 响应头表：nosniff + private 缓存 + 精确 Content-Length。"""
    response = client.get("/media/" + MEDIA_REL)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert int(response.headers["content-length"]) == len(PNG_BYTES)
    assert "content-security-policy" not in response.headers      # 只有 SVG 需要


def test_media_head_returns_headers_without_body(client, content_root):
    response = client.head("/media/" + MEDIA_REL)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert int(response.headers["content-length"]) == len(PNG_BYTES)
    assert response.content == b""


def test_media_svg_gets_csp(client, content_root):
    response = client.get("/media/frontend/javascript/a.assets/icon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_media_extension_is_case_insensitive(client, content_root):
    response = client.get("/media/frontend/javascript/a.assets/photo.JPG")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize(
    "path",
    [
        # 1) 不存在
        "frontend/javascript/a.assets/nope.png",
        # 2) 扩展名不在白名单（正文与配置永远不可经 /media 取到）
        "frontend/javascript/note.md",
        "frontend/javascript/data.json",
        # 3) 穿越：编码后的 `..`、`//` 空段、隐藏段
        "%2e%2e/%2e%2e/etc/passwd",
        "frontend//javascript/a.assets/pic.png",
        ".git/config.png",
        # 4) 绝对路径 / 盘符 / 反斜杠
        "%2Fetc%2Fpasswd",
        "C:/windows/win.ini",
        "frontend%5Cjavascript%5Ca.assets%5Cpic.png",
        # 5) 目录不是文件
        "frontend/javascript",
        # 6) M5 预留前缀：M2 恒 404 且不回落 content/
        "private/notes/pic.png",
    ],
)
def test_media_rejects_bad_paths_with_media_not_found(client, content_root, path):
    """任何一条安全规则不过 → **404 `MEDIA_NOT_FOUND`**（不是 400/403，不给探测信号）。"""
    response = client.get("/media/" + path)

    assert response.status_code == 404, path
    assert response.json()["code"] == "MEDIA_NOT_FOUND", path


def test_media_normalizes_double_encoded_traversal(client, content_root):
    """`%252e%252e`（双重编码）解码两轮后仍是 `..`，必须被拦。"""
    response = client.get("/media/%252e%252e/%252e%252e/etc/passwd")

    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_NOT_FOUND"


@pytest.mark.parametrize(
    "raw",
    [
        "../etc/passwd",
        "..",
        "frontend/../../etc/passwd",
        "frontend/javascript/../../../../windows/win.ini",
        "./frontend/javascript/a.assets/pic.png",
        "/etc/passwd",
        "\\\\host\\share\\x.png",
        "C:/windows/win.ini",
        "frontend/javascript/a.assets",
        "frontend/javascript/a.assets/pic.png\x00.png",
    ],
)
def test_media_service_rejects_traversal_paths(content_root, raw):
    """服务层单测：`resolve_media_file` 对穿越 / 绝对 / UNC / 盘符 / NUL 一律抛错。

    单独测服务层是因为 httpx / 浏览器会把字面 `..` 在**发出前**规范化掉，
    集成测试只能验编码后的形式（`%2e%2e`）；真实 uvicorn + curl 走的是这一层的判定。
    """
    from app.services.media import MediaPathError, resolve_media_file

    with pytest.raises(MediaPathError):
        resolve_media_file(content_root, raw)


def test_media_service_resolves_inside_root(content_root):
    """正向对照：同一函数对合法路径必须给出根内的普通文件。"""
    from app.services.media import resolve_media_file

    path, mime, normalized = resolve_media_file(
        content_root, "frontend/javascript/a.assets/pic.png"
    )
    assert path == (content_root / "frontend/javascript/a.assets/pic.png").resolve()
    assert mime == "image/png"
    assert normalized == "frontend/javascript/a.assets/pic.png"


def test_media_symlink_escape_is_404(client, content_root, tmp_path):
    """符号链接指向根外 → 404（§3.26 规则 5）；Windows 无权限建链接时跳过。"""
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    link = content_root / "frontend" / "javascript" / "a.assets" / "link.png"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")

    response = client.get("/media/frontend/javascript/a.assets/link.png")
    assert response.status_code == 404
    assert response.json()["code"] == "MEDIA_NOT_FOUND"


def test_media_rejects_cross_site_fetch(client, content_root):
    """`Sec-Fetch-Site: cross-site` → 403 `CROSS_SITE`（媒体唯一免令牌，但仍仅同源）。"""
    blocked = client.get(
        "/media/" + MEDIA_REL, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "CROSS_SITE"

    allowed = client.get(
        "/media/" + MEDIA_REL, headers={"Sec-Fetch-Site": "same-origin"}
    )
    assert allowed.status_code == 200


def test_media_rejects_bad_host(client, content_root):
    """Host 白名单仍然有效（中间件挡在最外层）。"""
    response = client.get("/media/" + MEDIA_REL, headers={"Host": "evil.example.com"})
    assert response.status_code == 403
    assert response.json()["code"] == "BAD_HOST"


def test_media_post_is_405(client, content_root):
    """只读端点：非 GET/HEAD → 405（经 `flatten_http_exception` 归到 `BAD_REQUEST`）。"""
    response = client.post("/media/" + MEDIA_REL)
    assert response.status_code == 405


def test_media_error_detail_has_no_absolute_path(client, content_root):
    """§13.1-3：错误 detail 只回显内容根相对路径，不泄露服务器绝对路径。"""
    response = client.get("/media/frontend/javascript/a.assets/nope.png")
    detail = response.json()["detail"]

    assert detail == {"path": "frontend/javascript/a.assets/nope.png"}
    assert str(content_root) not in json.dumps(detail, ensure_ascii=False)


