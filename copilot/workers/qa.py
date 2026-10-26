"""QA worker: anticipate the sharpest audience or judge question and draft a short answer.

Every ~30 s it reads the last 90 s of transcript plus the plan, picks the single most likely
pointed question about what was just said, and drafts an answer the speaker can give in under
15 seconds (40 words max), grounded only in what the speaker said or planned to say.

Run (on the box, against staging):
    COPILOT_URL=http://127.0.0.1:8001 GATE_URL=http://127.0.0.1:8090/v1 \
        python -m copilot.workers.qa
"""

import html
import logging
import re

from .base import Card, Talk, Worker

log = logging.getLogger("worker")

MAX_ANSWER_WORDS = 40
MAX_QUESTION_CHARS = 140

SYSTEM = """You prepare a speaker for Q&A while they are still presenting. You see the talk \
plan and the last ninety seconds of the live transcript. Predict the ONE sharpest question a \
skeptical judge or audience member is most likely to ask about what was JUST said, and draft \
the answer the speaker could give out loud in under 15 seconds.

Good questions are pointed and specific to a claim the speaker just made (examples from \
other talks, do not reuse them):
  "Faster than what, and who measured it?"
  "If the data never leaves the device, how do you improve the model?"
  "Who signs the check, the teacher or the school?"
Bad questions (never write like these):
  "Can you tell us more about the product?"  (vague)
  "What inspired you to build this?"  (soft, not about a claim)

Rules for the question: at most 15 words, one sentence, ends with ?, no preamble \
restating what the speaker said.

Rules for the answer:
- About 30 words, never more than 40. First person plural ("we"), plain spoken sentences, no lists.
- Lead with the direct answer, built from a specific fact in the transcript or plan.
- Use ONLY facts stated in the transcript or the plan. Never invent numbers, customers, \
benchmarks, prices, names, results, or internal mechanics. Never contradict the plan.
- Only when the material truly has no answer, admit it in a few honest words, then point to \
the closest thing the plan or transcript does support. Do not overclaim.
- No emoji, no dashes between clauses, no exclamation marks, no hype words.

Set substantive to false (and leave the rest empty) when the last ninety seconds contain no \
real claim worth questioning: small talk, a personal story unrelated to the objective, filler.
Do not ask a question that is the same as one already prepared (listed below).

Reply with ONLY a JSON object:
{"substantive": true | false,
 "question": "<one question, max 15 words, ends with ?>",
 "answer": "<max 40 words>",
 "evidence": "<a short exact phrase from the transcript or plan the answer relies on>",
 "likelihood": <0.0 to 1.0, how likely someone really asks this>}"""

CHECK = """You fact check an answer a speaker will give out loud, against their own talk plan \
and transcript. Flag ONLY invented specifics the material never states: numbers, dates, \
customers, results, storage or deletion policies, or mechanics the material never describes. \
Paraphrases, summaries and fair restatements of plan points or transcript lines are supported; \
do not flag them.
If you flag anything, rewrite the answer without those claims: same first person "we" voice, \
spoken to the audience, at most 40 words. Never mention "the plan", "the transcript" or \
"the material" in the answer.
Reply with ONLY a JSON object:
{"unsupported": [<short invented claims, usually empty>], "answer": "<revised answer, or the original if nothing was flagged>"}"""

_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff‍️⬀-⯿]"
)


def clean(text) -> str:
    """Plain, single-line, no emoji, no em/en dashes, no curly quotes."""
    t = str(text or "")
    t = _EMOJI.sub("", t)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = re.sub(r"\s+-{1,2}\s+", ", ", t)  # spaced hyphen used as a dash
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    t = t.replace("!", ".")
    t = re.sub(r"\s+", " ", t).strip().strip('"').strip()
    return re.sub(r",\s*,", ",", t)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def trim_words(text: str, limit: int) -> str:
    """Keep whole sentences within `limit` words; '' if even the first sentence is too long."""
    if len(text.split()) <= limit:
        return text
    out: list[str] = []
    for sent in re.split(r"(?<=[.?])\s+", text):
        if len(" ".join(out + [sent]).split()) > limit:
            break
        out.append(sent)
    return " ".join(out)


def numbers_grounded(text: str, source: str) -> bool:
    """Every number in `text` must appear in the source (transcript + plan)."""
    src = set(re.findall(r"\d+(?:\.\d+)?", source))
    return all(n in src for n in re.findall(r"\d+(?:\.\d+)?", text))


def evidence_grounded(evidence: str, source: str) -> bool:
    ev = _words(evidence)
    return bool(ev) and len(ev & _words(source)) / len(ev) >= 0.6


def slide(question: str, answer: str, point: str) -> str:
    q, a, p = html.escape(question), html.escape(answer), html.escape(point)
    q_size = 76 if len(question) <= 70 else 62 if len(question) <= 110 else 52
    a_size = 52 if len(answer.split()) <= 28 else 44
    return f"""<div style="width:1920px;height:1080px;box-sizing:border-box;background:#0b0d10;\
color:#e8eaed;font-family:Inter,Helvetica,Arial,sans-serif;padding:120px 160px;display:flex;\
flex-direction:column;justify-content:center;overflow:hidden;overflow-wrap:break-word">
<div style="font-size:30px;letter-spacing:4px;text-transform:uppercase;color:#76b900;\
font-weight:700;margin-bottom:28px">Question{' &middot; ' + p if p else ''}</div>
<div style="font-size:{q_size}px;line-height:1.18;font-weight:700;margin-bottom:64px">{q}</div>
<div style="width:140px;height:8px;background:#76b900;margin-bottom:56px"></div>
<div style="font-size:{a_size}px;line-height:1.35;color:#c9ccd1">{a}</div>
</div>"""


logging.getLogger("httpx").setLevel(logging.WARNING)


class QA(Worker):
    name = "qa"
    every_s = 30.0
    min_words = 25

    async def produce(self, talk: Talk) -> list[Card]:
        recent = re.sub(r"\s+", " ", talk.recent(90)).strip()
        if len(recent.split()) < 20:
            return []
        sec = talk.section or {}
        point = clean(sec.get("title", ""))
        plan = talk.plan_text()
        asked = [c.get("title", "") for c in talk.cards if c.get("kind") == "qa"]

        user = (
            f"TALK PLAN\n{plan}\n\n"
            f"CURRENT SECTION: {point or 'unknown'}\n\n"
            f"ALREADY PREPARED QUESTIONS (do not repeat):\n"
            + ("\n".join(f"- {q}" for q in asked[-8:]) or "- none")
            + f"\n\nLAST 90 SECONDS OF TRANSCRIPT:\n\"{recent}\"\n\n"
            "Predict the single sharpest question and draft the answer. JSON only."
        )
        out = await self.llm_json(user, SYSTEM, max_tokens=300, temperature=0.3)
        if not out or not out.get("substantive"):
            log.info("skip: nothing substantive")
            return []

        question = clean(out.get("question"))
        answer = clean(out.get("answer"))
        if not question or not answer:
            log.info("skip: empty question/answer %r", out)
            return []
        if len(question) > MAX_QUESTION_CHARS and "?" in question:
            # the model often restates the claim first; keep the actual question sentence
            question = re.split(r"(?<=[.?])\s+", question.rstrip())[-1]
        if not question.endswith("?"):
            question = question.rstrip(".") + "?"
        if len(question) > MAX_QUESTION_CHARS:
            log.info("skip: question too long %r", question)
            return []
        answer = trim_words(answer, MAX_ANSWER_WORDS)
        if not answer:
            log.info("skip: answer has no sentence under %d words", MAX_ANSWER_WORDS)
            return []

        source = f"{talk.full()} {plan}"
        answer = await self.fact_check(question, answer, source)
        if not answer:
            return []
        if not numbers_grounded(question + " " + answer, source):
            log.info("skip: ungrounded number in %r / %r", question, answer)
            return []
        if not evidence_grounded(str(out.get("evidence") or ""), source):
            log.info("skip: evidence not in transcript/plan %r", out.get("evidence"))
            return []
        if any(similar(question, q) for q in asked):
            log.info("skip: duplicate of an earlier question %r", question)
            return []

        try:
            relevance = max(0.0, min(1.0, float(out.get("likelihood", 0.5))))
        except (TypeError, ValueError):
            relevance = 0.5
        log.info("card: Q: %s | A: %s | rel=%.2f", question, answer, relevance)
        return [Card(kind="qa", title=question, body=f"Q: {question} A: {answer}",
                     html=slide(question, answer, point), relevance=relevance, point=point)]

    async def fact_check(self, question: str, answer: str, source: str) -> str:
        """Second pass: strip claims the transcript and plan don't support. '' to skip."""
        user = (f"SOURCE MATERIAL\n{source[-6000:]}\n\nQUESTION: {question}\n"
                f"DRAFTED ANSWER: {answer}\n\nJSON only.")
        out = await self.llm_json(user, CHECK, max_tokens=250, temperature=0.1)
        if not out:
            return answer  # checker unavailable/garbled: keep the draft, other guards still apply
        bad = [clean(c) for c in out.get("unsupported") or [] if clean(c)]
        revised = trim_words(clean(out.get("answer")), MAX_ANSWER_WORDS)
        if bad:
            log.info("fact check removed: %s", "; ".join(bad))
            if re.search(r"\b(transcript|the (talk )?plan (says|states)|the material)\b", revised, re.I):
                revised = ""  # checker slipped into narrator voice; not sayable on stage
            if not revised:
                log.info("skip: nothing supported left")
                return ""
            return revised
        return answer


if __name__ == "__main__":
    QA().run()
