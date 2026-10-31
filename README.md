# pgassistant

本地运行的学习资料库与学习计划助手。154 篇资料随仓库分发，首次启动自动建库索引；中文全文检索，
内置分层路由的助手，能按目标生成学习计划。**不配模型也能浏览和关键词搜索**，只有助手需要你自己的 API key。

> 设计文档：[需求文档.md](需求文档.md)（v2.2，22 章）· 交接说明：[HANDOFF.md](HANDOFF.md) · 首次 clone：[MIGRATION.md](MIGRATION.md) · 贡献：[CONTRIBUTING.md](CONTRIBUTING.md)

---

## 现状

M0–M6 全部落地。

| 模块 | 状态 | 说明 |
|---|---|---|
| 内容包 | ✅ 完成 | 154 篇 / 700 文件 / 11.9 MB，覆盖 4 个技术栈 |
| 索引与检索 | ✅ 完成 | schema v2（14 表 + FTS5 + 软删除）、jieba 分词、bm25 列权重、三级降级；配了 embedding 才建语义向量 |
| 后端接口 | ✅ 完成 | health / taxonomy / resources / search / plans / plan-templates / agent / settings / index / private / media / media-private |
| 内容校验 | ✅ 完成 | `validate_content.py`，当前 0 错误 19 警告（警告都是导读页摘要短） |
| 鉴权 | ✅ 完成 | M-1：Host / Origin / Sec-Fetch-Site / 一次性 `X-App-Token`；`/media/*` 与 `/media/private/*` 免令牌但仅同源 |
| schema 迁移 | ✅ 完成 | `PRAGMA user_version` + 顺序脚本 + 迁移前备份（当前 v2） |
| 前端 | ✅ 完成 | M0–M6：React 18 + Vite + Tailwind + 自建组件库，资料库 / 详情 / 筛选 / 命令面板 / 计划 / 助手 / 设置 / 代码运行抽屉 |
| 计划模块 | ✅ 完成 | M3：计划 CRUD、时间轴、打勾进度、乐观锁 |
| 助手 | ✅ 完成 | M4：L0–L3 路由、两段式计划生成、SSE；**只配 LLM 即可用，不依赖 embedding** |
| 设置 / 私有资料 / 语义检索 | ✅ 完成 | M5：`/api/settings`、私有目录扫描、embedding 建向量、设置页四个 Section |
| 代码运行 | ✅ 完成 | M6：Pyodide / Web Worker 抽屉，只对 `runnable` 资料里的 Python / JavaScript 代码块出入口 |
| 回归测试 | ✅ 通过 | 293 passed / 1 skipped（Windows 跳过符号链接用例，Linux / CI 跑全套） |

覆盖率自检：70 个典型查询命中 54 个（77%），缺口见 [docs/crawl-targets.md](docs/crawl-targets.md)。

---

## 快速开始

需要 **Python 3.11+** 与 **Node 20+**（前端构建用）。

```bash
# Windows 一键：建 .venv、装依赖、npm ci、npm run build、索引、起服务
.\start.cmd              # 推荐入口：用 -ExecutionPolicy Bypass 调 start.ps1，未签名也能跑
.\start.ps1              # 执行策略已放行本地脚本时等价
# 只起服务，不开浏览器
.\start.cmd -NoBrowser
# 开发态：后端 + Vite 热更新（见下）
.\start.cmd -Dev
# macOS / Linux
./start.sh
```

`start.ps1` 没有 Authenticode 签名。执行策略为 `AllSigned`（或脚本带“下载自互联网”标记）时，
直接运行会报“未对文件 … 进行数字签名，无法在当前系统上运行该脚本”。`start.cmd` 只在启动这
一个 PowerShell 进程时加 `-ExecutionPolicy Bypass`：不改系统设置、不装证书，参数原样透传。

脚本会在 8000–8010 里挑一个空闲端口，把实际地址写进 `data/runtime.json` 并打开浏览器。
选定端口会同时传给 uvicorn 和 `PG_PORT`：`core/config.py` 按它生成 Origin 白名单，
只改 `--port` 不改 `PG_PORT` 时，非 8000 端口的写请求会被 403 `BAD_ORIGIN` 挡掉。

手动等价命令：

```bash
# 1. 建环境并装依赖
python -m venv .venv
.venv\Scripts\activate        # Windows；macOS / Linux 用 source .venv/bin/activate
python -m pip install -r backend/requirements.txt

# 2. 建库并索引（首次约 2 秒；索引会读 content/_meta 下的 taxonomy、jieba 词典与计划模板）
python backend/scripts/bootstrap_index.py

# 3. 构建前端（首次；之后改了前端代码才需重跑）
# npm ci 按 package-lock.json 精确安装；没有 lock 时才退回 npm install
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

服务日志写到 `data/logs/app.log`（JSON Lines，单文件 5 MB × 5 轮转，`data/` 不入库）；
启动时会打印 token、Host 与 Origin 白名单，5xx 响应里的 `request_id` 可直接用来过滤。
`pytest` 进程默认不写这个文件，跑测试不会污染 `data/`。

### 开发态（Vite 热更新）

```bash
.\start.cmd -Dev        # 后端（8000–8010 的空闲端口）+ Vite :5173，自动开 :5173
```

`-Dev` 会设 `PG_DEV_ORIGINS=http://127.0.0.1:5173`，并让 Vite 把 **`/api` 与 `/media`** 都代理到后端：

- `/api` 代理：否则写请求的 Origin 不在白名单里。
- `/media` 代理：`<img>` 带不了 `X-App-Token`，内容媒体走免令牌的 `/media/*`；Vite 不代理会把它
  当成 SPA 路由回 `index.html`，dev 下图片全裂。生产态同源，没有这个问题。

> **接口要带令牌**：除 `/api/health` 与 `/media/*`（含 `/media/private/*`）外，所有 `/api/*`
> 都要带 `X-App-Token` 头（防本地 CSRF 与 DNS rebinding，见需求文档 §13.7）。令牌每次启动随机生成，
> 打印在启动日志里，也写在页面 head 的 `<meta name="x-app-token">` 中：
>
> ```bash
> curl -H "X-App-Token: <令牌>" "http://127.0.0.1:8000/api/search?q=闭包"
> ```
>
> `/media/*` 与 `/media/private/*` 免令牌但**只同源可用**：仍校验 Host、拒绝
> `Sec-Fetch-Site: cross-site`、只允许 GET/HEAD，且按扩展名白名单
> （`.webp .png .jpg .jpeg .gif .svg .avif`）取文件。

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
│  │  ├─ core/            # config / errors / security（Host·Origin·Token）/ logging
│  │  ├─ middleware/      # ASGI 鉴权 + HTML token 注入 + request_id
│  │  ├─ api/             # pages / resources / search / taxonomy / media / media_private /
│  │  │                   # plans / plan_templates / agent / settings / index / private / fallback
│  │  ├─ db/              # connection / migrate / migrations / schema_v1.sql
│  │  ├─ prompts/         # 提示词模板
│  │  ├─ schemas/         # Pydantic v2 模型
│  │  └─ services/        # indexer / search / search_ext / search_semantic / embeddings /
│  │                      # media / plan_service / planner / router / sse / tokenizer /
│  │                      # settings_service / secrets / index_task / private_scanner
│  ├─ scripts/            # bootstrap / validate / gaps / security_selfcheck
│  ├─ tests/              # pytest：鉴权、迁移、检索、计划、助手、设置、索引、私有资料、日志
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
| GET | /api/search?q=&stack=&level=&type=&limit= | 检索，返回 mode / tokens / items；有向量时走混合排序 |
| POST | /api/search | 同一查询的 JSON body 版（命令面板用） |
| GET | /api/resources?stack=&page=&size= | 分页列表 |
| GET | /api/resources/{slug} | 详情（正文、标签、`media_base`、`runnable`） |
| GET | /api/resources/{slug}/related | 相关推荐 |
| GET/HEAD | /media/{path} · /media/private/{path} | 内容 / 私有媒体（**免令牌**，但只同源、扩展名白名单） |
| GET/POST | /api/plans | 计划列表 / 保存计划 |
| GET/PATCH/DELETE | /api/plans/{plan_id} | 详情 / 改名归档 / 软删除 |
| POST | /api/plans/{plan_id}/stages · PATCH/DELETE /api/stages/{stage_id} | 阶段增删改 |
| POST | /api/plan-items · PATCH/DELETE /api/plan-items/{item_id} | 条目增删改（打勾 / 改时长 / 换阶段） |
| GET | /api/plan-templates · /api/plan-templates/{template_id} | 计划模板列表 / 单个模板（套模板零 token） |
| POST | /api/agent/extract | 一句话 → 表单字段 |
| POST | /api/agent/plan-draft | 表单 → 计划草稿（不落库） |
| POST | /api/agent/chat | SSE 流式对话（`fetch + ReadableStream`，不能用 EventSource） |
| GET/PUT | /api/settings | 读取 / 局部更新设置（`api_key` 只出掩码） |
| POST | /api/settings/test | LLM / embedding 连通性探针（失败也是 200，`ok=false`） |
| POST | /api/index/rebuild | 重建索引，202 + `task_id`；已有任务 409 `INDEX_BUILDING`，5 分钟内重复 429 |
| GET | /api/index/status?task_id= | 索引进度（running 时 1s 轮询；不带 `task_id` 取最近一次） |
| POST | /api/private/rescan | 重扫 `data/private`（与内容索引用同一套增量 diff） |

`/api/*` 里只有 `/api/health` 免令牌；`/media/*` 与 `/media/private/*` 是唯二完全免
`X-App-Token` 的出口，但仍校验 Host、拒绝跨站 `Sec-Fetch-Site`，且只允许 GET / HEAD。
路径存在但方法不对时返回 405 `METHOD_NOT_ALLOWED`（`api/fallback.py` 兜底）。

```bash
curl -H "X-App-Token: <令牌>" "http://127.0.0.1:8000/api/search?q=闭包&limit=2"
```

请求被拒绝时返回统一信封 `{ code, message, detail?, hint? }`，
`code` 常见值：`TOKEN_MISSING` / `TOKEN_INVALID` / `BAD_HOST` / `BAD_ORIGIN` / `CROSS_SITE`，
M5 新增 `INDEX_BUILDING` / `RATE_LIMITED` / `PATH_FORBIDDEN` / `PRIVATE_DIR_INVALID`。

---

## 内容维护

不要在 `content/` 里手写内容，改脚本重跑，保证可复现。详见 [tools/README.md](tools/README.md)。

```
改 sources.json → fetch_intro.py → validate_content.py → bootstrap_index.py → content_gaps.py
```

---

## 已知限制

不是 bug，是当前版本的边界：

- **没有 video 内容**。154 篇只有 article / docs / exercise / overview / summary 五类，
  video / course / book 是 0 篇；按「只注册有内容的分类」，它们不会出现在 taxonomy 里。
- **代码运行入口只覆盖少数资料**。154 篇里正文含 ```` ```python ```` 围栏的只有 16 篇
  （其中 15 篇标了 `runnable`，另一篇没标）；97 篇 `runnable` 里其余 82 篇的代码块是
  `text` / `src` 排版块，不出现运行按钮。站内只跑 Python / JavaScript，C++ / Java 等显示禁用态。
- **Windows 下密钥文件靠目录 ACL**。keyring 不可用时密钥降级写到 `data/llm_key`，
  `0600` 在 Windows 上只能去掉只读位，实际隔离依赖 `data/` 目录的 ACL；
  多用户机器上需要自己收紧该目录权限。
- **全量重建索引会清掉私有资料**。`POST /api/index/rebuild` 或 `bootstrap_index.py --rebuild`
  会重建 `resources` 表，`source='private'` 的行一并删除；重建完要 `POST /api/private/rescan`
  重新扫描 `data/private`（文件本身不会丢）。

---

## 开发路线

M-1 安全地基（已完成）→ M0 骨架（已完成）→ M1 内容管道（已完成）→ M2 资料前端（已完成）→
M3 计划（已完成）→ M4 助手（已完成）→ M5 打磨（已完成）→ M6 代码运行（已完成）。

每个阶段的验收标准见 [需求文档.md](需求文档.md) §15 / §16。

---

## 许可

代码 MIT（见 LICENSE）。学习资料来自 MIT / CC-BY-SA 等开源项目，逐条署名见 [content/CREDITS.md](content/CREDITS.md)。
