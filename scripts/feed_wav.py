"""Stream a 16 kHz mono WAV into a running server's /audio socket in real time,
exactly like the browser mic, and print what the server transcribes.

usage: python -m scripts.feed_wav <file.wav>
"""

import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import httpx
import websockets

BASE = "127.0.0.1:8000"


async def main(path: str) -> None:
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, "need 16 kHz mono"
        pcm = w.readframes(w.getnframes())
    httpx.post(f"http://{BASE}/session/start", timeout=30,
               json={"plan": Path("plans/pitch.md").read_text(), "name": "mic test"}).raise_for_status()
    heard = []
    t0 = time.monotonic()

    async def listen():
        async with websockets.connect(f"ws://{BASE}/events") as ev:
            async for msg in ev:
                e = json.loads(msg)
                if e["type"] == "text":
                    heard.append(e["text"])
                    print(f"[{time.monotonic() - t0:5.1f}s] {e['text']!r}", flush=True)

    listener = asyncio.create_task(listen())
    async with websockets.connect(f"ws://{BASE}/audio") as audio:
        step = 3200  # 100 ms of int16 samples
        silence = b"\0" * step
        for i in range(0, len(pcm), step):
            await audio.send(pcm[i : i + step])
            await asyncio.sleep(0.1)
        for _ in range(30):  # 3 s of silence to flush the stream
            await audio.send(silence)
            await asyncio.sleep(0.1)
    await asyncio.sleep(2)
    listener.cancel()
    httpx.post(f"http://{BASE}/session/stop", timeout=120)
    print("\nFULL:", "".join(heard))


asyncio.run(main(sys.argv[1]))
