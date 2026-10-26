#!/bin/bash
# usage: worker.sh <name> [staging|live]   starts copilot.workers.<name>, pid in ~/run/<name>.pid
name=$1; target=${2:-staging}
if [ "$target" = live ]; then dir=~/copilot; url=http://127.0.0.1:8000; data=~/copilot-data
else dir=~/copilot-staging; url=http://127.0.0.1:8001; data=~/copilot-staging-data; fi
mkdir -p ~/run
cd "$dir"
COPILOT_URL=$url GATE_URL=http://127.0.0.1:8090/v1 COPILOT_DATA=$data \
  setsid nohup ~/copilot/.venv/bin/python -m copilot.workers.$name > ~/logs/worker-$name.log 2>&1 < /dev/null &
echo $! > ~/run/$name.pid
echo "started $name ($target) pid $(cat ~/run/$name.pid)"
