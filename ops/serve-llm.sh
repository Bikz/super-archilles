#!/bin/bash
# usage: serve-llm.sh <model.gguf> [extra llama-server args]
M=${1:-$HOME/models/ornith-9b/Ornith-1.5-9B-Q4_K_M.gguf}; shift
exec $HOME/llama.cpp/build/bin/llama-server -m "$M" -ngl 99 -c 131072 -np 2 -fa on --jinja   --host 127.0.0.1 --port 8080 --alias local --metrics "$@"
