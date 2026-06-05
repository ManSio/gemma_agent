#!/usr/bin/env bash
# Удаление известного дрейфа на сервере (stale scp, старые пути core/pipeline.py, бэкапы .env).
# Не трогает .env, data/, venv целиком.
#
#   bash scripts/gemma_clean_server_drift.sh [BOT_DIR]
set -o pipefail
set -u

BOT_DIR="${1:-${GEMMA_BOT_DIR:-/opt/gemma_agent}}"
cd "$BOT_DIR" || { echo "Нет каталога: $BOT_DIR"; exit 1; }

if [[ ! -d .git ]]; then
  echo "Не git-репозиторий: $BOT_DIR"
  exit 1
fi

removed=0

_remove() {
  local p="$1"
  if [[ -e "$p" ]]; then
    rm -rf -- "$p"
    echo "removed: $p"
    removed=$((removed + 1))
  fi
}

# Старые пути до переноса в core/brain/ (опасны: путаница при scp, не используются импортами).
_remove "core/pipeline.py"
_remove "core/prompt_pack.py"

# Дубликат корневого CHANGELOG.md (часто от старого scp в docs/).
_remove "docs/CHANGELOG.md"

# Бэкапы секретов — не в git.
for f in config/agent_telegram.env.bak.* config/*.env.bak.*; do
  [[ -e "$f" ]] || continue
  _remove "$f"
done

echo ""
echo "==> git status (ожидается: только venv/ вне .gitignore или чисто)"
git status -sb 2>/dev/null | head -12

if [[ "$removed" -eq 0 ]]; then
  echo "OK: известный дрейф не найден."
else
  echo "OK: удалено файлов/каталогов: $removed"
fi
