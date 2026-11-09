#!/bin/sh

set -eu

RKLLAMA_LIB=/userdata/rkllama-lab/rkllama-83891fba4783dfe1dc93ce0168b8e826983c126e/src/rkllama/lib
export LD_LIBRARY_PATH="$RKLLAMA_LIB:${LD_LIBRARY_PATH:-}"

exec /userdata/voice-venv/bin/python -u \
  /userdata/voice-assistant/voice_assistant.py "$@"
