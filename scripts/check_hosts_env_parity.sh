#!/usr/bin/env bash
# OPS-3: сверка критичных .env между HOST_LAN и VPS_PROD (без вывода секретов).
#
#   bash scripts/check_hosts_env_parity.sh
#   HOST_LAN_SSH=… VPS_PROD_SSH=… bash scripts/check_hosts_env_parity.sh
#
# SSH: env HOST_LAN_SSH / VPS_PROD_SSH, или docs/OPS_PRIVATE.local.md, иначе ~/.ssh Host HOST_LAN|VPS_PROD.
#
# Exit 0 — нет неожиданных расхождений.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEYS_FILE="${ENV_PARITY_KEYS_FILE:-$ROOT/config/env_parity_keys.txt}"
GEMMA_ROOT="${GEMMA_ROOT:-/opt/gemma_agent}"
OPS_PRIVATE="${OPS_PRIVATE:-$ROOT/docs/OPS_PRIVATE.local.md}"

_read_ops_ssh() {
  local key="$1"
  [[ -f "$OPS_PRIVATE" ]] || return 1
  local line val
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == "${key}="* ]] || continue
    val="${line#*=}"
    val="$(echo "$val" | tr -d '\r' | xargs)"
    [[ -n "$val" ]] || return 1
    printf '%s' "$val"
    return 0
  done <"$OPS_PRIVATE"
  return 1
}

_resolve_ssh() {
  local env_val="$1"
  local ops_key="$2"
  local host_alias="$3"
  if [[ -n "$env_val" ]]; then
    printf '%s' "$env_val"
    return 0
  fi
  if _read_ops_ssh "$ops_key"; then
    return 0
  fi
  printf '%s' "$host_alias"
}

LAN_SSH="$(_resolve_ssh "${HOST_LAN_SSH:-}" HOST_LAN_SSH HOST_LAN)"
VPS_SSH="$(_resolve_ssh "${VPS_PROD_SSH:-}" VPS_PROD_SSH VPS_PROD)"

if [[ ! -f "$KEYS_FILE" ]]; then
  echo "Нет файла ключей: $KEYS_FILE" >&2
  exit 2
fi

declare -a KEYS=()
declare -A EXPECT_DIFF
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"
  line="$(echo "$line" | tr -d '\r' | xargs)"
  [[ -z "$line" ]] && continue
  if [[ "$line" == *"=diff:"* ]]; then
    k="${line%%=*}"
    EXPECT_DIFF["$k"]=1
    KEYS+=("$k")
  else
    KEYS+=("$line")
  fi
done <"$KEYS_FILE"

_fetch() {
  local host="$1"
  local pat
  pat="$(printf '%s|' "${KEYS[@]}")"
  pat="${pat%|}"
  ssh -o ConnectTimeout=12 "$host" "grep -E '^(${pat})=' '${GEMMA_ROOT}/.env' 2>/dev/null | sort" \
    | tr -d '\r' || true
}

echo "=== env parity: LAN ($LAN_SSH) vs VPS ($VPS_SSH) ==="
mapfile -t lan_lines < <(_fetch "$LAN_SSH")
mapfile -t vps_lines < <(_fetch "$VPS_SSH")

declare -A lan_vals vps_vals
for row in "${lan_lines[@]}"; do
  [[ "$row" == *=* ]] || continue
  lan_vals["${row%%=*}"]="${row#*=}"
done
for row in "${vps_lines[@]}"; do
  [[ "$row" == *=* ]] || continue
  vps_vals["${row%%=*}"]="${row#*=}"
done

fail=0
ok_diff=0
for k in "${KEYS[@]}"; do
  lv="${lan_vals[$k]:-}"
  vv="${vps_vals[$k]:-}"
  if [[ -n "${EXPECT_DIFF[$k]:-}" ]]; then
    if [[ "$lv" != "$vv" ]]; then
      echo "OK (intentional diff) $k: LAN=${lv:-<unset>} VPS=${vv:-<unset>}"
      ok_diff=$((ok_diff + 1))
    else
      echo "WARN $k: ожидали различие LAN/VPS (C6?), сейчас одинаково: ${lv:-<unset>}"
    fi
    continue
  fi
  if [[ "$lv" == "$vv" ]]; then
    echo "OK $k=${lv:-<unset>}"
  else
    echo "FAIL $k: LAN=${lv:-<unset>} VPS=${vv:-<unset>}"
    fail=$((fail + 1))
  fi
done

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "env_parity: OK (${#KEYS[@]} keys, $ok_diff intentional diffs)"
  exit 0
fi
echo "env_parity: FAILED ($fail unexpected diffs)"
exit 1
