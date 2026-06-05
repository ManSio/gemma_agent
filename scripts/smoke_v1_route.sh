#!/usr/bin/env bash
# Мастер-план §8 — автоматический smoke без Telegram (route + pytest).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${GEMMA_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY=python3
  elif command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "python not found" >&2
    exit 1
  fi
fi
if [[ -x "$ROOT/venv/bin/python3" ]]; then
  PY="$ROOT/venv/bin/python3"
fi
echo "=== pytest (gate, memory, route) ==="
if "$PY" -m pytest --version >/dev/null 2>&1; then
  "$PY" -m pytest -q \
    tests/test_heuristic_false_positives.py \
    tests/test_heuristic_context_gate.py \
    tests/test_memory_regression.py \
    tests/test_profile_route_guard.py \
    tests/test_incident_route_regression.py
else
  echo "WARN: pytest not in venv — только route_only (локально: python scripts/release_guard.py)"
fi
echo "=== corpus (regression slice) ==="
"$PY" scripts/build_test_corpus.py --target 200 --out data/testing/corpus.jsonl >/dev/null
echo "=== agent_test_runner route_only ==="
"$PY" scripts/agent_test_runner.py --tier smoke --route-only \
  --report data/testing/reports/smoke_route_only.jsonl
echo "smoke_v1_route: OK"
