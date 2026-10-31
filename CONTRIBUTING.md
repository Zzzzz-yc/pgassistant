# 贡献指南

> 先读 [需求文档.md](需求文档.md) 的 §3（技术栈）/ §4（内容包）/ §12.2（测试）；契约细节在
> [docs/contracts/](docs/contracts/)，冲突以 `DECISIONS.md` 的裁定为准。

## 1. 本地启动

需要 Python ≥3.11、Node ≥20。

```bash
# Windows 一键（建 .venv、装依赖、npm ci、npm run build、索引、起服务）
.\start.cmd
.\stop.cmd
# macOS / Linux
./start.sh
./stop.sh
```

手动等价命令：

```bash
python -m venv .venv
.venv\Scripts\activate                  # macOS / Linux: source .venv/bin/activate
python -m pip install -r backend/requirements.txt

python tools/prepare_content.py                  # 首次：准备内容库（需要联网；已有内容会跳过）
python backend/scripts/bootstrap_index.py        # 建库 + 索引（首次约 2 秒）
python -m uvicorn app.main:app --app-dir backend --port 8000
```

开发态（Vite :5173 热更新 + 后端 :8000）：

```bash
.\start.cmd --dev
```

Vite 只代理 `/api` 与 `/media`。**`/media` 这条不能少**：`<img>` 带不了 `X-App-Token`，不代理就会在
dev 下裂图，生产态同源没有这个问题。

## 2. 测试

```bash
python -m pip install -r backend/requirements-dev.txt
python backend/scripts/validate_content.py        # 内容体检，退出码 0 才算过
python -m pytest -q                               # 临时文件默认放系统 tmp
```

`--basetemp` 把 pytest 临时目录固定到指定位置，排查失败用例时方便；**跑完自己删**：

```bash
python -m pytest -q --basetemp=data/.pytest-tmp   # 该路径已在 .gitignore
```

`data/` 是运行时目录，测试**不得**污染它；需要隔离时用 `PG_DB` 指到临时文件。
单独跑某个用例：`python -m pytest backend/tests/test_search.py -q -k 闭包`。

## 3. 项目结构

```
.
├─ start.cmd / start.sh / stop.cmd / stop.sh   # 一键启动与停止
├─ launch.py                                   # 启动器逻辑（环境准备、端口、进程）
├─ backend/                                    # FastAPI 后端
│  ├─ app/                                     # 接口、服务、数据库迁移与提示词
│  ├─ scripts/                                 # 建索引、内容校验、自检
│  ├─ tests/                                   # pytest
│  └─ requirements*.txt
├─ frontend/                                   # React + Vite（dist/、node_modules/ 不入库）
├─ content/                                    # 内容库：_meta/ 手写元数据随仓库，正文本地生成
├─ tools/                                      # 内容准备与转换脚本
├─ docs/contracts/                             # 接口、数据库、模块契约
└─ data/                                       # 运行时数据（不入库）
```

内容正文由 `tools/prepare_content.py` 生成，`content/_meta/` 是手写的分类名、计划模板和分词词典，
随仓库维护。`data/` 存数据库、私有资料、日志、备份与运行状态，不提交。

## 4. 内容维护

- **`content/` 的正文由 `tools/prepare_content.py` 生成，不要手改**：重建用
  `python tools/prepare_content.py --force`（见 [tools/README.md](tools/README.md)）。
- `content/_meta/` 是手写元数据（分类名、计划模板、分词词典），随仓库维护，可以直接改。
- front-matter 必填 `slug / title / summary / stack / category / level / type / author / license`，
  格式见需求文档 §4.2；`slug` 由路径派生，**改目录等于改主键**，已有计划会错位。
- 单条资料的准入标准见需求文档 §4.5（学习目标 / 前置知识 / 正文 / 来源 / 学时 / 元数据 / 时效），
  逐条自查后再提 PR。
- 新分类先加 `content/_meta/taxonomy.yml`，且**只登记有内容的分类**（空分类会让筛选器出现死选项）。
- 媒体：WebP、长边 ≤1600px、单文件 ≤300KB、仓库总量 ≤50MB；**视频不落仓库**，只存 url。
- 改完跑 `validate_content.py`；改了内容再重跑 `bootstrap_index.py`。

## 5. 提交前检查

- [ ] `python backend/scripts/validate_content.py` 退出码 0
- [ ] `python -m pytest -q` 全绿
- [ ] 改了前端：`npm run build --prefix frontend` 通过（`build` 内含 `tsc --noEmit`）
- [ ] 改了契约（`docs/contracts/**`）：先在 `DECISIONS.md` 增补编号，再改代码
- [ ] 没有 `data/`、`*.db`、`.env`、`.venv/`、`.deps/`、`handoff/`
- [ ] 没有 `jieba.cache`、`pytest-of-*`、`dist/`、`node_modules/`、截图等本地产物
- [ ] 没有把本机绝对路径（`C:/Users/...`）写进代码或文档
- [ ] 新增英文文案已同步 `frontend/src/i18n/locales/en-US/`

提交信息用 Conventional Commits：`feat(scope): ...`、`fix(scope): ...`、`docs(contract): ...`、`chore: ...`。
一个提交只做一件事；契约变更与实现变更分开提交，方便回滚。

## 6. 不要提交 data/

`data/` 存数据库、私有资料、备份、日志与 `runtime.json`，全是本机私产，已在 `.gitignore` 里。
删除 `data/` 即彻底清除你的对话与计划。备份含对话内容，属隐私数据，同样不入库。
