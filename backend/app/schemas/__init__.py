"""契约层包（Pydantic v2 模型）。

用法：
    from app.schemas import SearchResponse, PlanDraft, Intent

导入面只有 `models`，外部不要直接 import `app.schemas.models`，
方便以后拆文件时不动调用方。

本包不含任何业务逻辑，也不 import FastAPI / DB / services（避免循环依赖）。
契约文档在 `docs/contracts/`，前端类型在 `frontend/src/types/api.ts`。
"""
from __future__ import annotations

from .models import *  # noqa: F401,F403
from .models import __all__ as _models_all

__all__ = list(_models_all)
