#!/bin/bash
cd ~/copilot
exec ~/.local/bin/uv run --no-project --with httpx python -m scripts.replay ${1:-1}
