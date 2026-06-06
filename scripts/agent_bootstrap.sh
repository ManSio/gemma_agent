#!/usr/bin/env bash
# First-time setup for Gemma Agent (Linux/macOS/Git Bash).
#
#   bash scripts/agent_bootstrap.sh
#   bash scripts/agent_bootstrap.sh /opt/gemma_agent
#
# Options (env):
#   GEMMA_MEM0_USE_STUB=true   — Mem0 stub from repo (default for dev)
#   GEMMA_SKIP_SEARX_CHECK=1   — skip SearXNG curl probe
#   GEMMA_SKIP_PIP=1           — skip pip install
set -euo pipefail

_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="${1:-${GEMMA_BOT_DIR:-$(cd "$_script_dir/.." && pwd)}}"
BOT_DIR="$(cd "$BOT_DIR" && pwd)"

info() { printf '[bootstrap] %s\n' "$*"; }
warn() { printf '[bootstrap] WARN: %s\n' "$*" >&2; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

_env_is_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
  true | 1 | yes | on) return 0 ;;
  *) return 1 ;;
  esac
}

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || die "python3 not found"

info "BOT_DIR=$BOT_DIR"

# --- venv ---
if [[ ! -x "$BOT_DIR/venv/bin/python3" ]]; then
  info "Creating venv…"
  "$PY" -m venv "$BOT_DIR/venv"
fi
VPY="$BOT_DIR/venv/bin/python3"
PIP="$BOT_DIR/venv/bin/pip"

if ! _env_is_true "${GEMMA_SKIP_PIP:-}"; then
  info "pip install -r requirements.txt"
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$PIP" install -r "$BOT_DIR/requirements.txt" -q
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$PIP" install fastapi uvicorn -q
fi

# --- .env ---
if [[ ! -f "$BOT_DIR/.env" ]]; then
  if [[ -f "$BOT_DIR/.env.example" ]]; then
    cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
    info "Created .env from .env.example — fill TELEGRAM_TOKEN and OPENROUTER_API_KEY"
  else
    warn "No .env.example — create .env manually"
  fi
fi

# --- panel local conf (stub mem0 by default) ---
CONF_EX="$BOT_DIR/scripts/gemma_panel.local.conf.example"
CONF="$BOT_DIR/scripts/gemma_panel.local.conf"
if [[ -f "$CONF_EX" && ! -f "$CONF" ]]; then
  {
    echo "BOT_DIR=$BOT_DIR"
    echo "MEM0_PORT=8001"
    echo "GEMMA_MEM0_USE_STUB=true"
  } >"$CONF"
  info "Created $CONF (Mem0 stub enabled)"
fi

# --- data dirs ---
mkdir -p "$BOT_DIR/data/runtime" "$BOT_DIR/data/users/logs" "$BOT_DIR/data/users/behavior"

# --- Mem0 stub env hints ---
if _env_is_true "${GEMMA_MEM0_USE_STUB:-true}"; then
  _set_env() {
    local k="$1" v="$2" f="$BOT_DIR/.env"
    [[ -f "$f" ]] || return 0
    if grep -q "^${k}=" "$f" 2>/dev/null; then return 0; fi
    printf '\n%s=%s\n' "$k" "$v" >>"$f"
  }
  _set_env "MEM0_LOCAL" "true"
  _set_env "MEM0_API_URL" "http://127.0.0.1:8001"
  _set_env "MEM0_API_PREFIX" "v3"
  info "Mem0 stub: MEM0_API_URL=http://127.0.0.1:8001 (start via: bash scripts/gemma_panel.sh mem0-start)"
fi

# --- SearXNG probe ---
if ! _env_is_true "${GEMMA_SKIP_SEARX_CHECK:-}"; then
  url=""
  if [[ -f "$BOT_DIR/.env" ]]; then
    url="$(grep -E '^SEARXNG_INSTANCE_URL=' "$BOT_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
  fi
  url="${url:-http://127.0.0.1:8080}"
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "$url" 2>/dev/null || echo 000)"
    if [[ "$code" == "200" ]]; then
      info "SearXNG OK: $url"
    else
      warn "SearXNG not reachable ($url HTTP $code). Install: sudo bash scripts/searxng_install_native.sh"
      warn "Or set SEARXNG_INSTANCE_URL in .env to your LAN instance."
    fi
  fi
fi

# --- TTS hint ---
piper_model="$BOT_DIR/models/piper/ru_RU-irina-medium.onnx"
if [[ ! -f "$piper_model" ]]; then
  warn "Piper TTS model not found ($piper_model). Voice replies need VOICE_TTS_ENABLED + model — see docs/BOX_SETUP_EN.md"
fi

# --- security quick check ---
if [[ -x "$VPY" ]]; then
  info "Running security audit…"
  "$VPY" "$BOT_DIR/scripts/agent_security_audit.py" --quick || warn "Security audit reported issues (see above)"
fi

info "Done."
info "Next:"
info "  1) Edit $BOT_DIR/.env — TELEGRAM_TOKEN, OPENROUTER_API_KEY, ADMIN_USER_IDS"
info "  2) bash scripts/gemma_panel.sh start-all"
info "  3) python scripts/gemma_status.py --online"
info "Docs: docs/BOX_SETUP_EN.md · docs/BOX_SETUP_RU.md"
