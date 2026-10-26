"""Post a burst of fake candidates to staging so the director has something to judge.

Run on the box while staging runs a talk and the director is up:
    ~/copilot/.venv/bin/python scripts/director_test.py
then watch ~/logs/worker-director.log. Expect at most one PROPOSED (the on-topic card),
the off-topic one skipped with a low score, the duplicate skipped as a repeat.
"""

import os
import sys

import httpx

URL = os.environ.get("COPILOT_URL", "http://127.0.0.1:8001")

CARDS = [
    {"kind": "visual", "title": "Where your words go: mic to GB10 to screen",
     "body": "Diagram: audio is transcribed and coached on the GB10 in the room. Nothing is sent to a cloud server."},
    {"kind": "note", "title": "History of the Roman aqueducts",
     "body": "Romans built eleven aqueducts to supply the city with water."},
    {"kind": "qa", "title": "Does any audio leave the room",
     "body": "Likely audience question about privacy of recordings."},
]

if __name__ == "__main__":
    for c in CARDS:
        r = httpx.post(f"{URL}/cards", json={**c, "source": "director-test"})
        print(r.status_code, r.json().get("id") if r.status_code == 200 else r.text, c["title"])
    sys.exit(0)
