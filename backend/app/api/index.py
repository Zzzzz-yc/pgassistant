"""索引端点（`API.md` §3.22 / §3.23、需求文档 §6.1）。

- `POST /api/index/rebuild`：202 + `task_id`；已有任务 → 409 `INDEX_BUILDING`；
  5 分钟内重复 → 429 `RATE_LIMITED` + `Retry-After`（§7.10）；
- `GET /api/index/status`：200 `IndexStatus`；`task_id` 不存在 → 404 `TASK_NOT_FOUND`。

`db_path` 取 `deps.DB_PATH`（模块属性，测试可替换）、内容目录取 `SETTINGS.content_dir`，
所以重建用的库与当前请求看到的是**同一个库**。
"""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core import config as core_config
from ..core import errors
from ..schemas.models import IndexRebuildRequest
from ..services import index_task
from . import deps

router = APIRouter()


@router.post("/index/rebuild", status_code=202, tags=["index"],
             summary="重建索引（1 次 / 5 分钟）")
def rebuild_index(body: IndexRebuildRequest | None = None):
    mode = body.mode if body is not None else "full"
    try:
        task_id = index_task.start_rebuild(
            mode, db_path=deps.DB_PATH, content_dir=core_config.SETTINGS.content_dir,
        )
    except index_task.IndexAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail=errors.error_payload(
                errors.INDEX_BUILDING,
                "正在建立索引",
                detail={"task_id": exc.task_id, "percent": round(float(exc.percent), 1)},
                hint="等当前任务结束，或轮询 /api/index/status 看进度",
            ),
        ) from None
    except index_task.RateLimited as exc:
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(max(1, int(math.ceil(exc.retry_after))))},
            detail=errors.error_payload(
                errors.RATE_LIMITED,
                "重建索引过于频繁",
                detail={"retry_after": round(float(exc.retry_after), 1)},
                hint="重建索引限 1 次 / 5 分钟，等一会再试",
            ),
        ) from None

    return {
        "task_id": task_id,
        "accepted": True,
        "status": index_task.status(task_id, db_path=deps.DB_PATH),
    }


@router.get("/index/status", tags=["index"], summary="索引进度（running 时可 1s 轮询）")
def index_status(task_id: str | None = Query(None, max_length=64)):
    try:
        return index_task.status(task_id, db_path=deps.DB_PATH)
    except index_task.TaskNotFound:
        raise HTTPException(
            status_code=404,
            detail=errors.error_payload(
                errors.TASK_NOT_FOUND,
                "任务不存在",
                detail={"task_id": task_id},
                hint="不带 task_id 调用可拿到最近一次任务",
            ),
        ) from None
