#!/usr/bin/env bash
# HEU-4 / Q2: еженедельный обзор метрик на сервере (deploy-host).
# Запуск: bash scripts/weekly_ops_review.sh
# Не меняет env и не рестартует бота.
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
  PY="python3"
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT}"

sec() { echo ""; echo "=== $1 ==="; }

sec "git"
git log -1 --oneline 2>/dev/null || true

sec "heuristic_misses (gate review)"
"$PY" scripts/heuristic_misses_review.py --tail 200 || true

sec "KV session hit rate (7d)"
"$PY" scripts/analyze_kv_session_metrics.py --days 7 || true

sec "LLM telemetry tags (7d)"
"$PY" scripts/analyze_llm_telemetry_tags.py --days 7 || true

sec "Brain recent A/B + C6 snapshot (7d)"
bash scripts/capture_c6_ab_snapshot.sh || true

sec "Agent reliability horizon (7d)"
"$PY" scripts/agent_reliability_horizon.py --days 7 --json-out "$ROOT/data/benchmarks/horizon_latest.json" || true

sec "Policy memory offline matrix"
"$PY" scripts/research_policy_memory.py --json-out "$ROOT/data/benchmarks/policy_memory_latest.json" || true

sec "Combined agent metrics (7d)"
"$PY" scripts/research_agent_metrics.py --days 7 --json-out "$ROOT/data/benchmarks/agent_metrics_latest.json" || true

sec "change baseline (optional label)"
if [[ -n "${GEMMA_BASELINE_LABEL:-}" ]]; then
  "$PY" scripts/capture_metrics_baseline.py --label "$GEMMA_BASELINE_LABEL" --note "${GEMMA_BASELINE_NOTE:-weekly}" || true
else
  echo "Skip: set GEMMA_BASELINE_LABEL=weekly-YYYYMMDD to append baseline"
fi

sec "done"
echo "См. docs/PROACTIVE_OPS_RU.md §Еженедельный обзор"
