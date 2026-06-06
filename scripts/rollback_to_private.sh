#!/usr/bin/env bash
# Restore private/full install after migrate_private_to_public.sh
#
#   bash scripts/rollback_to_private.sh --rollback-dir /srv/gemma_bot_private_20260606T120000Z --bot-dir /srv/gemma_bot
#   bash scripts/rollback_to_private.sh --archive /var/backups/gemma/gemma_private_full_*.tar.gz --bot-dir /srv/gemma_bot
set -euo pipefail

BOT_DIR=""
ROLLBACK_DIR=""
ARCHIVE=""
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage:
  rollback_to_private.sh --rollback-dir PATH --bot-dir PATH
  rollback_to_private.sh --archive PATH --bot-dir PATH

  --dry-run   Show plan only
EOF
  exit "${1:-0}"
}

info() { printf '[rollback] %s\n' "$*"; }
die() { printf '[rollback] ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bot-dir) BOT_DIR="$2"; shift 2 ;;
    --rollback-dir) ROLLBACK_DIR="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) die "Unknown: $1" ;;
  esac
done

[[ -n "$BOT_DIR" ]] || die "--bot-dir required"
[[ -n "$ROLLBACK_DIR" || -n "$ARCHIVE" ]] || die "need --rollback-dir or --archive"

if pgrep -f "python.*main\\.py" >/dev/null 2>&1; then
  die "Stop bot first: systemctl stop gemma_bot.service"
fi

if [[ -n "$ROLLBACK_DIR" ]]; then
  [[ -d "$ROLLBACK_DIR" ]] || die "Not found: $ROLLBACK_DIR"
  info "Restore from directory: $ROLLBACK_DIR → $BOT_DIR"
  if [[ "$DRY_RUN" -eq 1 ]]; then exit 0; fi
  rm -rf "$BOT_DIR"
  mv "$ROLLBACK_DIR" "$BOT_DIR"
elif [[ -n "$ARCHIVE" ]]; then
  [[ -f "$ARCHIVE" ]] || die "Not found: $ARCHIVE"
  info "Extract $ARCHIVE → $(dirname "$BOT_DIR")"
  if [[ "$DRY_RUN" -eq 1 ]]; then exit 0; fi
  rm -rf "$BOT_DIR"
  tar -xzf "$ARCHIVE" -C "$(dirname "$BOT_DIR")"
fi

info "OK. Start: systemctl start gemma_bot.service  OR  bash $BOT_DIR/scripts/gemma_panel.sh start-all"
