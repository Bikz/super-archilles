"""Live notes: running minutes of the conversation, posted as a card every ~45 s.

Tracks decisions, action items (owner and due date only when said), open questions,
and risks. When the agenda looks legal (client, matter, counsel, privilege...), it
also keeps "issues to flag": follow-up questions for counsel, never conclusions.

Every item the model proposes must carry a short verbatim quote from the transcript;
items whose quote cannot be found in the transcript are dropped. A card is posted
only when at least one new, grounded item arrived since the last card.

Run:  python -m copilot.workers.notes   (COPILOT_URL, GATE_URL as in base.py)
"""

import html
import re

from copilot.workers.base import Card, Talk, Worker, log

LEGAL_RE = re.compile(
    r"\b(client|matter|counsel|privilege\w*|attorney|lawyer|legal|contract\w*|liabilit\w*|"
    r"litigat\w*|indemn\w*|settlement|nda|dispute|statute|lawsuit|engagement letter|"
    r"retainer|conflict check|discovery|deposition|breach)\b",
    re.I,
)

CATS = ["decisions", "actions", "questions", "risks", "issues"]
LABELS = {
    "decisions": "Decisions",
    "actions": "Action items",
    "questions": "Open questions",
    "risks": "Risks and issues",
    "issues": "Issues to flag",
}

# Background LLM calls are cancelled whenever the live coach needs the GPU (every few
# seconds), so a round must fit in a short gap: small prompt (only new transcript) and a
# terse line format instead of JSON.
SYSTEM = """You take minutes for a private meeting, live. You get the agenda, the minutes \
so far, and the NEW transcript since the last update (speech-to-text, may lack punctuation). \
Write minute lines ONLY for things someone actually said in the NEW transcript. Never invent.

Line types, one item per line:
D | <decision someone explicitly agreed or made (a statement of fact or a claim is NOT a decision)> | <quote>
A | <task someone committed to or was asked to do> | <owner as said, I=speaker, we=team, else -> | <deadline as said, else -> | <quote>
Q | <question raised and not yet answered> | <quote>
R | <risk, concern, blocker or problem raised> | <quote>
{issues_rule}
<quote> is 4 to 10 words copied EXACTLY from the NEW transcript.
Item text: plain, max 14 words. Skip anything already in MINUTES SO FAR.
Most updates have 0 to 3 lines. If nothing qualifies, reply with exactly: NONE"""

ISSUES_RULE = """I | <possible legal issue for counsel to follow up, phrased as a QUESTION ending in "?"> | <quote>
   (e.g. "Was the indemnity cap ever amended?"). Never a legal conclusion: never say illegal, liable, breach or violation."""

TYPE_CAT = {"D": "decisions", "A": "actions", "Q": "questions", "R": "risks", "I": "issues"}

CONCLUSION_RE = re.compile(
    r"\b(is|are|was|were) (illegal|unlawful|liable|in breach|negligent|guilty)\b|"
    r"\b(constitutes|amounts to) (a )?(breach|violation|fraud|negligence)\b|\bclearly (liable|illegal)\b",
    re.I,
)
EMOJI_RE = re.compile("[\U0001f000-\U0001ffff☀-➿⬀-⯿️‍]")
WORD_RE = re.compile(r"[a-z0-9']+")
STOP = set("a an the and or of to in on for with is are was were be it this that we i you they "
           "he she our us at by as so do does did will would can could should".split())


def clean(s, limit: int = 160) -> str:
    """Model text -> one plain line: no emoji, no em/en dashes, bounded length."""
    s = str(s or "")
    s = EMOJI_RE.sub("", s)
    s = re.sub(r"\s*[—–]\s*", ", ", s)
    s = re.sub(r"\s+", " ", s).strip().strip('"').strip()
    if len(s) > limit:
        s = s[: limit - 3].rsplit(" ", 1)[0] + "..."
    return s


def words(s: str) -> list[str]:
    return WORD_RE.findall(s.lower())


def grams(ws: list[str], n: int) -> set[tuple]:
    return {tuple(ws[i: i + n]) for i in range(len(ws) - n + 1)}


def grounded(quote: str, tx_words: list[str], tx_tri: set, tx_bi: set) -> bool:
    """The quote (near-)verbatim appears in the transcript."""
    q = words(quote)
    if len(q) < 3:
        return False
    if len(q) >= 4:
        tri = grams(q, 3)
        return len(tri & tx_tri) / len(tri) >= 0.6
    bi = grams(q, 2)
    return len(bi & tx_bi) / len(bi) >= 0.75


# A small model over-extracts (it calls any statement a "decision"), so each item type
# also needs a cue word near its quote in the transcript.
CUES = {
    "decisions": r"agree\w*|decid\w*|decision|approv\w*|resolv\w*|let's|lets|go with|going with|"
                 r"settled|final|confirm\w*|we will|we'll|we are going to|we're going to|plan is",
    "actions": r"will|i'll|we'll|you'll|he'll|she'll|action|to do|todo|need to|needs to|has to|"
               r"can you|could you|please|follow up|send|by (monday|tuesday|wednesday|thursday|"
               r"friday|tomorrow|next|end)|owner|assign\w*|take care|going to",
    "questions": r"\?|question|unclear|not sure|don't know|do we know|whether|how do|how will|"
                 r"what if|who will|when will|need to find out|open item|tbd",
    "risks": r"risk\w*|concern\w*|worr\w*|problem|issue|block\w*|danger\w*|exposure|might|could|"
             r"fail\w*|drop\w*|lose|losing|leak\w*|delay\w*|behind|threat|fear|careful|liabilit\w*",
    "issues": r".",
}
CUES = {k: re.compile(rf"(?:^|\W)(?:{v})(?:\W|$)", re.I) if k != "issues" else re.compile(".")
        for k, v in CUES.items()}


def has_cue(cat: str, quote: str, tx_words: list[str], raw_tx: str) -> bool:
    """Cue word in the quote, or in the transcript around where the quote was said."""
    cue = CUES[cat]
    if cue.search(quote):
        return True
    q = words(quote)
    if len(q) < 3:
        return False
    # locate the quote by its first trigram, then look at ~12 words either side
    first = tuple(q[:3])
    for i in range(len(tx_words) - 2):
        if tuple(tx_words[i:i + 3]) == first:
            around = " ".join(tx_words[max(0, i - 12): i + len(q) + 12])
            if cue.search(around):
                return True
    if cat == "questions":  # '?' is stripped from tx_words; check the raw text
        idx = raw_tx.lower().find(" ".join(q[:3]))
        return idx >= 0 and "?" in raw_tx[idx: idx + 200]
    return False


def content(s: str) -> set[str]:
    return {w for w in words(s) if w not in STOP and len(w) > 2}


def similar(a: str, b: str) -> bool:
    ca, cb = content(a), content(b)
    if not ca or not cb:
        return a.lower() == b.lower()
    return len(ca & cb) / len(ca | cb) >= 0.5


def mmss(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class Notes(Worker):
    name = "notes"
    every_s = 45.0
    min_words = 25
    per_panel = 3  # items shown per category on the slide (newest first); sized to fit

    def __init__(self):
        super().__init__()
        self._talk_obj = None
        self.items: dict[str, list[dict]] = {c: [] for c in CATS}
        self._done_until = 0.0  # transcript (elapsed s) already turned into minutes

    def _reset_if_new_talk(self, talk: Talk) -> None:
        if talk is not self._talk_obj:
            self._talk_obj = talk
            self.items = {c: [] for c in CATS}
            self._done_until = 0.0

    async def produce(self, talk: Talk) -> list[Card]:
        self._reset_if_new_talk(talk)
        # Only the transcript since the last successful round (plus a little overlap), so
        # the prompt stays small and the call fits between coach ticks. Earlier minutes
        # are kept in self.items and shown to the model so it does not repeat them.
        until = talk.elapsed
        new = "".join(t for ts, t in talk.transcript if ts >= self._done_until - 15).strip()[-5000:]
        if len(new.split()) < 20:
            return []
        plan_text = talk.plan_text()
        legal = bool(LEGAL_RE.search(plan_text))

        system = SYSTEM.format(issues_rule=ISSUES_RULE if legal else "")
        user = (f"AGENDA:\n{plan_text}\n\nMINUTES SO FAR:\n{self._minutes_text() or '(none yet)'}"
                f"\n\nNEW TRANSCRIPT:\n{new}\n\nMinute lines (or NONE):")
        out = await self.llm(user, system, max_tokens=260, temperature=0.2, retries=12)
        self._done_until = until
        log.info("llm: %r", out[:400])

        raw_tx = talk.full(10**9)
        tx_words = words(raw_tx)
        tx_tri, tx_bi = grams(tx_words, 3), grams(tx_words, 2)
        tx_lower = " ".join(tx_words)
        at = mmss(talk.elapsed)
        added = 0
        for line in out.splitlines()[:12]:
            parts = [p.strip() for p in line.strip().lstrip("-* ").split("|")]
            cat = TYPE_CAT.get(parts[0].upper().rstrip(":.)")) if parts else None
            if not cat or (cat == "issues" and not legal):
                continue
            want = 5 if cat == "actions" else 3
            if len(parts) < want:
                continue
            text, quote = clean(parts[1], 140), clean(parts[-1], 200)
            if not text or not grounded(quote, tx_words, tx_tri, tx_bi):
                log.info("drop ungrounded %s: %r / %r", cat, text, quote)
                continue
            if not has_cue(cat, quote, tx_words, raw_tx):
                log.info("drop uncued %s: %r / %r", cat, text, quote)
                continue
            if cat == "issues" and (not text.endswith("?") or CONCLUSION_RE.search(text)):
                log.info("drop non-question issue: %r", text)
                continue
            if any(similar(text, p["text"]) for p in self.items[cat]):
                continue
            item = {"text": text, "at": at}
            if cat == "actions":
                item["owner"] = self._said(parts[2], tx_lower, owner=True)
                item["due"] = self._said(parts[3], tx_lower)
            self.items[cat].append(item)
            added += 1

        if not added:
            log.info("nothing new")
            return []
        sec = talk.section or {}
        return [Card(
            kind="note",
            title=f"Live notes at {at}",
            body=self._minutes_text(),
            html=self._slide(talk, at, legal),
            relevance=0.4,
            point=sec.get("title", ""),
        )]

    @staticmethod
    def _said(val, tx_lower: str, owner: bool = False) -> str:
        """Keep an owner/due only if it was actually said in the transcript."""
        v = clean(val, 40)
        low = v.lower()
        if not v or low in ("none", "null", "n/a", "unknown", "unassigned", "tbd"):
            return ""
        if owner and low in ("speaker", "i", "me"):
            return "speaker"
        if owner and low in ("team", "we", "us"):
            return "team"
        vw = words(v)
        tx = set(tx_lower.split())
        return v if vw and all(w in tx for w in vw) else ""

    def _minutes_text(self) -> str:
        parts = []
        for cat in CATS:
            its = self.items[cat]
            if not its:
                continue
            parts.append(LABELS[cat].upper())
            for it in its:
                extra = ", ".join(x for x in (
                    f"owner: {it['owner']}" if it.get("owner") else "",
                    f"due: {it['due']}" if it.get("due") else "") if x)
                parts.append(f"- {it['text']}" + (f" ({extra})" if extra else ""))
        return "\n".join(parts)

    def _slide(self, talk: Talk, at: str, legal: bool) -> str:
        e = html.escape
        cats = [c for c in CATS if c != "issues" or legal]
        cols = 3 if len(cats) > 4 else 2
        fs = 26 if cols == 2 else 22
        panels = []
        for cat in cats:
            its = self.items[cat]
            shown = list(reversed(its))[: self.per_panel]
            lis = []
            for it in shown:
                meta = ""
                if cat == "actions":
                    tags = [f'<span class="tag">{e(it["owner"])}</span>' if it.get("owner") else
                            '<span class="tag dim">no owner</span>']
                    if it.get("due"):
                        tags.append(f'<span class="tag">due {e(it["due"])}</span>')
                    meta = f'<div class="meta">{"".join(tags)}</div>'
                new = " new" if it["at"] == at else ""
                lis.append(f'<li class="it{new}"><span class="t">{e(clean(it["text"], 100))}</span>'
                           f'<span class="at">{e(it["at"])}</span>{meta}</li>')
            more = len(its) - len(shown)
            if more > 0:
                lis.append(f'<li class="more">+{more} earlier</li>')
            if not lis:
                lis.append('<li class="none">Nothing yet</li>')
            panels.append(f'<section class="p p-{cat}"><h2>{e(LABELS[cat])}'
                          f'<span class="n">{len(its)}</span></h2><ul>{"".join(lis)}</ul></section>')
        objective = clean(talk.plan.get("objective", ""), 150)
        sec = (talk.section or {}).get("title", "")
        sub = " | ".join(x for x in (e(clean(talk.name, 60)), e(clean(sec, 60))) if x)
        foot = ("Issues to flag are prompts for counsel to follow up, not legal conclusions. "
                if legal else "")
        return f"""<div class="notes" style="width:1920px;height:1080px">
<style>
.notes{{box-sizing:border-box;width:1920px;height:1080px;overflow:hidden;background:#0b0d10;color:#e8eaed;
font-family:Inter,"Helvetica Neue",Arial,sans-serif;padding:48px 72px;display:flex;flex-direction:column;gap:24px}}
.notes *{{box-sizing:border-box;margin:0;padding:0}}
.notes header{{display:flex;align-items:baseline;justify-content:space-between;border-bottom:3px solid #76b900;padding-bottom:20px}}
.notes h1{{font-size:50px;font-weight:700;letter-spacing:-0.5px}}
.notes h1 b{{color:#76b900;font-weight:700}}
.notes .sub{{font-size:26px;color:#9aa0a6;margin-top:8px}}
.notes .obj{{font-size:24px;color:#9aa0a6;max-width:900px;text-align:right;line-height:1.35}}
.notes .grid{{flex:1;display:grid;grid-template-columns:repeat({cols},1fr);gap:28px;min-height:0}}
.notes .p{{background:#13161b;border:1px solid #23272e;border-radius:18px;padding:28px 32px;overflow:hidden;display:flex;flex-direction:column}}
.notes .p-issues{{border-color:#76b900}}
.notes h2{{font-size:30px;font-weight:700;color:#76b900;text-transform:uppercase;letter-spacing:1.5px;display:flex;justify-content:space-between;margin-bottom:18px}}
.notes h2 .n{{color:#5f6368}}
.notes ul{{list-style:none;display:flex;flex-direction:column;gap:16px}}
.notes .it{{font-size:{fs}px;line-height:1.3;padding-left:18px;border-left:5px solid #2c3138}}
.notes .it.new{{border-left-color:#76b900}}
.notes .at{{font-size:20px;color:#5f6368;margin-left:12px}}
.notes .meta{{margin-top:6px}}
.notes .tag{{display:inline-block;font-size:20px;background:#1d2a10;color:#b5e06a;border-radius:8px;padding:3px 12px;margin-right:10px}}
.notes .tag.dim{{background:#1c1f24;color:#9aa0a6}}
.notes .more,.notes .none{{font-size:22px;color:#5f6368;padding-left:23px}}
.notes footer{{font-size:20px;color:#5f6368;display:flex;justify-content:space-between}}
</style>
<header><div><h1><b>Live notes</b> at {e(at)}</h1><div class="sub">{sub}</div></div>
<div class="obj">{e(objective)}</div></header>
<div class="grid">{"".join(panels)}</div>
<footer><span>{foot}Every item is quoted from the transcript.</span><span>On-box minutes, nothing left this machine</span></footer>
</div>"""


if __name__ == "__main__":
    Notes().run()
