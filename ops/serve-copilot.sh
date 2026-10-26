#!/bin/bash
# Live Super Archilles on :8000 (the main links). Same features as staging, Telegram on,
# LLM calls through the priority gate (:8090) so the coach preempts background workers.
cd ~/copilot
set -a; source .env.local; set +a
export FEATURES=${FEATURES:-cards,coverage}
export LLM_URL=${LLM_URL:-http://127.0.0.1:8090/v1}
export COPILOT_DATA=~/copilot-data
if [ "$ASR" = "off" ]; then
  exec ~/.local/bin/uv run --no-project --with fastapi --with "uvicorn[standard]" --with httpx --with numpy python -m uvicorn copilot.server:app --host 0.0.0.0 --port 8000
fi
exec ~/copilot/.venv/bin/python -m uvicorn copilot.server:app --host 0.0.0.0 --port 8000
