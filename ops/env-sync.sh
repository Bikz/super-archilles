#!/bin/bash
cd ~/copilot
export UV_HTTP_TIMEOUT=30 UV_CONCURRENT_DOWNLOADS=16
for i in 1 2 3 4 5; do ~/.local/bin/uv sync && break; echo retry $i; done
