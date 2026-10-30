"""设置端点（`API.md` §3.19–§3.21 / 需求文档 §10）。

三条硬约束：

1. `GET` 与 `PUT` 都**只出掩码**（`api_key_masked`），明文只在后端内存里流转（§13.1）；
2. `PUT` 是**局部更新**：未提供的组保持原值；`api_key` 不传 = 保持原值；
3. `POST /api/settings/test` 的探针失败也是 **200**（业务结果不是 HTTP 错误）。

错误映射（`MODULES.md` §4.3）：
`PathForbidden` → 403 `PATH_FORBIDDEN`；`PrivateDirInvalid` → 400 `PRIVATE_DIR_INVALID`；
`SecretUnavailable` → 500 `INTERNAL`（detail 说明已降级为文件）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..core import errors
from ..schemas.models import SettingsTestRequest, SettingsWrite
from ..services import secrets, settings_service
from ..services.private_scanner import PathForbidden, PrivateDirInvalid
from .deps import get_conn

router = APIRouter()
log = logging.getLogger("pgassistant.settings")


@router.get("/settings", tags=["settings"], summary="读取设置（密钥只出掩码）")
def get_settings(conn=Depends(get_conn)):
    return settings_service.read_settings(conn)


@router.put("/settings", tags=["settings"], summary="写入设置（局部更新）")
def put_settings(body: SettingsWrite, conn=Depends(get_conn)):
    try:
        return settings_service.write_settings(conn, body)
    except PathForbidden:
        raise HTTPException(
            status_code=403,
            detail=errors.error_payload(
                errors.PATH_FORBIDDEN,
                "私有资料目录越界",
                detail={"private_dir": body.private_dir},
                hint="路径必须位于 data/private 内（防目录穿越，§13.3）",
            ),
        ) from None
    except PrivateDirInvalid:
        raise HTTPException(
            status_code=400,
            detail=errors.error_payload(
                errors.PRIVATE_DIR_INVALID,
                "私有资料目录不可用",
                detail={"private_dir": body.private_dir},
                hint="路径必须存在、可读，且位于 data/private 内",
            ),
        ) from None
    except secrets.SecretUnavailable as exc:
        raise HTTPException(
            status_code=500,
            detail=errors.error_payload(
                errors.INTERNAL,
                "系统凭据库不可用，已降级为文件",
                detail={"error": str(exc)},
                hint="检查 data/ 目录的写权限",
            ),
        ) from None


@router.post("/settings/test", tags=["settings"], summary="LLM / embedding 连通性探针")
async def test_settings(body: SettingsTestRequest, conn=Depends(get_conn)):
    """带 override 时用请求里的配置测，**不落库**（`API.md` §3.21）。"""
    return await settings_service.test_connection(conn, body)
