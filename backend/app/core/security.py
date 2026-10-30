"""§13.7 本地服务的三道防线（纯函数，便于单测）。

1. Host 头白名单 —— 挡 DNS Rebinding（浏览器不允许脚本伪造 Host）
2. Origin / Sec-Fetch-Site —— 挡 CSRF
3. 一次性令牌（X-App-Token）—— CSRF 兜底，跨站页面读不到 token

设计取舍写在注释里，改之前先看需求文档 §13.7。
"""
from __future__ import annotations

import hmac
import re
import secrets

TOKEN_HEADER = "x-app-token"
TOKEN_META_NAME = "x-app-token"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# 浏览器只在同源请求里发 same-origin；none 表示用户直接输入地址（含书签）
SAFE_FETCH_SITES = frozenset({"same-origin", "none"})
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})

META_TAG = '<meta name="%s" content="%s">' % (TOKEN_META_NAME, "%s")
# 精确匹配「真的有一个 meta 标签」，页面正文里提到这个名字不算已注入
META_PRESENT = re.compile(r"<meta\s+[^>]*name=[\"']%s[\"']" % TOKEN_META_NAME, re.I)


def new_token() -> str:
    """每个进程一枚，随重启失效，不落盘（§13.7 第 4 条）。"""
    return secrets.token_urlsafe(32)


def parse_host_header(value: str | None):
    """把 Host 拆成 (主机名, 端口)。格式不对一律返回 (None, None) —— 宁拒绝不放行。"""
    if not value:
        return None, None
    raw = value.strip().lower()
    if not raw:
        return None, None

    port = None
    if raw.startswith("["):                      # [::1]:8000
        end = raw.find("]")
        if end < 0:
            return None, None
        name = raw[1:end]
        rest = raw[end + 1:]
        if rest:
            if not rest.startswith(":"):
                return None, None
            port = rest[1:]
    else:
        if raw.count(":") > 1:                   # 未加方括号的 IPv6，浏览器不会这么发
            return None, None
        name, sep, tail = raw.partition(":")
        if sep:
            port = tail

    name = name.rstrip(".")
    if not name:
        return None, None
    if port is not None:
        if not port.isdigit():
            return None, None
        port = int(port)
        if not 0 < port < 65536:
            return None, None
    return name, port


class SecurityPolicy:
    """一个进程一份策略：token + 合法 Host / Origin 集合。"""

    def __init__(self, token: str, port: int, bind_host: str = "127.0.0.1",
                 dev_origins=()):
        self.token = token
        self.port = port
        self.bind_host = bind_host
        self.allowed_hostnames = set(LOCAL_HOSTNAMES)
        if bind_host and bind_host not in {"0.0.0.0", "::"}:
            self.allowed_hostnames.add(bind_host.lower())
        self.allowed_origins = {
            "http://127.0.0.1:%d" % port,
            "http://localhost:%d" % port,
            "http://[::1]:%d" % port,
        }
        for origin in dev_origins:
            self.allowed_origins.add(origin.strip().rstrip("/").lower())

    # --- 1. Host 白名单 ---------------------------------------------------
    def host_ok(self, host_header: str | None) -> bool:
        """只认本机主机名。

        端口不做严格比对：Host 的防线是「域名」，而 8001–8010 自动选端口时
        实际端口与配置端口可能不一致；多接受一个本机端口不会放宽任何攻击面。
        """
        name, _port = parse_host_header(host_header)
        return name in self.allowed_hostnames

    # --- 2. Origin / Sec-Fetch-Site ---------------------------------------
    def origin_ok(self, origin: str | None) -> bool:
        """Origin 必须精确命中白名单；'null'（沙箱 iframe、file://）一律拒绝。"""
        if not origin:
            return False
        return origin.strip().rstrip("/").lower() in self.allowed_origins

    def fetch_site_ok(self, value: str | None) -> bool:
        if not value:
            return True                              # 非浏览器客户端不发这个头
        return value.strip().lower() in SAFE_FETCH_SITES

    # --- 3. 一次性令牌 ----------------------------------------------------
    def token_ok(self, provided: str | None) -> bool:
        if not provided:
            return False
        return hmac.compare_digest(provided, self.token)

    # --- 4. 注入 ----------------------------------------------------------
    def inject_token(self, html: str) -> str:
        return inject_token_into_html(html, self.token)


def inject_token_into_html(html: str, token: str) -> str:
    """把 token 写进 <head>，前端从 meta 读（§13.7 第 3 条）。

    只在 Host 校验通过后才走到这里；重复注入会返回原文，避免 token 出现在两处。
    """
    if not html:
        return html
    if META_PRESENT.search(html):
        return html
    meta = META_TAG % token
    lower = html.lower()
    head_end = lower.rfind("</head>")
    if head_end >= 0:
        return html[:head_end] + meta + html[head_end:]
    head_start = lower.find("<head")
    if head_start >= 0:
        gt = lower.find(">", head_start)
        if gt >= 0:
            return html[:gt + 1] + meta + html[gt + 1:]
    return meta + html
