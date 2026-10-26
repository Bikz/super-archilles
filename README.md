# Super Archilles

A private AI stage manager for talks and meetings, built for the Dell Pro Max GB10 (DGX Spark) hackathon.
It listens live, coaches the speaker in under a second when they drift or fall behind, puts the right visual
on screen, and writes a debrief at the end. Every model runs on the GB10: no cloud LLM calls, and no audio
or text leaves the room.

## What runs on the box

| Model | Role | Served by |
| --- | --- | --- |
| Nemotron 3.5 Bolt 74B-A9B (2-bit GGUF, DFlash drafter) | live coach, director, visual and Q&A workers, plans, debrief | llama.cpp |
| Ornith 1.5 9B (Q4_K_M) | number slides, image prompts, NemoClaw Telegram agent | llama.cpp |
| Nemotron 3.5 ASR streaming 0.6B | real-time speech to text | transformers, in-process |
| Z-Image-Turbo 6B | section illustrations, streamed while drawing | diffusers worker |
| Ming-3.0-tts 0.5B, Kokoro-82M fallback | voice for a spoken coaching mode (tested) | `scripts/tts.py` |

## How it fits together

- `copilot/server.py`: FastAPI app. Speaker view (`/`), stage view (`/stage`), box console (`/box`), debrief
  (`/debrief`), mic audio over a WebSocket, deck upload, and the event bus the workers follow.
- `copilot/coach.py`: every 3 seconds, checks the transcript against the plan and the clock, and writes a cue
  plus a line the speaker can say.
- `copilot/gate.py`: OpenAI-compatible priority proxy in front of both llama.cpp servers. Coach requests
  preempt background work, so cues stay fast while the GPU is busy.
- `copilot/workers/`: the background crew (instant, coverage, qa, visual, director, image). Each follows the
  live events and proposes cards.
- `copilot/planner.py`: turns decks, docs and notes into a timed plan. Uploaded decks are rendered to slides on
  the box with LibreOffice and poppler, one agenda section per slide.
- `nemoclaw/`: NemoClaw setup (OpenClaw in an OpenShell sandbox) for the Telegram agent, with a read-only
  `talkctl` skill over past talks.
- `ops/`: launch scripts for the models, gate, server and workers; `ops/start-all.sh` brings everything back
  after a reboot.
- `plans/`: sample talk plans (pitch, lecture, legal consult, board meeting, sales kickoff, keynote).

## Running it

On the GB10, with llama.cpp built for CUDA and the models downloaded (`ops/dl-*.sh`):

```bash
cp .env.example .env.local   # Telegram bot token and chat id are optional
ops/start-all.sh
```

Then open `http://<box>:8000`. The mic needs HTTPS or localhost; we used a Tailscale Funnel link with
`COPILOT_KEY` as the access key.

See `docs/demo-script.md` for a walkthrough and `docs/submission.md` for the full write-up.
