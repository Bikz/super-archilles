"""The coaching loop: every few seconds, judge the talk so far against the plan."""

import json
import os
import re
import time

import httpx

from .plan import Plan, fmt

LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:8080/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "local")
# The live coach always outranks background workers at the priority gate (copilot/gate.py).
COACH_HEADERS = {"X-Priority": "coach"}

SYSTEM = """You are a live coach whispering to a speaker mid-talk. You see the talk plan, \
the clock, and the live transcript. Decide, right now, whether the speaker needs a nudge.

Judge three things:
1. WHERE: which plan section are they actually in, and which planned points have they covered?
2. DRIFT: if they are off the plan, is it a VALUABLE tangent (concrete example, story, \
analogy or answer that serves the objective) or UNPRODUCTIVE drift (rambling, repeating, \
off-objective)? Valuable tangents should be allowed briefly and saved to the library.
3. TIME: compare elapsed time to the plan. If they are behind, re-plan the rest so the \
talk still lands the objective: keep every [MUST COVER] section, trim or cut the rest.

How to tell the two kinds of tangent apart:
- VALUABLE: an analogy, example or story that explains a planned point or the objective (e.g. comparing the product to something familiar). Status valuable_tangent, no nudge, and put it in save_tangent so it can be reused in future talks.
- UNPRODUCTIVE: personal anecdotes or details unrelated to the objective, repetition, filler. Status drifting, nudge them back to the specific next point.
Judge the content, not the speaker's stated intent: a story counts as VALUABLE only if what it \
says explains a planned point. A personal story about something unrelated (a band, a pet, a trip, \
food) is UNPRODUCTIVE even if the speaker announces it as an example or a demo of drifting.
Only save a tangent the speaker actually said in the transcript. Sections only move forward unless the transcript clearly goes back; silence does not change the section.

You are Super Archilles, the speaker's stage manager. Nudges land on their phone \
mid-sentence, so they must read in one glance: 3 to 8 words, one instruction, the specific \
point from the plan by name. Talk like a calm stage manager on a headset, not an assistant.
Good nudges:
  "Band story's done. Back to the demo."
  "Skip the history. Show the timeline."
  "Ninety seconds left. Go to the close."
  "Nice analogy. Now the GB10 part."
Bad nudges (never write like this):
  "Cut the band story, go to the local-privacy point or the demo."  (two options, robotic)
  "Consider returning to the planned content."  (vague, assistant voice)
  "Great job! Keep it up!"  (cheerleading)
Every nudge comes with say_next: a line they can say verbatim to get back on track without \
it sounding like a hard turn. Good say_next: "Which is exactly why we built this to run on the box, \
so let me show you." Bad: "Let's get back on track."  (generic)
Whenever status is "drifting" or "behind" you MUST write a nudge (and say_next); never leave it null then.
Stay silent (nudge null) when they are on track; a stage manager who nags gets ignored. \
Never repeat a nudge you already sent. No emoji, no em dashes, no exclamation marks.

Reply with ONLY a JSON object:
{"section": <int index they are in>,
 "covered": [<short labels of planned points covered so far>],
 "status": "on_track" | "valuable_tangent" | "drifting" | "behind" | "wrapping_up",
 "reason": "<one short factual sentence for the dashboard, about what was said>",
 "nudge": "<3 to 8 words> or null",
 "say_next": "<only when nudging: one sentence, max 20 words, the speaker could say out loud right now to bridge smoothly back to the plan, in their own voice, using a specific point from the plan> or null",
 "replan": null or {"cut": [<section titles to drop/trim>], "focus": "<what to prioritise>", "new_pace": "<e.g. 'Demo by 2:30, close at 4:45'>"},
 "save_tangent": null or {"title": "<short>", "explanation": "<the reusable explanation in 1-3 sentences>"}}"""


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def similar(a: str, b: str) -> bool:
    """Same idea under a different title, e.g. "Copilot as a talk GPS" vs "GPS analogy for the copilot"."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


class Coach:
    def __init__(self, plan: Plan, known_titles: list[str] | None = None):
        self.plan = plan
        self.client = httpx.Client(timeout=60)
        self.nudges: list[tuple[float, str]] = []
        self.covered: list[str] = []
        self.library: list[dict] = []
        self.known_titles = list(known_titles or [])  # already in the library from earlier talks
        self.history: list[dict] = []

    def tick(self, elapsed_s: float, transcript: list[tuple[float, str]]) -> dict:
        planned = self.plan.planned_section_at(elapsed_s)
        recent = [t for ts, t in transcript if ts >= elapsed_s - 45]
        earlier = " ".join(t for ts, t in transcript if ts < elapsed_s - 45)
        # keep the prompt small enough for ~1 s turns; old talk is summarised by `covered`
        earlier = earlier[-3000:]
        overrun = elapsed_s - self.plan.total_s
        user = f"""PLAN
{self.plan.as_prompt()}

CLOCK: {fmt(elapsed_s)} elapsed of {fmt(self.plan.total_s)} planned \
({"OVER by " + fmt(overrun) if overrun > 0 else fmt(-overrun) + " left"}). \
By the plan they should now be in section [{planned}] {self.plan.sections[planned].title}.

POINTS ALREADY COVERED: {", ".join(self.covered) or "none yet"}
NUDGES ALREADY SENT: {"; ".join(f"{fmt(t)} {n}" for t, n in self.nudges[-5:]) or "none"}

EARLIER TRANSCRIPT (tail): ...{earlier}

LAST 45 SECONDS: {" ".join(recent) or "(silence)"}"""

        t0 = time.monotonic()
        r = self.client.post(
            f"{LLM_URL}/chat/completions",
            headers=COACH_HEADERS,
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "max_tokens": 400,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        r.raise_for_status()
        body = r.json()
        verdict = parse_json(body["choices"][0]["message"]["content"])
        verdict["reason"] = plain(verdict.get("reason") or "")
        verdict["llm_ms"] = int((time.monotonic() - t0) * 1000)
        verdict["tokens_per_s"] = (body.get("timings") or {}).get("predicted_per_second")
        verdict["prompt_tokens"] = (body.get("usage") or {}).get("prompt_tokens")
        verdict["completion_tokens"] = (body.get("usage") or {}).get("completion_tokens")
        verdict["elapsed_s"] = elapsed_s
        verdict["planned_section"] = planned

        for label in verdict.get("covered") or []:
            if label not in self.covered:
                self.covered.append(label)
        nudge = plain(verdict.get("nudge") or "")
        say = plain(verdict.get("say_next") or "")
        verdict["say_next"] = say if say and say.lower() != "null" else None
        if nudge and nudge.lower() != "null" and not self._recently_said(nudge, elapsed_s):
            self.nudges.append((elapsed_s, nudge))
            verdict["send"] = nudge
        if tangent := verdict.get("save_tangent"):
            title = tangent.get("title") or ""
            seen = self.known_titles + [t["title"] for t in self.library]
            if title and not any(similar(title, t) for t in seen):
                tangent["at_s"] = elapsed_s
                self.library.append(tangent)
                verdict["saved"] = tangent
        self.history.append(verdict)
        return verdict

    def _recently_said(self, nudge: str, now: float) -> bool:
        # No time cooldown: a cue goes out on the very next check-in. Only skip a cue that
        # repeats the previous one (exactly, or the same idea reworded) so drift doesn't spam.
        if not self.nudges:
            return False
        last = self.nudges[-1][1]
        return nudge.lower() == last.lower() or similar(nudge, last)

    def debrief(self, elapsed_s: float, transcript: list[tuple[float, str]]) -> str:
        """Facts computed here; the model only adds one grounded line of advice."""
        reached = max([v.get("section", 0) for v in self.history if isinstance(v.get("section"), int)] or [0])
        titles = [sec.title for sec in self.plan.sections]
        missed = [t for i, t in enumerate(titles) if i > reached]
        diff = elapsed_s - self.plan.total_s
        lines = [
            f"Done. {fmt(elapsed_s)} against {fmt(self.plan.total_s)} planned"
            + (f", {fmt(diff)} over." if diff > 15 else f", {fmt(-diff)} under." if diff < -15 else ", right on time."),
            f"Got through: {', '.join(titles[: reached + 1])}."
            + (f" Never reached: {', '.join(missed)}." if missed else ""),
        ]
        if self.nudges:
            lines.append(f"I nudged you {len(self.nudges)} time{'s' * (len(self.nudges) > 1)}.")
        if self.library:
            lines.append("Keepers for your library: " + ", ".join(f'"{t["title"]}"' for t in self.library) + ".")

        full = " ".join(t for _, t in transcript)[-12000:]
        try:
            r = self.client.post(
                f"{LLM_URL}/chat/completions",
            headers=COACH_HEADERS,
                json={
                    "model": LLM_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are Super Archilles, a calm stage manager. Give the speaker ONE "
                            "piece of advice for next time, at most 2 short sentences. Base it only on "
                            "the transcript, plan and nudges below. Never claim anything about the "
                            "audience, reactions or delivery that the transcript doesn't show. "
                            "No praise, no emoji, no em dashes, no exclamation marks.",
                        },
                        {
                            "role": "user",
                            "content": f"PLAN\n{self.plan.as_prompt()}\n\nFACTS\n" + "\n".join(lines)
                            + f"\nNUDGES: {[n for _, n in self.nudges]}\nTRANSCRIPT: {full}",
                        },
                    ],
                    "temperature": 0.3,
                    "max_tokens": 120,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            r.raise_for_status()
            lines.append("Next time: " + plain(strip_think(r.json()["choices"][0]["message"]["content"])).removeprefix("Next time: "))
        except httpx.HTTPError:
            pass
        return "\n".join(lines)


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S)


def plain(text: str) -> str:
    """House style for anything a person reads: no em or en dashes."""
    return re.sub(r"\s*[—–]\s*", ", ", text).strip()


def parse_json(text: str) -> dict:
    text = strip_think(text)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m[0] if m else text)
    except json.JSONDecodeError:
        return {"status": "unknown", "reason": "unparseable model reply", "nudge": None}
