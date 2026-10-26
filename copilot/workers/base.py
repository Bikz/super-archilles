"""Base for background workers: follow the live talk, think in the gaps, post cards.

A worker is its own process. It never imports or edits the server; it only uses
  - the /events websocket   (talk start/stop, transcript text, coach verdicts)
  - POST /cards             (publish a card)
  - the priority gate       (LLM calls that yield to the live coach)

Write a worker by subclassing Worker and implementing `produce`:

    class KeyStat(Worker):
        name = "keystat"
        every_s = 20

        async def produce(self, talk: Talk) -> list[Card]:
            text = await self.llm(system="...", user=talk.recent(60))
            return [Card(kind="visual", title="...", html="...")]

    if __name__ == "__main__":
        KeyStat().run()

Environment:
  COPILOT_URL  copilot server (default http://127.0.0.1:8001, i.e. staging)
  GATE_URL     priority gate  (default http://127.0.0.1:8090/v1)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import websockets

COPILOT_URL = os.environ.get("COPILOT_URL", "http://127.0.0.1:8001")
MEDIA = Path(os.environ.get("COPILOT_DATA", Path.home() / "copilot-data")) / "media"
GATE_URL = os.environ.get("GATE_URL", "http://127.0.0.1:8090/v1")

log = logging.getLogger("worker")


@dataclass
class Card:
    kind: str
    title: str
    body: str = ""
    html: str = ""
    image: str = ""
    relevance: float = 0.5
    point: str = ""


@dataclass
class Talk:
    """Everything a worker knows about the talk in progress."""

    name: str = ""
    plan: dict = field(default_factory=dict)  # same shape as Plan.to_dict()
    started: float = 0.0  # time.monotonic() when the talk started (adjusted for late joins)
    transcript: list[tuple[float, str]] = field(default_factory=list)  # (elapsed_s, text)
    verdicts: list[dict] = field(default_factory=list)  # coach verdicts, newest last
    cards: list[dict] = field(default_factory=list)  # every card posted this talk, any worker

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started if self.started else 0.0

    @property
    def running(self) -> bool:
        return bool(self.started)

    def recent(self, seconds: float = 60) -> str:
        cut = self.elapsed - seconds
        return "".join(t for ts, t in self.transcript if ts >= cut).strip()

    def full(self, max_chars: int = 12000) -> str:
        return "".join(t for _, t in self.transcript)[-max_chars:].strip()

    @property
    def section(self) -> dict | None:
        """Section the coach last said they're in, else the planned one."""
        secs = self.plan.get("sections") or []
        if not secs:
            return None
        for v in reversed(self.verdicts):
            if isinstance(v.get("section"), int) and 0 <= v["section"] < len(secs):
                return secs[v["section"]]
        el = self.elapsed
        return next((s for s in secs if el < s["end_s"]), secs[-1])

    def plan_text(self) -> str:
        lines = [f"OBJECTIVE: {self.plan.get('objective', '')}"]
        for s in self.plan.get("sections") or []:
            lines.append(f"- {s['title']} ({s['minutes']:g} min){' [must]' if s.get('must') else ''}")
            lines += [f"    - {p}" for p in s.get("points", [])]
        return "\n".join(lines)


class Preempted(Exception):
    pass


class Worker:
    name = "worker"
    every_s = 20.0  # how often produce() runs while a talk is live
    min_words = 25  # skip a round if fewer new words than this arrived since the last one

    def __init__(self):
        self.talk = Talk()
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5))
        self._words_at_last_round = 0
        logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {self.name} %(message)s")

    # ---- override these ----
    async def produce(self, talk: Talk) -> list[Card]:
        raise NotImplementedError

    async def on_card(self, card: dict) -> None:
        """Called for every card event (any worker). The director overrides this."""

    # ---- helpers for subclasses ----
    async def llm(self, user: str, system: str = "", model: str = "main", max_tokens: int = 700,
                  temperature: float = 0.4, json_mode: bool = False, retries: int = 6) -> str:
        """Chat completion through the priority gate. Retries when the coach preempts us."""
        body = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(retries):
            r = await self.http.post(f"{GATE_URL}/chat/completions", json=body)
            if r.status_code == 409:  # coach needed the GPU; try again in a moment
                await asyncio.sleep(1.5 + attempt)
                continue
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
            return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        raise Preempted(f"{self.name}: preempted {retries} times")

    async def llm_json(self, user: str, system: str = "", **kw) -> dict:
        text = await self.llm(user, system, json_mode=True, **kw)
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m[0] if m else text)
        except json.JSONDecodeError:
            return {}

    async def post(self, card: Card) -> dict | None:
        payload = {**asdict(card), "source": self.name}
        payload["title"] = plain(payload["title"])
        payload["body"] = plain(payload["body"])
        r = await self.http.post(f"{COPILOT_URL}/cards", json=payload)
        if r.status_code != 200:
            log.warning("post card failed: %s %s", r.status_code, r.text[:200])
            return None
        return r.json()

    # ---- runtime ----
    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        asyncio.create_task(self._rounds())
        ws_url = COPILOT_URL.replace("http", "ws", 1) + "/events"
        while True:
            try:
                async with websockets.connect(ws_url, max_size=None) as ws:
                    log.info("connected to %s", ws_url)
                    async for msg in ws:
                        await self._event(json.loads(msg))
            except (OSError, websockets.WebSocketException) as e:
                log.info("events socket down (%s), retrying", e)
                await asyncio.sleep(2)

    async def _event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "start":
            self.talk = Talk(name=ev.get("name", ""), plan=ev.get("plan") or {},
                             started=time.monotonic() - ev.get("elapsed", 0))
            self._words_at_last_round = 0
        elif t == "stop":
            self.talk.started = 0.0
        elif t == "text" and self.talk.running:
            self.talk.transcript.append((ev.get("t", self.talk.elapsed), ev["text"]))
        elif t == "verdict" and self.talk.running:
            self.talk.verdicts.append(ev)
        elif t == "card":
            self.talk.cards = [c for c in self.talk.cards if c["id"] != ev["id"]] + [ev]
            await self.on_card(ev)

    async def _rounds(self) -> None:
        while True:
            await asyncio.sleep(self.every_s)
            if not self.talk.running:
                continue
            words = len(self.talk.full(10**9).split())
            if words - self._words_at_last_round < self.min_words:
                continue
            self._words_at_last_round = words
            t0 = time.monotonic()
            try:
                cards = await self.produce(self.talk)
                for c in cards or []:
                    await self.post(c)
                log.info("round: %d card(s) in %.1fs", len(cards or []), time.monotonic() - t0)
            except Preempted as e:
                log.info("%s", e)
            except Exception:
                log.exception("round failed")


def section_key(section: dict) -> str:
    """Stable id for a plan section's prepared visuals; same section text, same files."""
    text = section["title"] + "\n" + "\n".join(section.get("points") or [])
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def prep_image(section: dict) -> Path:
    return MEDIA / f"prep-{section_key(section)}.jpg"


def plain(text: str) -> str:
    return re.sub(r"\s*[—–]\s*", ", ", text or "").strip()
