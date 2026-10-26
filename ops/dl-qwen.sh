#!/bin/bash
cd ~/models/qwen3.8-flash-next
export CONNS=8
exec ~/.local/bin/uv run --no-project --with httpx python ~/copilot/scripts/pdl.py unsloth/Qwen3.8-Flash-Next-GGUF ~/models/qwen3.8-flash-next UD-IQ4_XS/Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf UD-IQ4_XS/Qwen3.8-Flash-Next-UD-IQ4_XS-00002-of-00003.gguf UD-IQ4_XS/Qwen3.8-Flash-Next-UD-IQ4_XS-00003-of-00003.gguf
