# Submission: Super Archilles

## One-line pitch
A private AI stage manager for sales teams, trainers and executives. It runs five models on the Dell Pro Max GB10 and coaches the speaker live, in under a second, so every must-cover point lands on time, with no audio or transcript ever leaving the room.

## Repository URL
https://github.com/Bikz/super-archilles

## Demo video URL
TODO

## What you built, and what runs locally
**The problem:** reps and presenters drift, run long and skip the points that close deals. Call-coaching tools upload the recording to the cloud, which legal, board and many enterprise rooms won't allow.

**The agent loop:**
1. Mic audio streams over a WebSocket to **Nemotron 3.5 ASR streaming 0.6B** on the GPU, which keeps the transcript about 1 s behind speech.
2. Every 3 s, the coach (**Nemotron 3.5 Bolt 74B-A9B**) reads the transcript, the plan and the clock. It classifies the moment as on track, valuable tangent, drifting or behind, and returns structured JSON: a cue, an exact line to say, and what to cut.
3. Cues arrive in **0.2 to 1 s** on the speaker view and Telegram. A repeat filter stops the coach from nagging.
4. Six tool-using workers run in parallel on a shared event bus. Each proposes cards (candidate, then proposed, then shown):
   - instant: section slide 0.3 s after the speaker reaches it, and number slides in about 4 s
   - coverage: scores every must-cover point
   - Q&A: anticipates the next audience question with a grounded answer
   - visual: charts and diagrams
   - director: lets only cards scoring 0.75 or higher through
   - image: Z-Image-Turbo illustrations, streamed while they paint
5. **Priority gate:** a proxy we wrote in front of llama.cpp. Live coaching preempts background requests (cancel and retry), so cues stay sub-second with the GPU at 90%.
6. **After the talk:** a debrief with time per section, covered and missed points, and the next plan. The NemoClaw agent answers follow-up questions on Telegram.

**100% local:** every inference call (ASR, reasoning, tool selection, image, TTS) runs on the GB10, about 95 GB of the 128 GB in use. There are no cloud LLM calls and no API keys. The only outbound traffic is Telegram text, and the `/box` console shows GPU, memory and bytes out live.

**Who pays:** sales enablement and L&D leaders, priced per seat or per box. It measures the outcomes they care about: on-time finish, must-cover points hit, and time per section, for every talk.

## Walk us through getting started with the DGX Spark
- **Hardware:** GB10 Grace Blackwell, 128 GB of unified memory (121 GiB usable), 273 GB/s, a 20-core Grace CPU, DGX OS 7 (Ubuntu 24.04 aarch64), CUDA 13.
- **What went smoothly:** llama.cpp built from source for sm_121 in minutes. NVIDIA's Nemotron Bolt Spark recipe (DFlash speculative decoding, q8 KV cache) worked as written.
- **Hurdles:**
  - Venue Wi-Fi isolated clients; we used Tailscale, plus a Funnel HTTPS link for phone mics.
  - Hugging Face Xet downloads stalled; we wrote a 16-connection resumable downloader.
  - The aarch64 PyTorch CUDA wheels were slow to arrive.
- **Highlight:** unified memory let five models run together. We unplugged the box and moved rooms mid-day, and one `start-all.sh` brought every service back.

## Pitch deck
TODO

## Which resources helped most, and what do you wish you'd had access to?
Helped most: NVIDIA's DGX Spark model card recipes (Bolt with DFlash on llama.cpp), the build.nvidia.com playbooks, and forum threads with real Spark benchmarks.
Wished for: models pre-cached on the box or a LAN mirror, aarch64 CUDA wheels preinstalled, and Wi-Fi without client isolation.

## What would have got you there faster?
Pre-staged Nemotron weights and PyTorch CUDA wheels on the box, plus a one-page guide to remote access. Downloads and network access cost us about two hours.

## What model(s) did you move forward with, and for what?
- **Nemotron 3.5 Bolt 74B-A9B (MoE):**
  - Format: 2-bit QAT GGUF (24 GB), LiLiCorr DFlash drafter, q8 KV cache, 131k context, 2 slots, on llama.cpp.
  - Role: live coach, director, Q&A and visual agents, plan building, debrief.
- **Nemotron 3.5 ASR streaming 0.6B:**
  - Format: cache-aware streaming, in-process on the GPU.
  - Role: live transcript, about 1 s behind speech.
- **Ornith 1.5 9B:**
  - Format: Q4_K_M (5.8 GB), 131k context.
  - Role: fast side tasks and the NemoClaw Telegram agent.
- **Z-Image-Turbo 6B:**
  - Format: bf16, diffusers.
  - Role: 1344x768 slide images in about 14 s, with a live preview.
- **Ming-3.0-tts 0.5B** (Artificial Analysis open-weights #3), with **Kokoro-82M** as a CPU fallback:
  - Format: about 0.6 s of compute per second of audio.
  - Role: voice for spoken sales coaching (tested).

## What drove your model choices?
- **Bandwidth, not compute:** token speed on the GB10 is bound by its 273 GB/s of memory bandwidth. Bolt is a MoE that reads only 9B of its 74B parameters per token, so we get 74B-level judgment at sub-second latency. In our tests it beat the 9B at telling a useful tangent from drift.
- **Room for the rest:** at 24 GB, Bolt leaves room for ASR, a 9B model, image and TTS models in parallel.
- **Streaming ASR:** keeps the coach about 1 s behind speech.
- **A turbo image model:** few-step diffusion takes about 14 s per image, where 50-step models took about 54 s.

## How did you get set up with your models?
- **llama.cpp:** built from source with CUDA for sm_121. Bolt follows NVIDIA's Spark recipe (DFlash speculative decoding, flash attention, q8 KV) on :8081, and the 9B is on :8080.
- **Priority gate:** an OpenAI-compatible proxy we wrote (:8090). The coach preempts background work.
- **ASR and images:** Nemotron ASR via transformers and Z-Image via diffusers, both on PyTorch CUDA 13 aarch64. TTS runs in its own uv environment.
- **NemoClaw:** onboarded with a custom provider pointing at our local llama.cpp server, with talk data mounted read-only.

## Did you use NVIDIA NemoClaw and/or OpenShell today?
Yes

## How likely are you to recommend it? (1 to 10)
6

## Please share why or provide additional feedback.
Strong sandboxing, read-only mounts, and reliable tool calls on a local 9B model. On the same model it beat Hermes Agent: it quoted saved material word for word where Hermes paraphrased. The friction was default ports that collide with an existing llama.cpp server (8080 and 8081), a sandbox recreate that failed after a reboot (we needed `--force`), and telemetry on by default.

## How secure did you feel building with it? (1 to 10)
8

## What influenced your rating?
The agent runs as a separate user in a Landlock container. Our data is mounted read-only, outbound network is denied by default, and the inference credentials stay outside the sandbox. We took points off for a token proxy listening on all interfaces, an open default Telegram group policy and installer changes to the shell config.
