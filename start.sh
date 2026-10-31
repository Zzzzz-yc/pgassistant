#!/usr/bin/env sh
# pgassistant one-click launcher (macOS / Linux / WSL / Git Bash).
#
# All launcher logic lives in launch.py; this wrapper only finds Python and
# forwards every argument unchanged:
#
#   ./start.sh
#   ./start.sh --no-browser     (old spelling -NoBrowser also works)
#   ./start.sh --dev
#   ./start.sh --port 8020
#   ./stop.sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# 真跑一次 -c 再采用：Git Bash 下 python3 / python 可能解析到 Microsoft Store
# 的占位符（能 command -v 到，但一执行就非 0），低于 3.11 的解释器也跳过。
# Git Bash 里官方 py 启动器通常可用，所以它也在候选里。
PY=""
PY_ARGS=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ] && command -v py >/dev/null 2>&1 \
    && py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="py"
    PY_ARGS="-3"
fi
if [ -z "$PY" ]; then
    echo "[start.sh] Python 3.11+ not found. Install it from https://www.python.org/downloads/ first." >&2
    exit 1
fi

# $PY_ARGS 故意不加引号：为空时整体消失，为 "-3" 时正好是一个参数。
exec "$PY" $PY_ARGS "$ROOT/launch.py" "$@"
