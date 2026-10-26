#!/bin/bash
# Bring the whole Super Archilles stack back after a reboot or move. Safe to re-run:
# anything already up is left alone. Log: ~/logs/start-all.log
mkdir -p ~/logs ~/run
up() { curl -sf "$1" -o /dev/null; }
wait_for() { for i in $(seq 1 ${3:-90}); do up "$2" && { echo "  $1 up"; return 0; }; sleep 2; done; echo "  $1 NOT up"; return 1; }
running() { ps -eo args | grep -q "$1"; }

echo "== models"
up http://127.0.0.1:8080/health || setsid nohup ~/serve-llm.sh > ~/logs/llm.log 2>&1 < /dev/null &
up http://127.0.0.1:8081/health || setsid nohup ~/serve-bolt.sh > ~/logs/bolt.log 2>&1 < /dev/null &
wait_for "Ornith 9B (:8080)" http://127.0.0.1:8080/health
wait_for "Nemotron Bolt (:8081)" http://127.0.0.1:8081/health 150

echo "== gate and live server"
up http://127.0.0.1:8090/gate/stats || setsid nohup ~/serve-gate.sh > ~/logs/gate.log 2>&1 < /dev/null &
wait_for "gate (:8090)" http://127.0.0.1:8090/gate/stats
up http://127.0.0.1:8000/features || setsid nohup ~/serve-copilot.sh > ~/logs/copilot.log 2>&1 < /dev/null &
wait_for "Super Archilles (:8000)" http://127.0.0.1:8000/features 120

echo "== workers"
for w in instant coverage qa visual director image; do
  running "[c]opilot.workers.$w\$" || running "[c]opilot.workers.$w " || ~/worker.sh $w live
done

echo "== NemoClaw Telegram agent"
source ~/.nvm/nvm.sh >/dev/null 2>&1
if docker ps --format "{{.Names}}" | grep -q archilles; then echo "  sandbox already running"
else
  WITH_TELEGRAM=1 timeout 600 ~/nemoclaw-archilles/start.sh > ~/logs/nemoclaw-start.log 2>&1
  for i in 1 2 3; do timeout 150 nemoclaw archilles gateway restart > ~/logs/nemoclaw-gw.log 2>&1 && break; sleep 20; done
  docker ps --format "{{.Names}}" | grep -q archilles && echo "  sandbox up" || echo "  sandbox NOT up (see ~/logs/nemoclaw-start.log)"
fi

echo "== console on the monitor"
export DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
setsid nohup firefox --new-window http://localhost:8000/box >/dev/null 2>&1 < /dev/null &
echo "== done $(date +%H:%M:%S)"
