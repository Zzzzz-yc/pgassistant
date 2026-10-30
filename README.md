# pgassistant

本地运行的学习资料库与学习计划助手。154 篇资料随仓库分发，首次启动自动建库索引；中文全文检索，计划与助手模块按里程碑推进。

> 设计文档：[需求文档.md](需求文档.md)（v2.1，22 章）· 交接说明：[HANDOFF.md](HANDOFF.md)

---

## 现状

| 模块 | 状态 | 说明 |
|---|---|---|
| 内容包 | ✅ 完成 | 154 篇 / 694 文件 / 11.8 MB，覆盖 4 个技术栈 |
| 索引与检索 | ✅ 完成 | schema v1（14 表 + FTS5）、jieba 分词、bm25 列权重、三级降级 |
| HTTP 接口 | ✅ 完成 | health / search / resources / taxonomy 五组只读接口 |
| 内容校验 | ✅ 完成 | validate_content.py，当前 0 错误 19 警告 |
| 鉴权 | ✅ 完成 | M-1：Host / Origin / Sec-Fetch-Site / 一次性 X-App-Token |
| schema 迁移 | ✅ 完成 | PRAGMA user_version + 顺序脚本 + 迁移前备份 |
| 前端 | ⬜ 待做 | React + Vite，侧栏为计划节点树 |
| 计划与助手 | ⬜ 待做 | M3 / M4 |

覆盖率自检：70 个典型查询命中 54 个（77%），缺口见 [docs/crawl-targets.md](docs/crawl-targets.md)。

---

## 快速开始

需要 Python 3.11+。

```bash
# 1. 装依赖
python -m pip install -r backend/requirements.txt

# 2. 建库并索引（首次约 2 秒）
python backend/scripts/bootstrap_index.py

# 3. 起服务
python -m uvicorn app.main:app --app-dir backend --port 8000
```

打开 http://127.0.0.1:8000/ 是一张状态页（前端在 M2 落地），
http://127.0.0.1:8000/docs 是接口文档。

> **接口要带令牌**：除 `/api/health` 外，所有 `/api/*` 都要带 `X-App-Token` 头
> （防本地 CSRF 与 DNS rebinding，见需求文档 §13.7）。令牌每次启动随机生成，
> 打印在启动日志里，也写在状态页 head 的 `<meta name="x-app-token">` 中：
>
> ```bash
> curl -H "X-App-Token: <令牌>" "http://127.0.0.1:8000/api/search?q=闭包"
> ```

验证是否正常：

```bash
python backend/scripts/validate_content.py     # 内容体检
python backend/scripts/content_gaps.py         # 主题覆盖率
python backend/scripts/security_selfcheck.py   # M-1 鉴权与迁移自检
python -m pip install -r backend/requirements-dev.txt && pytest   # 回归测试
```

---

## 目录结构

```
.
├─ 需求文档.md            # 唯一规格来源
├─ HANDOFF.md             # 交接说明与待办
├─ content/               # 资料库（只读，随仓库分发）
│  ├─ CREDITS.md          # 内容来源与许可
│  ├─ assets/             # 全局图片
│  └─ {stack}/{category}/*.md
├─ backend/
│  ├─ app/
│  │  ├─ main.py          # FastAPI 入口（中间件 / 启动迁移 / health）
│  │  ├─ core/            # config / errors / security（Host·Origin·Token）
│  │  ├─ middleware/      # ASGI 鉴权中间件 + HTML token 注入
│  │  ├─ api/             # 路由：pages / search / resources / taxonomy
│  │  ├─ db/              # connection / migrate / migrations / schema_v1.sql
│  │  └─ services/        # tokenizer / frontmatter / indexer / search
│  ├─ scripts/            # bootstrap / validate / gaps / security_selfcheck
│  ├─ tests/              # pytest：鉴权 + 迁移
│  ├─ requirements.txt
│  └─ requirements-dev.txt
├─ tools/                 # 内容维护脚本（不参与运行）
└─ docs/                  # 缺口报告、补充框架、探针数据
```

---

## 已经能用的接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 服务与数据库状态（唯一免令牌的接口） |
| GET | /api/search?q=&stack=&level=&type=&limit= | 检索，返回 mode / tokens / items |
| GET | /api/resources?stack=&page=&size= | 分页列表 |
| GET | /api/resources/{slug} | 详情（含正文与标签） |
| GET | /api/taxonomy | 分类树（按库内实际数据聚合，无空分类） |
| GET | / | M0 状态页（M2 换成前端） |

```bash
curl -H "X-App-Token: <令牌>" "http://127.0.0.1:8000/api/search?q=闭包&limit=2"
```

请求被拒绝时返回统一信封 `{ code, message, detail?, hint? }`，
`code` 常见值：`TOKEN_MISSING` / `TOKEN_INVALID` / `BAD_HOST` / `BAD_ORIGIN` / `CROSS_SITE`。

---

## 内容维护

不要在 content/ 里手写内容，改脚本重跑，保证可复现。详见 [tools/README.md](tools/README.md)。

```
改 sources.json → fetch_intro.py → validate_content.py → bootstrap_index.py → content_gaps.py
```

---

## 开发路线

M-1 安全地基（已完成）→ M0 骨架 → M1 内容管道（已完成）→ M2 资料前端 → M3 计划 → M4 助手 → M5 打磨 → M6 代码运行。

每个阶段的验收标准见 [需求文档.md](需求文档.md) §15 / §16。

---

## 许可

代码 MIT（见 LICENSE）。学习资料来自 MIT / CC-BY-SA 等开源项目，逐条署名见 [content/CREDITS.md](content/CREDITS.md)。
