#!/usr/bin/env bash
# OPS: ЛС админу при исчерпании баланса/лимита OpenRouter (идемпотентно).
#   cd /opt/gemma_agent && bash scripts/patch_admin_quota_dm_env.sh
set -euo pipefail

ENV_FILE="${1:-/opt/gemma_agent/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] нет $ENV_FILE" >&2
  exit 1
fi

declare -A PAIRS=(
  [ADMIN_QUOTA_DM_ENABLED]=true
  [ADMIN_QUOTA_DM_COOLDOWN_SEC]=3600
  [ADMIN_QUOTA_DM_RATE_LIMIT_COOLDOWN_SEC]=900
)

for key in "${!PAIRS[@]}"; do
  val="${PAIRS[$key]}"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '\n# admin quota DM (patch_admin_quota_dm_env.sh)\n%s=%s\n' "$key" "$val" >>"$ENV_FILE"
  fi
done

chmod 600 "$ENV_FILE" 2>/dev/null || true
echo "[OK] patch_admin_quota_dm_env: ${#PAIRS[@]} ключей"
grep -E '^(ADMIN_QUOTA_DM_|ADMIN_NOTIFY_USER_IDS=|ADMIN_USER_IDS=|ADMIN_STARTUP_NOTIFY=|BOT_INSTANCE_ID=)' "$ENV_FILE" | grep -v '^#' || true
