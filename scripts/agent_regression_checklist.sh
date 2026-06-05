#!/usr/bin/env bash
# Чеклист AGENT_FAILURE_REGISTRY — probe после деплоя.
# POST_DEPLOY_PROBE_USER_ID=123456789 bash scripts/agent_regression_checklist.sh
set -o pipefail
set -u

BOT_DIR="${1:-${GEMMA_BOT_DIR:-.}}"
cd "$BOT_DIR" || exit 1
PY="${GEMMA_VENV_PY:-./venv/bin/python3}"
UID_PROBE="${POST_DEPLOY_PROBE_USER_ID:-${OWNER_TELEGRAM_ID:-}}"
if [[ -z "$UID_PROBE" ]]; then
  echo "Set POST_DEPLOY_PROBE_USER_ID or OWNER_TELEGRAM_ID" >&2
  exit 1
fi
fail=0

run_probe() {
  local label="$1"
  local text="$2"
  echo ""
  echo "=== $label ==="
  if ! "$PY" scripts/agent_turn_probe.py --user-id "$UID_PROBE" --text "$text" --json-out /tmp/gemma_probe.json 2>/dev/null; then
    echo "FAIL probe: $label"
    fail=1
    return
  fi
  "$PY" -c "
import json
o=json.load(open('/tmp/gemma_probe.json'))
msgs=o.get('telegram_messages') or []
print('messages:', len(msgs))
if msgs:
    print((msgs[0] or '')[:220])
if not msgs:
    raise SystemExit(2)
" || fail=1
}

run_probe "translate" 'переведи на английский: "спокойной ночи"'
run_probe "math" "решить уравнение: 2x + 5 = 15"
run_probe "capital" "Назови столицу Беларуси"
run_probe "cancel_reminder" "Отмени напоминание про тест"
run_probe "news" "последние новости Беларуси"

if [[ "$fail" -eq 0 ]]; then
  echo ""
  echo "agent_regression_checklist: OK"
else
  echo ""
  echo "agent_regression_checklist: FAIL"
  exit 1
fi
