"""Run a live session on the server using the scripted talk from coach_sim, in real time.

usage: python -m scripts.replay [speedup]   (speedup 2 = talk plays twice as fast)
"""

import os
import sys
import time
from pathlib import Path

import httpx

from scripts.coach_sim import TALK

BASE = os.environ.get("COPILOT_URL", "http://127.0.0.1:8000")
speed = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
c = httpx.Client(base_url=BASE, timeout=120)
c.post("/session/start", json={"plan": Path("plans/pitch.md").read_text(), "name": os.environ.get("TALK_NAME", "pitch rehearsal"),
                               "client": os.environ.get("CLIENT", ""), "force": bool(os.environ.get("FORCE"))}).raise_for_status()
t0 = time.monotonic()
for ts, text in TALK:
    time.sleep(max(0, ts / speed - (time.monotonic() - t0)))
    words = text.split()
    for word_chunk in [" ".join(words[i : i + 6]) for i in range(0, len(words), 6)]:
        c.post("/dev/say", json={"text": word_chunk})
        time.sleep(0.4)
    print(f"[{time.monotonic() - t0:5.1f}s] said: {text[:60]}", flush=True)
time.sleep(25)
print(c.post("/session/stop").json()["report"].get("debrief"))
