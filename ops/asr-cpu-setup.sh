#!/bin/bash
set -e
export UV_HTTP_TIMEOUT=30 UV_CONCURRENT_DOWNLOADS=16
~/.local/bin/uv venv --python 3.12 ~/asr-cpu
~/.local/bin/uv pip install --python ~/asr-cpu/bin/python torch --index-url https://download.pytorch.org/whl/cpu
~/.local/bin/uv pip install --python ~/asr-cpu/bin/python "transformers>=5.13.0" fastapi "uvicorn[standard]" httpx numpy
cd ~/copilot && ~/asr-cpu/bin/python -c "import torch, transformers; print('torch', torch.__version__, 'transformers', transformers.__version__)"
echo SETUP_DONE
