#!/usr/bin/env bash
# Проверка после деплоя на LAN: API + orchestrator probe + опционально Telegram.
# bash scripts/lan_verify_deploy.sh
set -o pipefail
set -u

BOT_DIR="${1:-${GEMMA_BOT_DIR:-/opt/gemma_agent}}"
cd "$BOT_DIR" || exit 1

VENV_PY="${GEMMA_VENV_PY:-$BOT_DIR/venv/bin/python3}"
fail=0
PROBE_USER="${POST_DEPLOY_PROBE_USER_ID:-${OWNER_TELEGRAM_ID:-}}"

sec() { echo ""; echo "=== $1 ==="; }

sec "env stream/memory"
grep -E '^TELEGRAM_STREAM_REPLY_ENABLED=|^CONVERSATION_EPOCH|^LEXICAL_DIALOG' .env 2>/dev/null || true

sec "API /health"
api_port=8000
grep -q '^API_PORT=' .env 2>/dev/null && api_port="$(grep '^API_PORT=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
if curl -sf --max-time 5 "http://127.0.0.1:${api_port}/health" >/dev/null; then
  echo "OK /health"
else
  echo "FAIL /health"
  fail=1
fi

sec "API agent_probe_http"
if [[ -x "$VENV_PY" ]] && [[ -f scripts/agent_probe_http.py ]]; then
  set -a
  # shellcheck disable=SC1091
  [[ -f .env ]] && source <(grep -E '^(API_TOKEN|API_PORT|API_HOST)=' .env | sed 's/\r$//')
  set +a
  if [[ -n "${API_TOKEN:-}" ]]; then
    for q in "привет" "Почему небо голубое?"; do
      echo "probe http: $q"
      if ! "$VENV_PY" scripts/agent_probe_http.py \
        --url "http://127.0.0.1:${api_port}" \
        --user-id "$PROBE_USER" \
        --text "$q"; then
        fail=1
      fi
      sleep 3
    done
  else
    echo "SKIP: нет API_TOKEN в .env"
  fi
else
  echo "SKIP agent_probe_http"
fi

sec "orchestrator agent_turn_probe"
if [[ -x "$VENV_PY" ]]; then
  for q in "Почему небо голубое?" "2+2"; do
    echo "turn: $q"
    if ! "$VENV_PY" scripts/agent_turn_probe.py --user-id "$PROBE_USER" --text "$q"; then
      fail=1
    fi
    sleep 2
  done
  echo "conversation_epoch /new"
  if ! "$VENV_PY" -c "
import os, sys
os.chdir('$BOT_DIR')
sys.path.insert(0, '.')
from core.behavior_store import BehaviorStore
from core.conversation_epoch import start_new_conversation, get_epoch_id
bs = BehaviorStore()
uid = '$PROBE_USER'
nid, rec = start_new_conversation(bs, uid, None, reason='lan_verify')
assert get_epoch_id(rec) == nid
print('OK epoch', nid)
"; then
    fail=1
  fi
else
  echo "SKIP turn probe"
fi

sec "agent_test_runner smoke (limit 8)"
if [[ -x "$VENV_PY" ]] && [[ -f scripts/agent_test_runner.py ]]; then
  if ! "$VENV_PY" scripts/agent_test_runner.py --tier smoke --limit 8 \
    --report "data/benchmarks/lan_verify_smoke_$(date -u +%Y%m%dT%H%M%SZ).jsonl"; then
    echo "WARN smoke runner had failures (см. report)"
    fail=1
  fi
else
  echo "SKIP smoke runner"
fi

sec "Telegram live (agent_telegram_client)"
if [[ -x "$VENV_PY" ]] && [[ -f config/agent_telegram.env ]]; then
  out="/tmp/lan_tg_verify_$$.json"
  if "$VENV_PY" scripts/agent_telegram_client.py \
    --text "Почему небо голубое?" \
    --timeout 120 \
    --json-out "$out"; then
    echo "OK telegram turn (см. $out)"
  else
    echo "FAIL telegram client"
    fail=1
  fi
  if [[ -f "$out" ]]; then
    "$VENV_PY" -c "
import json, sys
d=json.load(open('$out'))
tests=d.get('tests') or [d]
for t in tests:
    texts=[r.get('text','') for r in (t.get('replies') or [])]
    blob=' '.join(texts).lower()
    if any(w in blob for w in ('небо','свет','голуб','рассе')):
        print('OK reply content')
        sys.exit(0)
print('WARN: нет ожидаемых слов в ответе', texts[:1])
sys.exit(0)
"
  fi
else
  echo "SKIP Telegram (нет config/agent_telegram.env)"
fi

sec "log stream markers (last 200 lines)"
log="${GEMMA_BOT_NOHUP_LOG:-$BOT_DIR/panel_nohup_bot.log}"
if [[ -f "$log" ]]; then
  if tail -n 200 "$log" | grep -qE 'brain_direct_dialog|conversation_epoch|lexical'; then
    echo "OK markers in log"
    tail -n 200 "$log" | grep -E 'brain_direct_dialog|conversation_epoch|lexical' | tail -5
  else
    echo "WARN: нет маркеров stream/epoch в хвосте лога (могло не быть direct_dialog хода)"
  fi
fi

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "lan_verify_deploy: OK"
  exit 0
fi
echo "lan_verify_deploy: FAILED"
exit 1
