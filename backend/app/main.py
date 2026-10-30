"""pgassistant 后端入口。

M1：只读接口（search / resources / taxonomy / health）。
M-1：本地服务鉴权（Host / Origin / X-App-Token）+ schema 迁移框架，
     见需求文档 §13.7 与 §5.2。

启动:
  python -m uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import mimetypes
import sqlite3
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
for p in (str(BACKEND), str(PROJECT / ".deps")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import (
    agent,
    deps,
    fallback,
    index,
    media,
    media_private,
    pages,
    plan_templates,
    plans,
    private,
    resources,
    search,
    settings as settings_api,
    taxonomy,
)
from .core import errors
from .core.config import SETTINGS
from .core.security import SecurityPolicy, new_token
from .db import migrate
from .middleware.guard import SecurityGuard
from .middleware.request_id import (
    RequestIDMiddleware,
    install_log_filter,
    request_id_of,
    with_request_id,
)
from .services import embeddings
from .services.llm_client import load_embedding_config, load_llm_config

APP_VERSION = "0.2.0"
log = logging.getLogger("pgassistant")

#: 前端构建产物里出现的扩展名 → MIME。
#: Windows 的注册表常把 `.js` 报成 `text/plain`，浏览器会直接拒绝执行 module script
#: （"Expected a JavaScript module script but ... MIME type of text/plain"），
#: 所以这里显式钉死，只影响本进程，不改系统设置。
STATIC_MIME_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def _register_static_mime_types() -> None:
    for suffix, mime in STATIC_MIME_TYPES.items():
        mimetypes.add_type(mime, suffix)


_register_static_mime_types()

# 一次性令牌：每进程一枚，不持久化、不落盘（§13.7）
APP_TOKEN = new_token()
POLICY = SecurityPolicy(
    token=APP_TOKEN,
    port=SETTINGS.port,
    bind_host=SETTINGS.host,
    dev_origins=SETTINGS.dev_origins,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, SETTINGS.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s",
    )
    # §12.3：每条日志都带 request_id（没有请求上下文时是 "-"）
    install_log_filter()
    log.info("X-App-Token=%s（随进程重启更换）", APP_TOKEN)
    log.info("允许的 Host: 127.0.0.1 / localhost / [::1]；Origin: %s",
             ", ".join(sorted(POLICY.allowed_origins)))

    if not deps.DB_PATH.exists():
        log.warning("数据库不存在：先跑 python backend/scripts/bootstrap_index.py")
    else:
        # 老库在这里自动升级并留下 data/backups/ 里的备份（§5.2）
        result = migrate.ensure_schema(deps.DB_PATH, backup_dir=SETTINGS.backup_dir)
        if result["applied"]:
            log.info("数据库已迁移 v%s → v%s（备份: %s）",
                     result["from"], result["to"], result["backup"])
        try:
            saved = migrate.daily_backup(deps.DB_PATH, SETTINGS.backup_dir)
            if saved:
                log.info("今日备份: %s", saved)
        except OSError as exc:                   # 备份失败不拦启动，但要留痕
            log.warning("每日备份失败（继续启动）: %s", exc)
        # §6.4：启动时把向量矩阵载进内存（没有向量时只是空矩阵，不影响 FTS）
        try:
            conn = deps.connect(deps.DB_PATH)
            try:
                if embeddings.warm_cache(conn):
                    log.info("语义检索向量已载入内存")
            finally:
                conn.close()
        except Exception as exc:                 # 载入失败只降级，不拦启动
            log.warning("向量矩阵载入失败（继续启动）: %s", type(exc).__name__)
    yield


app = FastAPI(
    title="pgassistant",
    description=(
        "本地学习资料库与学习计划助手。\n\n"
        "**鉴权（需求文档 §13.7）**：除 `/api/health` 外，所有 `/api/*` 必须带 "
        "`X-App-Token` 头，值来自页面 `<meta name=\"x-app-token\">`；"
        "Host 只接受 127.0.0.1 / localhost / [::1]。"
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

# 开发态只放行 Vite；生产同源部署根本不需要 CORS（§13.4 / §13.7）
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(SETTINGS.dev_origins),
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
# 最后添加 = 最外层：Host 不合法时连 CORS 预处理都不给
app.add_middleware(SecurityGuard, policy=POLICY)
# request_id 必须是最外层（比鉴权更外）：403 拒绝也要带 X-Request-Id（§9.9 / §12.3）
app.add_middleware(RequestIDMiddleware)

app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(resources.router, prefix="/api", tags=["resources"])
app.include_router(taxonomy.router, prefix="/api", tags=["taxonomy"])
app.include_router(plans.router, prefix="/api", tags=["plans"])
app.include_router(plan_templates.router, prefix="/api", tags=["plan-templates"])
# 助手（M4）：`/api/agent/*`。必须早于 pages 的 SPA 回退（同 /media 的理由）。
app.include_router(agent.router, prefix="/api", tags=["agent"])
# 打磨（M5）：设置 / 索引 / 私有资料。同样早于 pages 的 SPA 回退。
app.include_router(settings_api.router, prefix="/api", tags=["settings"])
app.include_router(index.router, prefix="/api", tags=["index"])
app.include_router(private.router, prefix="/api", tags=["private"])
# 私有媒体（M5，D24）：**必须早于 media.router**，否则 `/media/{path:path}` 先命中，
# 把 `private/...` 整段当成内容媒体的相对路径（media.py 对 private 前缀恒 404）。
app.include_router(media_private.router, tags=["media-private"])
# 内容媒体（D24）：**不在 `/api` 前缀下**，因此天然免 X-App-Token；
# Sec-Fetch-Site 与路径校验在 api/media.py 里，中间件（含 EXEMPT_API_PATHS）不动。
# 必须早于 pages 的 SPA 回退 `/{full_path:path}`，否则 /media/* 会被吞成 HTML。
app.include_router(media.router, tags=["media"])


class AssetsMount:
    """`/assets` 的静态托管，目录**每次请求**解析。

    为什么不用 `app.mount("/assets", StaticFiles(directory=...))`：
    `StaticFiles` 在构造时把目录焊死，而 M0 的测试要用 tmp 目录造一个假 dist，
    构建产物也可能是服务起来之后才生成的。这里按当前 `pages.dist_dir()` 复用一个
    `StaticFiles` 实例，目录变了就重建（只在目录切换时付一次代价）。

    `check_dir=False`：`frontend/dist` 不存在时不要在建 app 阶段抛错——
    没有构建产物也必须能起服务、跑测试（`/assets/*` 会老实返回 404）。
    """

    def __init__(self, dist_getter: Callable[[], Path]) -> None:
        self._dist_getter = dist_getter
        self._directory: Path | None = None
        self._app: StaticFiles | None = None

    async def __call__(self, scope, receive, send):
        directory = self._dist_getter() / "assets"
        if self._app is None or directory != self._directory:
            self._directory = directory
            self._app = StaticFiles(directory=str(directory), check_dir=False)
        await self._app(scope, receive, send)


# 顺序要求：/api 路由 → /assets 静态 → pages（含 SPA 回退）。
# SPA 回退是 `/{full_path:path}`，一旦提前注册就会把接口请求吞成 HTML，
# 所以 `pages.router` 在文件末尾（`/api/health` 之后）才 include。
app.mount("/assets", AssetsMount(pages.dist_dir), name="assets")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """把 FastAPI / Starlette 默认的 {"detail": ...} 收敛成 §11 的错误信封。

    注册的是 Starlette 基类：路由层的 404/405 抛的是它，fastapi.HTTPException
    是它的子类，异常处理器按 MRO 查找，两个都能命中。

    `5xx` 补 `request_id`（§9.9）：顶层 + `detail.request_id` 各一份，
    与响应头 `X-Request-Id` 同值。
    """
    payload = errors.flatten_http_exception(exc.status_code, exc.detail)
    headers = dict(exc.headers or {})
    if exc.status_code >= 500:
        payload = with_request_id(payload, request)
        # ServerErrorMiddleware 在最外层，5xx 的响应头可能不经过 request_id 中间件，
        # 所以这里显式补一次（与 body 里的 request_id 同值，§9.9）
        headers.setdefault("X-Request-Id", request_id_of(request))
    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """字段级校验失败也走 §11 错误信封（默认的 {"detail": [...]} 没有 code）。"""
    return JSONResponse(
        status_code=422,
        content=errors.error_payload(
            "VALIDATION_ERROR",
            "请求参数不合法",
            detail=jsonable_encoder(exc.errors()),
            hint="检查请求体字段类型与取值范围",
        ),
    )


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_error_handler(request: Request, exc: sqlite3.OperationalError):
    return JSONResponse(
        status_code=503,
        headers={"X-Request-Id": request_id_of(request)},
        content=with_request_id(
            errors.error_payload(
                errors.DB_NOT_READY, "数据库不可用",
                detail=str(exc),
                hint="先运行 python backend/scripts/bootstrap_index.py 建库",
            ),
            request,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常 → 500 `INTERNAL`，**必须带 request_id**（§9.9 / §12.3）。

    Starlette 的 ServerErrorMiddleware 用这个处理器产出响应；默认的
    `PlainTextResponse("Internal Server Error")` 没有错误信封，前端没法按 code 分支。
    `str(exc)` 不进 `message`（可能含绝对路径 / 上游细节），只写到服务端日志。
    """
    log.exception("未捕获异常：%s", type(exc).__name__)
    rid = request_id_of(request)
    return JSONResponse(
        status_code=500,
        headers={"X-Request-Id": rid},
        content=with_request_id(
            errors.error_payload(
                errors.INTERNAL,
                "服务内部错误",
                hint="复制 request_id 反馈问题；日志在 data/logs/app.log",
            ),
            request,
        ),
    )


@app.get("/api/health", tags=["health"])
def health():
    """不鉴权的存活探针：只暴露「能不能用」，不暴露任何内容。

    `model_configured` / `embedding_configured` / `index_version` 是 API.md §6 第 6 行
    的契约义务（M4/M5 必做）：助手入口与设置页靠它们决定显示引导卡片还是表单。
    只返回布尔与内容指纹，不暴露 base_url / model / key（§13.1）。
    """
    db_path = deps.DB_PATH
    payload = {
        "status": "ok",
        "db_ready": db_path.exists(),
        "schema_version": None,
        "version": APP_VERSION,
        "index": None,
        "index_version": None,
        "model_configured": False,
        "embedding_configured": False,
    }
    if not db_path.exists():
        return payload
    try:
        conn = deps.connect(db_path)
    except sqlite3.Error:
        return payload
    try:
        payload["schema_version"] = migrate.current_version(conn)
        row = conn.execute(
            "SELECT file_count, last_indexed_at, content_version"
            " FROM index_state WHERE id = 1"
        ).fetchone()
        if row:
            payload["index"] = {
                "files": row["file_count"],
                "indexed_at": row["last_indexed_at"],
            }
            payload["index_version"] = row["content_version"]
        # 配置读取只回布尔；M5 接管 settings 后这里不用改
        payload["model_configured"] = load_llm_config(conn) is not None
        payload["embedding_configured"] = load_embedding_config(conn) is not None
    except sqlite3.Error:
        payload["db_ready"] = False
    finally:
        conn.close()
    return payload


# 405 兜底：必须在**所有** `/api/*` 真实路由之后（含上面装饰器注册的 `/api/health`），
# 且在 pages 的 SPA 回退之前（见 api/fallback.py 的文件头）。
app.include_router(fallback.router, tags=["fallback"])

# 必须最后 include：pages 里含 SPA 回退 `/{full_path:path}`，
# 提前注册会把 /api/* 与 /api/health 吞成 HTML（见上面的顺序说明）。
app.include_router(pages.router)
