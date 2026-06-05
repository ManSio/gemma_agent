#!/usr/bin/env bash
# VPS: приоритет Baidu FP8 для deepseek-v4-flash, без DeepInfra FP4.
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

apply_kv "OPENROUTER_PROVIDER_ORDER" "baidu,DeepSeek,SiliconFlow"
apply_kv "OPENROUTER_PROVIDER_QUANTIZATIONS" "fp8"
apply_kv "OPENROUTER_PROVIDER_IGNORE" "deepinfra"
apply_kv "OPENROUTER_PROVIDER_ALLOW_FALLBACKS" "true"
echo "OK: ${ENV_FILE} — OpenRouter provider → Baidu FP8 first, ignore deepinfra"
