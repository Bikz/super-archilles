# Submission: Super Archilles

## One-line pitch
A private AI stage manager that listens to live talks, sales calls and board meetings on the GB10, coaches the speaker in under a second when they drift or run long, and writes the debrief, with no audio ever leaving the room.

## Repository URL
https://github.com/Bikz/super-archilles

## Demo video URL
TODO

## What you built, and what runs locally
Mic audio streams to the GB10. Nemotron ASR transcribes it live, and every 3 seconds a Nemotron 3.5 Bolt 74B coach checks the speaker against their plan and clock. When they drift or fall behind, it sends a cue and a line to say, to the speaker view and Telegram, in 0.2 to 1 s.
- **Background agents:** six of them prepare visuals, track coverage of must-hit points, anticipate audience questions and draw images (Z-Image-Turbo).
- **Priority gate:** a proxy we wrote lets the live coach preempt all background work, so cues stay fast with the GPU at 90%.
- **Telegram agent:** NemoClaw (OpenClaw in an OpenShell sandbox) answers questions about past talks.
- **Your own deck:** uploaded slides are rendered on the box.

**100% local:** five models on one GB10 using about 95 GB. All reasoning and tool selection run on the box, with no cloud LLM calls and no API keys. The only outbound traffic is Telegram text, and the console shows bytes leaving the box live.

## Walk us through getting started with the DGX Spark
- DGX OS, CUDA 13 and Docker worked out of the box. llama.cpp built for sm_121 in minutes, and NVIDIA's Nemotron Bolt Spark recipe (DFlash speculative decoding) worked as written.
- **Hurdles:**
  - Venue Wi-Fi blocked laptop-to-box traffic; we used Tailscale.
  - Hugging Face downloads stalled; we wrote a 16-connection downloader.
  - The aarch64 PyTorch CUDA wheels were slow to arrive.
- **Highlight:** 128 GB of unified memory let us run a 74B MoE, a 9B model, streaming ASR, a 6B image model and TTS at the same time.

## Pitch deck
TODO

## Which resources helped most, and what do you wish you'd had access to?
Helped most: NVIDIA's DGX Spark model card recipes, the build.nvidia.com playbooks, and forum threads with real Spark benchmarks.
Wished for: models pre-cached on the box or a LAN mirror, and Wi-Fi without client isolation.

## What would have got you there faster?
Pre-downloaded Nemotron models and aarch64 PyTorch CUDA wheels, plus a setup note on remote access. Downloads and access cost us about two hours.

## What model(s) did you move forward with, and for what?
- **Nemotron 3.5 Bolt 74B-A9B** (2-bit GGUF and DFlash drafter): live coach, agents, plans, debrief
- **Nemotron 3.5 ASR streaming 0.6B:** live speech to text
- **Ornith 1.5 9B:** fast side tasks and the NemoClaw agent
- **Z-Image-Turbo 6B:** slide images (about 14 s each)
- **Ming-3.0-tts 0.5B:** spoken coaching voice (tested)

## What drove your model choices?
Decode on the GB10 is limited by memory bandwidth, so we picked a MoE: Bolt reads only 9B of its 74B per token. That gives sub-second coaching with 74B-level judgment, and its 24 GB footprint leaves room for four more models. Streaming ASR keeps the transcript about a second behind speech, and a turbo image model takes 14 s instead of 54 s.

## How did you get set up with your models?
- **llama.cpp:** built from source for sm_121, serving Bolt (NVIDIA's Spark recipe) and the 9B.
- **Priority gate:** a proxy we wrote, in front of both.
- **ASR and images:** Nemotron ASR through transformers and Z-Image through diffusers, on CUDA 13.
- **NemoClaw:** onboarded with a custom provider pointing at our local llama.cpp server.

## Did you use NVIDIA NemoClaw and/or OpenShell today?
Yes

## How likely are you to recommend it? (1 to 10)
6

## Please share why or provide additional feedback.
The sandboxing and read-only mounts are excellent, and tool calls were reliable on a local 9B model. In head-to-head tests on the same model, it beat Hermes Agent. The friction was port conflicts with an existing llama.cpp server (8080 and 8081 assumed), a failed sandbox recreate after a reboot (we needed `--force`), and telemetry on by default.

## How secure did you feel building with it? (1 to 10)
8

## What influenced your rating?
The agent runs as a separate user in a Landlock container. Our data was mounted read-only, outbound network is denied by default, and credentials stay outside the sandbox. We took points off for a token proxy on all interfaces, an open default Telegram group policy and installer changes to the shell config.
