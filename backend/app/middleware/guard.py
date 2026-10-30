"""鉴权中间件（需求文档 §13.7 / M-1）。

纯 ASGI 实现，不用 BaseHTTPMiddleware：
- 只在 text/html 上缓冲响应做 token 注入，SSE（text/event-stream）保持流式，
  M4 的 /api/agent/chat 不会被这里糊住；
- 拒绝响应直接在这里构造，形状统一为 { code, message, detail?, hint? }。
"""
from __future__ import annotations

import json
import logging

from starlette.datastructures import Headers

from ..core import errors, security

log = logging.getLogger("pgassistant.security")

EXEMPT_API_PATHS = frozenset({"/api/health"})
HTML_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def harden(message: dict) -> dict:
    """给响应补三个安全头（内容可缓存与否由各路由自己决定）。"""
    headers = list(message.get("headers") or [])
    present = {k.lower() for k, _ in headers}
    for name, value in HTML_SECURITY_HEADERS:
        if name not in present:
            headers.append((name, value))
    cloned = dict(message)
    cloned["headers"] = headers
    return cloned


class SecurityGuard:
    def __init__(self, app, policy: security.SecurityPolicy,
                 exempt_paths=EXEMPT_API_PATHS, inject_html: bool = True):
        self.app = app
        self.policy = policy
        self.exempt_paths = frozenset(exempt_paths)
        self.inject_html = inject_html

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()
        host = headers.get("host")

        # ① Host 白名单：不合法的 Host 连页面都不给（§13.7 配套要求 2）
        if not self.policy.host_ok(host):
            await self._deny(
                send, 403, errors.BAD_HOST, "Host 不在白名单内",
                detail={"host": host},
                hint="只允许 127.0.0.1 / localhost / [::1] 访问；"
                     "这是本机服务，不接受域名访问",
            )
            return

        # ②③ /api/* 的 Origin、Sec-Fetch-Site、X-App-Token
        if is_api_path(path) and path not in self.exempt_paths:
            denial = self._check_api(headers, method)
            if denial:
                await self._deny(send, 403, **denial)
                return

        await self.app(scope, receive, self._rewriter(send) if self.inject_html else send)

    # ------------------------------------------------------------------ 判定
    def _check_api(self, headers: Headers, method: str) -> dict | None:
        origin = headers.get("origin")
        fetch_site = headers.get("sec-fetch-site")

        if method == "OPTIONS":
            # CORS 预检必然不带自定义头；只验 Origin，其余交给 CORS 中间件
            if origin is not None and not self.policy.origin_ok(origin):
                return self._bad_origin(origin)
            return None

        if origin is not None:
            # 浏览器里 Origin 是禁止脚本伪造的头，命中白名单即可信；
            # 开发态 5173 -> 8000 直连时 Sec-Fetch-Site 必然是 cross-site，
            # 此时以 Origin 为准，否则 Vite 代理之外的路由全废。
            if not self.policy.origin_ok(origin):
                return self._bad_origin(origin)
        else:
            if method not in security.SAFE_METHODS:
                return {
                    "code": errors.MISSING_ORIGIN,
                    "message": "写操作必须带同源 Origin",
                    "hint": "浏览器会自动带上；脚本调用请显式设置 "
                            "Origin: http://127.0.0.1:<端口>",
                }
            if not self.policy.fetch_site_ok(fetch_site):
                return {
                    "code": errors.CROSS_SITE,
                    "message": "跨站请求被拒绝",
                    "detail": {"sec_fetch_site": fetch_site},
                    "hint": "本机接口只接受本站页面发起的请求",
                }

        token = headers.get(security.TOKEN_HEADER)
        if not self.policy.token_ok(token):
            if not token:
                return {
                    "code": errors.TOKEN_MISSING,
                    "message": "缺少 X-App-Token",
                    "hint": "token 在页面 <meta name=\"x-app-token\"> 里，"
                            "随进程重启更换",
                }
            return {
                "code": errors.TOKEN_INVALID,
                "message": "X-App-Token 不正确",
                "hint": "服务重启后旧 token 失效，重新加载页面即可",
            }
        return None

    @staticmethod
    def _bad_origin(origin: str | None) -> dict:
        return {
            "code": errors.BAD_ORIGIN,
            "message": "Origin 不在白名单内",
            "detail": {"origin": origin},
            "hint": "只接受 http://127.0.0.1:<端口> / http://localhost:<端口>",
        }

    # ------------------------------------------------------------------ 响应
    async def _deny(self, send, status: int, code: str, message: str,
                    detail=None, hint=None):
        log.warning("security deny code=%s status=%d", code, status)
        body = json.dumps(
            errors.error_payload(code, message, detail, hint), ensure_ascii=False
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("latin-1")),
            (b"cache-control", b"no-store"),
        ] + list(HTML_SECURITY_HEADERS)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    def _rewriter(self, send):
        """只在 HTML 里注入 token；其它 content-type 原样透传（含 SSE）。"""
        state: dict = {"start": None, "headers": [], "buffering": False, "chunks": []}

        async def wrapped(message):
            mtype = message["type"]

            if mtype == "http.response.start":
                raw = list(message.get("headers") or [])
                ctype = ""
                for name, value in raw:
                    if name.lower() == b"content-type":
                        ctype = bytes(value).decode("latin-1").lower()
                        break
                if ctype.startswith("text/html"):
                    state["buffering"] = True
                    state["start"] = message
                    state["headers"] = [
                        (k, v) for k, v in raw if k.lower() != b"content-length"
                    ] + list(HTML_SECURITY_HEADERS)
                    state["chunks"] = []
                    return                      # 先压住，等 body 到齐
                await send(harden(message))
                return

            if mtype == "http.response.body" and state["buffering"]:
                state["chunks"].append(bytes(message.get("body") or b""))
                if message.get("more_body", False):
                    return
                body = b"".join(state["chunks"])
                try:
                    body = self.policy.inject_token(body.decode("utf-8")).encode("utf-8")
                except UnicodeDecodeError:
                    pass                     # 不是 UTF-8 就原样返回，注入失败不阻断
                start = dict(state["start"])
                start["headers"] = state["headers"] + [
                    (b"content-length", str(len(body)).encode("latin-1"))
                ]
                state["buffering"] = False
                await send(start)
                await send(
                    {"type": "http.response.body", "body": body, "more_body": False}
                )
                return

            await send(message)

        return wrapped
