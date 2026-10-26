"""Visual worker: turns what the speaker is saying right now into ONE slide-quality visual.

Two LLM calls per round, both small JSON:
  1. decide: is there a key number, comparison, flow, timeline or 3-item list in the last
     minute worth a visual that no existing card already covers?
  2. spec:   type-specific structured content (never raw HTML).
The HTML/SVG is rendered here from fixed templates, so the style is guaranteed and nothing
the model writes reaches the page unescaped. Every number in a spec must appear in what the
speaker said or in the plan, otherwise the visual is dropped.

Run:  COPILOT_URL=http://127.0.0.1:8001 python -m copilot.workers.visual
"""

import html
import logging
import re

from copilot.workers.base import Card, Talk, Worker

log = logging.getLogger("worker")

TYPES = ("stat", "compare", "steps", "timeline", "list")
TYPE_NAMES = {"stat": "Big number", "compare": "Comparison", "steps": "Flow",
              "timeline": "Timeline", "list": "Three points"}
WINDOW_S = 60  # how much recent talk the visual is about
COOLDOWN_S = 40  # min gap between two visuals from this worker

# ---------------------------------------------------------------- prompts

DECIDE_SYSTEM = """You decide whether ONE slide visual would help the audience of a live talk \
right now, based only on what the speaker said in the last minute.

A visual helps only for one of these:
- stat: one key number the speaker said, with its unit (example: "128 GB of memory")
- compare: the speaker explicitly contrasts two named options with a concrete difference \
(example: cloud API versus on the box, 3 seconds versus under 1 second)
- steps: a process or flow of 3 to 5 steps in order (example: mic, speech model, language model, nudge)
- timeline: 3 to 5 dated or timed events in order
- list: 3 parallel items the speaker listed (example: three kinds of customers)

Say no when the last minute is small talk, a joke, a personal story, a greeting, or vague; \
when the idea is already covered by an existing card; or when you would need facts the speaker \
did not say. When unsure, say no.

Reply with ONLY a JSON object:
{"visual": true or false,
 "type": "stat" | "compare" | "steps" | "timeline" | "list" | null,
 "about": "<what the visual would show, max 10 words>",
 "relevance": <0.0 to 1.0, how much it would help the audience right now>}
Relevance guide: 0.9 a concrete number, contrast or list the speaker is making right now and \
that is central to the talk objective; 0.6 useful but secondary; 0.3 a stretch."""

SPEC_SYSTEM = """You write the content of ONE slide visual for a live talk, from what the \
speaker just said.

Rules:
- Use only facts, words and numbers the speaker actually said, or that are in the plan.
- Never invent, estimate, round or convert numbers. Copy each number exactly as said.
  If a field needs a number the speaker did not give, leave that field as "".
- Slide labels, not sentences: every text is very short. Plain words, no emoji, no dashes.
- The title names the one idea in 2 to 6 words (a claim or topic, never the section name).

Reply with ONLY a JSON object in exactly this shape:
"""

SCHEMAS = {
    "stat": """{"title": "<2 to 6 words>",
 "value": "<the number exactly as said, digits only, e.g. 128 or 1.5 or 90>",
 "unit": "<unit, 0 to 2 words, e.g. GB, %, minutes, or empty>",
 "label": "<what the number measures, 3 to 9 words>"}""",
    "compare": """{"title": "<2 to 6 words>",
 "left":  {"label": "<1 to 3 words>", "value": "<number with unit if one was said, else empty>", "note": "<2 to 8 words>"},
 "right": {"label": "<1 to 3 words>", "value": "<number with unit if one was said, else empty>", "note": "<2 to 8 words>"}}
Put the old, usual or worse option on the left and the speaker's option on the right.""",
    "steps": """{"title": "<2 to 6 words>",
 "steps": ["<step, 1 to 4 words>", "<step>", "<step>"]}
3 to 5 steps, in the order they happen.""",
    "timeline": """{"title": "<2 to 6 words>",
 "events": [{"when": "<time or date as said, 1 to 3 words>", "what": "<1 to 5 words>"}, ...]}
3 to 5 events, in time order.""",
    "list": """{"title": "<2 to 6 words>",
 "items": ["<item, 1 to 5 words>", "<item>", "<item>"]}
Exactly 3 items, parallel in form.""",
}

# ---------------------------------------------------------------- text hygiene

_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_DASH = "\u2012\u2013\u2014\u2015\u2212"


def clean(value, max_words: int = 12, max_chars: int = 80) -> str:
    """Model text -> short plain text: no emoji, no long dashes, bounded length. NOT escaped."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    s = str(value)
    s = _CTRL.sub(" ", s)
    s = _EMOJI.sub("", s)
    s = re.sub(rf"(\d)\s*[{_DASH}]\s*(\d)", r"\1 to \2", s)  # 2019-2021 ranges
    s = re.sub(rf"\s*[{_DASH}]\s*", ", ", s)
    s = re.sub(r"\s+-\s+", ", ", s)  # spaced hyphen used as a dash
    s = re.sub(r"\s+", " ", s).strip().strip("\"'`*_ ").strip()
    s = s.strip(",;:").strip()
    words = s.split(" ")
    if len(words) > max_words:
        s = _trim(" ".join(words[:max_words]))
    if len(s) > max_chars:
        s = _trim(s[:max_chars].rsplit(" ", 1)[0])
    return "" if s.lower() in {"null", "none", "n/a", "na", "empty"} else s


_DANGLING = set("and or the a an of to for with by at in on from as but that which our your their is are".split())


def _trim(s: str) -> str:
    """After a cut: drop trailing punctuation and words that leave the phrase hanging."""
    words = s.rstrip(",;:. ").split(" ")
    while len(words) > 1 and words[-1].lower() in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(",;:. ")


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def check_html(frag: str) -> bool:
    """Last guard before anything is posted: no scripts, handlers, external refs or dashes."""
    bad = [r"<\s*script", r"<[^>]*\son\w+\s*=", r"https?:", r"//", r"url\s*\(", r"@import",
           r"javascript:", r"<\s*(iframe|object|embed|link|img|image|foreignobject|a)\b",
           "[" + _DASH + "]"]
    hits = [b for b in bad if re.search(b, frag.lower())]
    if hits:
        log.warning("html rejected by guard: %s", hits)
    return not hits


# ---------------------------------------------------------------- number grounding

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate(
    "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _to_float(tok: str) -> float | None:
    t = tok.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", tok) else tok.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def numbers_in(text: str) -> set[float]:
    out = {f for f in (_to_float(m) for m in _NUM.findall(text)) if f is not None}
    # spelled-out numbers, as ASR often writes them ("one hundred twenty eight", "ninety")
    total = cur = 0
    have = False
    prev = ""
    for w in re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower()) + ["."]:
        if w in _UNITS or w in _TENS:
            cur += _UNITS.get(w, 0) + _TENS.get(w, 0)
            have = True
            out.add(float(_UNITS.get(w, 0) + _TENS.get(w, 0)))
        elif w in _SCALES and (have or re.fullmatch(r"\d+(\.\d+)?", prev)):
            base = cur if have else float(prev)
            if _SCALES[w] == 100:
                cur = base * 100
            else:
                total += base * _SCALES[w]
                cur = 0
            have = True
        elif w == "and" and have:
            pass
        else:
            if have:
                out.add(float(total + cur))
            total = cur = 0
            have = False
        prev = w
    return out


def ungrounded(spec_text: str, source: str) -> list[str]:
    """Numbers in the spec that the speaker never said (and the plan doesn't contain)."""
    have = numbers_in(source)
    missing = []
    for tok in _NUM.findall(spec_text):
        f = _to_float(tok)
        if f is None or f in have:
            continue
        # "1.5x" style or percentages said as fractions are not accepted: stay strict
        missing.append(tok)
    return missing


# ---------------------------------------------------------------- spec validation

def _strip_prefix(s: str) -> str:
    return re.sub(r"^(step\s*)?\d+\s*[.):]\s*", "", s, flags=re.I)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def validate(kind: str, raw: dict) -> dict | None:
    """Raw model JSON -> clean spec, or None if it doesn't fit the template."""
    if not isinstance(raw, dict):
        return None
    title = clean(raw.get("title"), 8, 60)
    if kind == "stat":
        value = clean(raw.get("value"), 2, 12)
        m = re.search(r"\d+(?:[.,]\d+)*", value)
        if not m:
            return None
        unit = clean(raw.get("unit"), 3, 16)
        rest = value.replace(m[0], "", 1).strip()
        if rest and not unit:
            unit = clean(rest, 2, 16)
        value = m[0]
        label = clean(raw.get("label"), 10, 70)
        if not label:
            return None
        return {"title": title or label, "value": value, "unit": unit, "label": label}
    if kind == "compare":
        sides = []
        for k in ("left", "right"):
            side = raw.get(k)
            if not isinstance(side, dict):
                return None
            lab = clean(side.get("label"), 4, 28)
            if not lab:
                return None
            sides.append({"label": lab, "value": clean(side.get("value"), 3, 16),
                          "note": _cap(clean(side.get("note"), 9, 60))})
        if sides[0]["label"].lower() == sides[1]["label"].lower():
            return None
        return {"title": title or f"{sides[0]['label']} vs {sides[1]['label']}",
                "left": sides[0], "right": sides[1]}
    if kind == "steps":
        steps = [clean(_strip_prefix(clean(s, 6, 40)), 5, 32) for s in raw.get("steps") or []
                 if isinstance(s, (str, int, float))]
        steps = [s for s in steps if s][:5]
        if len(steps) < 3 or len({s.lower() for s in steps}) < len(steps):
            return None
        return {"title": title or "How it works", "steps": [_cap(s) for s in steps]}
    if kind == "timeline":
        events = []
        for e in raw.get("events") or []:
            if not isinstance(e, dict):
                continue
            when, what = clean(e.get("when"), 3, 16), clean(e.get("what"), 6, 40)
            if when and what:
                events.append({"when": _cap(when), "what": _cap(what)})
        events = events[:5]
        if len(events) < 3:
            return None
        return {"title": title or "Timeline", "events": events}
    if kind == "list":
        items = [clean(_strip_prefix(clean(s, 7, 48)), 6, 44) for s in raw.get("items") or []
                 if isinstance(s, (str, int, float))]
        items = [s for s in items if s]
        if len(items) < 2 or len({s.lower() for s in items}) < len(items):
            return None
        return {"title": title or "Three points", "items": [_cap(s) for s in items[:4]]}
    return None


def spec_text(spec: dict) -> str:
    """All text in a spec, flattened (for grounding and dedup)."""
    out = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif v:
            out.append(str(v))

    walk(spec)
    return " ".join(out)


def summary(kind: str, spec: dict) -> str:
    """One plain line for the director."""
    if kind == "stat":
        line = f"{spec['value']}{' ' + spec['unit'] if spec['unit'] else ''}, {spec['label']}"
    elif kind == "compare":
        lt, rt = spec["left"], spec["right"]
        line = " vs ".join(
            " ".join(x for x in (side["label"], side["value"],
                                 f"({side['note']})" if side["note"] else "") if x)
            for side in (lt, rt))
    elif kind == "steps":
        line = " then ".join(spec["steps"])
    elif kind == "timeline":
        line = "; ".join(f"{e['when']} {e['what']}" for e in spec["events"])
    else:
        line = ", ".join(spec["items"])
    return f"{TYPE_NAMES[kind]}: {spec['title']}. {line}"[:220]


# ---------------------------------------------------------------- rendering

# Archilles brand palette (matches copilot/static/theme.css). GREEN is the accent slot (indigo).
BG, FG, MUTED, PANEL, LINE = "#0b0d10", "#e8eaed", "#9aa0a6", "#14171c", "#262b33"
GREEN, BLUE, AMBER = "#76b900", "#4da3ff", "#f5a623"
FONT = ("Inter, 'Helvetica Neue', Helvetica, Arial, 'Liberation Sans', 'DejaVu Sans', "
        "Roboto, sans-serif")

BASE_CSS = f"""html,body{{margin:0;padding:0;width:1920px;height:1080px;overflow:hidden;background:{BG}}}
.sa{{box-sizing:border-box;position:relative;width:1920px;height:1080px;padding:96px 128px 112px;
background:{BG};color:{FG};font-family:{FONT};display:flex;flex-direction:column;overflow:hidden;
-webkit-font-smoothing:antialiased}}
.sa *{{box-sizing:border-box}}
.sa-h{{display:flex;align-items:center;gap:36px;font-weight:650;letter-spacing:-0.02em;line-height:1.1;
flex:none}}
.sa-bar{{width:12px;height:1em;border-radius:4px;background:{GREEN};flex:none}}
.sa-main{{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;width:100%}}
.sa-foot{{position:absolute;left:128px;right:128px;bottom:56px;height:6px;border-radius:3px;
background:{LINE}}}
.sa-foot i{{display:block;width:180px;height:6px;border-radius:3px;background:{GREEN}}}"""


def _size(text: str, steps: list[tuple[int, int]], default: int) -> int:
    n = len(text)
    for limit, px in steps:
        if n <= limit:
            return px
    return default


def _longest_word(texts) -> int:
    return max((len(w) for t in texts for w in t.split()), default=0)


def frame(kind: str, title: str, content: str, css: str) -> str:
    tsize = _size(title, [(26, 84), (40, 72), (52, 62)], 54)
    return (f'<style>{BASE_CSS}\n{css}</style>'
            f'<div class="sa sa-k-{kind}">'
            f'<div class="sa-h" style="font-size:{tsize}px"><span class="sa-bar"></span>'
            f'<span>{esc(title)}</span></div>'
            f'<div class="sa-main">{content}</div>'
            f'<div class="sa-foot"><i></i></div></div>')


def render_stat(s: dict) -> str:
    big = s["value"] + s["unit"]
    vsize = _size(big, [(4, 400), (6, 340), (9, 260), (12, 200)], 160)
    usize = min(150, int(vsize * 0.42)) if len(s["unit"]) <= 6 else min(110, int(vsize * 0.3))
    lsize = _size(s["label"], [(32, 72), (50, 62)], 54)
    css = f""".sa-stat{{text-align:center;display:flex;flex-direction:column;align-items:center}}
.sa-num{{color:{GREEN};font-weight:700;line-height:0.95;letter-spacing:-0.04em}}
.sa-unit{{font-weight:700;margin-left:28px;color:{FG};letter-spacing:-0.01em}}
.sa-ul{{display:block;margin-top:56px}}
.sa-label{{color:#c9ccd1;margin-top:48px;max-width:1500px;line-height:1.2;font-weight:500}}"""
    unit = f'<span class="sa-unit" style="font-size:{usize}px">{esc(s["unit"])}</span>' if s["unit"] else ""
    content = (f'<div class="sa-stat"><div><span class="sa-num" style="font-size:{vsize}px">'
               f'{esc(s["value"])}</span>{unit}</div>'
               f'<svg class="sa-ul" width="260" height="16" viewBox="0 0 260 16" aria-hidden="true">'
               f'<rect x="0" y="0" width="260" height="16" rx="8" fill="{GREEN}" opacity="0.9"/></svg>'
               f'<div class="sa-label" style="font-size:{lsize}px">{esc(s["label"])}</div></div>')
    return frame("stat", s["title"], content, css)


def _num(v: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", v or "")
    return _to_float(m[0]) if m else None


def render_compare(s: dict) -> str:
    lt, rt = s["left"], s["right"]
    nl, nr = _num(lt["value"]), _num(rt["value"])
    bars = nl is not None and nr is not None and max(nl, nr) > 0
    vlen = max(len(lt["value"]), len(rt["value"]))
    vsize = _size("x" * vlen, [(5, 150), (8, 120), (12, 96)], 80)
    lsize = 76 if max(len(lt["label"]), len(rt["label"])) <= 16 else 62
    css = f""".sa-cmp{{display:flex;align-items:stretch;width:100%;height:660px}}
.sa-col{{flex:1;min-width:0;background:{PANEL};border-radius:28px;padding:64px 72px;border:2px solid {LINE};
display:flex;flex-direction:column;justify-content:center;gap:32px;border-top:12px solid}}
.sa-cl{{font-weight:700;line-height:1.1}}
.sa-cv{{font-weight:700;line-height:1;letter-spacing:-0.03em}}
.sa-cn{{font-size:50px;line-height:1.25;color:{MUTED}}}
.sa-vs{{flex:none;width:150px;display:flex;align-items:center;justify-content:center;
font-size:56px;font-weight:600;color:#9ca3af}}"""

    def col(side, color, n):
        parts = [f'<div class="sa-cl" style="font-size:{lsize}px">{esc(side["label"])}</div>']
        if side["value"]:
            parts.append(f'<div class="sa-cv" style="font-size:{vsize}px;color:{color}">'
                         f'{esc(side["value"])}</div>')
        if bars:
            w = max(12, int(600 * n / max(nl, nr)))
            parts.append(f'<svg width="600" height="36" viewBox="0 0 600 36" aria-hidden="true">'
                         f'<rect width="600" height="36" rx="18" fill="{LINE}"/>'
                         f'<rect width="{w}" height="36" rx="18" fill="{color}"/></svg>')
        if side["note"]:
            nsize = 50 if side["value"] else 58
            parts.append(f'<div class="sa-cn" style="font-size:{nsize}px">{esc(side["note"])}</div>')
        return f'<div class="sa-col" style="border-top-color:{color}">{"".join(parts)}</div>'

    content = (f'<div class="sa-cmp">{col(lt, BLUE, nl)}<div class="sa-vs">vs</div>'
               f'{col(rt, GREEN, nr)}</div>')
    return frame("compare", s["title"], content, css)


ARROW = (f'<svg class="sa-arr" width="72" height="72" viewBox="0 0 72 72" aria-hidden="true">'
         f'<path d="M12 36h44M38 18l18 18-18 18" stroke="{GREEN}" stroke-width="8" fill="none" '
         f'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def render_steps(s: dict) -> str:
    steps = s["steps"]
    n = len(steps)
    size = {3: 68, 4: 58, 5: 48}.get(n, 48)
    if _longest_word(steps) * size * 0.58 > (1664 - (n - 1) * 88) / n - 64:
        size = int(size * 0.8)
    css = f""".sa-steps{{display:flex;align-items:center;width:100%;gap:8px}}
.sa-step{{flex:1;min-width:0;height:440px;background:{PANEL};border:3px solid {LINE};border-radius:28px;
padding:48px 32px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:40px;
text-align:center}}
.sa-step.last{{border-color:{GREEN}}}
.sa-sn{{width:104px;height:104px;border-radius:52px;background:{GREEN};color:{BG};font-size:56px;
font-weight:700;display:flex;align-items:center;justify-content:center;flex:none}}
.sa-st{{font-weight:700;line-height:1.15;overflow-wrap:break-word;max-width:100%}}
.sa-arr{{flex:none}}"""
    parts = []
    for i, st in enumerate(steps):
        if i:
            parts.append(ARROW)
        cls = "sa-step last" if i == n - 1 else "sa-step"
        parts.append(f'<div class="{cls}"><div class="sa-sn">{i + 1}</div>'
                     f'<div class="sa-st" style="font-size:{size}px">{esc(st)}</div></div>')
    return frame("steps", s["title"], f'<div class="sa-steps">{"".join(parts)}</div>', css)


def render_timeline(s: dict) -> str:
    ev = s["events"]
    n = len(ev)
    wsize = {3: 72, 4: 64, 5: 54}.get(n, 54)
    if max(len(e["when"]) for e in ev) > 9:
        wsize = int(wsize * 0.8)
    tsize = {3: 56, 4: 50, 5: 44}.get(n, 44)
    css = f""".sa-tl{{position:relative;width:100%;display:flex}}
.sa-rail{{position:absolute;left:0;top:156px}}
.sa-ev{{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;text-align:center;
padding:0 24px;position:relative}}
.sa-when{{height:150px;display:flex;align-items:flex-end;padding-bottom:30px;font-weight:700;
color:{GREEN};line-height:1;white-space:nowrap}}
.sa-dot{{width:52px;height:52px;border-radius:26px;background:{BG};border:10px solid {GREEN};
position:relative;z-index:1;flex:none}}
.sa-dot.last{{background:{GREEN}}}
.sa-what{{padding-top:40px;font-weight:600;line-height:1.2;overflow-wrap:break-word;max-width:100%;
height:260px}}"""
    pad = int(1664 / n / 2)
    rail = (f'<svg class="sa-rail" width="1664" height="20" viewBox="0 0 1664 20" aria-hidden="true">'
            f'<line x1="{pad}" y1="10" x2="{1664 - pad}" y2="10" stroke="{LINE}" stroke-width="10" '
            f'stroke-linecap="round"/></svg>')
    cols = "".join(
        f'<div class="sa-ev"><div class="sa-when" style="font-size:{wsize}px">{esc(e["when"])}</div>'
        f'<div class="sa-dot{" last" if i == n - 1 else ""}"></div>'
        f'<div class="sa-what" style="font-size:{tsize}px">{esc(e["what"])}</div></div>'
        for i, e in enumerate(ev))
    return frame("timeline", s["title"], f'<div class="sa-tl">{rail}{cols}</div>', css)


def render_list(s: dict) -> str:
    items = s["items"]
    n = len(items)
    longest = max(len(i) for i in items)
    size = _size("x" * longest, [(18, 76), (30, 64), (40, 56)], 50) if n <= 3 else \
        _size("x" * longest, [(18, 60), (30, 52)], 46)
    css = f""".sa-list{{display:flex;gap:48px;width:100%}}
.sa-item{{flex:1;min-width:0;height:540px;background:{PANEL};border-radius:28px;padding:64px 56px;
display:flex;flex-direction:column;justify-content:space-between;border:2px solid {LINE};border-top:10px solid {GREEN}}}
.sa-in{{font-size:112px;font-weight:700;color:{GREEN};line-height:1;letter-spacing:-0.03em}}
.sa-it{{font-weight:700;line-height:1.15;overflow-wrap:break-word}}"""
    tiles = "".join(
        f'<div class="sa-item"><div class="sa-in">{i + 1}</div>'
        f'<div class="sa-it" style="font-size:{size}px">{esc(t)}</div></div>'
        for i, t in enumerate(items))
    return frame("list", s["title"], f'<div class="sa-list">{tiles}</div>', css)


RENDER = {"stat": render_stat, "compare": render_compare, "steps": render_steps,
          "timeline": render_timeline, "list": render_list}


def render(kind: str, spec: dict) -> str:
    return RENDER[kind](spec)


# ---------------------------------------------------------------- dedup

_STOP = set("the a an of to and or in on for is are was with vs versus by at it this that our your "
            "we you big number comparison flow timeline three points".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


def duplicate(kind: str, spec: dict, cards: list[dict]) -> str | None:
    """Title of an existing card that already covers this visual, if any."""
    mine = spec_text(spec)
    nums = {_to_float(t) for t in _NUM.findall(mine)} - {None}
    words = _words(mine)
    for c in cards:
        if c.get("status") == "dismissed" and c.get("source") != "visual":
            continue
        theirs = f"{c.get('title', '')} {c.get('body', '')}"
        if c.get("kind") == "visual" and nums:
            if nums <= ({_to_float(t) for t in _NUM.findall(theirs)} - {None}):
                return c.get("title", "")
        tw = _words(theirs)
        if words and tw and len(words & tw) / len(words | tw) >= 0.5:
            return c.get("title", "")
    return None


# ---------------------------------------------------------------- worker

def _cards_prompt(cards: list[dict]) -> str:
    rows = [f"- [{c.get('kind')}] {c.get('title', '')}: {c.get('body', '')[:120]}"
            for c in cards[-12:] if c.get("status") != "dismissed"]
    return "\n".join(rows) or "none"


class Visual(Worker):
    name = "visual"
    every_s = 20.0

    def __init__(self):
        super().__init__()
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self._last_post: tuple[float, float] = (0.0, -1e9)  # (talk start, elapsed at post)

    async def decide(self, talk: Talk, recent: str) -> dict | None:
        sec = talk.section or {}
        user = (f"TALK OBJECTIVE: {talk.plan.get('objective', '')}\n"
                f"CURRENT SECTION: {sec.get('title', '')}: {'; '.join(sec.get('points', []))}\n\n"
                f"EXISTING CARDS (already on screen or queued, do not repeat):\n"
                f"{_cards_prompt(talk.cards)}\n\n"
                f"LAST MINUTE OF THE TALK:\n{recent}")
        d = await self.llm_json(user, DECIDE_SYSTEM, max_tokens=150, temperature=0.2)
        kind = str(d.get("type") or "").strip().lower()
        if d.get("visual") is not True or kind not in TYPES:
            log.info("decide: no (%s)", clean(d.get("about"), 12, 90) or d.get("type") or "-")
            return None
        try:
            rel = float(d.get("relevance", 0.6))
        except (TypeError, ValueError):
            rel = 0.6
        return {"type": kind, "about": clean(d.get("about"), 12, 90),
                "relevance": max(0.05, min(0.95, rel))}

    async def make_spec(self, talk: Talk, kind: str, about: str, recent: str) -> dict | None:
        sec = talk.section or {}
        user = (f"PLAN\n{talk.plan_text()}\n\n"
                f"CURRENT SECTION: {sec.get('title', '')}\n"
                f"VISUAL TYPE: {kind}\nIT SHOULD SHOW: {about}\n\n"
                f"WHAT THE SPEAKER SAID IN THE LAST MINUTE:\n{recent}")
        raw = await self.llm_json(user, SPEC_SYSTEM + SCHEMAS[kind], max_tokens=320,
                                  temperature=0.2)
        spec = validate(kind, raw)
        if not spec:
            log.info("spec: invalid %s %s", kind, str(raw)[:200])
        return spec

    async def produce(self, talk: Talk) -> list[Card]:
        start, at = self._last_post
        if start == talk.started and talk.elapsed - at < COOLDOWN_S:
            return []
        recent = talk.recent(WINDOW_S)
        if len(recent.split()) < 15:
            return []
        d = await self.decide(talk, recent)
        if not d:
            return []
        spec = await self.make_spec(talk, d["type"], d["about"], recent)
        if not spec:
            return []
        kind = d["type"]
        sec_title = str((talk.section or {}).get("title", "")).strip().lower()
        if sec_title and spec["title"].lower() == sec_title and d["about"]:
            about = _trim(clean(d["about"], 6, 48))
            spec["title"] = about[:1].upper() + about[1:]
        source =f"{talk.recent(WINDOW_S * 2)}\n{talk.plan_text()}"
        if missing := ungrounded(spec_text(spec), source):
            log.info("spec: ungrounded numbers %s in %s", missing, spec)
            return []
        if dup := duplicate(kind, spec, talk.cards):
            log.info("spec: duplicate of %r", dup)
            return []
        frag = render(kind, spec)
        if not check_html(frag):
            return []
        self._last_post = (talk.started, talk.elapsed)
        sec = talk.section or {}
        body = summary(kind, spec)
        log.info("visual: rel=%.2f %s", d["relevance"], body)
        return [Card(kind="visual", title=spec["title"], body=body, html=frag,
                     relevance=round(d["relevance"], 2), point=sec.get("title", ""))]


if __name__ == "__main__":
    Visual().run()
