#!/usr/bin/env bash
# Максимальная конфигурация веб-поиска (SearXNG-first) для прод/VPS.
# Usage: bash scripts/apply_search_max_env.sh [BOT_DIR]
set -euo pipefail
BOT_DIR="${1:-${GEMMA_BOT_DIR:-$(pwd)}}"
ENV_FILE="$BOT_DIR/.env"
MARKER="# --- SEARCH_MAX (auto) ---"
END_MARKER="# --- END SEARCH_MAX ---"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No .env at $ENV_FILE" >&2
  exit 1
fi

apply_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

if grep -qF "$MARKER" "$ENV_FILE"; then
  awk -v m="$MARKER" -v e="$END_MARKER" '
    $0 == m { skip=1; next }
    $0 == e { skip=0; next }
    !skip { print }
  ' "$ENV_FILE" >"${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi

{
  echo ""
  echo "$MARKER"
  echo "# SearXNG-first, без платных API; см. docs/UPSTREAM_STACK_RU.md"
} >>"$ENV_FILE"

apply_kv "SEARXNG_ENABLED" "true"
apply_kv "SEARXNG_INSTANCE_URL" "http://127.0.0.1:8080"
apply_kv "SEARXNG_MAX_RESULTS" "20"
apply_kv "UNIVERSAL_SEARCH_ENABLED" "true"
apply_kv "UNIVERSAL_SEARCH_LOCAL_FIRST" "true"
apply_kv "UNIVERSAL_SEARCH_FREE_ONLY" "true"
apply_kv "UNIVERSAL_SEARCH_TIMEOUT_SEC" "45"
apply_kv "UNIVERSAL_SEARCH_MAX_SUMMARY_CHARS" "8000"
apply_kv "PRODUCT_BEHAVIOR_SEARCH_CONTRACT" "true"
apply_kv "BRAIN_WEATHER_UNIVERSAL_SEARCH_FALLBACK" "true"
apply_kv "DUCKDUCKGO_HTML_FALLBACK" "false"

echo "$END_MARKER" >>"$ENV_FILE"
echo "OK: SEARCH_MAX applied to $ENV_FILE"
