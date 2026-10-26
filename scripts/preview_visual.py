#!/usr/bin/env python3
"""Render every visual template with sample data so humans can eyeball them.

  python scripts/preview_visual.py              # templates -> /tmp/visual-previews/*.html
  python scripts/preview_visual.py --llm        # also run decide+spec on sample transcripts
                                                # through the gate (GATE_URL, default :8090)
  python scripts/preview_visual.py --out DIR

Open DIR/index.html: each visual is embedded the way the stage shows it (sandboxed iframe
srcdoc at 1920x1080, scaled down). Each <type>.html is the bare fragment at full size.
"""

import argparse
import asyncio
import html
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot.workers.base import Talk  # noqa: E402
from copilot.workers.visual import (  # noqa: E402
    Visual, check_html, render, spec_text, summary, ungrounded, validate,
)

SAMPLES = {
    "stat": {"title": "All in unified memory", "value": "128", "unit": "GB",
             "label": "unified memory, models and audio stay on the box"},
    "stat_long": {"title": "Speakers who run over their planned time slot", "value": "1,250,000",
                  "unit": "minutes", "label": "lost every year to talks that run long, by our count"},
    "compare": {"title": "Cloud versus the GB10",
                "left": {"label": "Cloud API", "value": "", "note": "audio leaves the room"},
                "right": {"label": "GB10", "value": "", "note": "no cloud calls, works with wifi off"}},
    "compare_bars": {"title": "Nudge latency",
                     "left": {"label": "Cloud round trip", "value": "2.5 s", "note": "network plus queue"},
                     "right": {"label": "On the box", "value": "0.8 s", "note": "local model, no network"}},
    "steps": {"title": "How a nudge happens",
              "steps": ["Microphone", "Streaming speech model", "Language model", "Nudge on phone"]},
    "steps_5": {"title": "From talk plan to debrief",
                "steps": ["Upload deck", "Plan sections", "Give the talk", "Live nudges", "Debrief"]},
    "timeline": {"title": "The four minute pitch",
                 "events": [{"when": "0:30", "what": "Hook"}, {"when": "1:15", "what": "Who pays"},
                            {"when": "2:45", "what": "Live demo"},
                            {"when": "3:30", "what": "Runs on the GB10"},
                            {"when": "4:00", "what": "Close and ask"}]},
    "list": {"title": "Who pays for this",
             "items": ["Lecturers", "Corporate trainers", "Sales teams"]},
    "list_long": {"title": "Why speakers drift and run over",
                  "items": ["Nobody can watch the clock and the plan at once",
                            "Good tangents eat the time for the demo",
                            "Questions arrive at the worst possible moment"]},
}

LLM_SAMPLES = [
    ("GB10 memory", "How it runs on the GB10",
     "So how does this run? Everything is on this one box. There's an NVIDIA streaming speech "
     "model and a large language model, both running locally. The GB10 has 128 gigabytes of "
     "unified memory, so both models fit with room to spare, and there are no cloud calls at "
     "all. It keeps working with the wifi off."),
    ("Who pays", "Problem and who pays",
     "Who pays for this? Three groups. Lecturers, who teach the same course every term. Corporate "
     "trainers, who run the same workshop fifty times a year. And sales teams, who pitch all day. "
     "And that audio is private, so it can't go to a cloud API."),
    ("Small talk", "Hook",
     "Hi everyone, thanks for having us. It's great to be here, the coffee was good this morning "
     "and I'm excited to talk to you today."),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Visual previews</title>
<style>body{{margin:0;padding:32px;background:#1b1e23;color:#e8eaed;font:16px system-ui,sans-serif}}
.g{{display:flex;flex-wrap:wrap;gap:32px}} figure{{margin:0}}
.t{{width:480px;height:270px;overflow:hidden;border-radius:8px;background:#0b0d10}}
iframe{{width:1920px;height:1080px;border:0;transform:scale(.25);transform-origin:0 0}}
figcaption{{margin-top:8px;max-width:480px;color:#9aa0a6}} b{{color:#e8eaed}}
</style></head><body><h1>Visual previews</h1><div class="g">{tiles}</div></body></html>"""


def tile(name: str, frag: str, caption: str) -> str:
    return (f'<figure><div class="t"><iframe sandbox="" srcdoc="{html.escape(frag, quote=True)}">'
            f'</iframe></div><figcaption><b>{html.escape(name)}</b> '
            f'<a href="{html.escape(name)}.html" style="color:#4da3ff">full size</a><br>'
            f'{html.escape(caption)}</figcaption></figure>')


def write(out: Path, name: str, frag: str) -> None:
    (out / f"{name}.html").write_text(
        f'<!doctype html><html><head><meta charset="utf-8"></head><body>{frag}</body></html>')


async def run_llm(out: Path, tiles: list[str]) -> None:
    w = Visual()
    plan = {"objective": "Judges believe a live talk copilot running 100% on the GB10 is useful",
            "sections": [{"title": t, "minutes": 1, "end_s": 60.0 * (i + 1), "must": True,
                          "points": []} for i, t in enumerate(
                ["Hook", "Problem and who pays", "Live demo", "How it runs on the GB10", "Close"])]}
    for label, section, text in LLM_SAMPLES:
        idx = [s["title"] for s in plan["sections"]].index(section)
        talk = Talk(name="preview", plan=plan, started=time.monotonic() - 60 * idx - 50,
                    transcript=[(60 * idx + 5.0, text)])
        talk.verdicts = [{"section": idx}]
        t0 = time.monotonic()
        d = await w.decide(talk, talk.recent(60))
        print(f"\n[{label}] decide ({time.monotonic() - t0:.1f}s): {d}")
        if not d:
            continue
        t0 = time.monotonic()
        spec = await w.make_spec(talk, d["type"], d["about"], talk.recent(60))
        print(f"[{label}] spec ({time.monotonic() - t0:.1f}s): {spec}")
        if not spec:
            continue
        missing = ungrounded(spec_text(spec), f"{text}\n{talk.plan_text()}")
        if missing:
            print(f"[{label}] REJECTED, ungrounded numbers: {missing}")
            continue
        frag = render(d["type"], spec)
        name = "llm_" + label.lower().replace(" ", "_")
        write(out, name, frag)
        tiles.append(tile(name, frag, summary(d["type"], spec)))
        print(f"[{label}] body: {summary(d['type'], spec)}  guard={check_html(frag)}")
    await w.http.aclose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/visual-previews")
    ap.add_argument("--llm", action="store_true", help="also run the LLM steps on samples")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for name, raw in SAMPLES.items():
        kind = name.split("_")[0]
        spec = validate(kind, raw)
        if not spec:
            print(f"{name}: sample failed validation")
            continue
        frag = render(kind, spec)
        ok = check_html(frag)
        write(out, name, frag)
        tiles.append(tile(name, frag, summary(kind, spec)))
        print(f"{name:14s} guard={'ok' if ok else 'FAIL'}  {len(frag):5d} bytes  {summary(kind, spec)}")
    if a.llm:
        asyncio.run(run_llm(out, tiles))
    (out / "index.html").write_text(PAGE.format(tiles="".join(tiles)))
    print(f"\nwrote {out}/index.html")


if __name__ == "__main__":
    main()
