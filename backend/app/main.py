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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import deps, pages, resources, search, taxonomy
from .core import errors
from .core.config import SETTINGS
from .core.security import SecurityPolicy, new_token
from .db import migrate
from .middleware.guard import SecurityGuard

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
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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

app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(resources.router, prefix="/api", tags=["resources"])
app.include_router(taxonomy.router, prefix="/api", tags=["taxonomy"])


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
    """
    payload = errors.flatten_http_exception(exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_error_handler(request: Request, exc: sqlite3.OperationalError):
    return JSONResponse(
        status_code=503,
        content=errors.error_payload(
            errors.DB_NOT_READY, "数据库不可用",
            detail=str(exc),
            hint="先运行 python backend/scripts/bootstrap_index.py 建库",
        ),
    )


@app.get("/api/health", tags=["health"])
def health():
    """不鉴权的存活探针：只暴露「能不能用」，不暴露任何内容。"""
    db_path = deps.DB_PATH
    payload = {
        "status": "ok",
        "db_ready": db_path.exists(),
        "schema_version": None,
        "version": APP_VERSION,
        "index": None,
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
            "SELECT file_count, last_indexed_at FROM index_state WHERE id = 1"
        ).fetchone()
        if row:
            payload["index"] = {
                "files": row["file_count"],
                "indexed_at": row["last_indexed_at"],
            }
    except sqlite3.Error:
        payload["db_ready"] = False
    finally:
        conn.close()
    return payload


# 必须最后 include：pages 里含 SPA 回退 `/{full_path:path}`，
# 提前注册会把 /api/* 与 /api/health 吞成 HTML（见上面的顺序说明）。
app.include_router(pages.router)
