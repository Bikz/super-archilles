#!/bin/bash
export CONNS=6
exec ~/.local/bin/uv run --no-project --with httpx python ~/copilot/scripts/pdl.py nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr-GGUF ~/models/nemotron-bolt NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr.gguf
