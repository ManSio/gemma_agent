#!/usr/bin/env bash
# HTTP API (uvicorn api:app) — отдельный процесс от Telegram-бота.
#
#   bash scripts/gemma_api.sh start|stop|restart|status|health
#
# Env: API_ENABLED, API_HOST, API_PORT, API_TOKEN — в $BOT_DIR/.env
set -o pipefail
set -u

BOT_DIR="${GEMMA_BOT_DIR:-/opt/gemma_agent}"
ENV_FILE="${GEMMA_BOT_ENV_FILE:-$BOT_DIR/.env}"
VENV_PY="$BOT_DIR/venv/bin/python3"
PID_FILE="$BOT_DIR/gemma_api.pid"
NOHUP_LOG="${GEMMA_API_NOHUP_LOG:-$BOT_DIR/panel_nohup_api.log}"

_api_env() {
  API_HOST="${API_HOST:-127.0.0.1}"
  API_PORT="${API_PORT:-8000}"
  API_ENABLED="${API_ENABLED:-false}"
  [[ -f "$ENV_FILE" ]] || return 0
  if [[ -x "$VENV_PY" ]]; then
    while IFS= read -r export_line; do
      [[ -n "$export_line" ]] || continue
      # shellcheck disable=SC1090
      eval "$export_line"
    done < <(
      "$VENV_PY" - "$ENV_FILE" <<'PY'
import shlex
import sys
from pathlib import Path

from dotenv import dotenv_values

path = Path(sys.argv[1])
for key, val in dotenv_values(path).items():
    if val is None:
        continue
    print(f"export {key}={shlex.quote(str(val))}")
PY
    )
    return 0
  fi
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE" || true
  set +a
}

_is_enabled() {
  _api_env
  case "${API_ENABLED:-false}" in
    1 | true | yes | on | TRUE | YES | ON) return 0 ;;
    *) return 1 ;;
  esac
}

_pid_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

cmd_status() {
  _api_env
  echo "API_ENABLED=${API_ENABLED:-false} bind=${API_HOST}:${API_PORT}"
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(tr -d ' \n\r' <"$PID_FILE" 2>/dev/null || true)"
    if _pid_running "$pid"; then
      echo "status: running pid=$pid"
      return 0
    fi
    echo "status: stale pid file ($pid)"
    return 1
  fi
  echo "status: stopped"
  return 1
}

cmd_health() {
  _api_env
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://${API_HOST}:${API_PORT}/api/v1/health" 2>/dev/null || echo 000)"
  echo "health http://${API_HOST}:${API_PORT}/api/v1/health -> $code"
  [[ "$code" == "200" ]]
}

cmd_start() {
  if ! _is_enabled; then
    echo "API_ENABLED не true — старт пропущен (см. .env)"
    return 0
  fi
  _api_env
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(tr -d ' \n\r' <"$PID_FILE" 2>/dev/null || true)"
    if _pid_running "$pid"; then
      echo "API уже запущен pid=$pid"
      return 0
    fi
    rm -f "$PID_FILE"
  fi
  if [[ ! -x "$VENV_PY" ]]; then
    echo "Нет venv: $VENV_PY" >&2
    return 1
  fi
  cd "$BOT_DIR" || exit 1
  nohup "$VENV_PY" -m uvicorn api:app --host "$API_HOST" --port "$API_PORT" \
    >>"$NOHUP_LOG" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 1
  cmd_status
  cmd_health || true
}

cmd_stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    local orphan
    orphan="$(pgrep -f "uvicorn api:app" 2>/dev/null | head -n1)" || true
    if [[ -n "$orphan" ]]; then
      echo "Останавливаю чужой uvicorn api:app pid=$orphan (нет $PID_FILE)"
      kill "$orphan" 2>/dev/null || true
      sleep 1
      kill -9 "$orphan" 2>/dev/null || true
    else
      echo "API не запущен (нет pid)"
    fi
    return 0
  fi
  local pid
  pid="$(tr -d ' \n\r' <"$PID_FILE" 2>/dev/null || true)"
  if _pid_running "$pid"; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    if _pid_running "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
  echo "API остановлен"
}

cmd_restart() {
  cmd_stop
  cmd_start
}

case "${1:-status}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  health) cmd_health ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|health}" >&2
    exit 2
    ;;
esac
