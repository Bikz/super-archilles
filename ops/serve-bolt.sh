#!/bin/bash
# Nemotron 3.5 Bolt 74B-A9B (22 GB, 2-bit QAT) on :8081, per NVIDIA's DGX Spark recipe:
# vision projector, DFlash speculative decoding with the LiLiCorr drafter, q8 KV cache.
# --parallel 2 so the coach and one background worker can each hold a slot.
M=~/models/nemotron-bolt
exec ~/llama.cpp/build/bin/llama-server \
  --model $M/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf \
  --mmproj $M/mmproj-NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf \
  --spec-type draft-dflash --spec-draft-n-max 6 \
  --spec-draft-model $M/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr.gguf --spec-draft-ngl 999 \
  --batch-size 2048 --ubatch-size 512 --ctx-size 131072 --parallel 2 -ngl 999 \
  --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on --jinja \
  --host 127.0.0.1 --port 8081 --alias local --metrics
