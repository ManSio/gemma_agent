#!/usr/bin/env bash
# C6 (Q2): снимок метрик A/B recent для deploy-host — раз в неделю или после деплоя telemetry.
# Не меняет env. Запуск на сервере: bash scripts/capture_c6_ab_snapshot.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${GEMMA_VENV_PY:-}"
if [[ -x "$ROOT/venv/bin/python3" ]]; then
  PY="$ROOT/venv/bin/python3"
elif [[ -x "$ROOT/venv/Scripts/python.exe" ]]; then
  PY="$ROOT/venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/data/benchmarks/c6_ab_snapshot_${STAMP}.json"

"$PY" scripts/analyze_brain_recent_ab.py --days 7 --json-out "$OUT"
"$PY" scripts/analyze_kv_session_metrics.py --days 7 || true
echo "C6 snapshot: $OUT"
echo "Сравнение через 3–7 дн.: diff snapshots или analyze_brain_recent_ab.py"
