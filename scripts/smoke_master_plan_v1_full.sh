#!/usr/bin/env bash
# Ultimate v1 — полный автоматический smoke: route (без LLM) + regression LLM.
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
echo "=== phase 1: route_only ==="
bash scripts/smoke_v1_route.sh
echo "=== phase 2: regression LLM (smoke tier, без image_gen) ==="
"$PY" scripts/build_test_corpus.py --target 200 --out data/testing/corpus.jsonl >/dev/null
"$PY" scripts/agent_test_runner.py --tier smoke --llm-only \
  --report data/testing/reports/smoke_llm.jsonl
echo "smoke_master_plan_v1_full: OK"
