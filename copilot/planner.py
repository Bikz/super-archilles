"""Turn whatever the speaker has (deck, doc, notes) into a timed talk plan.

Text extraction uses only the standard library for pptx/docx (they're zipped XML),
so it works with no extra downloads. PDF needs pypdf if it's installed.
"""

import base64
import io
import re
import zipfile

import httpx

from .coach import LLM_MODEL, LLM_URL, plain, strip_think
from .plan import parse_plan

CONVERT = """Turn the speaker's material into a talk plan for a {minutes:g}-minute talk.

Output ONLY markdown in exactly this format, nothing before or after:
# Objective: <the one thing the audience must walk away believing or able to do>

## <Section title> (<minutes> min) [must]
- <key point>
- <key point>

## <Section title> (<minutes> min)
- <key point>

Rules:
- Section minutes must add up to {minutes:g}. Use decimals like 0.5 or 1.5 when needed.
- Follow the order of the source material. Group related slides into one section; 3 to 7 sections.
- Mark the 2 to 4 sections the talk cannot skip with [must]. Leave the rest unmarked.
- 1 to 3 short key points per section, taken from the material, in plain words.
- No emoji, no em dashes.

MATERIAL:
{text}"""


def extract_text(filename: str, data_b64: str) -> str:
    raw = base64.b64decode(data_b64)
    name = filename.lower()
    if name.endswith((".md", ".markdown", ".txt")):
        return raw.decode("utf-8", errors="replace")
    if name.endswith(".pptx"):
        return _pptx(raw)
    if name.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return _xml_text(z.read("word/document.xml").decode(), "w:t", para="w:p")
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ValueError("PDF reading isn't installed on the box yet. Export the deck as .pptx, "
                             "or paste the text into the plan box.") from e
        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join(f"[Page {i + 1}]\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages))
    raise ValueError("Use a .md, .txt, .pptx, .docx or .pdf file.")


def _pptx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        slides = sorted(
            (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1])[1]),
        )
        out = []
        for i, name in enumerate(slides, 1):
            body = _xml_text(z.read(name).decode(), "a:t", para="a:p")
            notes_name = f"ppt/notesSlides/notesSlide{name.rsplit('slide', 1)[1]}"
            notes = _xml_text(z.read(notes_name).decode(), "a:t", para="a:p") if notes_name in z.namelist() else ""
            out.append(f"[Slide {i}]\n{body}" + (f"\nSpeaker notes: {notes}" if notes.strip() else ""))
        return "\n\n".join(out)


def _xml_text(xml: str, tag: str, para: str) -> str:
    paras = re.findall(rf"<{para}[ >].*?</{para}>", xml, re.S)
    lines = ["".join(re.findall(rf"<{tag}(?: [^>]*)?>(.*?)</{tag}>", p, re.S)) for p in paras]
    text = "\n".join(line for line in lines if line.strip())
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'"))


def to_plan(text: str, minutes: float) -> str:
    """Return plan markdown. Text that already is a plan comes back unchanged."""
    try:
        if len(parse_plan(text).sections) >= 2:
            return text
    except ValueError:
        pass
    prompt = CONVERT.format(minutes=minutes, text=text[:30000])
    last_err = None
    for _ in range(2):
        r = httpx.post(
            f"{LLM_URL}/chat/completions",
            headers={"X-Priority": "coach"},  # someone is waiting on this
            timeout=120,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 1200,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        r.raise_for_status()
        md = plain(strip_think(r.json()["choices"][0]["message"]["content"]))
        md = re.sub(r"^```\w*\n|```$", "", md.strip(), flags=re.M).strip()
        try:
            parse_plan(md)
            return mark_musts(md)
        except ValueError as e:
            last_err = e
    raise ValueError(f"Couldn't turn that into a plan: {last_err}")


def mark_musts(md: str) -> str:
    """Small models often drop [must]; default to the opening and the close."""
    if "[must]" in md.lower():
        return md
    heads = [i for i, line in enumerate(md.splitlines()) if line.startswith("## ")]
    lines = md.splitlines()
    for i in {heads[0], heads[-1]} if heads else set():
        lines[i] = lines[i].rstrip() + " [must]"
    return "\n".join(lines)
