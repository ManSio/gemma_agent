#!/usr/bin/env bash
# CI / pre-push: release_guard + маршрутизация + память v2 + новости (без полного pytest).
# Запуск: bash scripts/ci_smoke.sh
# Опционально: CI_SMOKE_AGENT_TEST=1 — agent_test_runner --tier smoke --limit 8 (нужен .env).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${GEMMA_VENV_PY:-}"
if [[ -x "$ROOT/venv/bin/python3" ]]; then
  PY="$ROOT/venv/bin/python3"
elif [[ -x "$ROOT/venv/Scripts/python.exe" ]]; then
  PY="$ROOT/venv/Scripts/python.exe"
fi
if [[ -z "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v python >/dev/null 2>&1; then
    PY="python"
  else
    echo "ERROR: python not found (venv or PATH)"
    exit 127
  fi
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

sec() { echo ""; echo "=== $1 ==="; }

sec "release_guard (smoke + anti-regression)"
"$PY" scripts/release_guard.py

ROUTE_TESTS=(
  tests/test_profile_route_guard.py
  tests/test_incident_route_regression.py
  tests/test_user_correction_bus.py
  tests/test_heavy_response_reflection.py
  tests/test_heuristic_context_gate.py
  tests/test_heuristic_false_positives.py
  tests/test_heuristic_misses_log.py
  tests/test_news_reply.py
  tests/test_weather_reply.py
  tests/test_pipeline_chat_lock.py
  tests/test_conversation_epoch.py
  tests/test_lexical_dialog_recall.py
  tests/test_session_digest_dedup.py
  tests/test_llm_telemetry_kind.py
  tests/test_context_budget_user_note.py
  tests/test_golden_promote_and_telemetry.py
)

sec "route + memory + news pytest bundle"
"$PY" -m pytest -q "${ROUTE_TESTS[@]}"

if [[ "${CI_SMOKE_AGENT_TEST:-0}" == "1" ]]; then
  sec "agent_test_runner smoke (limit 8)"
  mkdir -p data/benchmarks
  "$PY" scripts/agent_test_runner.py \
    --tier smoke \
    --limit 8 \
    --report "data/benchmarks/ci_smoke_agent_$(date -u +%Y%m%dT%H%M%SZ).jsonl" \
    || {
      echo "WARN: agent_test smoke had failures (см. report)"
      exit 1
    }
fi

sec "ci_smoke OK"
