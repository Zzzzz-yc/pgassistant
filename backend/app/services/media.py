"""内容媒体：路径规范化与 MIME 表（D24 / `API.md` §3.26）。

职责只有两件事，签名冻结以便 M5 的 `api/media_private.py` 原样复用（`MODULES.md` §1.1）：

1. `normalize_media_path(raw)`：把 URL 里的 content 根相对路径**收成安全的分段元组**，
   任何一条不合规就抛 `MediaPathError`（调用方统一回 404，不给探测者区分信号）。
2. `resolve_media_file(root, raw)`：在给定根（M2 是 `content/`，M5 是 `data/private/`）
   下解析成**普通文件**的绝对路径；符号链接逃逸、目录、设备文件一律拒绝。

三条设计取舍：

- **先判定、后落盘**：所有字符串规则在 `Path.resolve()` 之前跑完，避免把怪路径交给文件系统。
- **判定用解码后的路径**：`%2e%2e` 这类编码穿越必须先解码再判定（`API.md` §3.26 规则 1）。
- **MIME 硬编码**：不用 `mimetypes` 猜——Windows 注册表会把 `.svg` / `.js` 报成文本类型。
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

#: 扩展名 → Content-Type。大小写不敏感；不在此表的一律 404（含 `.md` / `.json`）。
MEDIA_MIME_TYPES: dict[str, str] = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
}

#: 只有 SVG 需要显式 CSP：它能内嵌脚本与外链，禁掉一切子资源。
MEDIA_SVG_SUFFIX = ".svg"
MEDIA_SVG_CSP = "default-src 'none'"
#: 本机私有内容不进共享缓存，1 小时够 `<img>` 用。
MEDIA_CACHE_CONTROL = "private, max-age=3600"

#: Windows 盘符（含 `C:file` 这种相对当前盘的形式）；任何段里的 `:` 都按盘符/ADS 处理。
_DRIVE = re.compile(r"[A-Za-z]:")

#: 错误只用这一个：`API.md` §3.26 要求失败统一 404 `MEDIA_NOT_FOUND`。
MESSAGE_MEDIA_NOT_FOUND = "媒体文件不存在"
HINT_MEDIA_NOT_FOUND = "检查正文里的图片相对路径，或重跑内容校验与索引"


class MediaPathError(ValueError):
    """路径不合法（穿越 / 盘符 / 隐藏段 / 扩展名不在白名单 / 超出根）。"""


def normalize_media_path(raw: str | None) -> tuple[str, ...]:
    """把 content 根相对路径收成分段元组；不合法抛 `MediaPathError`。

    覆盖 `API.md` §3.26 的规则 1–4 与 7（规则 5–6 需要文件系统，见 `resolve_media_file`）。
    """
    if not raw:
        raise MediaPathError("空路径")

    # 规则 1：判定发生在 percent-decoded 之后。FastAPI 已解一次，这里再解一次，
    # 让 `%252e%252e`（双重编码）同样落成 `..` 被拦下。
    decoded = unquote(unquote(raw))

    # 规则 2：NUL 与反斜杠（Windows 分隔符，绕过 POSIX 风格分段判定）
    if "\x00" in decoded or "\\" in decoded:
        raise MediaPathError("含 NUL 或反斜杠")

    # 规则 3：绝对路径、盘符、UNC（UNC 已被反斜杠规则挡住，双斜杠由空段规则挡住）
    if decoded.startswith("/"):
        raise MediaPathError("绝对路径")
    if _DRIVE.search(decoded):
        raise MediaPathError("盘符前缀")

    # 规则 1 续：`.` / `..` / 空段（`//`、结尾 `/`）
    segments = decoded.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise MediaPathError("非法路径段")

    # 规则 4：隐藏段（`.git` / `.env` …）
    if any(segment.startswith(".") for segment in segments):
        raise MediaPathError("隐藏路径段")

    # 规则 7：扩展名白名单
    suffix = Path(segments[-1]).suffix.lower()
    if suffix not in MEDIA_MIME_TYPES:
        raise MediaPathError("扩展名不在白名单")

    return tuple(segments)


def media_mime_type(segments) -> str:
    """按白名单返回 Content-Type；调用前必须已经过 `normalize_media_path`。"""
    suffix = Path(tuple(segments)[-1]).suffix.lower()
    return MEDIA_MIME_TYPES[suffix]


def resolve_media_file(root: Path | str, raw: str | None) -> tuple[Path, str, str]:
    """解析成 `(绝对路径, Content-Type, 规范化相对路径)`。

    - 规则 5：`resolve()` 之后必须仍在**已 resolve 的根**内（符号链接指向根外 → 拒绝）；
    - 规则 6：目标必须是普通文件（目录 / 设备文件 → 拒绝）；
    - 返回值里的相对路径只含 content 根相对段，**不含服务器绝对路径**（§13.1-3）。
    """
    segments = normalize_media_path(raw)
    mime = media_mime_type(segments)

    base = Path(root).resolve()
    target = base.joinpath(*segments)
    try:
        resolved = target.resolve(strict=False)
    except OSError as exc:                       # 权限 / 非法字符：一律当不存在
        raise MediaPathError("路径无法解析") from exc

    if not resolved.is_relative_to(base):
        raise MediaPathError("超出内容根")
    if not resolved.is_file():
        raise MediaPathError("不是普通文件")

    return resolved, mime, "/".join(segments)


def content_root() -> Path:
    """M2 的内容根；测试可用环境变量覆盖（`PG_CONTENT_DIR`）。"""
    from ..core.config import SETTINGS

    return Path(SETTINGS.content_dir)
