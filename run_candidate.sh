#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$APP_DIR"
export LD_LIBRARY_PATH="$APP_DIR/vendor-local${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
if [ -n "${LOCAL_AI_PYTHON:-}" ]; then
    PYTHON_BIN=$LOCAL_AI_PYTHON
elif [ -x "$APP_DIR/.venv/bin/python" ]; then
    PYTHON_BIN=$APP_DIR/.venv/bin/python
elif [ -x /userdata/rkllama-venv/bin/python ]; then
    PYTHON_BIN=/userdata/rkllama-venv/bin/python
else
    PYTHON_BIN=$(command -v python3 || true)
fi
if [ ! -x "$PYTHON_BIN" ]; then
    echo "Set LOCAL_AI_PYTHON to a Python 3.10+ environment with transformers installed." >&2
    exit 1
fi
exec "$PYTHON_BIN" -B -m local_ai.server --config "$APP_DIR/config.local.json" "$@"
