#!/usr/bin/env bash
# Replace private/full install with public GitHub tree (same path, low RAM).
# Preserves .env + data/. Full tarball + renamed dir for rollback.
#
#   sudo systemctl stop gemma_bot.service
#   bash scripts/migrate_private_to_public.sh --bot-dir /srv/gemma_bot --dry-run
#   bash scripts/migrate_private_to_public.sh --bot-dir /srv/gemma_bot
#
# Rollback: bash scripts/rollback_to_private.sh --backup-dir /var/backups/gemma --pick latest
set -euo pipefail

BOT_DIR=""
BACKUP_PARENT="${GEMMA_BACKUP_DIR:-/var/backups/gemma}"
DRY_RUN=0
SKIP_STOP_CHECK=0
REPO_URL="${GEMMA_PUBLIC_REPO:-https://github.com/ManSio/gemma_agent.git}"
BRANCH="${GEMMA_PUBLIC_BRANCH:-master}"

STAMP=""
ARCHIVE=""
RENAMED=""
CLONE_TMP=""
RESTORED=0

usage() {
  cat <<'EOF'
Usage: migrate_private_to_public.sh --bot-dir PATH [options]

  --bot-dir PATH       Current install (private/full)
  --backup-dir DIR     Backup parent (default: /var/backups/gemma)
  --repo URL           Public repo URL
  --branch NAME        Git branch (default: master)
  --dry-run            Show plan only
  --skip-stop-check    Skip running-process check (not recommended)

Preserves: .env, data/
Does NOT copy: private modules_catalog (public tree has its own 19 plugins)
EOF
  exit "${1:-0}"
}

info() { printf '[migrate] %s\n' "$*"; }
warn() { printf '[migrate] WARN: %s\n' "$*" >&2; }
die() { printf '[migrate] ERROR: %s\n' "$*" >&2; exit 1; }

restore_on_fail() {
  if [[ "$RESTORED" -eq 1 ]]; then
    return
  fi
  if [[ -n "$RENAMED" && -d "$RENAMED" && ! -e "$BOT_DIR" ]]; then
    warn "Migration failed — restoring $RENAMED → $BOT_DIR"
    mv "$RENAMED" "$BOT_DIR"
    RESTORED=1
  fi
  if [[ -n "$CLONE_TMP" && -d "$CLONE_TMP" ]]; then
    rm -rf "$CLONE_TMP"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bot-dir) BOT_DIR="$2"; shift 2 ;;
    --backup-dir) BACKUP_PARENT="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-stop-check) SKIP_STOP_CHECK=1; shift ;;
    -h|--help) usage 0 ;;
    *) die "Unknown arg: $1 (try --help)" ;;
  esac
done

[[ -n "$BOT_DIR" ]] || die "--bot-dir required"
[[ -d "$BOT_DIR" ]] || die "Not a directory: $BOT_DIR"
BOT_DIR="$(cd "$BOT_DIR" && pwd)"

command -v git >/dev/null || die "git not found"
command -v tar >/dev/null || die "tar not found"
command -v python3 >/dev/null || die "python3 not found"
[[ -f "$BOT_DIR/.env" ]] || die "$BOT_DIR/.env missing — wrong --bot-dir?"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_PARENT/gemma_private_full_${STAMP}.tar.gz"
ENV_COPY="$BACKUP_PARENT/gemma_env_${STAMP}.bak"
RENAMED="${BOT_DIR}_private_${STAMP}"
CLONE_TMP="$(dirname "$BOT_DIR")/gemma_agent_public_${STAMP}"
ROLLBACK_NOTE="$BACKUP_PARENT/ROLLBACK_${STAMP}.txt"

# --- preflight ---
if [[ "$SKIP_STOP_CHECK" -eq 0 ]]; then
  if pgrep -f "python.*${BOT_DIR}.*main\\.py" >/dev/null 2>&1; then
    die "Bot still running under $BOT_DIR — stop first: systemctl stop gemma_bot.service"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet gemma_bot.service 2>/dev/null; then
      die "gemma_bot.service is active — run: sudo systemctl stop gemma_bot.service"
    fi
  fi
fi

for key in TELEGRAM_TOKEN OPENROUTER_API_KEY; do
  if ! grep -qE "^${key}=.+" "$BOT_DIR/.env" 2>/dev/null; then
    die ".env missing or empty: $key"
  fi
done

DIR_KB="$(du -sk "$BOT_DIR" | awk '{print $1}')"
AVAIL_KB="$(df -k "$(dirname "$BOT_DIR")" | awk 'NR==2 {print $4}')"
NEED_KB=$((DIR_KB * 2))
if [[ "$AVAIL_KB" -lt "$NEED_KB" ]]; then
  die "Low disk: need ~$((NEED_KB / 1024)) MB free, have ~$((AVAIL_KB / 1024)) MB"
fi

info "Plan ($STAMP):"
info "  bot-dir:    $BOT_DIR ($(du -sh "$BOT_DIR" | awk '{print $1}'))"
info "  archive:    $ARCHIVE"
info "  env copy:   $ENV_COPY"
info "  renamed:    $RENAMED"
info "  clone:      $REPO_URL @ $BRANCH"
info "  rollback:   $ROLLBACK_NOTE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "[dry-run] OK — no changes made"
  exit 0
fi

trap restore_on_fail ERR

mkdir -p "$BACKUP_PARENT"
info "Extra .env copy…"
cp -a "$BOT_DIR/.env" "$ENV_COPY"
chmod 600 "$ENV_COPY"

info "Full tarball (includes .env, venv, data)…"
tar -czf "$ARCHIVE" -C "$(dirname "$BOT_DIR")" "$(basename "$BOT_DIR")"
info "Archive: $ARCHIVE ($(du -h "$ARCHIVE" | awk '{print $1}'))"

cat >"$ROLLBACK_NOTE" <<EOF
Gemma migrate snapshot $STAMP

Rollback (fast — renamed dir):
  sudo systemctl stop gemma_bot.service
  rm -rf $BOT_DIR
  mv $RENAMED $BOT_DIR
  sudo systemctl start gemma_bot.service

Rollback (from tarball):
  sudo systemctl stop gemma_bot.service
  rm -rf $BOT_DIR
  tar -xzf $ARCHIVE -C $(dirname "$BOT_DIR")
  sudo systemctl start gemma_bot.service

.env only: cp -a $ENV_COPY $BOT_DIR/.env && chmod 600 $BOT_DIR/.env

Or: bash scripts/rollback_to_private.sh --rollback-dir $RENAMED --bot-dir $BOT_DIR
EOF

info "Renaming current install…"
mv "$BOT_DIR" "$RENAMED"

info "Cloning public repo…"
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$CLONE_TMP"

info "Restore .env + data/ (not private modules_catalog)…"
cp -a "$RENAMED/.env" "$CLONE_TMP/.env"
chmod 600 "$CLONE_TMP/.env"
if [[ -d "$RENAMED/data" ]]; then
  cp -a "$RENAMED/data" "$CLONE_TMP/"
fi
if [[ -f "$RENAMED/config/heuristic_fixes.json" ]]; then
  mkdir -p "$CLONE_TMP/config"
  cp -a "$RENAMED/config/heuristic_fixes.json" "$CLONE_TMP/config/"
fi

info "Merge .env with public .env.example…"
python3 "$CLONE_TMP/scripts/sync_env_from_example.py" "$CLONE_TMP/.env"

# Sensible prod defaults (only if unset or falsey in merged file)
for kv in \
  "VOICE_ENABLED=false" \
  "VOICE_STT_ENABLED=false" \
  "VOICE_TTS_ENABLED=false" \
  "USER_ACCESS_APPROVAL_REQUIRED=true"; do
  key="${kv%%=*}"
  val="${kv#*=}"
  if ! grep -qE "^${key}=" "$CLONE_TMP/.env" 2>/dev/null; then
    echo "${key}=${val}" >>"$CLONE_TMP/.env"
  fi
done

info "Move public tree into place…"
mv "$CLONE_TMP" "$BOT_DIR"
CLONE_TMP=""

info "Bootstrap (fresh venv)…"
bash "$BOT_DIR/scripts/agent_bootstrap.sh" "$BOT_DIR"

trap - ERR
RESTORED=1

info "Smoke check…"
"$BOT_DIR/venv/bin/python3" "$BOT_DIR/scripts/release_guard.py" --smoke

info "Done."
info "  Active:       $BOT_DIR"
info "  Rollback dir: $RENAMED"
info "  Tarball:      $ARCHIVE"
info "  Notes:        $ROLLBACK_NOTE"
info "Start:"
info "  bash $BOT_DIR/scripts/gemma_panel.sh start-all"
info "  python $BOT_DIR/scripts/gemma_status.py --online"
