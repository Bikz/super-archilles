"""LLM priority gate: an OpenAI-compatible proxy in front of the local llama-servers.

The live coach must never wait on background work, and llama.cpp has no request
priority, so this proxy provides it:

  - Requests with header `X-Priority: coach` go straight through.
  - Everything else is background work. On the coach's model it runs one at a time,
    only while no coach request is in flight, and is cancelled the moment a coach request
    arrives; the caller gets HTTP 409 {"error": "preempted"} and should retry later
    (copilot.workers.base does this automatically). On another model ("small" when it
    points elsewhere) it runs right away, one at a time, and is never preempted.

`model` picks the upstream: "main" (or anything unknown) -> GATE_MAIN, "small" -> GATE_SMALL.

Run:  python -m uvicorn copilot.gate:app --host 127.0.0.1 --port 8090
"""

import asyncio
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

UPSTREAMS = {
    "main": os.environ.get("GATE_MAIN", "http://127.0.0.1:8080"),
    "small": os.environ.get("GATE_SMALL", os.environ.get("GATE_MAIN", "http://127.0.0.1:8080")),
}
COACH_QUIET_S = float(os.environ.get("GATE_COACH_QUIET_S", "0.3"))

app = FastAPI()
client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5))


class State:
    coach_inflight = 0
    coach_done_at = 0.0
    coach_idle = asyncio.Event()
    background: set[asyncio.Task] = set()  # background requests on the coach's model
    background_lock = asyncio.Lock()
    side_lock = asyncio.Lock()  # background requests on other models: don't wait for the coach
    stats = {"coach": 0, "background": 0, "preempted": 0}


S = State()
S.coach_idle.set()


def upstream_for(body: dict) -> str:
    return UPSTREAMS.get(str(body.get("model", "main")), UPSTREAMS["main"])


async def forward(path: str, body: dict) -> httpx.Response:
    return await client.post(f"{upstream_for(body)}{path}", json=body)


@app.post("/v1/{path:path}")
async def proxy(path: str, request: Request):
    body = await request.json()
    body.pop("stream", None)  # gate is non-streaming; coach and workers don't stream
    if request.headers.get("x-priority", "").lower() == "coach":
        return await coach_call(f"/v1/{path}", body)
    return await background_call(f"/v1/{path}", body)


async def coach_call(path: str, body: dict):
    S.coach_inflight += 1
    S.coach_idle.clear()
    S.stats["coach"] += 1
    for task in list(S.background):  # preempt: free the GPU for the coach right now
        task.cancel()
    try:
        r = await forward(path, body)
        return JSONResponse(r.json(), status_code=r.status_code)
    finally:
        S.coach_inflight -= 1
        S.coach_done_at = time.monotonic()
        if S.coach_inflight == 0:
            S.coach_idle.set()


async def background_call(path: str, body: dict):
    if upstream_for(body) != UPSTREAMS["main"]:
        # A different llama-server than the coach's: no need to wait or be preempted.
        async with S.side_lock:
            S.stats["background"] += 1
            r = await forward(path, body)
            return JSONResponse(r.json(), status_code=r.status_code)
    async with S.background_lock:  # one background request at a time
        await S.coach_idle.wait()
        if (wait := COACH_QUIET_S - (time.monotonic() - S.coach_done_at)) > 0:
            await asyncio.sleep(wait)
        task = asyncio.create_task(forward(path, body))
        S.background.add(task)
        S.stats["background"] += 1
        try:
            r = await task
        except asyncio.CancelledError:
            S.stats["preempted"] += 1
            return JSONResponse({"error": "preempted"}, status_code=409)
        finally:
            S.background.discard(task)
        return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/gate/stats")
async def stats():
    return {**S.stats, "coach_inflight": S.coach_inflight, "background_running": len(S.background),
            "upstreams": UPSTREAMS}
