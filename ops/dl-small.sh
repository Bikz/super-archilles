#!/bin/bash
exec ~/.local/bin/uv run --no-project --with httpx python ~/copilot/scripts/pdl.py ornith-ai/Ornith-1.5-9B-GGUF ~/models/ornith-9b Ornith-1.5-9B-Q4_K_M.gguf
