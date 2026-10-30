"""HTML 出口：`frontend/dist` 的 SPA 托管 + 无构建时的 M0 状态页。

两个职责，都在 M0（前端骨架）名下（MODULES.md §1.1）：

1. **有 dist 时**：`/` 与所有非 `/api` 路径返回 `dist/index.html`（前端自己路由），
   `/assets/*` 由 `main.py` 挂的静态目录出。
2. **无 dist 时**：`/` 返回 M0 状态页——token 注入（§13.7）需要一个 HTML 出站口，
   没有前端时也能一眼确认「服务起来了、库建好了、鉴权生效了」，并且 `pytest` 不依赖构建产物。

`/api/*` 的 404 **永远**走 JSON 信封（`NOT_FOUND`），不得被这里的 HTML 回退吞掉。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

PROJECT = Path(__file__).resolve().parents[3]

#: 前端构建产物目录。环境变量只给测试/特殊部署用；默认就是 `frontend/dist`。
#: 读模块属性（不是闭包捕获），测试可以 monkeypatch 到 tmp_path。
DIST_DIR = Path(os.environ.get("PG_FRONTEND_DIST", "").strip() or (PROJECT / "frontend" / "dist"))

router = APIRouter()

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pgassistant</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.7 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; padding: 48px 24px; background: #fafafa; color: #1a1a1a; }
  main { max-width: 640px; margin: 0 auto; }
  h1 { font-size: 20px; letter-spacing: .01em; margin: 0 0 4px; }
  p.sub { color: #6b6b6b; margin: 0 0 28px; }
  dl { display: grid; grid-template-columns: 120px 1fr; gap: 8px 16px; margin: 0 0 28px; }
  dt { color: #6b6b6b; }
  dd { margin: 0; font-variant-numeric: tabular-nums; }
  a { color: #2563eb; text-decoration: none; }
  code { background: #eeeeee; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
  @media (prefers-color-scheme: dark) {
    body { background: #141414; color: #e8e8e8; }
    code { background: #262626; }
    p.sub, dt { color: #9a9a9a; }
  }
</style>
</head>
<body>
<main>
  <h1>pgassistant</h1>
  <p class="sub">本地学习资料库 · 前端还没构建（<code>frontend/dist</code> 不存在），这是 M0 状态页</p>
  <dl>
    <dt>服务</dt><dd id="health">检查中…</dd>
    <dt>资料库</dt><dd id="taxonomy">检查中…</dd>
    <dt>令牌</dt><dd id="token">检查中…</dd>
  </dl>
  <p>接口文档 <a href="/docs">/docs</a>（除 <code>/api/health</code> 外都要带
     <code>X-App-Token</code> 请求头，值就在本页 head 的 meta 标签里）</p>
  <p>跑 <code>npm run build</code>（或 <code>start.ps1</code>）后刷新，就会换成真正的网站。</p>
</main>
<script>
  const meta = document.querySelector('meta[name="x-app-token"]');
  const token = meta ? meta.content : "";
  const tokenEl = document.getElementById("token");
  tokenEl.textContent = token
    ? "已注入（" + token.slice(0, 6) + "…，长度 " + token.length + "）"
    : "未注入（Host 校验未通过时页面根本不会返回）";

  const auth = token ? { "X-App-Token": token } : {};
  fetch("/api/health")
    .then((r) => r.json())
    .then((d) => {
      document.getElementById("health").textContent =
        d.status + " · schema v" + (d.schema_version ?? "?") +
        " · " + (d.db_ready ? "数据库就绪" : "数据库未建");
    })
    .catch(() => { document.getElementById("health").textContent = "请求失败"; });

  fetch("/api/taxonomy", { headers: auth })
    .then((r) => r.ok ? r.json() : Promise.reject(r.status))
    .then((d) => {
      document.getElementById("taxonomy").textContent =
        d.total + " 篇 · " + d.stacks.length + " 个技术栈";
    })
    .catch((e) => { document.getElementById("taxonomy").textContent = "读取失败 " + e; });
</script>
</body>
</html>
"""


def dist_dir() -> Path:
    """当前构建产物目录（每次读模块属性，便于测试替换）。"""
    return DIST_DIR


def dist_index() -> Path | None:
    """`dist/index.html`，不存在返回 None（= 还没构建）。"""
    index = dist_dir() / "index.html"
    return index if index.is_file() else None


def _not_found() -> HTTPException:
    """交给 main.py 的 HTTPException 处理器收敛成 `{code:"NOT_FOUND", ...}`。"""
    return HTTPException(status_code=404, detail="Not Found")


def _index_response(index: Path) -> FileResponse:
    # index.html 引用带哈希的 assets，重建后必须立刻生效，所以不缓存
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    """首页：有 dist 就给 SPA，没有就给 M0 状态页。"""
    built = dist_index()
    if built is not None:
        return _index_response(built)
    return HTMLResponse(PAGE)


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def spa_fallback(full_path: str):
    """SPA 回退：除 `/api` 与文档路径外的所有请求都交给前端路由。

    三条注意事项：
    1. 本路由必须在所有 `/api/*` 路由**之后**注册（main.py 末尾 include），
       否则会把接口吞成 HTML；
    2. `/api/*` 的未知路径在这里显式 404，保持 JSON 信封；
    3. 之所以不限定 `GET`：限定之后「路由存在但方法不对」的请求会变成
       `405 Method Not Allowed`，与 M1 基线里「未实现端点一律 404」的断言冲突
       （`tests/test_security.py::test_write_with_same_origin_passes_guard`）。
       放开方法名后，未实现端点仍然是 404 —— 语义与改动前一致。
    """
    if full_path == "api" or full_path.startswith("api/"):
        raise _not_found()
    # `media` 也是服务端前缀（D24 24.1：`/media/*` 由 api/media.py 提供，免令牌）：
    # 真文件由 media.router 在 pages.router 之前接管；这里只保证「缺文件/未实现」时
    # 返回 JSON 404，而不是把一张图片的 URL 回退成 200 的 index.html。
    if full_path.split("/", 1)[0] in {"docs", "redoc", "openapi.json", "assets", "media"}:
        raise _not_found()

    built = dist_index()
    if built is not None:
        return _index_response(built)
    # 没有构建产物时，只有 `/` 有意义，其它路径就是真的不存在
    raise _not_found()
