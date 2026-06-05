#!/usr/bin/env bash
# VPS/deploy-host: память диалога + меньше ложных search_skipped в quality_loop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$ROOT/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Нет $ENV_FILE" >&2
  exit 1
fi
BLOCK_START="# --- memory context VPS fix (auto) ---"
BLOCK_END="# --- end memory context VPS fix ---"
apply_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}
# Абсолютный путь — иначе tools без cwd читают data/behavior (пусто).
apply_kv "BEHAVIOR_DATA_DIR" "/opt/gemma_agent/data/users"
apply_kv "BRAIN_QUICK_EXPLAIN_RECENT_COUNT" "8"
apply_kv "BRAIN_CODE_RECENT_COUNT" "6"
apply_kv "DIALOGUE_MEMORY_MAX" "12"
echo "OK: $ENV_FILE updated (BEHAVIOR_DATA_DIR, BRAIN_*_RECENT_COUNT, DIALOGUE_MEMORY_MAX)"
