#!/bin/bash
pkill -f "^/home/dell/.cache/uv/.*python -m scripts.replay"
pkill -f "^/home/dell/.local/bin/uv run --no-project --with httpx python -m scripts.replay"
pkill -f "^/bin/bash /home/dell/replay.sh"
sleep 1
curl -s -X POST localhost:8000/session/stop >/dev/null
echo stopped
