"""M0 占位页（真实前端在 M2 用 Vite 构建后替换）。

留着它的两个理由：
1. token 注入（§13.7）需要一个 HTML 出站口，M-1 验收要看得到
   <meta name="x-app-token">；
2. 没有前端时也能一眼确认「服务起来了、库建好了、鉴权生效了」。
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

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
  <p class="sub">本地学习资料库 · M1 内容管道已完成，前端在 M2 落地</p>
  <dl>
    <dt>服务</dt><dd id="health">检查中…</dd>
    <dt>资料库</dt><dd id="taxonomy">检查中…</dd>
    <dt>令牌</dt><dd id="token">检查中…</dd>
  </dl>
  <p>接口文档 <a href="/docs">/docs</a>（除 <code>/api/health</code> 外都要带
     <code>X-App-Token</code> 请求头，值就在本页 head 的 meta 标签里）</p>
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


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return HTMLResponse(PAGE)
