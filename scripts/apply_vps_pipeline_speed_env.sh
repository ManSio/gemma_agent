#!/usr/bin/env bash
# VPS: убрать лишние LLM-проходы (auto-reasoning, heavy reflection), снизить max_tokens.
set -euo pipefail
ROOT="${1:-/opt/gemma_agent}"
ENV_FILE="${ROOT}/.env"

apply_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

apply_kv "BRAIN_AUTO_REASONING_PLUGINS" "false"
apply_kv "REFLECTION_HEAVY_ENABLED" "false"
apply_kv "OPENROUTER_GEN_BRAIN_FIRST_MAX_TOKENS" "3500"
apply_kv "OPENROUTER_GEN_BRAIN_SECOND_MAX_TOKENS" "8000"
apply_kv "BRAIN_LLM_FREE_ATTEMPTS" "1"
echo "OK: ${ENV_FILE} — pipeline speed tuning (no extra LLM passes, lower caps)"
