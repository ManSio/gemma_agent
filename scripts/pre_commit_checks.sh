#!/usr/bin/env bash
# Pre-commit privacy + smoke gate (run before git commit).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:-}:$ROOT"

echo "=== pre_commit: check_public_privacy (CI) ==="
python3 scripts/check_public_privacy.py --ci

echo "=== pre_commit: agent_security_audit (CI) ==="
python3 scripts/agent_security_audit.py --ci

if [[ "${PRE_COMMIT_SMOKE:-0}" == "1" ]]; then
  echo "=== pre_commit: release_guard smoke ==="
  python3 scripts/release_guard.py --smoke
fi

echo "[OK] pre_commit_checks"
