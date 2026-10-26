#!/bin/bash
# Bring up / re-create the Super Archilles OpenClaw sandbox on local Ornith 9B (127.0.0.1:8080 via forwarder :8095).
# Usage: ~/nemoclaw-archilles/start.sh            (no messaging)
#        WITH_TELEGRAM=1 ~/nemoclaw-archilles/start.sh   (Telegram, your team group only; stop Hermes gateway FIRST)
set -e
source ~/.nvm/nvm.sh >/dev/null
export NEMOCLAW_RECREATE_WITHOUT_BACKUP=1  # workspace is rebuilt from our files below
export NEMOCLAW_DISABLE_TELEMETRY=1 NEMOCLAW_GATEWAY_PORT=18080 NEMOCLAW_VLLM_PORT=8095
# NemoClaw refuses no-auth loopback endpoints on 8080 (its own default gateway port), so forward 8095 -> 8080.
if ! ss -ltn | grep -q "127.0.0.1:8095 "; then
  nohup python3 ~/nemoclaw-fwd.py 8095 8080 > ~/nemoclaw-fwd.log 2>&1 & echo $! > ~/nemoclaw-fwd.pid
  sleep 1
fi
if [ "${WITH_TELEGRAM:-0}" = 1 ]; then
  export TELEGRAM_BOT_TOKEN="$(grep -E "^TELEGRAM_BOT_TOKEN=" ~/copilot/.env.local | cut -d= -f2- | tr -d "\"")"
  export TELEGRAM_GROUP_POLICY=allowlist TELEGRAM_REQUIRE_MENTION=0
  TELEGRAM_GROUP="$(grep -E "^TELEGRAM_CHAT_ID=" ~/copilot/.env.local | cut -d= -f2- | tr -d "\"")"
  unset TELEGRAM_CHAT_ID TELEGRAM_ALLOWED_IDS
else
  unset TELEGRAM_BOT_TOKEN
fi
if nemoclaw list 2>/dev/null | grep -q archilles; then nemoclaw archilles destroy --yes --force || true; fi
NEMOCLAW_NO_EXPRESS=1 NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8095/v1 NEMOCLAW_MODEL=local \
NEMOCLAW_COMPATIBLE_AUTH_MODE=none NEMOCLAW_AGENT=openclaw NEMOCLAW_SANDBOX_NAME=archilles NEMOCLAW_POLICY_MODE=suggested \
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_NON_INTERACTIVE=1 \
nemoclaw onboard --name archilles --non-interactive --no-gpu \
  --host-mount /home/dell/copilot-data:/sandbox/copilot-data --host-mount /home/dell/copilot/plans:/sandbox/copilot/plans </dev/null
N="nemoclaw archilles"
$N exec -- mkdir -p /sandbox/.openclaw/workspace/skills/talk-copilot
$N upload ~/nemoclaw-archilles/skills/talk-copilot/SKILL.md /sandbox/.openclaw/workspace/skills/talk-copilot/
cp ~/copilot/scripts/talkctl.py ~/nemoclaw-archilles/skills/talk-copilot/talkctl.py
$N upload ~/nemoclaw-archilles/skills/talk-copilot/talkctl.py /sandbox/.openclaw/workspace/skills/talk-copilot/
$N upload ~/nemoclaw-archilles/SOUL.md /sandbox/.openclaw/workspace/
if [ "${WITH_TELEGRAM:-0}" = 1 ]; then
  # Restrict to the team group (UNTESTED: written from the OpenClaw schema, verify replies only land in your group).
  $N exec -- openclaw config set channels.telegram.groupPolicy allowlist
  $N exec -- openclaw config set channels.telegram.groups "{\"$TELEGRAM_GROUP\":{\"requireMention\":false}}" --strict-json
  $N gateway restart
fi
$N status | head -20
