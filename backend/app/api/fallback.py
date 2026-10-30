"""405 兜底路由（M5，`API.md` §1.6 / §5，DECISIONS D25 25.6）。

**为什么需要它**：本版 FastAPI（0.162 / Starlette 1.10）对"路径存在、方法不匹配"不产生
405——`pages.py` 的 SPA 回退 `/{full_path:path}` 声明了全部方法，于是 `PUT /api/health`
先命中它并返回 404 `NOT_FOUND`，`flatten_http_exception` 的 405 分支永远不可达。

**做法**：在所有真实 `/api/*` 路由之后、`pages.router` 之前注册一条
`/api/{path:path}` 的全方法兜底（`main.py` 的 include 顺序保证这一点）：

- 该路径**在别的 HTTP 方法下存在** → 405 `METHOD_NOT_ALLOWED` + `Allow`；
- 路径根本不存在 → 仍然 404 `NOT_FOUND`（M1 的既有语义不变）。

"某路径声明了哪些方法"取自 **OpenAPI 路由表**（`app.openapi()["paths"]`，FastAPI 自己
缓存），不解析 Starlette 内部对象、也不复制路径正则：这样 `include_router(prefix=...)`
加前缀、路径参数（`{slug}`）都由框架算好。带参数的模板按 `.+`（path 转换器）匹配，
所以 `/api/resources/某slug` 这类请求也能给出 405 而不是 404。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

#: 候选方法与 `Allow` 的稳定顺序
CANDIDATE_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
ALL_METHODS = list(CANDIDATE_METHODS)
#: OpenAPI 里出现的方法键（小写）
SCHEMA_METHOD_KEYS = frozenset({"get", "head", "post", "put", "patch", "delete", "options", "trace"})

#: 本兜底端点自己的名字（文档/调试用；它不在 OpenAPI 里，不会污染 Allow）
FALLBACK_ENDPOINT = "api_method_fallback"


def _schema_paths(app) -> dict:
    """OpenAPI 的 `paths`（FastAPI 缓存在 `app.openapi_schema`，第二次调用是零成本）。"""
    try:
        return (app.openapi() or {}).get("paths") or {}
    except Exception:                              # schema 生成失败：退化成 404
        return {}


def _template_pattern(template: str):
    """`/api/resources/{slug}` → `^/api/resources/.+$`（path 转换器允许斜杠）。"""
    parts = []
    for chunk in re.split(r"(\{[^{}]+\})", template):
        if chunk.startswith("{") and chunk.endswith("}"):
            parts.append(r".+")
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


def _entry_for(paths: dict, path: str):
    entry = paths.get(path)
    if entry is not None:
        return entry
    for template, item in paths.items():
        if "{" in template and _template_pattern(template).match(path):
            return item
    return None


def allowed_methods(app, path: str) -> list[str]:
    """该 path 在 API 层声明了哪些方法（含 `HEAD` 与 `OPTIONS` 的补齐）。"""
    entry = _entry_for(_schema_paths(app), path)
    if not entry:
        return []
    methods = {key.upper() for key in entry if key in SCHEMA_METHOD_KEYS}
    if "GET" in methods:
        methods.add("HEAD")
    methods.add("OPTIONS")
    return [method for method in CANDIDATE_METHODS if method in methods]


@router.api_route("/api/{path:path}", methods=ALL_METHODS, include_in_schema=False)
def api_method_fallback(request: Request, path: str):
    app = request.scope.get("app") or request.app
    allowed = allowed_methods(app, request.url.path)
    # 只有 OPTIONS（= 路径根本没被任何 /api 路由声明）就还是 404
    if any(method != "OPTIONS" for method in allowed):
        raise HTTPException(status_code=405, headers={"Allow": ", ".join(allowed)})
    raise HTTPException(status_code=404, detail="Not Found")
