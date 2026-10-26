#!/bin/bash
# Endless scripted talk on staging so background workers always have a live talk to follow.
cd ~/copilot-staging
export COPILOT_URL=http://127.0.0.1:8001 TALK_NAME="staging loop" CLIENT=staging-loop FORCE=1
while true; do
  ~/copilot/.venv/bin/python -m scripts.replay 1 || sleep 5
  sleep 3
done
