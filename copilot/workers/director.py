"""Director: decides which candidate cards are worth interrupting the speaker with.

Workers (visual, qa, notes, images) post CANDIDATE cards while the talk runs. The
director collects them for a few seconds, scores the batch with one LLM call against
what is being said right now, and PROPOSES at most the single best one to the
speaker's card tray. The speaker taps to show it. Proposing too much is worse than
proposing nothing, so the director is strict:

  - score >= MIN_SCORE to propose, at most one proposal per RATE_S seconds
  - at most MAX_WAITING proposed cards waiting; to make room it dismisses the oldest
    proposed card the speaker ignored for IGNORED_S seconds
  - candidates older than STALE_S are ignored; shown cards are never touched

Run:  python -m copilot.workers.director
"""

import asyncio
import logging
import os
import re
import time

from .base import COPILOT_URL, Preempted, Talk, Worker, plain

log = logging.getLogger("worker")

DEBOUNCE_S = 2.5  # wait this long after a candidate so simultaneous ones are judged together
MIN_SCORE = float(os.environ.get("DIRECTOR_MIN_SCORE", "0.75"))
RATE_S = float(os.environ.get("DIRECTOR_RATE_S", "25"))  # at most one proposal per this many seconds
MAX_WAITING = 3  # proposed cards waiting on the tray at once
IGNORED_S = 90.0  # a proposed card ignored this long may be dismissed to make room
STALE_S = 60.0  # candidates older than this are not worth proposing any more
MAX_BATCH = 6  # newest candidates per LLM call; keeps the prompt small for a 9B model

SYSTEM = """You are the director of a live talk. Background helpers made candidate cards \
(visuals, likely audience questions, notes, images). The speaker is on stage. You decide \
which ONE card, if any, is worth interrupting them with right now. Interrupting with a weak \
card is worse than showing nothing.

Score each candidate from 0.0 to 1.0. A high score needs ALL of these:
1. NOW: it is about what the speaker said in the last 45 seconds, not earlier or later.
2. PLAN: it fits the current section of the plan.
3. NEW: it does not repeat a card already on the tray or already shown.
4. AUDIENCE: it would help the audience understand or remember the point.
Generic, vague, off-topic or repeated cards score below 0.3. Only a clearly useful, \
on-topic card scores 0.7 or more. If the speaker is off on a tangent, score low.

Reply with ONLY a JSON object, no other text:
{"best": "<candidate id like c1> or null",
 "scores": {"c1": <0.0-1.0>, "c2": <0.0-1.0>},
 "reason": "<max 12 plain words, to the speaker, why show the best card now>"}
Use only candidate ids from the list. The reason is one short clause, e.g. \
"You just said audio never leaves the room." No emoji, no dashes, no exclamation marks."""

_EMOJI = re.compile("[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f900-\U0001f9ff️‍]")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3}


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def _clean_reason(text: str) -> str:
    text = _EMOJI.sub("", plain(str(text or "")))
    text = re.sub(r"[-‐‑‒―]+", " ", text).replace("!", ".")
    text = re.split(r";|[.:](?=\s+[A-Z])", text)[0].strip(" \"'`.,")  # first clause only
    words = text.split()[:12]
    return " ".join(words) or "Fits what you are saying now"


def _score(v) -> float | None:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


class Director(Worker):
    name = "director"
    every_s = 3600.0  # makes no cards of its own

    def __init__(self):
        super().__init__()
        logging.getLogger("httpx").setLevel(logging.WARNING)  # keep the decision log readable
        self.last_proposal = 0.0  # monotonic; survives reconnects so the rate limit holds
        self._reset()

    def _reset(self) -> None:
        self.pending: dict[str, dict] = {}  # candidate id -> card, waiting to be judged
        self.judged: set[str] = set()  # ids already decided on (proposed or skipped)
        self.proposed_at: dict[str, float] = {}  # card id -> monotonic time it became proposed
        self._task: asyncio.Task | None = None

    async def produce(self, talk: Talk):
        return []

    # ---- events ----
    async def _event(self, ev: dict) -> None:
        if ev.get("type") == "start":
            if self._task:
                self._task.cancel()
            self._reset()
        await super()._event(ev)
        if ev.get("type") == "start":
            asyncio.create_task(self._sync())
        elif ev.get("type") == "stop":
            self.pending.clear()

    async def _sync(self) -> None:
        """Catch up on cards posted before we connected (late join or restart)."""
        try:
            r = await self.http.get(f"{COPILOT_URL}/cards")
            r.raise_for_status()
            cards = r.json()
        except Exception as e:
            log.info("card sync failed: %s", e)
            return
        known = {c["id"] for c in self.talk.cards}
        self.talk.cards += [c for c in cards if c["id"] not in known]
        for c in cards:
            await self.on_card(c)
        log.info("synced %d card(s) from the server", len(cards))

    async def on_card(self, card: dict) -> None:
        cid, status = card.get("id"), card.get("status")
        if not cid:
            return
        if status == "proposed":
            if cid not in self.proposed_at:  # proposed before we started: best guess is its age
                self.proposed_at[cid] = time.monotonic() - max(0.0, time.time() - card.get("created", time.time()))
            self.judged.add(cid)
        elif status in ("shown", "dismissed"):
            self.proposed_at.pop(cid, None)
            self.pending.pop(cid, None)
            self.judged.add(cid)
        elif status == "candidate" and cid not in self.judged and card.get("source") != self.name:
            if self._age(card) > STALE_S:
                return
            self.pending[cid] = card
            if not self._task or self._task.done():
                self._task = asyncio.create_task(self._loop())

    # ---- decision loop ----
    def _age(self, card: dict) -> float:
        return time.time() - card.get("created", time.time())

    def _status(self, cid: str) -> str:
        for c in reversed(self.talk.cards):
            if c["id"] == cid:
                return c.get("status", "")
        return ""

    async def _loop(self) -> None:
        try:
            while self.pending and self.talk.running:
                await asyncio.sleep(DEBOUNCE_S)
                wait = RATE_S - (time.monotonic() - self.last_proposal)
                if wait > 0:
                    log.info("rate limit: holding %d candidate(s) %.0fs", len(self.pending), wait)
                    await asyncio.sleep(wait)
                await self._decide()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("director loop failed")

    async def _decide(self) -> None:
        batch = []
        for cid, c in list(self.pending.items()):
            if self._status(cid) != "candidate":
                self.pending.pop(cid)
            elif self._age(c) > STALE_S:
                self.pending.pop(cid)
                self.judged.add(cid)
                log.info("skipped %s %r: stale (%.0fs old)", cid, c.get("title", ""), self._age(c))
            else:
                batch.append(c)
        if not batch:
            return
        batch.sort(key=lambda c: c.get("created", 0), reverse=True)
        batch = batch[:MAX_BATCH]

        if not self._room()[0]:
            log.info("tray full (%d waiting, none ignored for %.0fs): holding %d candidate(s)",
                     MAX_WAITING, IGNORED_S, len(batch))
            await asyncio.sleep(10)  # recheck soon; candidates go stale on their own
            return

        on_tray = [c for c in self.talk.cards if c.get("status") in ("proposed", "shown")]
        fresh = []
        for c in batch:
            dup = next((o for o in on_tray if _similar(c.get("title", ""), o.get("title", ""))), None)
            if dup:
                self._done(c["id"])
                log.info("skipped %s %r: repeats %r", c["id"], c.get("title", ""), dup.get("title", ""))
            else:
                fresh.append(c)
        if not fresh:
            return

        ids = {f"c{i + 1}": c for i, c in enumerate(fresh)}
        t0 = time.monotonic()
        try:
            out = await self.llm_json(self._prompt(ids, on_tray), SYSTEM, max_tokens=250, temperature=0.1)
        except Preempted as e:
            log.info("%s; will retry", e)
            return  # still pending, the loop tries again
        dt = time.monotonic() - t0

        raw = out.get("scores") if isinstance(out.get("scores"), dict) else {}
        scores = {k: s for k, v in raw.items() if k in ids and (s := _score(v)) is not None}
        best = out.get("best") if out.get("best") in ids else None
        if scores:
            top = max(scores, key=scores.get)
            if best is None or scores.get(best, 0) < scores[top]:
                best = top if scores[top] >= MIN_SCORE else best
        best_score = scores.get(best, 0.0) if best else 0.0
        reason = _clean_reason(out.get("reason", ""))
        if not scores and not best:
            for c in ids.values():
                self._done(c["id"])
            log.info("skipped %d candidate(s): LLM gave no usable scores in %.1fs (%r)",
                     len(ids), dt, str(out)[:200])
            return

        for k, c in ids.items():
            if k == best and best_score >= MIN_SCORE:
                continue
            self._done(c["id"])
            log.info("skipped %s %r [%s/%s] score=%.2f", c["id"], c.get("title", ""), c.get("kind"),
                     c.get("source"), scores.get(k, 0.0))
        if best and best_score >= MIN_SCORE:
            await self._propose(ids[best], best_score, reason, dt)
        else:
            log.info("no proposal this batch (top score %.2f < %.2f), %.1fs",
                     max(scores.values(), default=0.0), MIN_SCORE, dt)

    def _done(self, cid: str) -> None:
        self.pending.pop(cid, None)
        self.judged.add(cid)

    def _prompt(self, ids: dict[str, dict], on_tray: list[dict]) -> str:
        talk = self.talk
        sec = talk.section or {}
        points = "; ".join(sec.get("points", [])) or "none"
        tray = "\n".join(f"- [{c.get('status')}] {c.get('title', '')}" for c in on_tray[-8:]) or "none"
        cands = "\n".join(
            f"{k}: [{c.get('kind', '')}] {c.get('title', '')}. {(c.get('body') or '')[:300]}"
            for k, c in ids.items())
        return f"""TALK OBJECTIVE: {talk.plan.get('objective', '')}
CURRENT SECTION: {sec.get('title', 'unknown')} (points: {points})

WHAT THE SPEAKER SAID IN THE LAST 45 SECONDS:
{talk.recent(45) or '(silence)'}

CARDS ALREADY ON THE TRAY OR SHOWN:
{tray}

CANDIDATES:
{cands}

Score every candidate. JSON only."""

    def _room(self) -> tuple[bool, dict | None]:
        """(room available, card to dismiss first). Pure check: dismissing happens only
        once a candidate has actually earned the slot."""
        waiting = [c for c in self.talk.cards if c.get("status") == "proposed"]
        if len(waiting) < MAX_WAITING:
            return True, None
        now = time.monotonic()
        oldest = min(waiting, key=lambda c: self.proposed_at.get(c["id"], now))
        if now - self.proposed_at.get(oldest["id"], now) < IGNORED_S:
            return False, None
        return True, oldest

    async def _evict(self, card: dict) -> bool:
        cid = card["id"]
        if self._status(cid) != "proposed":  # never touch shown cards
            return self._room()[0]
        idle = time.monotonic() - self.proposed_at.get(cid, time.monotonic())
        r = await self.http.post(f"{COPILOT_URL}/cards/{cid}/dismiss")
        if r.status_code != 200:
            log.warning("dismiss %s failed: %s", cid, r.status_code)
            return False
        self.proposed_at.pop(cid, None)
        log.info("dismissed %s %r: ignored for %.0fs, making room", cid, card.get("title", ""), idle)
        return True

    async def _propose(self, card: dict, score: float, reason: str, dt: float) -> None:
        cid = card["id"]
        self._done(cid)
        if self._status(cid) != "candidate":
            log.info("skipped %s: status changed to %s", cid, self._status(cid))
            return
        if self._age(card) > STALE_S:
            log.info("skipped %s %r: went stale while judging (%.0fs old)", cid, card.get("title", ""), self._age(card))
            return
        room, evict = self._room()
        if evict:
            room = await self._evict(evict)
        if not room:
            log.info("skipped %s %r: tray filled up while judging", cid, card.get("title", ""))
            return
        r = await self.http.post(f"{COPILOT_URL}/cards/{cid}/propose",
                                 json={"score": round(score, 2), "reason": reason})
        if r.status_code != 200:
            log.warning("propose %s failed: %s %s", cid, r.status_code, r.text[:200])
            return
        self.last_proposal = self.proposed_at[cid] = time.monotonic()
        log.info("PROPOSED %s %r [%s/%s] score=%.2f reason=%r (%.1fs)", cid, card.get("title", ""),
                 card.get("kind"), card.get("source"), score, reason, dt)


if __name__ == "__main__":
    Director().run()
