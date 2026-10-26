# Submission draft: Super Archilles

In the order of the form. Three fields need a link from you: repository, demo video, pitch deck.

## One-line pitch
Super Archilles is a private AI stage manager for anyone presenting or running a high-stakes conversation (lecturers, sales teams, executives, legal and board rooms): it listens live on the Dell Pro Max GB10, coaches the speaker in under a second when they drift or fall behind, and hands them a debrief at the end, with no audio or text ever leaving the room.

## Repository URL
https://github.com/bikz/super-archilles

## Demo video URL
TODO (under 2 minutes, on the GB10).

## What you built, and what runs locally
Super Archilles is an always-on copilot for talks and meetings that cannot go to a cloud API: lectures, sales calls, attorney-client consults, board meetings.

Workflow:
- **Before:** the speaker picks a plan or uploads their own deck (pptx, pdf, key, odp) or notes. The box renders the deck into slides with LibreOffice and poppler and builds a timed agenda, one section per slide, with must-cover sections. For plans without a deck, Z-Image-Turbo draws a visual for each section on the GPU, and the speaker can watch it paint live.
- **During:** the laptop or phone mic streams audio to the GB10. Nemotron 3.5 ASR transcribes it on the GPU about a second behind speech. Every 3 seconds the coach (Nemotron 3.5 Bolt 74B) checks the transcript against the plan and the clock: on track, valuable tangent, drifting, or behind. When needed, it sends a short cue plus a line the speaker can say word for word, to the speaker view and to Telegram, usually 0.2 to 1 s after the moment. The speaker flips their own slides; the crew stays in the side panel.
- **A crew of background agents works in the gaps:**
  - instant: puts the section visual on screen 0.3 s after the speaker reaches it, and turns spoken numbers into stat slides in about 4 s
  - coverage: scores every plan point as covered or missed
  - Q&A: anticipates the next audience question with a grounded answer
  - visual: drafts charts and diagrams
  - director: decides which drafts deserve the speaker's attention
  - image: draws section illustrations
- **Priority gate:** we wrote a small proxy in front of the models so the live coach always preempts background work. Cues stay under a second while the GPU runs near 90%.
- **After:** an instant wrap-up (time vs plan, covered and missed sections, one thing to change next time) and a full debrief page. The NemoClaw agent (OpenClaw in an OpenShell sandbox) answers questions about past talks on Telegram and preps the next one.

Tools the agents use:
- the live transcript stream, the plan and the clock
- a card bus (candidate, proposed, shown)
- Telegram send
- the talk history (talkctl, run by OpenClaw inside its sandbox, read-only)
- deck rendering and parsing
- the image model

All inference, both reasoning and tool selection, runs on the GB10: speech to text, the coach, the director, every worker, image generation and the NemoClaw agent, which points at our local llama.cpp server. There are no cloud LLM calls and no cloud API keys on the box. The only outbound traffic is short Telegram text messages. If the network drops, coaching carries on and the messages wait. The console view (`/box`) shows every model, GPU load, memory and bytes leaving the box, live.

## Walk us through getting started with the DGX Spark
- **Out of the box:** DGX OS 7 (Ubuntu 24.04, aarch64), CUDA 13, Docker installed. nvidia-smi and unified memory were easy to work with once we knew to read memory from /proc/meminfo, since the GPU shares the 128 GB.
- **Remote access was the first hurdle.** The venue Wi-Fi isolates clients, so our Mac could not reach the box even on the same network. Tailscale fixed it in minutes: SSH first, then a Funnel HTTPS link so teammates' laptops and phones could use the mic (browsers only allow the mic over HTTPS).
- **Setup gaps:** the default user is not in the docker group, which needed a sudo step at the keyboard. uv was not preinstalled; we installed it in user space.
- **Model downloads were the biggest time sink.** On congested Wi-Fi the Hugging Face Xet client stalled repeatedly, so we wrote a resumable 16-connection range downloader. The 3.3 GB aarch64 PyTorch CUDA wheels also took a long time; we ran ASR on the 20-core Grace CPU as a stopgap until they landed.
- **Builds and recipes worked:** llama.cpp built cleanly from source with CUDA for sm_121 in a few minutes. NVIDIA's model card recipe for Nemotron 3.5 Bolt on DGX Spark (DFlash speculative decoding, q8 KV cache) worked as written.
- **Recovery was easy:** we had to unplug the box and change rooms mid-day. One start-all script brought every model, worker and the agent back.
- **What impressed us:** five models live at once (a 74B MoE coach, a 9B worker model, streaming ASR, a 6B diffusion model and a TTS model we tested) in about 95 GB of unified memory, with the coach still answering in under a second.

## Pitch deck
TODO (public link).

## Which resources helped most, and what do you wish you'd had access to?
Helped most:
- NVIDIA's DGX Spark model card recipes, especially the Nemotron 3.5 Bolt llama.cpp recipe with DFlash speculative decoding
- the build.nvidia.com Spark playbooks for orientation
- Hugging Face model cards with GGUF builds
- NVIDIA developer forum threads with real Spark benchmarks (for example Z-Image timings), which told us what would be fast enough for live use
- the Artificial Analysis leaderboards for picking a TTS model

Wished for:
- a local model mirror or a pre-staged model cache at the venue
- a wired network, or a Wi-Fi network without client isolation, for the boxes
- one page on reaching the Spark from a laptop on restricted Wi-Fi

## What would have got you there faster?
- popular models pre-downloaded on the box or on a LAN mirror (the Nemotron family, a Qwen, an image model)
- aarch64 PyTorch CUDA wheels preinstalled or cached
- the default user already in the docker group
- a note in the setup guide about client isolation and Tailscale

We lost roughly two hours to downloads and access.

## What model(s) did you move forward with, and for what?
- **NVIDIA Nemotron 3.5 Bolt 74B-A9B** (24 GB, 2-bit QAT GGUF, with the LiLiCorr DFlash drafter): the live coach, the director, the visual and Q&A workers, deck and plan conversion, and the debrief.
- **Ornith 1.5 9B** (Q4_K_M, 5.8 GB): fast side work, such as number slides and image prompts, and the NemoClaw/OpenClaw Telegram agent.
- **NVIDIA Nemotron 3.5 ASR streaming 0.6B:** real-time speech to text on the GPU.
- **Z-Image-Turbo 6B** (bf16, diffusers): section illustrations, about 14 s each at 1344x768.
- **Ming-3.0-tts 0.5B**, with Kokoro-82M as a CPU fallback: tested for a spoken sales coaching mode. It is not in the live talk flow yet.

## What drove your model choices?
- **Bandwidth over compute:** memory bandwidth (273 GB/s) limits generation speed on the GB10, so we chose a mixture of experts with few active parameters. Bolt reads about 9B of its 74B per token.
- **A fleet, not one giant model:** we wanted several agents in parallel plus ASR and an image model. Bolt's 24 GB footprint left room for all of them; a 180B model would have taken the whole box.
- **Live latency:** a coaching cue is only useful within a second or two. Bolt with speculative decoding behind our priority gate answers in 0.2 to 1 s. It also judged better than the 9B: it stopped flagging on-track speech as drift and told good analogies from rambling.
- **Streaming ASR:** a cache-aware model keeps the transcript about a second behind speech.
- **A turbo (few-step) image model:** 50-step models took about 54 s per image on the Spark; Z-Image-Turbo takes about 14 s.
- **Voice quality first for TTS:** we picked the highest-ranked small open-weights model on the Artificial Analysis leaderboard that fits alongside everything else.

## How did you get set up with your models?
- **llama.cpp:** built from source with CUDA (sm_121). GGUFs came from Hugging Face through our resumable parallel downloader. Bolt is served per NVIDIA's DGX Spark recipe (DFlash speculative decoding, q8 KV cache, flash attention, 131k context, 2 slots), with the 9B on a second port.
- **Priority gate:** an OpenAI-compatible proxy in front of both servers, so the live coach preempts background requests.
- **ASR:** Nemotron via Hugging Face transformers on PyTorch CUDA 13 (aarch64 wheels), streaming in-process.
- **Image model:** Z-Image-Turbo via diffusers in its own worker process, with preview frames streamed while it draws.
- **TTS:** Ming-3.0-tts in its own uv environment.
- **NemoClaw:** installed with NVIDIA's script and onboarded non-interactively with a custom provider pointing at our llama.cpp server, our talk data mounted read-only, a custom Super Archilles skill, and Telegram limited to our team group.

## Did you use NVIDIA NemoClaw and/or OpenShell today?
Yes. NemoClaw (OpenClaw inside an OpenShell sandbox) runs Super Archilles' Telegram agent on the GB10, pointed at our local llama.cpp server. Through a custom skill it answers questions about past talks and coverage and helps prep the next one, entirely on the box. We started the day on Hermes Agent and switched. On the same local 9B model, OpenClaw's tool calls were reliable and its answers more faithful: it quoted saved material word for word where Hermes paraphrased it.

## If you used NemoClaw and/or OpenShell, how likely are you to recommend it? (1 to 10)
6

## Please share why or provide additional feedback.
What was good:
- no sudo needed and a clear CLI
- read-only host mounts
- docs a coding agent can follow
- once configured, a fresh onboard took about 30 seconds and made correct tool calls on a 9B local model

Friction on a box that was already serving models:
- the OpenShell gateway defaults to port 8080, where most people run llama.cpp
- local endpoints without auth are only accepted on ports 8000 and 11434, so we wrote a port forwarder
- Express mode wants to download a 35B model, and the llama.cpp path assumes port 8081
- after we moved the box and rebooted, recreating the sandbox failed on the workspace backup and the gateway was unreachable on destroy; we needed `--force` and `NEMOCLAW_RECREATE_WITHOUT_BACKUP=1`
- Telegram has no single-group restriction by default
- the installer edits .bashrc and installs its own Node
- telemetry is on by default

About 27 minutes from install to a working agent, most of it on those workarounds.

## If you used NemoClaw or OpenShell, how secure did you feel? (1 to 10)
8

## What influenced your rating?
What raised it:
- the agent runs as a separate user with Landlock rules inside a container
- our talk data was mounted read-only, and the sandbox could not write to it
- outbound network is denied by default (public sites and our host's other ports were blocked)
- the inference credential stays outside the sandbox

What cost points:
- a NemoClaw token proxy listens on all interfaces (token protected)
- telemetry is on by default
- the default Telegram group policy lets any group through
- the installer changes your shell configuration

---
Stack: NVIDIA NemoClaw with OpenClaw in an OpenShell sandbox (agent harness and Telegram channel), llama.cpp, Hugging Face transformers and diffusers, FastAPI, LibreOffice and poppler for decks, Tailscale for access. Models: Nemotron 3.5 Bolt 74B-A9B, Ornith 1.5 9B, Nemotron 3.5 ASR streaming 0.6B, Z-Image-Turbo 6B, Ming-3.0-tts 0.5B. All inference on the GB10.
