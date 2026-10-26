#!/bin/bash
# Staging copy of the copilot on :8001. Shares the models with live, never posts to
# the team's Telegram group, keeps its own data, and has the experimental features on.
cd ~/copilot-staging
set -a; source .env.local; set +a
unset TELEGRAM_CHAT_ID
export COPILOT_DATA=~/copilot-staging-data
export FEATURES=${FEATURES:-cards,coverage}
export LLM_URL=${LLM_URL:-http://127.0.0.1:8090/v1}
exec ~/copilot/.venv/bin/python -m uvicorn copilot.server:app --host 0.0.0.0 --port 8001
