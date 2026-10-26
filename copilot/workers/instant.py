"""Instant cards: no waiting on rounds, no director, no big model.

  - Prepared deck: when a talk starts, every plan section is rendered to a slide from its
    title and points (templates only, no LLM). The moment the transcript reaches a section
    (keywords heard, or the coach says they've moved there), that slide goes straight to
    the speaker's tray.
  - Number slides: when a finished sentence contains a number, one short call to the small
    model turns it into a stat or comparison spec, rendered with the visual templates and
    proposed directly. About two seconds from the end of the sentence.
"""

import asyncio
import base64
import logging
import re
import time

from .base import Card, Talk, Worker, prep_image
from .visual import BG, FG, FONT, GREEN, MUTED, PANEL, clean, esc, frame, numbers_in, render, ungrounded, validate

log = logging.getLogger("worker")

STOP = set("""the a an of to and or in on for is are was were be with vs versus by at it this that
these those our your their its we you they i me my us them what how why when where who which as from
into about over under than then so but not no yes can will would should could just also very really
more most less all any each every some one two three section min must""".split())
NUMBER_WORDS = re.compile(r"\b(\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|"
                          r"twenty|thirty|forty|fifty|hundred|thousand|million|billion|half|double|twice)\b", re.I)
SPEC = """Turn ONE sentence from a live talk into a slide. Use only numbers and words the speaker said.

If it compares two things with numbers (before/after, them/us, cloud/local), reply:
{"type": "compare", "title": "<2 to 6 words>",
 "left": {"label": "<1 to 3 words, the old or worse option>", "value": "<number + unit as said>", "note": "<2 to 6 words>"},
 "right": {"label": "<1 to 3 words, the speaker's option>", "value": "<number + unit as said>", "note": "<2 to 6 words>"}}
Otherwise, for one key number, reply:
{"type": "stat", "title": "<2 to 6 words>", "value": "<digits only>", "unit": "<0 to 2 words>", "label": "<what it measures, 3 to 9 words>"}
If the number is incidental (a year, a page number, counting off steps), reply {"type": "none"}.
Reply with ONLY the JSON."""


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def render_section(title: str, points: list[str]) -> str:
    """A clean agenda slide for one plan section: big title, its points as tiles."""
    spec = validate("list", {"title": title, "items": points})
    if spec:
        return render("list", spec)
    point = clean(points[0], 18, 120) if points else ""
    css = f""".sa-head{{display:flex;flex-direction:column;gap:56px;max-width:1600px}}
.sa-bar{{width:220px;height:16px;border-radius:8px;background:{GREEN}}}
.sa-pt{{font-size:84px;font-weight:700;line-height:1.15}}
.sa-sub{{font-size:44px;color:{MUTED}}}"""
    body = (f'<div class="sa-head"><div class="sa-bar"></div>'
            f'<div class="sa-pt">{esc(point or title)}</div></div>')
    return frame("list", clean(title, 8, 60), body, css)


def render_hero(title: str, points: list[str], image_url: str, kicker: str) -> str:
    """Section slide with its prepared illustration: text on the left, image bleeding off the right."""
    items = "".join(f"<li>{esc(clean(p, 14, 90))}</li>" for p in points[:3])
    tsize = 96 if len(title) <= 28 else 76
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:{BG};font-family:{FONT};color:{FG}}}
.hero{{display:grid;grid-template-columns:900px 1020px;height:1080px}}
.txt{{padding:120px 64px 120px 120px;display:flex;flex-direction:column;justify-content:center;gap:52px}}
.kick{{color:{GREEN};font-size:28px;letter-spacing:.12em;text-transform:uppercase;font-weight:600}}
h1{{font-size:{tsize}px;line-height:1.02;margin:0;font-weight:700;letter-spacing:-.025em}}
ul{{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:30px}}
li{{font-size:40px;line-height:1.28;color:#c9ccd1;padding-left:40px;position:relative}}
li:before{{content:"";position:absolute;left:0;top:.48em;width:14px;height:14px;border-radius:4px;background:{GREEN}}}
.img{{position:relative;background:{PANEL} url("{esc(image_url)}") center/cover no-repeat}}
.img:after{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#0b0d10 0%,rgba(11,13,16,.35) 18%,rgba(11,13,16,0) 40%)}}
</style></head><body><div class="hero"><div class="txt"><div class="kick">{esc(kicker)}</div>
<h1>{esc(clean(title, 8, 60))}</h1><ul>{items}</ul></div><div class="img"></div></div></body></html>"""


class Instant(Worker):
    name = "instant"
    every_s = 3600  # nothing on a timer; everything here reacts to events

    def __init__(self):
        super().__init__()
        self.deck: list[dict] = []  # per section: {"title", "html", "body", "keys", "done"}
        self.pending = ""  # transcript since the last finished sentence
        self.last_number_card = 0.0
        self.seen_numbers: set[float] = set()

    async def produce(self, talk: Talk) -> list[Card]:
        return []

    async def _event(self, ev: dict) -> None:
        await super()._event(ev)
        t = ev.get("type")
        if t == "start":
            self._build_deck()
            self.pending, self.seen_numbers = "", set()
        elif t == "text" and self.talk.running:
            self.pending += ev["text"]
            await self._match_section()
            if re.search(r"[.!?]\s*$", self.pending):
                sentence, self.pending = self.pending.strip(), ""
                if NUMBER_WORDS.search(sentence):
                    asyncio.create_task(self._number_card(sentence))
        elif t == "verdict" and self.talk.running and isinstance(ev.get("section"), int):
            await self._show_section(ev["section"], "You've moved on to this section")

    # ---- prepared deck ----
    def _build_deck(self) -> None:
        self.deck = []
        secs = self.talk.plan.get("sections") or []
        for n, sec in enumerate(secs):
            pts = sec.get("points") or []
            self.deck.append({
                "title": sec["title"],
                "sec": sec,
                "kicker": f"Section {n + 1} of {len(secs)}",
                "html": render_section(sec["title"], pts),
                "body": f"{sec['title']}: " + "; ".join(pts),
                "keys": keywords(sec["title"] + " " + " ".join(pts)),
                "done": False,
            })
        log.info("prepared %d section slides", len(self.deck))

    async def _match_section(self) -> None:
        heard = keywords(" ".join(self.talk.recent(25).split()[-40:]))
        best, best_hits = None, 0
        for i, s in enumerate(self.deck):
            hits = len(heard & s["keys"])
            if not s["done"] and hits >= 3 and hits > best_hits:
                best, best_hits = i, hits
        if best is not None:
            await self._show_section(best, f"You're on {self.deck[best]['title']}")

    async def _show_section(self, i: int, reason: str) -> None:
        if not (0 <= i < len(self.deck)) or self.deck[i]["done"]:
            return
        s = self.deck[i]
        s["done"] = True
        img = prep_image(s["sec"])
        # Prepared illustration if it's ready, else the text slide. The image is embedded so the slide
        # is self-contained: sandboxed iframes don't reliably resolve relative URLs.
        html = s["html"]
        deck = img.with_name(img.name.replace("prep-", "deck-"))
        if deck.exists():  # the speaker uploaded their deck: show their own slide, full frame
            data = base64.b64encode(deck.read_bytes()).decode()
            html = (f'<div style="width:1920px;height:1080px;background:#0b0d10;display:flex;align-items:center;justify-content:center">'
                    f'<img src="data:image/jpeg;base64,{data}" style="max-width:1920px;max-height:1080px;object-fit:contain"></div>')
        elif img.exists():
            data = base64.b64encode(img.read_bytes()).decode()
            html = render_hero(s["title"], s["sec"].get("points") or [], f"data:image/jpeg;base64,{data}", s["kicker"])
        card = await self.post(Card(kind="visual", title=s["title"], body=s["body"], html=html,
                                    relevance=0.8, point=s["title"]))
        if card:
            await self.http.post(f"{self._url()}/cards/{card['id']}/propose",
                                 json={"score": 0.8, "reason": reason})
            log.info("section slide %d '%s' proposed (%s)", i, s["title"], reason)

    # ---- number slides ----
    async def _number_card(self, sentence: str) -> None:
        if time.monotonic() - self.last_number_card < 6:
            return
        nums = numbers_in(sentence)
        if nums and nums <= self.seen_numbers:
            return
        t0 = time.monotonic()
        d = await self.llm_json(f"SENTENCE: {sentence}", SPEC, model="small", max_tokens=160, temperature=0.1)
        kind = d.get("type")
        spec = validate(kind, d) if kind in ("stat", "compare") else None
        if not spec or ungrounded(str(spec), sentence + " " + self.talk.recent(30)):
            return
        self.last_number_card = time.monotonic()
        self.seen_numbers |= nums
        card = await self.post(Card(kind="visual", title=spec["title"], html=render(kind, spec),
                                    body=f"{kind}: {sentence[:120]}", relevance=0.9,
                                    point=(self.talk.section or {}).get("title", "")))
        if card:
            await self.http.post(f"{self._url()}/cards/{card['id']}/propose",
                                 json={"score": 0.9, "reason": "You just said it"})
            log.info("number slide '%s' proposed %.1fs after the sentence", spec["title"], time.monotonic() - t0)

    def _url(self) -> str:
        from .base import COPILOT_URL
        return COPILOT_URL


if __name__ == "__main__":
    Instant().run()
