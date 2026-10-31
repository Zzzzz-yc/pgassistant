#!/usr/bin/env sh
# pgassistant one-click stop (macOS / Linux / WSL / Git Bash).
#
# Reads data/runtime.json, terminates the uvicorn pid tree (plus the Vite pid
# from --dev), falls back to port detection, then removes the runtime file.
# Idempotent: exits 0 when nothing is running.
#
# All logic lives in launch.py; this wrapper only finds Python and forwards args.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Same lookup as start.sh: skip the Microsoft Store python stub and < 3.11,
# then try the official py launcher (available in Git Bash on Windows).
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
    echo "[stop.sh] Python 3.11+ not found. Install it from https://www.python.org/downloads/ first." >&2
    exit 1
fi

# $PY_ARGS 故意不加引号：为空时整体消失，为 "-3" 时正好是一个参数。
exec "$PY" $PY_ARGS "$ROOT/launch.py" stop "$@"
