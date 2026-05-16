#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

YUME_VOICE_PORT="${YUME_VOICE_PORT:-7421}"
YUME_AGENT_PORT="${YUME_AGENT_PORT:-7422}"

start_service() {
  local name="$1"
  local module="$2"
  local port="$3"
  local log_file="logs/${name}.log"
  local pid_file="logs/${name}.pid"

  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "${name} already listening on ${port}"
    return
  fi

  nohup .venv/bin/python -m "$module" >"$log_file" 2>&1 &
  echo "$!" >"$pid_file"
  echo "started ${name} pid=$(cat "$pid_file") log=${log_file}"
}

start_service voice_service voice_service "$YUME_VOICE_PORT"
start_service agent_service agent_service "$YUME_AGENT_PORT"

sleep 1

lsof -nP -iTCP:"$YUME_VOICE_PORT" -sTCP:LISTEN >/dev/null 2>&1 \
  && echo "voice_service ready on ${YUME_VOICE_PORT}" \
  || { echo "voice_service did not start; see logs/voice_service.log" >&2; exit 1; }

lsof -nP -iTCP:"$YUME_AGENT_PORT" -sTCP:LISTEN >/dev/null 2>&1 \
  && echo "agent_service ready on ${YUME_AGENT_PORT}" \
  || { echo "agent_service did not start; see logs/agent_service.log" >&2; exit 1; }
