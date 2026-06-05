#!/usr/bin/env bash
# Закрепить провайдеров с рабочим prompt cache для deepseek-v4-flash (без DeepInfra).
# После правки: bash scripts/gemma_panel.sh restart
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

apply_kv "OPENROUTER_PROMPT_CACHE_MODE" "auto"
apply_kv "OPENROUTER_CACHE_FIRST_PROVIDERS" "true"
apply_kv "OPENROUTER_PROVIDER_ORDER" "DeepSeek,baidu"
apply_kv "OPENROUTER_PROVIDER_IGNORE" "deepinfra"
apply_kv "OPENROUTER_PROVIDER_ALLOW_FALLBACKS" "true"
echo "OK: ${ENV_FILE} — cache-first providers (DeepSeek, baidu; ignore deepinfra)"
