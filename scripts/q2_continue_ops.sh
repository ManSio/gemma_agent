#!/usr/bin/env bash
# Q2: следующий шаг после деплоя / §9 — метрики C6, live reform probe, опционально curated API.
# На сервере: bash scripts/q2_continue_ops.sh
# Локально через SSH: ssh HOST_LAN 'cd /opt/gemma_agent && bash scripts/q2_continue_ops.sh'
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${GEMMA_VENV_PY:-}"
if [[ -x "$ROOT/venv/bin/python3" ]]; then
  PY="$ROOT/venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$ROOT/data/benchmarks/q2_continue_ops_${STAMP}.json"

sec() { echo ""; echo "=== $1 ==="; }

sec "env C6 + news"
grep -E '^(BRAIN_STANDARD_RECENT_COUNT|BRAIN_NEWS_DIRECT_FROM_SEARCH|BRAIN_OWN_TURN_ALLOW_NEWS|NEWS_RSS_FALLBACK)=' .env 2>/dev/null | grep -v '^#' || true

smoke_fail=0
reform_rc=0

sec "post_deploy_smoke"
bash scripts/post_deploy_smoke.sh "$ROOT" || smoke_fail=1

sec "C6 snapshot"
bash scripts/capture_c6_ab_snapshot.sh || true

sec "reform_chain_probe --quick (rdel, без LLM)"
CHAIN_JSON="$ROOT/data/benchmarks/reform_chain_${STAMP}.json"
if [[ -f scripts/reform_chain_probe.py ]]; then
  set +e
  "$PY" scripts/reform_chain_probe.py --quick --json >"$CHAIN_JSON"
  reform_rc=$?
  set -e
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(f\"reform_chain quick: {d.get('passed')}/{d.get('total')}\"); sys.exit(0 if d.get('failed',1)==0 else 1)" "$CHAIN_JSON" || reform_rc=$reform_rc
else
  reform_rc=0
  echo "SKIP reform_chain_probe"
fi

sec "reform_live_probe (диагностика с seed — не гейт)"
REFORM_JSON="$ROOT/data/benchmarks/reform_live_${STAMP}.json"
if [[ -f scripts/reform_live_probe.py ]] && [[ "${REFORM_LIVE_PROBE:-0}" == "1" ]]; then
  set +e
  "$PY" scripts/reform_live_probe.py --timeout-sec 150 --json >"$REFORM_JSON"
  set -e
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(f\"reform_live (diag): {d.get('passed')}/{d.get('total')} passed\")" "$REFORM_JSON" || true
else
  echo "SKIP reform_live_probe (set REFORM_LIVE_PROBE=1 to run)"
fi

sec "curated API (news only, optional)"
CURATED_RC=0
if [[ -f scripts/agent_chat_probe_curated.py ]] && grep -q '^API_TOKEN=' .env 2>/dev/null; then
  set +e
  "$PY" scripts/agent_chat_probe_curated.py \
    --json-out "$ROOT/data/benchmarks/chat_probe_curated_${STAMP}.json" 2>&1 | tail -5
  CURATED_RC=$?
  set -e
else
  echo "SKIP curated (нет API_TOKEN)"
fi

sec "summary"
"$PY" -c "
import json, os
from pathlib import Path
out = {'ts': '$STAMP', 'smoke_fail': int('${smoke_fail:-0}'), 'reform_rc': int('${reform_rc:-0}'), 'curated_rc': int('${CURATED_RC:-0}')}
rf = Path('$REFORM_JSON')
if rf.is_file():
    out['reform_live'] = json.loads(rf.read_text(encoding='utf-8'))
Path('$REPORT').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print('Wrote', '$REPORT')
"

sec "done"
echo "§9 Telegram: заполнить docs/REFORM_S9_ACCEPTANCE_TRACKER_RU.md (владелец)"
echo "C6: сравнить snapshots через 3–7 дн. — docs/C6_AB_RECENT_RUNBOOK_RU.md"
exit $(( smoke_fail + reform_rc ))
