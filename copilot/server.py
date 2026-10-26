"""Live talk copilot server. Everything (ASR + LLM) runs on this box.

  browser mic --(16 kHz PCM over WS)--> LiveASR --> transcript
  every TICK_S: Coach(plan, clock, transcript) --> local LLM --> nudge / replan / tangent
  nudges --> dashboard (WS) + Telegram
"""

import asyncio
import json
import os
import subprocess
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import planner, telegram
from .boxstats import BoxStats
from .coach import Coach
from .plan import Plan, fmt, parse_plan

TICK_S = float(os.environ.get("TICK_S", "3"))
# Comma-separated feature flags. Off by default so the live server only changes when we say so.
FEATURES = {f.strip() for f in os.environ.get("FEATURES", "").split(",") if f.strip()}
DATA = Path(os.environ.get("COPILOT_DATA", Path.home() / "copilot-data"))
STATIC = Path(__file__).parent / "static"


class Session:
    def __init__(self, plan: Plan, name: str, owner: str = "", device: str = "",
                 known_titles: list[str] | None = None):
        self.plan = plan
        self.name = name
        self.owner = owner  # browser that started the talk; only its mic is transcribed
        self.device = device
        self.coach = Coach(plan, known_titles)
        self.t0 = time.monotonic()
        self.started_at = time.strftime("%Y-%m-%d %H:%M")
        self.transcript: list[tuple[float, str]] = []
        self.task: asyncio.Task | None = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0


def section_times(s: "Session") -> list[float]:
    """Seconds actually spent in each plan section, from the coach's check-ins."""
    spent = [0.0] * len(s.plan.sections)
    prev_t, prev_sec = 0.0, 0
    for v in s.coach.history:
        t = v.get("elapsed_s", 0)
        sec = v.get("section") if isinstance(v.get("section"), int) else prev_sec
        if 0 <= prev_sec < len(spent):
            spent[prev_sec] += max(0.0, t - prev_t)
        prev_t, prev_sec = t, sec if 0 <= sec < len(spent) else prev_sec
    if 0 <= prev_sec < len(spent):
        spent[prev_sec] += max(0.0, s.elapsed - prev_t)
    return [round(x, 1) for x in spent]


def library_titles() -> list[str]:
    path = DATA / "library.md"
    return re.findall(r"^## (.+)$", path.read_text(), re.M) if path.exists() else []


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.session: Session | None = None
        self.cards: dict[str, dict] = {}  # this talk's cards from background workers
        self.stage: dict | None = None  # card currently shown on the audience screen
        self.coverage: dict | None = None  # latest topic coverage from the coverage worker
        self.asr = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def broadcast(self, event: dict) -> None:
        msg = json.dumps(event)
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                self.clients.discard(ws)

    def on_text(self, text: str) -> None:  # called from the ASR thread
        s = self.session
        if not s or not self.loop:
            return
        s.transcript.append((s.elapsed, text))
        self.loop.call_soon_threadsafe(
            asyncio.create_task, self.broadcast({"type": "text", "text": text, "t": s.elapsed})
        )


hub = Hub()


class NullASR:
    def start(self): ...
    def stop(self): ...
    def feed(self, pcm): ...


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    telegram.listener = lambda ev: asyncio.get_running_loop().create_task(
        hub.broadcast({"type": "telegram", **ev})
    )
    DATA.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ASR", "on") == "off":  # UI work while torch is still installing
        hub.asr = NullASR()
    else:
        from .asr import LiveASR

        hub.asr = await asyncio.to_thread(LiveASR, hub.on_text)
    stats = asyncio.create_task(stats_loop())
    yield
    stats.cancel()
    if hub.asr:
        hub.asr.stop()


app = FastAPI(lifespan=lifespan)

# The public Funnel address (*.ts.net) needs ?key=... once; a cookie remembers it.
# localhost, the SSH tunnel and the tailnet IP stay open.
ACCESS_KEY = os.environ.get("COPILOT_KEY", "")


def _allowed(host: str, key: str | None, cookie: str | None) -> bool:
    if not ACCESS_KEY or not host.split(":")[0].endswith(".ts.net"):
        return True
    return ACCESS_KEY in (key, cookie)


@app.middleware("http")
async def require_key(request: Request, call_next):
    key = request.query_params.get("key")
    # Slide images load from sandboxed iframes, which can't send the key cookie; file names are random.
    open_path = request.url.path.startswith("/media/")
    if not open_path and not _allowed(request.headers.get("host", ""), key, request.cookies.get("copilot_key")):
        return PlainTextResponse("Ask the team for the link with the access key.", status_code=401)
    response = await call_next(request)
    if key == ACCESS_KEY and ACCESS_KEY:
        response.set_cookie("copilot_key", key, max_age=86400, httponly=True, secure=True, samesite="lax")
    return response


async def ws_allowed(ws: WebSocket) -> bool:
    if _allowed(ws.headers.get("host", ""), ws.query_params.get("key"), ws.cookies.get("copilot_key")):
        return True
    await ws.close(code=4401)
    return False


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/box")
async def box():
    return FileResponse(STATIC / "box.html")


@app.get("/plans")
async def list_plans():
    return sorted(p.stem for p in (Path(__file__).parent.parent / "plans").glob("*.md"))


@app.get("/plans/{name}")
async def get_plan(name: str):
    path = Path(__file__).parent.parent / "plans" / f"{Path(name).stem}.md"
    if not path.exists():
        raise HTTPException(404)
    return {"markdown": path.read_text()}


class ImportReq(BaseModel):
    filename: str = "notes.txt"
    data_b64: str | None = None
    text: str | None = None
    minutes: float = 5


@app.post("/plan/import")
async def import_plan(req: ImportReq):
    """Deck, doc or free-form notes in, timed plan markdown out (converted by the local LLM)."""
    try:
        text = req.text or planner.extract_text(req.filename, req.data_b64 or "")
        if not text.strip():
            raise ValueError("No text found in that file.")
        md = await asyncio.to_thread(planner.to_plan, text, req.minutes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"markdown": md, "plan": parse_plan(md).to_dict()}


# ---- the speaker's own deck: render every slide on the box and pair sections with their slides ----

def _slide_texts(pdf: Path) -> list[str]:
    from pypdf import PdfReader
    return [(pg.extract_text() or "") for pg in PdfReader(str(pdf)).pages]


def _render_deck(path: Path, workdir: Path) -> tuple[list[Path], list[str]]:
    """pptx/ppt/key/odp/docx -> PDF with LibreOffice, then one JPEG per page with poppler."""
    pdf = path
    if path.suffix.lower() != ".pdf":
        subprocess.run(["soffice", "--headless", "--norestore", "-env:UserInstallation=file:///tmp/so-copilot",
                        "--convert-to", "pdf", "--outdir", str(workdir), str(path)],
                       capture_output=True, timeout=180, check=True)
        pdf = workdir / (path.stem + ".pdf")
    subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=85", "-scale-to", "1600", str(pdf), str(workdir / "slide")],
                   capture_output=True, timeout=180, check=True)
    return sorted(workdir.glob("slide-*.jpg"), key=lambda q: int(q.stem.split("-")[-1])), _slide_texts(pdf)


def _deck_plan(name: str, texts: list[str], minutes: float, objective: str) -> str:
    """One section per slide, in deck order, so the agenda and the slides line up exactly."""
    n = max(len(texts), 1)
    each = round(minutes / n, 2) or 0.1
    last = round(max(minutes - each * (n - 1), 0.1), 2)  # the close absorbs rounding so the total is exact
    out, seen = [f"# Objective: {objective}", ""], set()
    for i, t in enumerate(texts):
        lines = [re.sub(r"\s+", " ", ln).strip(" -*#") for ln in t.splitlines()]
        lines = [ln for ln in lines if ln and not re.fullmatch(r"[\d\W]+", ln)]
        title = (lines[0][:70] if lines else f"Slide {i + 1}").replace("(", "").replace(")", "")
        if title.lower() in seen:  # repeated titles would share one slide image
            title = f"{title} {i + 1}"
        seen.add(title.lower())
        must = " [must]" if i in (0, len(texts) - 1) else ""
        out.append(f"## {title} ({last if i == n - 1 else each:g} min){must}")
        out += [f"- {ln[:120]}" for ln in lines[1:4]]
        out.append("")
    return "\n".join(out)


@app.post("/deck/upload")
async def deck_upload(request: Request, filename: str = "deck.pptx", minutes: float = 6):
    """Raw deck bytes in; plan out, with each section's own slide saved as its visual."""
    from .workers.base import section_key

    raw = await request.body()
    if not raw:
        raise HTTPException(400, "empty file")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name) or "deck.pptx"
    work = DATA / "decks" / f"{time.strftime('%Y%m%d-%H%M%S')}-{Path(safe).stem}"
    work.mkdir(parents=True, exist_ok=True)
    src = work / safe
    src.write_bytes(raw)
    try:
        images, texts = await asyncio.to_thread(_render_deck, src, work)
    except Exception as e:
        raise HTTPException(400, f"couldn't render the slides: {e}") from e
    if not images:
        raise HTTPException(400, "no slides found in that file")
    deck_text = "\n\n".join(f"[Slide {i + 1}]\n{t}" for i, t in enumerate(texts))
    objective = Path(safe).stem.replace("-", " ")
    try:  # the model only writes the objective; sections come straight from the slides
        objective = parse_plan(await asyncio.to_thread(planner.to_plan, f"{objective}\n\n{deck_text}", minutes)).objective
    except Exception:
        pass
    texts = (texts + [""] * len(images))[:len(images)]
    md = _deck_plan(Path(safe).stem, texts, minutes, objective)
    plan = parse_plan(md)
    media = DATA / "media"
    media.mkdir(parents=True, exist_ok=True)
    picks = list(range(len(images)))
    deck_id = work.name
    pages = []
    for n, img in enumerate(images):  # every page, so the speaker can flip through the whole deck
        name = f"page-{deck_id}-{n + 1}.jpg"
        (media / name).write_bytes(img.read_bytes())
        pages.append(f"/media/{name}")
    matched = {}
    for sec, i in zip(plan.to_dict()["sections"], picks):
        if i < len(images):
            (media / f"deck-{section_key(sec)}.jpg").write_bytes(images[i].read_bytes())
            matched[section_key(sec)] = i
    deck = {"id": deck_id, "pages": pages, "matched": matched}
    (DATA / "deck.json").write_text(json.dumps(deck))
    return {"markdown": md, "plan": plan.to_dict(), "slides": len(images), "matched": [i + 1 for i in picks]}


@app.get("/deck/current")
async def deck_current():
    """The last uploaded deck: all pages, and which page each plan section starts on."""
    path = DATA / "deck.json"
    return json.loads(path.read_text()) if path.exists() else {"pages": [], "matched": {}}


class StartReq(BaseModel):
    plan: str
    name: str = "talk"
    client: str = ""
    device: str = ""
    force: bool = False


@app.post("/session/start")
async def start(req: StartReq):
    try:
        plan = parse_plan(req.plan)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if (cur := hub.session) and cur.owner != req.client and not req.force:
        raise HTTPException(409, f"A talk is already running on {cur.device or 'another device'}.")
    await stop_session(debrief=False)
    hub.session = s = Session(plan, req.name, req.client, req.device, library_titles())
    hub.cards, hub.stage, hub.coverage = {}, None, None
    hub.asr.start()
    s.task = asyncio.create_task(coach_loop(s))
    await hub.broadcast({"type": "start", "plan": plan.to_dict(), "name": req.name,
                         "owner": req.client, "device": req.device})
    telegram.send(
        f"Super Archilles here. Listening to {req.name}: {fmt(plan.total_s)} planned, "
        f"{len(plan.sections)} sections. I'll stay quiet unless you drift."
    )
    return {"ok": True, "plan": plan.to_dict()}


@app.post("/session/stop")
async def stop():
    report = await stop_session(debrief=True)
    return {"ok": True, "report": report}


async def stop_session(debrief: bool) -> dict | None:
    s, hub.session = hub.session, None
    if not s:
        return None
    if s.task:
        s.task.cancel()
    hub.asr.stop()
    elapsed = s.elapsed
    report = {
        "name": s.name,
        "started_at": s.started_at,
        "duration_s": elapsed,
        "planned_s": s.plan.total_s,
        "plan": s.plan.to_dict(),
        "nudges": s.coach.nudges,
        "covered": s.coach.covered,
        "library": s.coach.library,
        "transcript": " ".join(t for _, t in s.transcript),
        # for the debrief page
        "transcript_timed": [[round(t, 1), x] for t, x in s.transcript],
        "timeline": [{"t": round(v.get("elapsed_s", 0), 1), "section": v.get("section"),
                      "status": v.get("status"), "reason": v.get("reason"), "nudge": v.get("send")}
                     for v in s.coach.history],
        "section_time_s": section_times(s),
        "coverage": hub.coverage,
        "cards": [{k: c.get(k) for k in ("id", "kind", "title", "source", "status", "at_s", "reason")}
                  for c in hub.cards.values()],
    }
    if debrief:
        try:
            report["debrief"] = await asyncio.to_thread(s.coach.debrief, elapsed, s.transcript)
        except Exception as e:
            report["debrief"] = f"(debrief failed: {e})"
        telegram.send(
            report["debrief"]
        )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (DATA / "sessions").mkdir(exist_ok=True)
    report["id"] = f"{stamp}-{re.sub(r'[^A-Za-z0-9_-]+', '-', s.name)}"
    (DATA / "sessions" / f"{report['id']}.json").write_text(json.dumps(report, indent=2))
    with (DATA / "library.md").open("a") as f:
        for t in s.coach.library:
            f.write(f"## {t['title']}\n_{s.name}, {s.started_at}, at {fmt(t['at_s'])}_\n\n")
            f.write(f"{t['explanation']}\n\n")
    await hub.broadcast({"type": "stop", "report": report})
    if hub.stage:  # projector goes back to idle when the talk ends
        hub.stage = None
        await hub.broadcast({"type": "stage", "card": None})
    return report


async def coach_loop(s: Session) -> None:
    while True:
        await asyncio.sleep(TICK_S)
        await hub.broadcast({"type": "thinking", "elapsed_s": s.elapsed,
                             "transcript_chars": sum(len(t) for _, t in s.transcript)})
        try:
            v = await asyncio.to_thread(s.coach.tick, s.elapsed, list(s.transcript))
        except Exception as e:
            await hub.broadcast({"type": "error", "error": str(e)})
            continue
        if hub.session is not s:
            return
        await hub.broadcast({"type": "verdict", **v})
        if nudge := v.get("send"):
            msg = nudge
            if say := v.get("say_next"):
                msg += f'\nTry: "{say}"'
            if (rp := v.get("replan")) and rp.get("cut"):
                msg += f"\nSkip: {', '.join(rp['cut'])}"
            telegram.send(msg)
        if saved := v.get("saved"):
            telegram.send(f"Keeper. Saved \"{saved['title']}\" to your library.")


async def stats_loop() -> None:
    stats = BoxStats()
    while True:
        await asyncio.sleep(1)
        snap = await asyncio.to_thread(stats.snapshot)
        if snap["online"] and telegram.pending():
            await telegram.flush()
        snap["telegram_pending"] = telegram.pending()
        if hub.clients:
            await hub.broadcast({"type": "stats", **snap})


# ---- card bus: workers post cards, the director proposes, the speaker shows ----
# Card lifecycle: candidate -> proposed -> shown | dismissed. Workers never touch this file;
# they talk to these endpoints (see copilot/workers/base.py).

class CardIn(BaseModel):
    kind: str  # "visual" | "image" | "qa" | "note" | ...
    title: str
    body: str = ""  # plain text summary, also what the director reads
    html: str = ""  # self-contained HTML/SVG fragment for visual cards
    image: str = ""  # /media/<file> for image cards
    source: str  # worker name
    relevance: float = 0.5  # worker's own guess, 0..1
    point: str = ""  # plan point or topic it's for


class ProposeReq(BaseModel):
    score: float
    reason: str = ""


def _need_cards():
    if "cards" not in FEATURES:
        raise HTTPException(404, "cards feature is off")


@app.post("/cards")
async def post_card(card: CardIn):
    _need_cards()
    if not hub.session:
        raise HTTPException(409, "no talk running")
    c = {**card.model_dump(), "id": os.urandom(4).hex(), "status": "candidate",
         "at_s": round(hub.session.elapsed, 1), "created": time.time()}
    hub.cards[c["id"]] = c
    await hub.broadcast({"type": "card", **c})
    return c


@app.get("/cards")
async def list_cards():
    _need_cards()
    return list(hub.cards.values())


async def _card_status(card_id: str, status: str, **extra) -> dict:
    _need_cards()
    if card_id not in hub.cards:
        raise HTTPException(404, "no such card")
    c = hub.cards[card_id]
    c.update(status=status, **extra)
    await hub.broadcast({"type": "card", **c})
    return c


@app.post("/cards/{card_id}/propose")
async def propose_card(card_id: str, req: ProposeReq):
    return await _card_status(card_id, "proposed", score=req.score, reason=req.reason,
                              proposed_at=time.time())


@app.post("/cards/{card_id}/dismiss")
async def dismiss_card(card_id: str):
    return await _card_status(card_id, "dismissed")


@app.post("/cards/{card_id}/show")
async def show_card(card_id: str):
    c = await _card_status(card_id, "shown")
    hub.stage = c
    await hub.broadcast({"type": "stage", "card": c})
    return c


@app.post("/stage/clear")
async def clear_stage():
    hub.stage = None
    await hub.broadcast({"type": "stage", "card": None})
    return {"ok": True}


@app.get("/stage")
async def stage_page():
    return FileResponse(STATIC / "stage.html")


@app.get("/stage/current")
async def stage_current():
    return {"card": hub.stage}


@app.get("/static/{name}")
async def static_file(name: str):
    path = STATIC / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    # fonts are loaded by sandboxed slide iframes (opaque origin), which needs CORS
    headers = {"Access-Control-Allow-Origin": "*"} if path.suffix == ".woff2" else None
    return FileResponse(path, headers=headers)


@app.get("/media/{name}")
async def media(name: str):
    path = DATA / "media" / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# ---- topic coverage: the coverage worker scores how deeply each plan point has been covered ----

class CoveragePoint(BaseModel):
    section: int  # index into plan sections
    point: int  # index into that section's points; -1 for the section as a whole
    score: int  # 0 not yet, 1 mentioned, 2 explained, 3 landed with an example or detail
    evidence: str = ""  # short quote or paraphrase from the transcript


class CoverageIn(BaseModel):
    points: list[CoveragePoint]
    summary: str = ""  # one line, e.g. "Pricing barely touched; demo well covered"


@app.post("/coverage")
async def post_coverage(req: CoverageIn):
    if "coverage" not in FEATURES:
        raise HTTPException(404, "coverage feature is off")
    if not hub.session:
        raise HTTPException(409, "no talk running")
    hub.coverage = {**req.model_dump(), "at_s": round(hub.session.elapsed, 1)}
    await hub.broadcast({"type": "coverage", **hub.coverage})
    return {"ok": True}


@app.get("/coverage")
async def get_coverage():
    return hub.coverage or {"points": [], "summary": ""}


# ---- deck prep: load a plan and the crew prepares visuals for every section before the talk ----

class PrepareReq(BaseModel):
    plan: str
    fresh: bool = False  # redraw every section even if a cached image exists


@app.post("/plan/prepare")
async def prepare_plan(req: PrepareReq):
    _need_cards()
    try:
        plan = parse_plan(req.plan)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await hub.broadcast({"type": "prepare", "plan": plan.to_dict(), "fresh": req.fresh})
    return {"ok": True, "sections": len(plan.sections)}


class PrepProgress(BaseModel):
    text: str
    ready: int = 0
    total: int = 0


class PrepFrame(BaseModel):
    key: str
    step: int
    steps: int
    image: str  # small data: URL of the partially denoised image
    title: str = ""


@app.post("/prep/frame")
async def prep_frame(req: PrepFrame):
    """The image worker streams preview frames while it draws, so the speaker sees it paint."""
    await hub.broadcast({"type": "prepframe", **req.model_dump()})
    return {"ok": True}


@app.post("/prep/progress")
async def prep_progress(req: PrepProgress):
    await hub.broadcast({"type": "prep", **req.model_dump()})
    return {"ok": True}


# ---- debrief: past talks, keep/drop on saved tangents, save the adjusted next plan ----

def _session_path(sid: str) -> Path:
    path = DATA / "sessions" / f"{Path(sid).name}.json"
    if not path.exists():
        raise HTTPException(404, "no such talk")
    return path


@app.get("/sessions")
async def list_sessions():
    out = []
    for f in sorted((DATA / "sessions").glob("*.json"), reverse=True)[:50]:
        try:
            r = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": f.stem, "name": r.get("name"), "started_at": r.get("started_at"),
                    "duration_s": r.get("duration_s"), "planned_s": r.get("planned_s"),
                    "nudges": len(r.get("nudges") or [])})
    return out


@app.get("/sessions/{sid}")
async def get_session(sid: str):
    return json.loads(_session_path(sid).read_text())


class KeepReq(BaseModel):
    keep: bool


@app.post("/sessions/{sid}/library/{i}")
async def keep_tangent(sid: str, i: int, req: KeepReq):
    path = _session_path(sid)
    r = json.loads(path.read_text())
    if not 0 <= i < len(r.get("library") or []):
        raise HTTPException(404, "no such library entry")
    r["library"][i]["keep"] = req.keep
    path.write_text(json.dumps(r, indent=2))
    return r["library"][i]


class SavePlanReq(BaseModel):
    name: str
    markdown: str


@app.post("/plans")
async def save_plan(req: SavePlanReq):
    try:
        parse_plan(req.markdown)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    name = re.sub(r"[^a-z0-9-]+", "-", req.name.lower()).strip("-") or "plan"
    (Path(__file__).parent.parent / "plans" / f"{name}.md").write_text(req.markdown)
    return {"ok": True, "name": name}


@app.get("/debrief")
async def debrief_page():
    return FileResponse(STATIC / "debrief.html")


@app.get("/features")
async def features():
    return sorted(FEATURES)


class SayReq(BaseModel):
    text: str


@app.post("/dev/say")
async def say(req: SayReq):
    """Inject transcript text as if it had been heard. For rehearsals without a mic."""
    if not hub.session:
        raise HTTPException(409, "no talk running")
    hub.on_text(" " + req.text.strip())
    return {"ok": True}


@app.websocket("/events")
async def events(ws: WebSocket):
    if not await ws_allowed(ws):
        return
    await ws.accept()
    hub.clients.add(ws)
    if s := hub.session:
        await ws.send_text(json.dumps({"type": "start", "plan": s.plan.to_dict(), "name": s.name,
                                       "elapsed": s.elapsed, "owner": s.owner, "device": s.device}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(ws)


@app.websocket("/audio")
async def audio(ws: WebSocket):
    if not await ws_allowed(ws):
        return
    await ws.accept()
    client = ws.query_params.get("client", "")
    try:
        while True:
            data = await ws.receive_bytes()
            if hub.session and hub.session.owner == client:
                hub.asr.feed(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0)
    except WebSocketDisconnect:
        pass
