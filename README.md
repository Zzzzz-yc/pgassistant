# pgassistant

本地运行的学习资料库与学习计划助手。154 篇资料随仓库分发，首次启动自动建库索引；中文全文检索，
内置分层路由的助手，能按目标生成学习计划。**不配模型也能浏览和关键词搜索**，只有助手需要你自己的 API key。

> 设计文档：[需求文档.md](需求文档.md)（v2.2，22 章）· 交接说明：[HANDOFF.md](HANDOFF.md) · 首次 clone：[MIGRATION.md](MIGRATION.md) · 贡献：[CONTRIBUTING.md](CONTRIBUTING.md)

---

## 现状

| 模块 | 状态 | 说明 |
|---|---|---|
| 内容包 | ✅ 完成 | 154 篇 / 700 文件 / 11.9 MB，覆盖 4 个技术栈 |
| 索引与检索 | ✅ 完成 | schema v2（14 表 + FTS5 + 软删除）、jieba 分词、bm25 列权重、三级降级 |
| 后端接口 | ✅ 完成 | health / taxonomy / resources / search / plans / plan-templates / agent / media |
| 内容校验 | ✅ 完成 | `validate_content.py`，当前 0 错误 19 警告（警告都是导读页摘要短） |
| 鉴权 | ✅ 完成 | M-1：Host / Origin / Sec-Fetch-Site / 一次性 `X-App-Token`；`/media` 是唯一免令牌例外 |
| schema 迁移 | ✅ 完成 | `PRAGMA user_version` + 顺序脚本 + 迁移前备份（当前 v2） |
| 前端 | ✅ 完成 | M0 + M2：React 18 + Vite + Tailwind + 自建组件库，资料库 / 详情 / 筛选 / 命令面板 |
| 计划模块 | ✅ 完成 | M3：计划 CRUD、时间轴、打勾进度、乐观锁 |
| 助手 | ✅ 完成 | M4：L0–L3 路由、两段式计划生成、SSE；**只配 LLM 即可用，不依赖 embedding** |
| 设置 / 私有资料 / 语义检索 | ⬜ 收尾中 | M5：`/api/settings`、私有目录扫描、embedding 建向量 |
| 代码运行 | ⬜ 收尾中 | M6：Pyodide / Web Worker 抽屉，仅算法页 |

覆盖率自检：70 个典型查询命中 54 个（77%），缺口见 [docs/crawl-targets.md](docs/crawl-targets.md)。

---

## 快速开始

需要 **Python 3.11+** 与 **Node 20+**（前端构建用）。

```bash
# Windows 一键：建 .venv、装依赖、npm ci、npm run build、索引、起服务
.\start.ps1
# macOS / Linux
./start.sh
```

脚本会在 8000–8010 里挑一个空闲端口，把实际地址写进 `data/runtime.json` 并打开浏览器。

手动等价命令：

```bash
# 1. 建环境并装依赖
python -m venv .venv
.venv\Scripts\activate        # Windows；macOS / Linux 用 source .venv/bin/activate
python -m pip install -r backend/requirements.txt

# 2. 建库并索引（首次约 2 秒）
python backend/scripts/bootstrap_index.py

# 3. 构建前端（首次；之后改了前端代码才需重跑）
npm ci --prefix frontend
npm run build --prefix frontend

# 4. 起服务
python -m uvicorn app.main:app --app-dir backend --port 8000
```

> **装依赖卡住怎么办**：直连 PyPI 在部分网络下会打印 `Looking in indexes` 后长时间没有输出，
> 换镜像即可：
> `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend/requirements.txt`。
>
> **jieba 也装不上**（sdist 构建卡住）时不必强求：把 jieba 包目录放进项目根 `.deps/jieba`，
> `app/main.py` 与各脚本都会把 `.deps` 加进 `sys.path`。缺 jieba 时中文检索退化为按字切分，
> `/api/search` 返回 `mode=empty` 而不是报错（见 [HANDOFF.md](HANDOFF.md) §3 第 7 条）。

打开 http://127.0.0.1:8000/ 就是应用本身（SPA）；http://127.0.0.1:8000/docs 是接口文档。

### 开发态（Vite 热更新）

```bash
.\start.ps1 -Dev        # 后端 :8000 + Vite :5173，自动开 :5173
```

`-Dev` 会设 `PG_DEV_ORIGINS=http://127.0.0.1:5173`，并让 Vite 把 **`/api` 与 `/media`** 都代理到后端：

- `/api` 代理：否则写请求的 Origin 不在白名单里。
- `/media` 代理：`<img>` 带不了 `X-App-Token`，内容媒体走免令牌的 `/media/*`；Vite 不代理会把它
  当成 SPA 路由回 `index.html`，dev 下图片全裂。生产态同源，没有这个问题。

> **接口要带令牌**：除 `/api/health` 与 `/media/*` 外，所有 `/api/*` 都要带 `X-App-Token` 头
> （防本地 CSRF 与 DNS rebinding，见需求文档 §13.7）。令牌每次启动随机生成，
> 打印在启动日志里，也写在页面 head 的 `<meta name="x-app-token">` 中：
>
> ```bash
> curl -H "X-App-Token: <令牌>" "http://127.0.0.1:8000/api/search?q=闭包"
> ```
>
> `/media/*` 免令牌但**只同源可用**：仍校验 Host、拒绝 `Sec-Fetch-Site: cross-site`、只允许 GET/HEAD，
> 且按扩展名白名单（`.webp .png .jpg .jpeg .gif .svg .avif`）取文件。

### 验证是否正常

```bash
python backend/scripts/validate_content.py     # 内容体检，退出码 0 才算过
python backend/scripts/content_gaps.py         # 主题覆盖率
python backend/scripts/security_selfcheck.py   # M-1 鉴权与迁移自检（12 项）
python -m pip install -r backend/requirements-dev.txt
python -m pytest -q                            # 回归测试（临时文件默认放系统 tmp）
npm run build --prefix frontend                # 前端类型检查 + 构建（build 内含 tsc --noEmit）
```

> **`--basetemp` 的用法**：想固定 pytest 的临时目录（排查失败用例时方便）就加
> `python -m pytest -q --basetemp=data/.pytest-tmp`。该路径已在 `.gitignore` 里，跑完可以随手删；
> **不带 `--basetemp` 时不要让它落在 `data/app.db` 旁边**——测试不该污染 `data/`。

---

## 目录结构

```
.
├─ 需求文档.md            # 唯一规格来源
├─ README.md / HANDOFF.md / MIGRATION.md / CONTRIBUTING.md
├─ content/               # 资料库（只读，随仓库分发）
│  ├─ _meta/              # taxonomy.yml、jieba_dict.txt、templates/*.yml
│  ├─ CREDITS.md          # 内容来源与许可
│  ├─ assets/             # 全局图片
│  └─ {stack}/{category}/*.md
├─ backend/
│  ├─ app/
│  │  ├─ main.py          # FastAPI 入口（中间件 / 启动迁移 / health）
│  │  ├─ core/            # config / errors / security（Host·Origin·Token）
│  │  ├─ middleware/      # ASGI 鉴权中间件 + HTML token 注入
│  │  ├─ api/             # pages / resources / search / taxonomy / media / plans /
│  │  │                   # plan_templates / agent（settings / index / private 属 M5）
│  │  ├─ db/              # connection / migrate / migrations / schema_v1.sql
│  │  ├─ prompts/         # 提示词模板
│  │  ├─ schemas/         # Pydantic v2 模型
│  │  └─ services/        # indexer / search / media / plan_service / llm_client /
│  │                      # planner / router / sse / tokenizer / frontmatter
│  ├─ scripts/            # bootstrap / validate / gaps / security_selfcheck
│  ├─ tests/              # pytest：鉴权、迁移、检索、计划、助手
│  ├─ requirements.txt
│  └─ requirements-dev.txt
├─ frontend/              # React + Vite（src/、public/、scripts/、dist/ 不入库）
├─ docs/                  # crawl-targets.md、content-backlog.md、gap-probe.json
│  └─ contracts/          # API.md / DB.md / MODULES.md / DECISIONS.md（契约，冻结）
├─ tools/                 # 内容维护脚本（不参与运行）
├─ .github/               # ci.yml + Issue 模板
└─ handoff/               # 交接包与团队状态**不入库**（.gitignore 已忽略）
```

> `handoff/` 是本地协作资料（状态文件、验收截图、转换脚本草稿），**不进仓库**；仓库内的内容
> 生产工具在 `tools/`。

---

## 已经能用的接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 服务与数据库状态（唯一免令牌的 `/api/*`） |
| GET | /api/taxonomy | 分类树（按库内实际数据聚合，无空分类） |
| GET | /api/search?q=&stack=&level=&type=&limit= | 检索，返回 mode / tokens / items |
| POST | /api/search | 同一查询的 JSON body 版（命令面板用） |
| GET | /api/resources?stack=&page=&size= | 分页列表 |
| GET | /api/resources/{slug} | 详情（正文、标签、`media_base`） |
| GET | /api/resources/{slug}/related | 相关推荐 |
| GET/HEAD | /media/{path} | 内容媒体（免令牌，仅同源） |
| GET/POST | /api/plans | 计划列表 / 保存计划 |
| GET/PATCH/DELETE | /api/plans/{plan_id} | 详情 / 改名归档 / 软删除 |
| GET | /api/plan-templates | 计划模板（套模板零 token） |
| POST | /api/plans/{plan_id}/stages · PATCH/DELETE /api/stages/{stage_id} | 阶段增删改 |
| POST | /api/plan-items · PATCH/DELETE /api/plan-items/{item_id} | 条目增删改（打勾 / 改时长 / 换阶段） |
| POST | /api/agent/extract | 一句话 → 表单字段 |
| POST | /api/agent/plan-draft | 表单 → 计划草稿（不落库） |
| POST | /api/agent/chat | SSE 流式对话（`fetch + ReadableStream`，不能用 EventSource） |

```bash
curl -H "X-App-Token: <令牌>" "http://127.0.0.1:8000/api/search?q=闭包&limit=2"
```

请求被拒绝时返回统一信封 `{ code, message, detail?, hint? }`，
`code` 常见值：`TOKEN_MISSING` / `TOKEN_INVALID` / `BAD_HOST` / `BAD_ORIGIN` / `CROSS_SITE`。

---

## 内容维护

不要在 `content/` 里手写内容，改脚本重跑，保证可复现。详见 [tools/README.md](tools/README.md)。

```
改 sources.json → fetch_intro.py → validate_content.py → bootstrap_index.py → content_gaps.py
```

---

## 开发路线

M-1 安全地基（已完成）→ M0 骨架（已完成）→ M1 内容管道（已完成）→ M2 资料前端（已完成）→
M3 计划（已完成）→ M4 助手（已完成）→ M5 打磨（收尾中）→ M6 代码运行（收尾中）。

每个阶段的验收标准见 [需求文档.md](需求文档.md) §15 / §16。

---

## 许可

代码 MIT（见 LICENSE）。学习资料来自 MIT / CC-BY-SA 等开源项目，逐条署名见 [content/CREDITS.md](content/CREDITS.md)。
