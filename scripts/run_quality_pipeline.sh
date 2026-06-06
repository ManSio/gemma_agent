#!/usr/bin/env bash
# Полный QA-цикл: архивы на утечки → smoke+chain → отчёт → golden.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/venv/bin/python"
SLUG="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="${ROOT}/data/benchmarks"
mkdir -p "$REPORT_DIR"

echo "=== 1) scan message_archive leaks ==="
"$PY" scripts/scan_archive_leaks.py --root "$ROOT" \
  --json-out "${REPORT_DIR}/archive_leaks_${SLUG}.json" || true

echo "=== 2) pytest route + quality ==="
"$PY" -m pytest -q tests/test_profile_route_guard.py tests/test_incident_route_regression.py \
  tests/test_turn_quality_loop.py tests/test_text_leak_scan.py tests/test_turn_chain_audit.py

echo "=== 3) rebuild corpus ==="
"$PY" scripts/build_test_corpus.py --root "$ROOT" --target 500

echo "=== 4) smoke with turn chain ==="
SMOKE_REPORT="${REPORT_DIR}/chain_smoke_${SLUG}.jsonl"
"$PY" scripts/agent_test_runner.py --tier smoke --report "$SMOKE_REPORT" || SMOKE_RC=$?
SMOKE_RC="${SMOKE_RC:-0}"

echo "=== 5) promote golden from smoke ==="
"$PY" scripts/promote_golden_from_report.py --report "$SMOKE_REPORT"

echo "=== 6) archive resume (optional long) ==="
if [[ "${RUN_ARCHIVE:-0}" == "1" ]]; then
  ARCHIVE_REPORT="${REPORT_DIR}/full_audit_${SLUG}.jsonl"
  "$PY" scripts/agent_test_runner.py --tier archive --report "$ARCHIVE_REPORT" --resume || true
  "$PY" scripts/promote_golden_from_report.py --report "$ARCHIVE_REPORT"
fi

echo "DONE slug=$SLUG smoke_rc=$SMOKE_RC"
exit "$SMOKE_RC"
