"""内容媒体出口 `GET/HEAD /media/{path:path}`（D24，规格 `API.md` §3.26）。

为什么单独一个文件、为什么免令牌：

- `<img>` 加不了 `X-App-Token`，所以内容媒体必须与 `/api/*` 分开；
- **不放宽 `/api/*`**：`middleware/guard.py` 与 `EXEMPT_API_PATHS` 一个字不改。
  `/media` 不在 `/api` 前缀下，中间件本来就不查令牌；这里只补两项媒体侧校验：
  `Sec-Fetch-Site` 与路径安全。策略对象复用 `core/security.py`（不复制魔法字符串）。
- `/media/private/*` 是 M5（`api/media_private.py`）。本文件的 `_media_root()` 是唯一
  根来源，M5 复制不上：**M2 阶段该前缀恒 404，且不回落到 `content/`**（白名单挡得住，
  因为 `private` 段虽是合法段，但 `content/private/...` 里的图片如果存在本会被服务——
  见下面的显式前缀拒绝）。

失败语义：路径非法、扩展名不在白名单、文件不存在，**统一 404 `MEDIA_NOT_FOUND`**
（不用 400/403，不给探测者区分信号），且只回显规范化后的相对路径。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..core import errors
from ..core.config import SETTINGS
from ..services import media as media_service

router = APIRouter()

#: 只读方法；其它方法交给 Starlette 回 405（`flatten_http_exception` 会归到 `BAD_REQUEST`）。
MEDIA_METHODS = ("GET", "HEAD")

#: M2 阶段预留前缀：`/media/private/**` 归打磨模块 M5，这里恒 404，**不回落 content**。
PRIVATE_PREFIX = "private"


def _policy():
    """延迟导入 `main.POLICY`：main 在导入期就会 `include_router`，直接 import 会循环。"""
    from ..main import POLICY

    return POLICY


def _media_root() -> Path:
    """内容媒体根。M5 的私有媒体用自己的根（`data/private/`），不改这里。"""
    return Path(SETTINGS.content_dir)


def _deny(message: str, detail: dict | None = None, hint: str | None = None) -> HTTPException:
    """统一 404 信封（§3.26）：状态码固定 404，码固定 `MEDIA_NOT_FOUND`。"""
    return HTTPException(
        status_code=404,
        detail=errors.error_payload(
            errors.MEDIA_NOT_FOUND,
            message,
            detail=detail,
            hint=hint,
        ),
    )


def _reject_convention(request: Request) -> None:
    """媒体侧的鉴权（`API.md` §1.2 的“媒体例外”一行）。

    - Host 白名单：中间件已经挡过（Host 不合法连页面都不给），这里再校验一次，
      让“`/media` 自己校验 Host”这件事在代码里可核对；
    - `Sec-Fetch-Site: cross-site` → 403 `CROSS_SITE`；缺省视为非浏览器客户端，放行。
    """
    policy = _policy()
    host = request.headers.get("host")
    if not policy.host_ok(host):
        raise HTTPException(
            status_code=403,
            detail=errors.error_payload(
                errors.BAD_HOST,
                "Host 不在白名单内",
                detail={"host": host},
                hint="只允许 127.0.0.1 / localhost / [::1] 访问",
            ),
        )

    fetch_site = request.headers.get("sec-fetch-site")
    if not policy.fetch_site_ok(fetch_site):
        raise HTTPException(
            status_code=403,
            detail=errors.error_payload(
                errors.CROSS_SITE,
                "跨站请求被拒绝",
                detail={"sec_fetch_site": fetch_site},
                hint="内容媒体只接受本站页面发起的请求",
            ),
        )


def _serve(raw_path: str, request: Request, head: bool) -> Response:
    _reject_convention(request)

    if raw_path == PRIVATE_PREFIX or raw_path.startswith(PRIVATE_PREFIX + "/"):
        # M5 之前：私有媒体恒 404，且绝不回落到 content/private/...
        raise _deny(
            media_service.MESSAGE_MEDIA_NOT_FOUND,
            detail={"path": raw_path},
            hint="私有媒体（/media/private/*）由 M5 提供",
        )

    try:
        path, mime, normalized = media_service.resolve_media_file(_media_root(), raw_path)
    except media_service.MediaPathError:
        # detail 只回显规范化后的相对路径；不能回显服务器绝对路径（§13.1-3）
        raise _deny(
            media_service.MESSAGE_MEDIA_NOT_FOUND,
            detail={"path": raw_path},
            hint=media_service.HINT_MEDIA_NOT_FOUND,
        ) from None

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": media_service.MEDIA_CACHE_CONTROL,
    }
    if mime == media_service.MEDIA_MIME_TYPES[media_service.MEDIA_SVG_SUFFIX]:
        # SVG 能内嵌脚本与外链，禁掉一切子资源（§13.2-3 / D24-24.1.7）
        headers["Content-Security-Policy"] = media_service.MEDIA_SVG_CSP

    if head:
        # HEAD 与 GET 同样的头 + 空 body；Content-Length 显式给出，避免 Starlette
        # 按空 body 回 0（`Response.init_headers` 见到 content-length 就不再补）。
        headers["Content-Length"] = str(path.stat().st_size)
        return Response(status_code=200, content=b"", media_type=mime, headers=headers)

    return Response(content=path.read_bytes(), media_type=mime, headers=headers)


@router.get("/media/{path:path}", tags=["media"], summary="内容媒体（免令牌）")
def get_media(path: str, request: Request) -> Response:
    return _serve(path, request, head=False)


@router.head("/media/{path:path}", tags=["media"], summary="内容媒体 HEAD")
def head_media(path: str, request: Request) -> Response:
    return _serve(path, request, head=True)


# 显式回 405：本版 FastAPI 在「方法不匹配」时若找不到同路径的其它处理器，会给
# `404 NOT_FOUND`（`/api/resources` 的 POST 也是这个行为），而 `API.md` §3.26 / D25 25.6
# 要求 405 + 独立码 `METHOD_NOT_ALLOWED`。所以这里自己声明其余方法。
@router.api_route(
    "/media/{path:path}",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def media_method_not_allowed(path: str) -> JSONResponse:
    return JSONResponse(
        status_code=405,
        content=errors.error_payload(
            errors.METHOD_NOT_ALLOWED,
            "内容媒体只支持 GET / HEAD",
            detail={"method": "not allowed"},
            hint="浏览器 <img> 只会发 GET；HEAD 用于探测",
        ),
        headers={"Allow": "GET, HEAD, OPTIONS"},
    )
