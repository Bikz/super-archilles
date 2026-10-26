"""Score the scripted pitch talk with the coverage worker, offline, and print the heatmap as text.

Runs the real Coverage.produce through the priority gate at checkpoints of the scripted talk
(scripts/coach_sim.TALK against plans/pitch.md). Nothing is posted to the copilot server unless
--post is given (then the final state goes to COPILOT_URL/coverage, which needs a running talk).

usage (on the box, from ~/copilot-staging):
  GATE_URL=http://127.0.0.1:8090/v1 ~/copilot/.venv/bin/python -m scripts.coverage_preview [--post]
"""

import asyncio
import sys
import time
from pathlib import Path

from copilot.plan import parse_plan
from copilot.workers.base import COPILOT_URL, Talk
from copilot.workers.coverage import Coverage
from scripts.coach_sim import TALK

CHECKPOINTS = [30, 60, 120, 180, 230]
SHADE = ".-+#"


class Capture:
    """Stands in for the worker's http client: LLM calls go through, /coverage posts are captured."""

    def __init__(self, real, post_through: bool):
        self.real, self.post_through, self.last = real, post_through, None

    async def post(self, url, **kw):
        if url.endswith("/coverage"):
            self.last = kw["json"]
            if not self.post_through:
                class R:
                    status_code = 200
                    text = ""
                return R()
        return await self.real.post(url, **kw)


async def main() -> None:
    w = Coverage()
    cap = Capture(w.http, "--post" in sys.argv)
    w.http = cap
    plan = parse_plan(Path("plans/pitch.md").read_text()).to_dict()
    base = time.monotonic()
    for i, t in enumerate(CHECKPOINTS):
        talk = Talk(name="preview", plan=plan, started=base - t,
                    transcript=[(ts, " " + txt) for ts, txt in TALK if ts <= t])
        if i:
            w._started = talk.started  # same talk at a later moment: keep the scores (no reset)
        w.talk = talk
        t0 = time.monotonic()
        await w.produce(talk)
        print(f"\n[{t // 60}:{t % 60:02d}] {time.monotonic() - t0:.1f}s  {cap.last['summary']}")
        by = {(p["section"], p["point"]): p for p in cap.last["points"]}
        for si, s in enumerate(plan["sections"]):
            row = [by.get((si, pi), {"score": 0}) for pi in range(len(s["points"]) or 1)]
            print(f"  {s['title'][:24]:24} {'[must]' if s['must'] else '      '} "
                  + " ".join(SHADE[p["score"]] for p in row)
                  + "   " + " | ".join(p.get("evidence", "") for p in row if p.get("evidence")))
    if "--post" in sys.argv:
        print("posted final state to", COPILOT_URL)


if __name__ == "__main__":
    asyncio.run(main())
