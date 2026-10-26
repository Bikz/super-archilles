#!/bin/bash
source ~/.nvm/nvm.sh >/dev/null
P1="How did my last talk go, and do I usually run over?"; P2="What good explanations have I saved?"
for r in 1 2 3; do for p in "$P1" "$P2"; do
  s=$(date +%s.%N); timeout 200 nemoclaw archilles agent --agent main --session-id b$r$RANDOM --timeout 180 --json -m "$p" 2>/dev/null > /tmp/oc.json; e=$(date +%s.%N)
  python3 ~/nemoclaw-archilles/parse.py "$p" "$s" "$e"
  s=$(date +%s); out=$(cd ~ && timeout 150 ~/.local/bin/hermes -z "$p" 2>&1 | tail -15 | tr "\n" " "); echo "HERMES ${p:0:20} $(( $(date +%s)-s ))s :: ${out:0:600}"
done; done
