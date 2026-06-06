#!/usr/bin/env bash
# Q2 plan: финальная проверка перед закрытием (не деплой).
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
    echo "python not found" >&2
    exit 1
  fi
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT}"

echo "=== release_guard ==="
"$PY" scripts/release_guard.py

echo ""
echo "=== ci_smoke bundle ==="
bash scripts/ci_smoke.sh

echo ""
echo "=== pytest collect (snapshot) ==="
"$PY" -m pytest --collect-only -q 2>/dev/null | tail -1 || true

echo ""
echo "=== C6 A/B (отложено) ==="
echo "На LAN: BRAIN_STANDARD_RECENT_COUNT=12; через 3–7 дн.:"
echo "  python scripts/analyze_brain_recent_ab.py --days 7"
echo "На VPS: оставить 10 до отчёта в DEV_DIARY."

echo ""
echo "=== weekly ops (на сервере) ==="
echo "  bash scripts/weekly_ops_review.sh"

echo ""
echo "Q2 close check: OK (кроме C6 — ждёт метрик)"
