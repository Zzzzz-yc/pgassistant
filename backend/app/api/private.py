"""私有资料端点（`API.md` §3.24、需求文档 §4.4 / §10）。

`POST /api/private/rescan`：body 可省略；增量 diff 与 `content/` 同一套逻辑（§6.2），
只在手动触发时执行（不做文件监听）。`PRIVATE_DIR_INVALID` → 400，
`PATH_FORBIDDEN` → 403（越界比"目录不存在"更值得区分，见 `MODULES.md` §4.3）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..core import errors
from ..schemas.models import PrivateRescanRequest
from ..services import private_scanner
from ..services.private_scanner import PathForbidden, PrivateDirInvalid
from .deps import get_conn

router = APIRouter()
log = logging.getLogger("pgassistant.private")


def _private_dir_detail(conn) -> str | None:
    try:
        return private_scanner._read_setting(conn, private_scanner.SETTING_KEY)
    except Exception:
        return None


@router.post("/private/rescan", tags=["private"], summary="重扫私有目录")
def rescan_private(body: PrivateRescanRequest | None = None, conn=Depends(get_conn)):
    try:
        return private_scanner.rescan(conn)
    except PathForbidden:
        raise HTTPException(
            status_code=403,
            detail=errors.error_payload(
                errors.PATH_FORBIDDEN,
                "私有资料目录越界",
                detail={"private_dir": _private_dir_detail(conn)},
                hint="路径必须位于 data/private 内（防目录穿越，§13.3）",
            ),
        ) from None
    except PrivateDirInvalid:
        raise HTTPException(
            status_code=400,
            detail=errors.error_payload(
                errors.PRIVATE_DIR_INVALID,
                "私有资料目录不可用",
                detail={"private_dir": _private_dir_detail(conn)},
                hint="路径必须存在、可读，且位于 data/private 内",
            ),
        ) from None
