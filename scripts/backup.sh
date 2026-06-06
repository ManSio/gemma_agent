#!/usr/bin/env bash
# Backup Gemma Agent runtime data and config (not .env secrets).
#
#   bash scripts/backup.sh
#   bash scripts/backup.sh --dry-run
#   bash scripts/backup.sh --dest /var/backups/gemma
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEST="${BACKUP_DEST:-$ROOT/backups}"
DRY_RUN=0
KEEP=7

usage() {
  echo "Usage: bash scripts/backup.sh [--dest DIR] [--keep N] [--dry-run]"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown arg: $1" >&2; usage 1 ;;
  esac
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="gemma_agent_backup_${STAMP}.tar.gz"
ARCHIVE_PATH="$DEST/$ARCHIVE_NAME"

mkdir -p "$DEST"

INCLUDE=()
for path in data config/modules_catalog.json config/heuristic_fixes.json; do
  if [[ -e "$path" ]]; then
    INCLUDE+=("$path")
  fi
done

if [[ ${#INCLUDE[@]} -eq 0 ]]; then
  echo "Nothing to backup (no data/ or config files found)." >&2
  exit 1
fi

echo "Backup source: $ROOT"
echo "Archive:       $ARCHIVE_PATH"
echo "Includes:      ${INCLUDE[*]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] tar -czf $ARCHIVE_PATH ${INCLUDE[*]}"
  exit 0
fi

tar -czf "$ARCHIVE_PATH" "${INCLUDE[@]}"
echo "OK: $(du -h "$ARCHIVE_PATH" | awk '{print $1}')"

# Prune old backups
if [[ "$KEEP" -gt 0 ]]; then
  mapfile -t OLD < <(ls -1t "$DEST"/gemma_agent_backup_*.tar.gz 2>/dev/null || true)
  if [[ ${#OLD[@]} -gt "$KEEP" ]]; then
    for f in "${OLD[@]:$KEEP}"; do
      rm -f "$f"
      echo "Pruned: $f"
    done
  fi
fi
