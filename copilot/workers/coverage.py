"""Coverage worker: scores how deeply every plan point has been covered so far.

Each round the fast model reads the transcript and scores each point 0..3
(0 not yet, 1 mentioned, 2 explained, 3 landed with an example or detail).
Coverage is monotonic: the best score seen for a point is kept for the rest of the talk.
The full set goes to POST /coverage with a one-line summary of the biggest gap,
and the dashboard draws it as a heatmap (static/coverage.js).

    COPILOT_URL=http://127.0.0.1:8001 python -m copilot.workers.coverage
"""

import re

from .base import COPILOT_URL, Card, Talk, Worker, log, plain

SYSTEM = """You track which planned points a speaker has covered so far in a live talk or meeting.

For EVERY point id in the list, score how deeply the transcript covers it:
0 = not covered at all
1 = mentioned or touched in passing
2 = explained: the speaker made the point clearly
3 = landed: explained AND backed with a concrete example, number, story or detail

Be strict and literal. Only count what the speaker actually said in the transcript.
A different topic that sounds related does not count. When unsure, score lower.
For each point with score 1 to 3, give a short exact quote (3 to 8 words) copied from the transcript as evidence.
Leave out points with score 0. Keep the reply short.

Reply with ONLY JSON, mapping point id to [score, evidence]:
{"scores": {"S0P0": [2, "exact words from the transcript"], "S1P1": [1, "other exact words"]}}"""

WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def short(text: str, n: int = 60) -> str:
    text = plain(text)
    return text if len(text) <= n else text[: n - 3].rstrip(" ,.;:") + "..."


class Coverage(Worker):
    name = "coverage"
    every_s = 15
    min_words = 15

    def __init__(self):
        super().__init__()
        self._started = 0.0  # talk.started of the talk our state belongs to
        self.best: dict[tuple[int, int], dict] = {}  # (section, point) -> {"score", "evidence"}

    def _items(self, talk: Talk) -> list[tuple[str, int, int, str, dict]]:
        """(id, section index, point index, text, section) for every point; -1 = whole section."""
        out = []
        for si, s in enumerate(talk.plan.get("sections") or []):
            pts = s.get("points") or []
            if pts:
                out += [(f"S{si}P{pi}", si, pi, p, s) for pi, p in enumerate(pts)]
            else:
                out.append((f"S{si}", si, -1, s["title"], s))
        return out

    def _sync(self, talk: Talk) -> None:
        # base builds a new Talk on every "start", including a websocket reconnect replay of
        # the same talk; only a real new talk moves `started` by more than a few seconds.
        if abs(talk.started - self._started) > 5:
            self._started = talk.started
            self.best = {}
            log.info("new talk %r: coverage reset", talk.name)

    async def produce(self, talk: Talk) -> list[Card]:
        self._sync(talk)
        started = talk.started
        items = self._items(talk)
        if not items:
            return []
        # points already at 3 can't go higher: don't spend tokens on them
        ask = [it for it in items if self.best.get((it[1], it[2]), {}).get("score", 0) < 3]
        transcript = talk.full(8000)
        tx_words = set(words(transcript))
        listing = "\n".join(
            f"{pid}: [{s['title']}{', must' if s.get('must') else ''}] {text}" for pid, _, _, text, s in ask
        )
        user = f"POINTS:\n{listing}\n\nTRANSCRIPT SO FAR:\n{transcript}"
        res = await self.llm_json(user, SYSTEM, model="small", temperature=0.1,
                                  max_tokens=min(1500, 80 + 30 * len(ask)), retries=15) if ask else {}

        by_id = {pid: (si, pi) for pid, si, pi, _, _ in items}
        got = dropped = 0
        rows = res.get("scores") or {}
        if isinstance(rows, list):  # tolerate [{"id", "score", "evidence"}] too
            rows = {str(r.get("id")): [r.get("score"), r.get("evidence")] for r in rows if isinstance(r, dict)}
        for pid, val in rows.items() if isinstance(rows, dict) else []:
            key = by_id.get(str(pid).strip())
            if key is None:
                continue
            val = val if isinstance(val, list) else [val, ""]
            try:
                score = max(0, min(3, int(val[0])))
            except (TypeError, ValueError, IndexError):
                continue
            evidence = short(str(val[1] if len(val) > 1 and val[1] else ""), 140)
            if score > 0:
                ev = [w for w in words(evidence) if len(w) > 3] or words(evidence)
                if not ev or sum(w in tx_words for w in ev) / len(ev) < 0.6:
                    dropped += 1  # quote not found in the transcript: ignore this round's score
                    continue
            got += 1
            cur = self.best.get(key)
            if cur is None or score > cur["score"]:
                self.best[key] = {"score": score, "evidence": evidence if score else ""}

        if self.talk is not talk and abs(self.talk.started - started) > 5:
            return []  # a new talk began while we were thinking
        points = [
            {"section": si, "point": pi, **self.best.get((si, pi), {"score": 0, "evidence": ""})}
            for _, si, pi, _, _ in items
        ]
        summary = self._summary(talk, items)
        r = await self.http.post(f"{COPILOT_URL}/coverage", json={"points": points, "summary": summary})
        if r.status_code != 200:
            log.warning("post coverage failed: %s %s", r.status_code, r.text[:200])
        log.info("scored %d of %d asked (dropped %d unquoted) | %s", got, len(ask), dropped, summary)
        return []

    def _summary(self, talk: Talk, items) -> str:
        el = talk.elapsed
        n = len(items)
        done = sum(1 for _, si, pi, _, _ in items if self.best.get((si, pi), {}).get("score", 0) >= 2)

        def gap_rank(it):
            _, si, pi, _, s = it
            score = self.best.get((si, pi), {}).get("score", 0)
            due = el >= s.get("start_s", 0)
            over = el >= s.get("end_s", 0)
            # thin points whose planned time has come first, least covered, must, overdue, plan order
            return (score >= 2, not due, score, not s.get("must"), not over, si, pi)

        gaps = sorted(items, key=gap_rank)
        _, si, pi, text, s = gaps[0]
        score = self.best.get((si, pi), {}).get("score", 0)
        if score >= 2:
            return f"All {n} points covered" if done == n else f"{done} of {n} points covered"
        tags = [s["title"]] if pi >= 0 else []
        if s.get("must"):
            tags.append("must")
        if el >= s.get("end_s", 0):
            tags.append("overdue")
        what = s["title"] if pi < 0 else short(text, 48)
        line = f"{'Not yet' if score == 0 else 'Thin'}: {what}" + (f" ({', '.join(tags)})" if tags else "")
        open_must = sum(1 for _, a, b, _, x in items if x.get("must") and self.best.get((a, b), {}).get("score", 0) < 2)
        more = open_must - (1 if s.get("must") else 0)
        if more > 0:
            line += f", +{more} more must"
        return plain(line)


if __name__ == "__main__":
    Coverage().run()
