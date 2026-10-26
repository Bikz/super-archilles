"""Replay a scripted talk through the coach and print each verdict.

The script below follows plans/pitch.md, then wanders into a long tangent about
the speaker's college band during the demo section so the coach has to react.
"""

import json
import sys
from pathlib import Path

from copilot.coach import Coach
from copilot.plan import fmt, parse_plan

TALK = [
    (0, "Hi everyone. Every speaker drifts and runs over, and nobody can watch their own clock and plan at the same time."),
    (12, "So we built a copilot, and it is listening to me right now, on the box on this table."),
    (30, "Who pays for this? Lecturers, corporate trainers, sales teams, anyone presenting at an all-hands."),
    (45, "And that audio is private. Classroom and meeting audio can't go to a cloud API, which is why it all runs locally."),
    (75, "Okay, live demo. Watch what happens when I go off on a tangent."),
    (85, "So actually this reminds me of my college band, we played this tiny bar in Oakland every Thursday."),
    (100, "The drummer was always late, and the owner had this dog that would sit on the amp, it was honestly the best era."),
    (115, "We even recorded an EP in a garage, the bass was terrible but the vibe was great, anyway the owner sold the bar."),
    (130, "And then the dog moved to Portland with him, which, you know, makes sense for a dog like that."),
    (150, "Right. Here is a better example: think of the copilot like a GPS for a talk. It knows the route, and reroutes when you miss a turn instead of making you start over."),
    (175, "And when we run behind, the timeline re-plans what to cut so we still land the point."),
    (200, "Under the hood this is NVIDIA's streaming speech model and a large language model, both running on the GB10."),
]
TICKS = [20, 60, 95, 125, 145, 165, 215, 260]


def main() -> None:
    plan = parse_plan(Path("plans/pitch.md").read_text())
    coach = Coach(plan)
    for t in TICKS:
        v = coach.tick(t, [(ts, txt) for ts, txt in TALK if ts <= t])
        print(f"\n[{fmt(t)}] {v.get('status')}  section={v.get('section')}  ({v['llm_ms']} ms)")
        print(f"   reason: {v.get('reason')}")
        if v.get("send"):
            print(f"   NUDGE -> {v['send']}")
            print(f"   TRY   -> {v.get('say_next')}")
        if v.get("replan"):
            print(f"   replan: {json.dumps(v['replan'])}")
        if v.get("saved"):
            print(f"   saved: {v['saved']['title']}: {v['saved']['explanation']}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
