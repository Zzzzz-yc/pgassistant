#!/usr/bin/env bash
#
# pgassistant 一键启动（macOS / Linux / WSL / Git Bash）。
#
# 与 start.ps1 同构，但**没有在 Windows 上实测**（Windows 用 start.ps1）：
#   ./start.sh            生产态：同源单进程
#   ./start.sh --no-browser   只起服务
#   ./start.sh --dev      开发态：另起 Vite :5173（代理 /api）
#
# 依次做：检查 Python >= 3.11 与 Node >= 20 → 缺 .venv 才建并装依赖 →
# frontend/dist 不存在才 npm ci/install + build → bootstrap_index.py（幂等）→
# 在 8000–8010 挑空闲端口 → 写 data/runtime.json（不含 token）→ 起 uvicorn。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND="$ROOT/frontend"
VENV_PY="$ROOT/.venv/bin/python"
RUNTIME_FILE="$ROOT/data/runtime.json"
OPEN_BROWSER=1
DEV=0

for arg in "$@"; do
  case "$arg" in
    --no-browser) OPEN_BROWSER=0 ;;
    --dev) DEV=1 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $arg（可用：--no-browser / --dev）" >&2; exit 2 ;;
  esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  ok  %s\033[0m\n' "$1"; }
die()  { printf '\033[31m  !!  %s\033[0m\n' "$1" >&2; exit 1; }

# 1. 工具链 ---------------------------------------------------------------
step "检查工具链"
py=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    py="$(command -v "$candidate")"; break
  fi
done
[ -n "$py" ] || die "找不到 Python >= 3.11"
command -v node >/dev/null 2>&1 || die "找不到 node（需要 >= 20）"
node_major="$(node --version | sed 's/^v\([0-9]*\).*/\1/')"
[ "$node_major" -ge 20 ] || die "Node 版本过低：$(node --version)，需要 >= 20"
command -v npm >/dev/null 2>&1 || die "找不到 npm"
ok "python: $py"
ok "node:   $(node --version)"

# 2. venv -----------------------------------------------------------------
step "准备 Python 环境（.venv）"
created_venv=0
if [ ! -x "$VENV_PY" ]; then
  "$py" -m venv "$ROOT/.venv"
  created_venv=1
  ok "已创建 .venv"
else
  ok ".venv 已存在，跳过创建"
fi

if [ "$created_venv" -eq 1 ] || ! "$VENV_PY" -c 'import fastapi, uvicorn, httpx, pydantic' 2>/dev/null; then
  step "安装 backend/requirements.txt"
  "$VENV_PY" -m pip install --disable-pip-version-check -r "$ROOT/backend/requirements.txt" \
    || die "pip install 失败；网络慢可加 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
  ok "Python 依赖就绪"
else
  ok "Python 依赖已就绪，跳过安装"
fi

# 3. 前端构建 -------------------------------------------------------------
if [ ! -f "$FRONTEND/dist/index.html" ]; then
  step "构建前端（frontend/dist 不存在）"
  cd "$FRONTEND"
  if [ ! -d node_modules ]; then
    if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi \
      || die "npm 安装失败；网络问题可加 --registry=https://registry.npmmirror.com 重试一次"
  fi
  npm run build || die "npm run build 失败"
  cd "$ROOT"
  [ -f "$FRONTEND/dist/index.html" ] || die "构建结束但没有 dist/index.html"
  ok "前端构建完成"
else
  ok "frontend/dist 已存在，跳过构建"
fi

# 4. 索引 -----------------------------------------------------------------
step "准备数据库与索引（幂等）"
"$VENV_PY" "$ROOT/backend/scripts/bootstrap_index.py" || die "bootstrap_index.py 失败"
ok "索引就绪"

# 5. 起服务 ---------------------------------------------------------------
find_port() {
  local port
  for port in $(seq 8000 8010); do
    if ! (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      echo "$port"; return 0
    fi
  done
  return 1
}
PORT="$(find_port)" || die "8000–8010 都被占用了，先关掉其它实例。"
BASE_URL="http://127.0.0.1:$PORT"

export PYTHONPATH="$ROOT/backend"
if [ "$DEV" -eq 1 ]; then
  export PG_DEV_ORIGINS="http://127.0.0.1:5173"
  export PG_BACKEND_ORIGIN="$BASE_URL"
fi

wait_http() {
  local url="$1" deadline=$((SECONDS + ${2:-45}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl -fsS -o /dev/null --max-time 2 "$url"; then return 0; fi
    sleep 0.4
  done
  return 1
}

step "启动 uvicorn（$BASE_URL，只绑定 127.0.0.1）"
( cd "$ROOT/backend" && exec "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" ) &
SERVER_PID=$!
VITE_PID=""

cleanup() {
  [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null || true
  kill "$SERVER_PID" 2>/dev/null || true
  rm -f "$RUNTIME_FILE"
}
trap cleanup EXIT INT TERM

wait_http "$BASE_URL/api/health" 45 || die "45 秒内 /api/health 没起来，检查端口 $PORT 与日志"
ok "uvicorn 就绪"

# runtime.json 只记「怎么连上它」；token 绝不落盘（每次启动重新生成，§13.7）
mkdir -p "$ROOT/data"
cat > "$RUNTIME_FILE" <<JSON
{
  "url": "$BASE_URL",
  "host": "127.0.0.1",
  "port": $PORT,
  "pid": $SERVER_PID,
  "dev": $([ "$DEV" -eq 1 ] && echo true || echo false),
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
}
JSON

OPEN_URL="$BASE_URL"
if [ "$DEV" -eq 1 ]; then
  step "启动 Vite dev server（:5173，代理 /api 到 $BASE_URL）"
  ( cd "$FRONTEND" && exec npm run dev -- --port 5173 ) &
  VITE_PID=$!
  if wait_http "http://127.0.0.1:5173/" 60; then
    OPEN_URL="http://127.0.0.1:5173"
    ok "Vite 就绪"
  else
    printf '\033[33m  !!  Vite 没起来，先按生产地址打开\033[0m\n'
  fi
fi

printf '\n  pgassistant 已启动\n'
printf '    站点      %s\n' "$OPEN_URL"
printf '    接口文档  %s/docs\n' "$BASE_URL"
printf '    令牌      在页面 <head> 的 <meta name="x-app-token"> 里（不落盘，随重启失效）\n'
printf '    停止      Ctrl+C\n\n'

if [ "$OPEN_BROWSER" -eq 1 ]; then
  (command -v xdg-open >/dev/null 2>&1 && xdg-open "$OPEN_URL" >/dev/null 2>&1) \
    || (command -v open >/dev/null 2>&1 && open "$OPEN_URL" >/dev/null 2>&1) \
    || true
fi

wait "$SERVER_PID"
