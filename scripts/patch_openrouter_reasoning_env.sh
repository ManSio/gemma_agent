#!/usr/bin/env bash
# OPS: OpenRouter unified reasoning + admin stream CoT (идемпотентно).
#   cd /opt/gemma_agent && bash scripts/patch_openrouter_reasoning_env.sh
set -euo pipefail

ENV_FILE="${1:-/opt/gemma_agent/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] нет $ENV_FILE" >&2
  exit 1
fi

declare -A PAIRS=(
  [OPENROUTER_REASONING_ENABLED]=true
  [OPENROUTER_REASONING_EXCLUDE]=true
  [OPENROUTER_BRAIN_REASONING_EFFORT]=high
  [OPENROUTER_REASONING_HIGH_MIN_MAX_TOKENS]=2048
  [OPENROUTER_REASONING_MODEL_PREFIXES]=deepseek/
  [OPENROUTER_GEN_REASONING_EFFORT]=none
  [OPENROUTER_GEN_BRAIN_FIRST_REASONING_EFFORT]=high
  [OPENROUTER_GEN_BRAIN_SECOND_REASONING_EFFORT]=high
  [TELEGRAM_STREAM_REPLY_ENABLED]=true
  [TELEGRAM_STREAM_PRIVATE_ONLY]=true
  [TELEGRAM_STREAM_DIRECT_ONLY]=true
  [TELEGRAM_ADMIN_STREAM_REASONING]=false
  [TELEGRAM_STREAM_REASONING_MAX_CHARS]=1800
)

for key in "${!PAIRS[@]}"; do
  val="${PAIRS[$key]}"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '\n# openrouter reasoning + admin stream (patch_openrouter_reasoning_env.sh)\n%s=%s\n' "$key" "$val" >>"$ENV_FILE"
  fi
done

chmod 600 "$ENV_FILE" 2>/dev/null || true
echo "[OK] patch_openrouter_reasoning_env: ${#PAIRS[@]} ключей"
grep -E '^(OPENROUTER_REASONING_|OPENROUTER_BRAIN_REASONING|OPENROUTER_GEN_REASONING|OPENROUTER_GEN_BRAIN_.*REASONING|TELEGRAM_STREAM_|TELEGRAM_ADMIN_STREAM)' "$ENV_FILE" | grep -v '^#' || true
