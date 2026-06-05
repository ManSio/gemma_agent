#!/usr/bin/env bash
# Применить блок PERSONAL_PROD к .env (идемпотентно).
# Usage: bash scripts/apply_personal_prod_env.sh [BOT_DIR]
set -euo pipefail
BOT_DIR="${1:-${GEMMA_BOT_DIR:-$(pwd)}}"
ENV_FILE="$BOT_DIR/.env"
MARKER="# --- PERSONAL_PROD (auto) ---"
END_MARKER="# --- END PERSONAL_PROD ---"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No .env at $ENV_FILE" >&2
  exit 1
fi

apply_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

# Удалить предыдущий auto-блок
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
  echo "# Стабильный режим для 3-8 пользователей (docs/archive/PRODUCT_FINISH_PLAN_RU.md)"
} >> "$ENV_FILE"

apply_kv "MCE_AUTO_APPLY" "false"
apply_kv "MCE_EXPERIMENT_ENABLED" "false"
apply_kv "GOAL_RUNNER_AUTO_START" "false"
apply_kv "GOAL_RUNNER_AUTO_START_SMART" "false"
apply_kv "ROUTER_PASSIVE_ENABLED" "false"
apply_kv "LLM_TRIAGE_ENABLED" "false"
apply_kv "ROUTE_RISK_CLUSTER_AUTO_LESSON" "false"
apply_kv "ROUTE_RISK_RECORD_CLARIFY" "false"
apply_kv "TURN_OBSERVER_ENABLED" "true"
apply_kv "BRAIN_OPERATOR_CORRECTIONS_IN_HINT" "true"
apply_kv "BRAIN_CHAT_CONTEXT_SLIM" "true"
apply_kv "BRAIN_REASONING_LOOP_ENABLED" "false"

echo "$END_MARKER" >> "$ENV_FILE"
echo "OK: PERSONAL_PROD applied to $ENV_FILE"
