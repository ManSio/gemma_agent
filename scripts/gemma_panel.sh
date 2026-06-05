#!/usr/bin/env bash
# Gemma Agent control panel (bot + Mem0). Bash required.
#
#   bash scripts/gemma_panel.sh              # interactive menu
#   bash scripts/gemma_panel.sh status
#   bash scripts/gemma_panel.sh start-all
#   bash scripts/gemma_panel.sh setup        # first-time bootstrap
#
# Override paths (no file edit):
#   export GEMMA_BOT_DIR=/opt/gemma_agent
#   export GEMMA_MEM0_DIR=/opt/mem0_local
#   export GEMMA_MEM0_USE_STUB=true          # use scripts/mem0_platform_stub.py
#   export GEMMA_PANEL_CONFIG=/path/gemma_panel.local.conf
set -o pipefail
set -u

_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_repo_root="$(cd "$_script_dir/.." && pwd)"

### === Defaults (overridden by env / gemma_panel.local.conf) ===
BOT_DIR="${GEMMA_BOT_DIR:-$_repo_root}"
SERVICE="${GEMMA_BOT_SERVICE:-gemma_bot.service}"
MEM0_DIR="${GEMMA_MEM0_DIR:-/opt/mem0_local}"
MEM0_PORT="${GEMMA_MEM0_PORT:-8001}"
MEM0_HOST_BIND="${GEMMA_MEM0_HOST_BIND:-127.0.0.1}"
MEM0_HEALTH_URL_EXPLICIT="${GEMMA_MEM0_HEALTH_URL:-}"
MEM0_START_TIMEOUT_SEC="${GEMMA_MEM0_START_TIMEOUT:-15}"
START_BOT_ENSURE_MEM0="${GEMMA_START_BOT_ENSURE_MEM0:-true}"
LOG_TAIL_LINES="${GEMMA_PANEL_LOG_LINES:-60}"

_conf="${GEMMA_PANEL_CONFIG:-$_script_dir/gemma_panel.local.conf}"
if [[ -f "$_conf" ]]; then
  # shellcheck source=/dev/null
  source "$_conf"
fi

if [[ -n "${MEM0_HEALTH_URL_EXPLICIT}" ]]; then
  MEM0_HEALTH_URL="${MEM0_HEALTH_URL_EXPLICIT}"
elif [[ -z "${MEM0_HEALTH_URL:-}" ]]; then
  MEM0_HEALTH_URL="http://127.0.0.1:${MEM0_PORT}/docs"
fi

VENV_PY="$BOT_DIR/venv/bin/python3"
MAIN="$BOT_DIR/main.py"
PID_FILE="$BOT_DIR/gemma.pid"
LOG_FILE="${GEMMA_BOT_LOG:-$BOT_DIR/data/users/logs/gemma_bot.log}"
NOHUP_LOG="${GEMMA_BOT_NOHUP_LOG:-$BOT_DIR/panel_nohup_bot.log}"

MEM0_VENV="$MEM0_DIR/venv/bin/python3"
MEM0_MAIN="$MEM0_DIR/mem0_server.py"
MEM0_PID="$MEM0_DIR/mem0.pid"
MEM0_NOHUP="${GEMMA_MEM0_NOHUP_LOG:-$MEM0_DIR/panel_nohup_mem0.log}"

_env_is_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
  true | 1 | yes | on) return 0 ;;
  *) return 1 ;;
  esac
}

if _env_is_true "${GEMMA_MEM0_USE_STUB:-}"; then
  MEM0_VENV="$VENV_PY"
  MEM0_DIR="$_script_dir"
  MEM0_MAIN="$MEM0_DIR/mem0_platform_stub.py"
  MEM0_PID="${GEMMA_MEM0_PID_FILE:-$BOT_DIR/data/runtime/mem0_stub.pid}"
  MEM0_NOHUP="${GEMMA_MEM0_NOHUP_LOG:-$BOT_DIR/data/runtime/panel_nohup_mem0_stub.log}"
fi

_sudo() {
  if [[ "${EUID:-0}" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

info() { printf '· %s\n' "$*"; }
warn() { printf '! %s\n' "$*" >&2; }
err() { printf '✖ %s\n' "$*" >&2; }
ok() { printf '✓ %s\n' "$*"; }

_read_pid() {
  local f="$1" p
  [[ -f "$f" ]] || return 1
  IFS= read -r p <"$f" || return 1
  p="${p//[^0-9]/}"
  [[ -n "$p" ]] || return 1
  printf '%s' "$p"
}

_alive() { [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null; }

is_systemd_active() { systemctl is-active --quiet "$SERVICE" 2>/dev/null; }

bot_pid() { _read_pid "$PID_FILE" 2>/dev/null || true; }
bot_running() { local p; p="$(bot_pid)"; [[ -n "$p" ]] && _alive "$p"; }

mem0_pid() { _read_pid "$MEM0_PID" 2>/dev/null || true; }
mem0_running() { local p; p="$(mem0_pid)"; [[ -n "$p" ]] && _alive "$p"; }

mem0_health_ok() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -sf --max-time 3 -o /dev/null "$MEM0_HEALTH_URL"
}

wait_mem0() {
  local i=0
  while (( i < MEM0_START_TIMEOUT_SEC )); do
    mem0_health_ok && return 0
    sleep 1
    ((i++)) || true
  done
  return 1
}

preflight_bot() {
  local okc=0
  [[ -d "$BOT_DIR" ]] || { err "BOT_DIR missing: $BOT_DIR"; okc=1; }
  [[ -f "$MAIN" ]] || { err "main.py missing: $MAIN"; okc=1; }
  [[ -x "$VENV_PY" ]] || { err "venv python missing: $VENV_PY (run: bash scripts/agent_bootstrap.sh)"; okc=1; }
  [[ -f "$BOT_DIR/.env" ]] || warn ".env missing — copy from .env.example"
  mkdir -p "$BOT_DIR/data" 2>/dev/null || true
  return "$okc"
}

preflight_mem0() {
  local okc=0
  [[ -f "$MEM0_MAIN" ]] || { err "Mem0 entry missing: $MEM0_MAIN"; okc=1; }
  [[ -x "$MEM0_VENV" ]] || { err "Mem0 python missing: $MEM0_VENV"; okc=1; }
  return "$okc"
}

status_all() {
  echo "━━ Gemma panel ━━"
  echo "BOT_DIR=$BOT_DIR"
  echo "MEM0_DIR=$MEM0_DIR (port $MEM0_PORT)"
  if is_systemd_active; then
    echo "bot: systemd active ($SERVICE)"
  elif bot_running; then
    echo "bot: running pid=$(bot_pid)"
  else
    echo "bot: stopped"
  fi
  if mem0_running; then
    if mem0_health_ok; then echo "mem0: running pid=$(mem0_pid) http_ok"; else echo "mem0: running http_fail"; fi
  else
    echo "mem0: stopped"
  fi
}

start_mem0() {
  preflight_mem0 || return 1
  if mem0_running; then
    mem0_health_ok && { info "Mem0 already up"; return 0; }
    warn "Mem0 process exists but HTTP fails — restart mem0"
    return 1
  fi
  mkdir -p "$(dirname "$MEM0_PID")" "$(dirname "$MEM0_NOHUP")" 2>/dev/null || true
  info "Starting Mem0 → $MEM0_NOHUP"
  (
    cd "$MEM0_DIR" || exit 1
    if [[ "$(basename "$MEM0_MAIN")" == mem0_platform_stub.py ]]; then
      nohup "$MEM0_VENV" -m uvicorn mem0_platform_stub:app --host "$MEM0_HOST_BIND" --port "$MEM0_PORT" \
        >>"$MEM0_NOHUP" 2>&1 &
    else
      nohup "$MEM0_VENV" -m uvicorn mem0_server:app --host "$MEM0_HOST_BIND" --port "$MEM0_PORT" \
        >>"$MEM0_NOHUP" 2>&1 &
    fi
    echo $! >"$MEM0_PID"
  )
  sleep 1
  mem0_running || { err "Mem0 exited — see $MEM0_NOHUP"; rm -f "$MEM0_PID"; return 1; }
  if command -v curl >/dev/null 2>&1; then
    wait_mem0 && ok "Mem0 healthy" || { err "Mem0 HTTP timeout"; return 1; }
  else
    ok "Mem0 pid $(mem0_pid) (no curl — skipped HTTP check)"
  fi
}

stop_mem0() {
  if mem0_running; then
    local p; p="$(mem0_pid)"
    kill "$p" 2>/dev/null || true
    sleep 1
    kill -9 "$p" 2>/dev/null || true
    rm -f "$MEM0_PID"
    ok "Mem0 stopped"
  else
    info "Mem0 not running"
  fi
}

start_bot() {
  if is_systemd_active; then
    _sudo systemctl start "$SERVICE"
    return
  fi
  if bot_running; then info "Bot already running pid=$(bot_pid)"; return 0; fi
  preflight_bot || return 1
  if _env_is_true "$START_BOT_ENSURE_MEM0" && ! mem0_health_ok; then
    info "Mem0 not healthy — starting first"
    start_mem0 || return 1
  fi
  mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$NOHUP_LOG")" 2>/dev/null || true
  rm -f "$PID_FILE"
  (
    cd "$BOT_DIR" || exit 1
    NPY_DISABLE_CPU_FEATURES="AVX512F,AVX512CD,AVX512ER,AVX512PF,AVX,AVX2,FMA" \
      nohup "$VENV_PY" "$MAIN" >>"$NOHUP_LOG" 2>&1 &
    echo $! >"$PID_FILE"
  )
  sleep 1
  bot_running && ok "Bot pid $(bot_pid)" || { err "Bot exited — see $NOHUP_LOG"; rm -f "$PID_FILE"; return 1; }
}

stop_bot() {
  if is_systemd_active; then
    _sudo systemctl stop "$SERVICE"
    return
  fi
  if bot_running; then
    local p; p="$(bot_pid)"
    kill "$p" 2>/dev/null || true
    sleep 1
    kill -9 "$p" 2>/dev/null || true
    rm -f "$PID_FILE"
    ok "Bot stopped"
  else
    info "Bot not running"
  fi
}

restart_bot() { stop_bot; sleep 1; start_bot; }
restart_mem0() { stop_mem0; sleep 1; start_mem0; }
start_all() { start_mem0 && start_bot; }
stop_all() { stop_bot; sleep 1; stop_mem0; }

show_log() {
  local f=""
  [[ -f "$NOHUP_LOG" ]] && f="$NOHUP_LOG"
  [[ -f "$LOG_FILE" && ( -z "$f" || "$LOG_FILE" -nt "$f" ) ]] && f="$LOG_FILE"
  if [[ -n "$f" ]]; then tail -n "$LOG_TAIL_LINES" "$f"; else warn "No log yet ($NOHUP_LOG)"; fi
}

run_setup() {
  bash "$_script_dir/agent_bootstrap.sh" "$BOT_DIR"
}

run_security_audit() {
  "$VENV_PY" "$BOT_DIR/scripts/agent_security_audit.py" 2>/dev/null || \
    python3 "$BOT_DIR/scripts/agent_security_audit.py"
}

update_bot() {
  stop_bot || true
  if [[ -d "$BOT_DIR/.git" ]]; then
    info "git pull"
    (cd "$BOT_DIR" && git pull) || { err "git pull failed"; return 1; }
  fi
  info "pip install"
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV_PY" -m pip install -r "$BOT_DIR/requirements.txt" -q || return 1
  start_bot
}

usage() {
  cat <<EOF
Usage: $0 [command]

Commands:
  menu          Interactive menu (default)
  status        Bot + Mem0 status
  start         Start bot
  stop          Stop bot
  restart       Restart bot
  start-all     Start Mem0 then bot
  stop-all      Stop bot then Mem0
  mem0-start    Start Mem0 only
  mem0-stop     Stop Mem0
  mem0-restart  Restart Mem0
  mem0-health   Exit 0 if Mem0 HTTP OK
  log           Tail bot log
  preflight     Check paths and venv
  setup         First-time install (agent_bootstrap.sh)
  security      Run agent_security_audit.py
  update        git pull + pip + restart
EOF
}

menu() {
  while true; do
    clear 2>/dev/null || true
    status_all
    echo
    echo "  1) Full status    2) Log tail"
    echo "  3) Start bot      4) Stop bot      5) Restart bot"
    echo "  6) Start Mem0     7) Stop Mem0     8) Start all"
    echo "  9) Setup/bootstrap  s) Security audit  u) Update"
    echo "  0) Exit"
    read -r -p "> " c || exit 0
    c="${c//$'\r'/}"
    case "$c" in
      1) status_all ;;
      2) show_log ;;
      3) start_bot ;;
      4) stop_bot ;;
      5) restart_bot ;;
      6) start_mem0 ;;
      7) stop_mem0 ;;
      8) start_all ;;
      9) run_setup ;;
      s|S) run_security_audit ;;
      u|U) update_bot ;;
      0|q|Q) exit 0 ;;
      *) warn "Unknown: $c" ;;
    esac
    echo
    read -r -p "Enter… " _ || true
  done
}

main() {
  local cmd="${1:-menu}"
  shift || true
  case "$cmd" in
    menu | "") menu ;;
    -h | --help | help) usage ;;
    status) status_all ;;
    start) start_bot ;;
    stop) stop_bot ;;
    restart) restart_bot ;;
    start-all) start_all ;;
    stop-all) stop_all ;;
    mem0-start) start_mem0 ;;
    mem0-stop) stop_mem0 ;;
    mem0-restart) restart_mem0 ;;
    mem0-health)
      mem0_health_ok && exit 0 || exit 1
      ;;
    log) show_log ;;
    preflight) preflight_bot; preflight_mem0 ;;
    setup) run_setup ;;
    security) run_security_audit ;;
    update) update_bot ;;
    *) err "Unknown: $cmd"; usage; exit 1 ;;
  esac
}

main "$@"
