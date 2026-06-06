#!/usr/bin/env bash
# Apply POWER_AGENT profile to .env (opt-in fuller agent loop).
# Usage: bash scripts/apply_power_agent_env.sh [BOT_DIR]
set -euo pipefail
BOT_DIR="${1:-${GEMMA_BOT_DIR:-$(pwd)}}"
cd "$BOT_DIR"
if [[ ! -f .env ]]; then
  echo "No .env — run: cp .env.example .env" >&2
  exit 1
fi
python scripts/apply_power_agent_env.py "$BOT_DIR"
