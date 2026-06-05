#!/usr/bin/env bash
# Расширенная проверка после start/restart: smoke + venv + webhook env + лог + API.
# Запуск: bash scripts/post_deploy_health.sh [BOT_DIR]
set -o pipefail
set -u

BOT_DIR="${1:-${GEMMA_BOT_DIR:-/opt/gemma_agent}}"
cd "$BOT_DIR" || exit 1

VENV_PY="${GEMMA_VENV_PY:-$BOT_DIR/venv/bin/python3}"
LOG_FILE="${GEMMA_BOT_NOHUP_LOG:-$BOT_DIR/panel_nohup_bot.log}"
fail=0

sec() { echo ""; echo "=== $1 ==="; }

sec "post_deploy_smoke (base)"
if ! bash "$BOT_DIR/scripts/post_deploy_smoke.sh" "$BOT_DIR"; then
  fail=1
fi

sec "venv pip"
if [[ -x "$BOT_DIR/venv/bin/pip" ]]; then
  echo "OK venv/bin/pip"
elif [[ -x "$VENV_PY" ]] && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
  echo "OK python -m pip ($("$VENV_PY" -m pip --version 2>/dev/null | head -1))"
else
  echo "FAIL: нет pip (venv/bin/pip или python -m pip)"
  fail=1
fi

sec "WEBHOOK_URL (.env)"
if [[ -f .env ]] && grep -q '^WEBHOOK_URL=' .env 2>/dev/null; then
  export HEALTH_CHECK_WEBHOOK_RAW
  HEALTH_CHECK_WEBHOOK_RAW="$(grep '^WEBHOOK_URL=' .env | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')"
  if [[ -x "$VENV_PY" ]]; then
    if ! "$VENV_PY" -c "
import os
import sys
sys.path.insert(0, '.')
from core.telegram_webhook_config import resolve_telegram_webhook_url
raw = os.environ.get('HEALTH_CHECK_WEBHOOK_RAW', '')
eff = resolve_telegram_webhook_url(raw)
if raw.strip() and not eff:
    print('WARN WEBHOOK_URL placeholder or invalid — bot will use polling (OK for LAN)')
else:
    print('WEBHOOK_URL effective:', eff or '(polling)')
"; then
      fail=1
    fi
  else
    echo "SKIP: нет $VENV_PY для проверки webhook"
  fi
else
  echo "WEBHOOK_URL не задан — polling"
fi

sec "bot log (recent errors)"
if [[ -f "$LOG_FILE" ]]; then
  if tail -n 120 "$LOG_FILE" 2>/dev/null | grep -q 'TelegramConflictError\|Conflict: terminated by other getUpdates'; then
    echo "WARN: TelegramConflict в последних 120 строках — два процесса на одном токене?"
    fail=1
  else
    echo "OK no TelegramConflict in tail"
  fi
  if tail -n 80 "$LOG_FILE" 2>/dev/null | grep -q 'bad webhook: Failed to resolve host'; then
    echo "WARN: bad webhook в логе — проверьте WEBHOOK_URL или очистите placeholder"
    fail=1
  fi
  if tail -n 40 "$LOG_FILE" 2>/dev/null | grep -q 'Fatal error:.*webhook'; then
    echo "WARN: Fatal webhook в недавнем логе"
    fail=1
  fi
else
  echo "нет лога $LOG_FILE"
fi

sec "forecast API (Open-Meteo)"
if curl -sf --max-time 8 \
  "https://api.open-meteo.com/v1/forecast?latitude=53.9&longitude=27.6&current=temperature_2m&timezone=auto" \
  | grep -q '"temperature_2m"'; then
  echo "OK Open-Meteo current=temperature_2m"
else
  echo "WARN Open-Meteo unreachable (погода может fallback)"
fi

sec "API health"
api_port=8000
if grep -q '^API_PORT=' .env 2>/dev/null; then
  api_port="$(grep '^API_PORT=' .env | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')"
fi
if curl -sf --max-time 3 "http://127.0.0.1:${api_port}/health" >/dev/null 2>&1; then
  echo "OK http://127.0.0.1:${api_port}/health"
elif curl -sf --max-time 3 "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
  echo "OK http://127.0.0.1:8080/health"
else
  echo "SKIP API health (API может быть выключен — не критично для бота)"
fi

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "post_deploy_health: OK"
  exit 0
fi
echo "post_deploy_health: FAILED"
exit 1
