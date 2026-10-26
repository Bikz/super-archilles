#!/usr/bin/env python3
"""Command-line access to the talk copilot's data, for the Hermes skill and for humans.

Standard library only, so Hermes can run it with the system python3.

  talkctl.py last                  debrief of the most recent talk
  talkctl.py history [N]           last N talks: time vs plan, nudges, sections missed
  talkctl.py library [query]       saved explanations (tangents that worked)
  talkctl.py plans                 saved talk plans
  talkctl.py plan <file> [minutes] [name]   turn a deck/doc/notes into a plan and save it
  talkctl.py prep <plan-name>      pre-talk brief: must-hit sections plus your past patterns
"""

import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path.home() / "copilot-data"
PLANS = Path.home() / "copilot" / "plans"
SERVER = "http://127.0.0.1:8000"


def fmt(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


def sessions() -> list[dict]:
    out = []
    for f in sorted((DATA / "sessions").glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("duration_s", 0) >= 20:  # skip aborted starts
            out.append(r)
    return out


def missed(r: dict) -> list[str]:
    covered = " ".join(r.get("covered") or []).lower()
    secs = r.get("plan", {}).get("sections", [])
    return [s["title"] for s in secs if s.get("must") and s["title"].lower() not in covered
            and not any(w in covered for w in s["title"].lower().split() if len(w) > 4)]


def cmd_last(_args):
    s = sessions()
    if not s:
        return "No talks recorded yet."
    r = s[-1]
    head = f"{r['name']} ({r['started_at']}): {fmt(r['duration_s'])} spoken vs {fmt(r['planned_s'])} planned, {len(r['nudges'])} nudges."
    return head + "\n\n" + (r.get("debrief") or "(no debrief)")


def cmd_history(args):
    n = int(args[0]) if args else 5
    s = sessions()[-n:]
    if not s:
        return "No talks recorded yet."
    lines = []
    over = []
    for r in s:
        diff = r["duration_s"] - r["planned_s"]
        over.append(diff)
        lines.append(f"- {r['started_at']} {r['name']}: {fmt(r['duration_s'])} / {fmt(r['planned_s'])} "
                     f"({'+' if diff >= 0 else '-'}{fmt(abs(diff))}), {len(r['nudges'])} nudges")
    avg = sum(over) / len(over)
    lines.append(f"\nAverage: {'over' if avg >= 0 else 'under'} by {fmt(abs(avg))} across {len(s)} talks.")
    return "\n".join(lines)


def cmd_library(args):
    path = DATA / "library.md"
    if not path.exists():
        return "The library is empty. Good tangents get saved here during talks."
    entries = [e.strip() for e in path.read_text().split("## ") if e.strip()]
    if args:
        q = " ".join(args).lower()
        entries = [e for e in entries if q in e.lower()]
    if not entries:
        return "Nothing in the library matches that."
    return "\n\n".join(e.replace("\n\n", "\n") for e in entries[-10:])


def cmd_plans(_args):
    names = sorted(p.stem for p in PLANS.glob("*.md"))
    return "Saved plans: " + ", ".join(names) if names else "No saved plans."


def cmd_plan(args):
    if not args:
        return "usage: plan <file> [minutes] [name]"
    src = Path(args[0]).expanduser()
    minutes = float(args[1]) if len(args) > 1 else 5
    name = re.sub(r"[^a-z0-9-]+", "-", (args[2] if len(args) > 2 else src.stem).lower()).strip("-") or "talk"
    body = json.dumps({"filename": src.name, "data_b64": base64.b64encode(src.read_bytes()).decode(),
                       "minutes": minutes}).encode()
    req = urllib.request.Request(f"{SERVER}/plan/import", data=body, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        return f"Couldn't make a plan: {json.load(e).get('detail', e)}"
    (PLANS / f"{name}.md").write_text(res["markdown"])
    secs = res["plan"]["sections"]
    outline = "\n".join(f"- {s['title']} ({s['minutes']:g} min){' [must]' if s['must'] else ''}" for s in secs)
    return (f"Saved plan '{name}' ({fmt(res['plan']['total_s'])}, {len(secs)} sections). "
            f"It's in the dashboard's plan list.\n{outline}")


def cmd_prep(args):
    if not args:
        return "usage: prep <plan-name>"
    path = PLANS / f"{args[0]}.md"
    if not path.exists():
        return f"No plan named '{args[0]}'. " + cmd_plans([])
    md = path.read_text()
    musts = re.findall(r"^##\s+(.+?)\s*\((\d+(?:\.\d+)?)\s*min\)\s*\[must\]", md, re.M | re.I)
    total = sum(float(m) for m in re.findall(r"^##\s+.+?\((\d+(?:\.\d+)?)\s*min\)", md, re.M))
    out = [f"Prep for '{args[0]}': {total:g} min planned."]
    if musts:
        out.append("Must hit: " + "; ".join(f"{t} ({m} min)" for t, m in musts) + ".")
    past = sessions()
    if past:
        avg = sum(r["duration_s"] - r["planned_s"] for r in past[-5:]) / len(past[-5:])
        out.append(f"Your last {len(past[-5:])} talks ran {'over' if avg >= 0 else 'under'} by {fmt(abs(avg))} on average.")
        nudges = [n for r in past[-5:] for _, n in r.get("nudges", [])]
        if nudges:
            out.append(f"Recent nudges: {'; '.join(nudges[-3:])}")
    return "\n".join(out)


COMMANDS = {"last": cmd_last, "history": cmd_history, "library": cmd_library, "plans": cmd_plans,
            "plan": cmd_plan, "prep": cmd_prep}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    print(COMMANDS[sys.argv[1]](sys.argv[2:]))
