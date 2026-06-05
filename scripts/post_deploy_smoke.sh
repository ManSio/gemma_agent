#!/usr/bin/env bash
# Быстрая проверка после start/restart: Lock в личке, env, опционально probe.
# Запуск: bash scripts/post_deploy_smoke.sh [BOT_DIR]
set -o pipefail
set -u

_env_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

BOT_DIR="${1:-${GEMMA_BOT_DIR:-/opt/gemma_agent}}"
cd "$BOT_DIR" || exit 1

VENV_PY="${GEMMA_VENV_PY:-$BOT_DIR/venv/bin/python3}"
fail=0

sec() { echo ""; echo "=== $1 ==="; }

sec "git"
if [[ -d .git ]]; then
  git rev-parse --short HEAD 2>/dev/null || true
  git status -sb 2>/dev/null | head -5 || true
  drift="$(git status --porcelain 2>/dev/null | grep -E '^\?\? (core/pipeline\.py|core/prompt_pack\.py|docs/CHANGELOG\.md|config/agent_telegram\.env\.bak)' || true)"
  if [[ -n "$drift" ]]; then
    echo "WARN git drift (запустите: bash scripts/gemma_clean_server_drift.sh):"
    echo "$drift"
    fail=1
  fi
else
  echo "нет .git"
fi

sec "env (pipeline / KV)"
for k in TELEGRAM_PIPELINE_PRIVATE_PARALLEL BRAIN_KV_PROFILE_STICKY TELEGRAM_PIPELINE_SERIALIZE_BY_CHAT; do
  if grep -q "^${k}=" .env 2>/dev/null; then
    grep "^${k}=" .env | sed 's/=.*/=***/'
  else
    echo "$k — не задан (см. .env.example)"
  fi
done

sec "news brain-own env (G1b)"
if [[ -x "$VENV_PY" ]]; then
  if ! "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
from core.brain_own_turn import pipeline_news_rss_fetch_enabled, news_rss_fallback_enabled, planner_direct_allowed
errs = []
raw = (os.getenv('BRAIN_NEWS_DIRECT_FROM_SEARCH') or 'false').strip().lower()
if raw in ('1', 'true', 'yes', 'on'):
    errs.append('BRAIN_NEWS_DIRECT_FROM_SEARCH must be false on prod brain-own')
if planner_direct_allowed('news'):
    errs.append('BRAIN_OWN_TURN_ALLOW_NEWS should be false')
if news_rss_fallback_enabled():
    errs.append('NEWS_RSS_FALLBACK_ENABLED should be false')
if pipeline_news_rss_fetch_enabled('Какие новости в мире'):
    errs.append('pipeline_news_rss_fetch_enabled should be false')
if errs:
    print('FAIL:', '; '.join(errs))
    sys.exit(1)
print('OK news brain-own gates')
" 2>&1; then
    fail=1
  fi
else
  echo "SKIP: нет $VENV_PY"
fi

sec "personal prod gates (шумовые автономии off)"
if [[ -x "$VENV_PY" ]]; then
  if ! "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
must_false = (
    'TURN_QUALITY_LOOP_ENABLED',
    'TURN_QUALITY_AUTO_PENDING_CORRECTION',
    'MCE_AUTO_APPLY',
    'MCE_ENABLED',
    'GOAL_RUNNER_AUTO_START',
    'ROUTER_PASSIVE_ENABLED',
    'ROUTE_RISK_CLUSTER_AUTO_LESSON',
)
errs = []
for k in must_false:
    v = (os.getenv(k) or '').strip().lower()
    if v in ('1', 'true', 'yes', 'on'):
        errs.append(k + '=on')
if (os.getenv('BRAIN_OPERATOR_CORRECTIONS_IN_HINT') or '').strip().lower() not in ('1', 'true', 'yes', 'on'):
    errs.append('BRAIN_OPERATOR_CORRECTIONS_IN_HINT must be true')
if errs:
    print('FAIL:', '; '.join(errs))
    print('fix: python3 scripts/apply_personal_prod_env.py', os.getcwd())
    sys.exit(1)
print('OK personal prod gates')
" 2>&1; then
    fail=1
  fi
else
  echo "SKIP: нет $VENV_PY"
fi

sec "reform route-only (§9 / G3, без LLM)"
if [[ -x "$VENV_PY" ]] && [[ -f scripts/reform_acceptance_runner.py ]]; then
  if ! "$VENV_PY" scripts/reform_acceptance_runner.py; then
    echo "FAIL: reform_route_regression (reform_acceptance_runner — не §9 TG)"
    fail=1
  fi
else
  echo "SKIP: нет $VENV_PY или scripts/reform_acceptance_runner.py"
fi

sec "private pipeline lock"
if [[ -x "$VENV_PY" ]]; then
  if ! "$VENV_PY" -c "
import asyncio, os, sys
os.chdir('$BOT_DIR')
sys.path.insert(0, '.')
from core.input_layer import InputLayer
async def m():
    L = InputLayer.__new__(InputLayer)
    L._pipeline_chat_locks = {}
    L._pipeline_chat_locks_guard = asyncio.Lock()
    t = type(await L._pipeline_lock_for_chat('1', True)).__name__
    assert t == 'Lock', t
    print('OK Lock')
asyncio.run(m())
" 2>&1; then
    echo "FAIL: ожидался asyncio.Lock для private"
    fail=1
  fi
else
  echo "SKIP: нет $VENV_PY"
fi

if _env_true "${POST_DEPLOY_PROBE:-false}"; then
  sec "agent_turn_probe (POST_DEPLOY_PROBE=true)"
  PROBE_USER="${POST_DEPLOY_PROBE_USER_ID:-${OWNER_TELEGRAM_ID:-}}"
  if [[ -z "$PROBE_USER" ]]; then
    echo "SKIP probe: задайте POST_DEPLOY_PROBE_USER_ID или OWNER_TELEGRAM_ID"
  elif [[ -x "$VENV_PY" ]] && [[ -f scripts/agent_turn_probe.py ]]; then
    for q in "Кто тебя создал?" "Почему небо голубое?"; do
      echo "probe: $q"
      "$VENV_PY" scripts/agent_turn_probe.py --user-id "$PROBE_USER" --text "$q" || fail=1
      sleep 2
    done
  else
    echo "SKIP probe"
  fi
fi

sec "bot process"
if [[ -f gemma.pid ]]; then
  pid="$(cat gemma.pid 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "OK pid=$pid"
  else
    echo "WARN gemma.pid есть, процесс не жив"
    fail=1
  fi
else
  echo "WARN нет gemma.pid"
fi

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "post_deploy_smoke: OK"
  exit 0
fi
echo "post_deploy_smoke: FAILED"
exit 1
