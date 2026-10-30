"""errors —— 统一错误信封（需求文档 §11：{ code, message, detail?, hint? }）。

中间件里不能抛 HTTPException（抛了就变成 FastAPI 的默认错误体），
所以拒绝响应统一走这里构造，保证所有 4xx/5xx 形状一致。
"""
from __future__ import annotations

import json

from starlette.responses import Response

# 常见错误码：集中定义，前端可以按 code 分支，不解析 message
BAD_HOST = "BAD_HOST"
BAD_ORIGIN = "BAD_ORIGIN"
MISSING_ORIGIN = "MISSING_ORIGIN"
CROSS_SITE = "CROSS_SITE"
TOKEN_MISSING = "TOKEN_MISSING"
TOKEN_INVALID = "TOKEN_INVALID"
RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
DB_NOT_READY = "DB_NOT_READY"
SCHEMA_TOO_NEW = "SCHEMA_TOO_NEW"
INTERNAL = "INTERNAL"
# D24 / API.md §3.26：`/media/*` 的路径非法、扩展名不在白名单或文件不存在 → HTTP 404。
# 常量只登记码本身；message / hint 由调用点给（风格见 API.md §1.6 与 §5 的示例）。
MEDIA_NOT_FOUND = "MEDIA_NOT_FOUND"
# M5 / API.md §1.6 + §5 + DECISIONS D25 25.6：405 独立码。
# 本版 FastAPI 对「路径存在、方法不匹配」默认给 404，M5 由 api/fallback.py 兜底成 405。
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
# M5 / MODULES.md §2.5 + §4.3：设置、索引、私有资料三块的码（值逐字对齐 API.md §5）。
TASK_NOT_FOUND = "TASK_NOT_FOUND"
INDEX_BUILDING = "INDEX_BUILDING"
INDEX_FAILED = "INDEX_FAILED"
PATH_FORBIDDEN = "PATH_FORBIDDEN"
PRIVATE_DIR_INVALID = "PRIVATE_DIR_INVALID"
RATE_LIMITED = "RATE_LIMITED"
NO_EMBEDDING = "NO_EMBEDDING"


def error_payload(code: str, message: str, detail=None, hint=None) -> dict:
    payload = {"code": code, "message": message}
    if detail is not None:
        payload["detail"] = detail
    if hint is not None:
        payload["hint"] = hint
    return payload


def error_response(status_code: int, code: str, message: str,
                   detail=None, hint=None, headers=None) -> Response:
    body = json.dumps(
        error_payload(code, message, detail, hint),
        ensure_ascii=False,
    ).encode("utf-8")
    merged = {
        "content-type": "application/json; charset=utf-8",
        "content-length": str(len(body)),
        "cache-control": "no-store",
    }
    if headers:
        merged.update({k.lower(): v for k, v in headers.items()})
    return Response(content=body, status_code=status_code, headers=merged)


def flatten_http_exception(status_code: int, detail):
    """把 HTTPException.detail 收敛成信封。

    已经写成 {code, message} 的（M1 里的 RESOURCE_NOT_FOUND 就是）直接透出；
    其它（字符串、FastAPI 校验错误）包一层，前端只看 code 与 message。
    """
    if isinstance(detail, dict) and "code" in detail:
        payload = dict(detail)
        payload.setdefault("message", status_code_phrase(status_code))
        return payload
    if status_code == 422:
        return error_payload(
            "VALIDATION_ERROR", "请求参数不合法",
            detail=detail,
            hint="检查查询参数类型与取值范围",
        )
    if status_code == 405:
        # D25 25.6：405 走独立码，Allow 头由异常自带（main 的处理器透传 exc.headers）
        return error_payload(
            METHOD_NOT_ALLOWED, "方法不允许",
            hint="该路径只支持响应头 Allow 里列出的方法",
        )
    message = detail if isinstance(detail, str) and detail else status_code_phrase(status_code)
    code = "NOT_FOUND" if status_code == 404 else INTERNAL if status_code >= 500 else "BAD_REQUEST"
    return error_payload(code, message)


def status_code_phrase(status_code: int) -> str:
    return {
        400: "请求不合法",
        401: "未通过鉴权",
        403: "请求被拒绝",
        404: "资源不存在",
        405: "方法不允许",
        409: "版本冲突",
        422: "请求参数不合法",
        429: "请求过于频繁",
        500: "服务内部错误",
        503: "服务尚未就绪",
    }.get(status_code, "请求失败")
