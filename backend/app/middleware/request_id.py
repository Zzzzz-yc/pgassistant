"""请求 ID 中间件（需求文档 §9.9 / §12.3，M5）。

三件事，一件都不能少（验收会逐条查）：

1. **每个请求一个 id**：优先透传客户端带来的 `X-Request-Id`（只接受
   `[A-Za-z0-9._-]{1,64}`，避免把换行/超长串写进日志），否则生成 16 位十六进制；
2. **响应头回显**：所有响应（含 4xx/5xx、含 SSE）都补 `X-Request-Id`；
3. **日志带上它**：`current_request_id` 是 `ContextVar`，`RequestIDLogFilter`
   在日志记录没有显式 `request_id` 时补当前值（formatter 里的 `%(request_id)s` 靠它）。

5xx 信封里的 `request_id` 由 `main.py` 的异常处理器用 `request_id_of()` 取值后塞进
`detail.request_id`（顶层也留一份，对齐 `API.md` §1.5 的字段表）。
"""
from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import Headers

HEADER = "X-Request-Id"
HEADER_BYTES = b"x-request-id"
#: 透传进来的 id 必须干净：不能有换行、不能超长（否则日志注入）
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

current_request_id: ContextVar[str] = ContextVar("pgassistant_request_id", default="-")


def new_request_id() -> str:
    """16 位十六进制（≤64，符合 API.md §1.5 对 request_id 的约束）。"""
    return uuid4().hex[:16]


def incoming_request_id(headers: Headers) -> str | None:
    raw = (headers.get(HEADER) or "").strip()
    return raw if raw and SAFE_REQUEST_ID.match(raw) else None


def request_id_of(request) -> str:
    """从 scope / contextvar 取当前请求 id；都取不到就现生成一个（不返回 None）。"""
    scope = getattr(request, "scope", None) or {}
    state = scope.get("state") or {}
    rid = state.get("request_id")
    if rid:
        return rid
    rid = current_request_id.get()
    return rid if rid and rid != "-" else new_request_id()


def with_request_id(payload: dict, request) -> dict:
    """5xx 信封补 request_id（顶层 + detail.request_id，两处都放，前端读哪处都行）。"""
    rid = request_id_of(request)
    payload = dict(payload)
    payload["request_id"] = rid
    detail = payload.get("detail")
    if isinstance(detail, dict):
        detail = dict(detail)
        detail.setdefault("request_id", rid)
        payload["detail"] = detail
    elif detail is None:
        payload["detail"] = {"request_id": rid}
    return payload


class RequestIDMiddleware:
    """纯 ASGI：只在 `http.response.start` 上补一个头，不缓冲 body（SSE 不受影响）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = incoming_request_id(Headers(scope=scope)) or new_request_id()
        # 供 Request.state / 异常处理器读取；不要覆盖已有的 state
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        token = current_request_id.set(rid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                if not any(name.lower() == HEADER_BYTES for name, _ in raw):
                    raw.append((HEADER_BYTES, rid.encode("latin-1")))
                message = {**message, "headers": raw}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            current_request_id.reset(token)


class RequestIDLogFilter(logging.Filter):
    """给没有 request_id 的日志记录补上当前值（formatter 用 `%(request_id)s`）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = current_request_id.get()
        return True


def install_log_filter() -> None:
    """在 lifespan 里调用：给 root 的所有 handler 挂上过滤器。

    `logging.basicConfig` 已经建好 handler；这里只补 filter，不重复添加 handler。
    """
    log_filter = RequestIDLogFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, RequestIDLogFilter) for item in handler.filters):
            handler.addFilter(log_filter)
