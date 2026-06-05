#!/usr/bin/env bash
# Деплой стека Gemma на Linux-хосте: git pull бота + проверка Mem0/SearXNG.
# Использование:
#   bash scripts/stack_deploy.sh status
#   bash scripts/stack_deploy.sh configure-panel
#   bash scripts/stack_deploy.sh pull
#   bash scripts/stack_deploy.sh update
set -o pipefail
set -u

BOT_DIR="${GEMMA_BOT_DIR:-/opt/gemma_agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$BOT_DIR" 2>/dev/null && pwd || echo "$BOT_DIR")"

MEM0_DIR="${GEMMA_MEM0_DIR:-/opt/mem0_local}"
MEM0_PORT="${GEMMA_MEM0_PORT:-8001}"
SEARX_URL="${STACK_SEARX_URL:-http://127.0.0.1:8080}"

_red() { printf '\033[31m%s\033[0m\n' "$*"; }
_grn() { printf '\033[32m%s\033[0m\n' "$*"; }
_ylw() { printf '\033[33m%s\033[0m\n' "$*"; }

usage() {
  cat <<'EOF'
stack_deploy.sh — gemma_bot (git) + проверка Mem0 и SearXNG

  status           — git HEAD, панель, curl Mem0/SearXNG
  configure-panel  — gemma_panel.local.conf (BOT_DIR, MEM0_DIR)
  pull             — gemma_git_pull_safe.sh
  update           — pull + gemma_panel.sh update + status

Переменные: GEMMA_BOT_DIR, GEMMA_MEM0_DIR, STACK_SEARX_URL
EOF
}

_http_code() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" 2>/dev/null || echo "000"
  else
    echo "000"
  fi
}

cmd_status() {
  echo "━━ stack status ━━"
  echo "BOT_DIR=$BOT_DIR"
  if [[ -d "$BOT_DIR/.git" ]]; then
    echo -n "git: "
    git -C "$BOT_DIR" log -1 --oneline 2>/dev/null || echo "?"
    echo -n "branch: "
    git -C "$BOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?"
  else
    _ylw "! нет git в $BOT_DIR"
  fi
  if [[ -x "$BOT_DIR/scripts/gemma_panel.sh" ]]; then
    bash "$BOT_DIR/scripts/gemma_panel.sh" status 2>/dev/null | head -20 || true
  fi
  local mcode
  mcode="$(_http_code "http://127.0.0.1:${MEM0_PORT}/docs")"
  if [[ "$mcode" == "200" ]]; then
    _grn "mem0: http://${MEM0_PORT} OK ($mcode)"
  else
    _red "mem0: http://${MEM0_PORT} FAIL ($mcode) — panel mem0-start или GEMMA_MEM0_USE_STUB=true"
  fi
  local sx
  sx="$(_http_code "${SEARX_URL%/}/")"
  if [[ "$sx" == "200" || "$sx" == "302" ]]; then
    _grn "searxng: $SEARX_URL OK ($sx)"
  else
    _red "searxng: $SEARX_URL FAIL ($sx) — systemctl status searxng"
  fi
  if [[ -f "$BOT_DIR/.env" ]]; then
    echo "env (имена):"
    grep -E '^(MEM0_|SEARXNG_)' "$BOT_DIR/.env" 2>/dev/null | sed 's/=.*$/=***/' || true
  fi
}

cmd_configure_panel() {
  local ex="$BOT_DIR/scripts/gemma_panel.local.conf.example"
  local lc="$BOT_DIR/scripts/gemma_panel.local.conf"
  if [[ ! -f "$ex" ]]; then
    _red "нет $ex"
    return 1
  fi
  if [[ -f "$lc" ]]; then
    _ylw "· $lc уже есть — не перезаписываю"
  else
    cp "$ex" "$lc"
    _grn "✓ создан $lc"
  fi
  if grep -q '^BOT_DIR=' "$lc" 2>/dev/null; then
    sed -i "s|^BOT_DIR=.*|BOT_DIR=$BOT_DIR|" "$lc" 2>/dev/null || \
      sed -i '' "s|^BOT_DIR=.*|BOT_DIR=$BOT_DIR|" "$lc" 2>/dev/null || true
  fi
  if grep -q '^MEM0_DIR=' "$lc" 2>/dev/null; then
    sed -i "s|^MEM0_DIR=.*|MEM0_DIR=$MEM0_DIR|" "$lc" 2>/dev/null || \
      sed -i '' "s|^MEM0_DIR=.*|MEM0_DIR=$MEM0_DIR|" "$lc" 2>/dev/null || true
  fi
  _grn "✓ пути: BOT_DIR=$BOT_DIR MEM0_DIR=$MEM0_DIR"
  echo "  Mem0 из git-заглушки: раскомментируйте GEMMA_MEM0_USE_STUB=true в $lc"
}

cmd_pull() {
  bash "$SCRIPT_DIR/gemma_git_pull_safe.sh" "$BOT_DIR"
}

cmd_update() {
  cmd_pull
  bash "$BOT_DIR/scripts/gemma_panel.sh" update
  echo ""
  cmd_status
}

main() {
  local cmd="${1:-status}"
  case "$cmd" in
  -h | --help | help) usage ;;
  status) cmd_status ;;
  configure-panel) cmd_configure_panel ;;
  pull) cmd_pull ;;
  update) cmd_update ;;
  *)
    _red "неизвестная команда: $cmd"
    usage
    exit 1
    ;;
  esac
}

main "$@"
