#!/bin/bash
# LLM priority gate on :8090. Coach requests (X-Priority: coach) preempt background workers.
cd ~/copilot-staging
export GATE_MAIN=${GATE_MAIN:-http://127.0.0.1:8081} GATE_SMALL=${GATE_SMALL:-http://127.0.0.1:8080}
exec ~/copilot/.venv/bin/python -m uvicorn copilot.gate:app --host 127.0.0.1 --port 8090
