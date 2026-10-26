"""Send-only Telegram client. Hermes owns polling for the same bot; sendMessage
doesn't conflict with that, so both can share one token.

Sends never block the coach and never raise. If the box is offline, messages
queue up and go out (marked as delayed) once the internet is back.
"""

import asyncio
import itertools
import os
import time
from collections.abc import Callable

import httpx

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_pending: list[tuple[int, float, str]] = []
_ids = itertools.count(1)

# set by the server so the box view can mirror the chat: f({"id", "text", "status", "ts"})
listener: Callable[[dict], None] = lambda event: None


def enabled() -> bool:
    return bool(TOKEN and CHAT_ID)


def pending() -> int:
    return len(_pending)


async def _post(text: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
            )
            return r.status_code == 200
    except httpx.HTTPError:
        return False


async def _send(msg_id: int, text: str) -> None:
    if await _post(text):
        listener({"id": msg_id, "text": text, "status": "sent", "ts": time.time()})
    else:
        _pending.append((msg_id, time.time(), text))
        listener({"id": msg_id, "text": text, "status": "queued", "ts": time.time()})


def send(text: str) -> None:
    """Fire and forget."""
    if enabled():
        asyncio.get_running_loop().create_task(_send(next(_ids), text))


async def flush() -> None:
    """Deliver anything queued while offline, oldest first."""
    while _pending:
        msg_id, queued_at, text = _pending[0]
        note = f"(written offline at {time.strftime('%H:%M:%S', time.localtime(queued_at))})"
        if not await _post(f"{text}\n{note}"):
            return
        _pending.pop(0)
        listener({"id": msg_id, "text": text, "status": "delivered_late", "ts": time.time()})
