#!/usr/bin/env bash
# Блок HEURISTIC_GATE + router/playbook (идемпотентно).
# Usage: bash scripts/apply_heuristic_gate_env.sh [prod|lan] [BOT_DIR]
set -euo pipefail

PROFILE="${1:-prod}"
BOT_DIR="${2:-${GEMMA_BOT_DIR:-$(pwd)}}"
ENV_FILE="$BOT_DIR/.env"
MARKER="# --- HEURISTIC_GATE (auto) ---"
END_MARKER="# --- END HEURISTIC_GATE ---"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No .env at $ENV_FILE" >&2
  exit 1
fi

case "$PROFILE" in
  prod|production|vps)
    UNCERTAIN="false"
    ;;
  lan|local|dev|deploy-host)
    UNCERTAIN="true"
    ;;
  *)
    echo "Unknown profile: $PROFILE (use prod or lan)" >&2
    exit 1
    ;;
esac

apply_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    if [[ "$(uname -s 2>/dev/null || echo)" == "Darwin" ]]; then
      sed -i '' "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
      sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    fi
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

if grep -qF "$MARKER" "$ENV_FILE"; then
  awk -v m="$MARKER" -v e="$END_MARKER" '
    $0 == m { skip=1; next }
    $0 == e { skip=0; next }
    !skip { print }
  ' "$ENV_FILE" > "${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi

{
  echo ""
  echo "$MARKER"
  echo "# profile=$PROFILE — docs/archive/MASTER_PLAN_HEURISTICS_MEMORY_RU.md"
} >> "$ENV_FILE"

apply_kv "HEURISTIC_GATE_ENABLED" "true"
apply_kv "HEURISTIC_PROSE_MAX_CHARS" "140"
apply_kv "HEURISTIC_UNCERTAIN_MIN_CHARS" "35"
apply_kv "HEURISTIC_UNCERTAIN_LLM_ENABLED" "$UNCERTAIN"
apply_kv "HEURISTIC_UNCERTAIN_MIN_CONFIDENCE" "0.55"
apply_kv "HEURISTIC_UNCERTAIN_MAX_CHARS" "600"
apply_kv "HEURISTIC_MISSES_LOG_ENABLED" "true"
apply_kv "HEURISTIC_MISSES_LOG_PATH" "data/runtime/heuristic_misses.jsonl"
apply_kv "HEURISTIC_SHORTCUTS_LOCAL_PATH" "config/heuristic_shortcuts.local.json"
apply_kv "ROUTER_LRU_INCLUDE_TOPIC" "true"
apply_kv "ROUTER_PROSE_GUARD_HEURISTIC" "true"
apply_kv "SITUATION_PLAYBOOK_HINTS_ONLY_ON_PROSE" "true"
apply_kv "BRAIN_TOPIC_ANCHOR_IN_HINT" "true"

echo "$END_MARKER" >> "$ENV_FILE"

LOCAL_JSON="$BOT_DIR/config/heuristic_shortcuts.local.json"
EXAMPLE="$BOT_DIR/config/heuristic_shortcuts.local.json.example"
if [[ ! -f "$LOCAL_JSON" && -f "$EXAMPLE" ]]; then
  cp "$EXAMPLE" "$LOCAL_JSON"
  echo "OK: created $LOCAL_JSON from example"
fi

echo "OK: HEURISTIC_GATE ($PROFILE, uncertain=$UNCERTAIN) → $ENV_FILE"
