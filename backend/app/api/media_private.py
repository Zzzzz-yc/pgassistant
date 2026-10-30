"""私有媒体出口 `GET/HEAD /media/private/{path:path}`（D24 预留 + M5 落地）。

与 `api/media.py` 的 `/media/{path:path}` **同一套规则**，只把根换成 `data/private/`
（`API.md` §1.10 / §3.26，`MODULES.md` §1.1）：

- 免 `X-App-Token`（`<img>` 带不了请求头），但仍校验 Host 与 `Sec-Fetch-Site`；
- 扩展名白名单、`resolve()` 后必须仍在根内、符号链接逃逸一律 404 `MEDIA_NOT_FOUND`；
- 只接受 `GET` / `HEAD`，其它方法 405 `METHOD_NOT_ALLOWED` + `Allow`（D25 25.6 的独立码）。

**注册顺序**：本路由必须排在 `api/media.py` 之前（`main.py` 里先 include 本模块），
否则 `/media/{path:path}` 会把 `private/...` 整段吃掉（media.py 里对 private 前缀恒 404）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..core import config as core_config
from ..core import errors
from ..services import media as media_service
from . import media as media_api

router = APIRouter()

#: 私有媒体固定挂在 `data/private/` 下（`API.md` §1.10；不跟随 `settings.private_dir`，
#: 避免"改了私有目录就把媒体根也跟着搬"造成旧链接全部失效）。
PRIVATE_DIR_NAME = "private"


def _private_root() -> Path:
    """`data/private`；每次读模块属性，测试可以把 SETTINGS 换到 tmp。"""
    return Path(core_config.SETTINGS.data_dir) / PRIVATE_DIR_NAME


def _deny(raw_path: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=errors.error_payload(
            errors.MEDIA_NOT_FOUND,
            media_service.MESSAGE_MEDIA_NOT_FOUND,
            detail={"path": raw_path},
            hint=media_service.HINT_MEDIA_NOT_FOUND,
        ),
    )


def _serve(raw_path: str, request: Request, head: bool) -> Response:
    # Host / Sec-Fetch-Site 与 `/media/*` 共用同一实现（不复制规则，避免两处漂移）
    media_api._reject_convention(request)

    try:
        path, mime, _normalized = media_service.resolve_media_file(_private_root(), raw_path)
    except media_service.MediaPathError:
        raise _deny(raw_path) from None

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": media_service.MEDIA_CACHE_CONTROL,
    }
    if mime == media_service.MEDIA_MIME_TYPES[media_service.MEDIA_SVG_SUFFIX]:
        headers["Content-Security-Policy"] = media_service.MEDIA_SVG_CSP

    if head:
        headers["Content-Length"] = str(path.stat().st_size)
        return Response(status_code=200, content=b"", media_type=mime, headers=headers)

    return Response(content=path.read_bytes(), media_type=mime, headers=headers)


@router.get("/media/private/{path:path}", tags=["media-private"],
            summary="私有媒体（免令牌）")
def get_private_media(path: str, request: Request) -> Response:
    return _serve(path, request, head=False)


@router.head("/media/private/{path:path}", tags=["media-private"],
             summary="私有媒体 HEAD")
def head_private_media(path: str, request: Request) -> Response:
    return _serve(path, request, head=True)


# 显式声明其余方法：`/media/*` 是唯一由路由自己给 405 的地方（`API.md` §3.26）
@router.api_route(
    "/media/private/{path:path}",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def private_media_method_not_allowed(path: str):
    return JSONResponse(
        status_code=405,
        content=errors.error_payload(
            errors.METHOD_NOT_ALLOWED,
            "私有媒体只支持 GET / HEAD",
            detail={"method": "not allowed"},
            hint="浏览器 <img> 只会发 GET；HEAD 用于探测",
        ),
        headers={"Allow": "GET, HEAD, OPTIONS"},
    )
