#!/usr/bin/env bash
# Безопасный git pull: если меняли scripts/gemma_panel.sh, сначала stash, потом pull, потом stash pop.
# Пароль не нужен (если только git не спрашивает credentials для origin).
#
# Использование:
#   bash scripts/gemma_git_pull_safe.sh [BOT_DIR]
#   GEMMA_BOT_DIR=/opt/gemma_agent bash scripts/gemma_git_pull_safe.sh
#
# Сбросить ЛОКАЛЬНЫЕ правки только в панели и взять версию из репозитория:
#   bash scripts/gemma_git_pull_safe.sh /opt/gemma_agent --discard-panel
set -o pipefail
set -u

BOT_DIR="${GEMMA_BOT_DIR:-/opt/gemma_agent}"
DISCARD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --discard-panel) DISCARD=1 ;;
    -*)
      echo "Неизвестный аргумент: $1" >&2
      exit 1 ;;
    *) BOT_DIR="$1" ;;
  esac
  shift
done

cd "$BOT_DIR" || { echo "Нет каталога: $BOT_DIR"; exit 1; }

if [[ ! -d .git ]]; then
  echo "Не git-репозиторий: $BOT_DIR"
  exit 1
fi

if [[ "$DISCARD" -eq 1 ]]; then
  echo "==> Сбрасываю локальные правки только в scripts/gemma_panel.sh (как в origin после pull)"
  git checkout -- scripts/gemma_panel.sh 2>/dev/null || true
fi

stash=0
if ! git diff --quiet -- scripts/gemma_panel.sh 2>/dev/null; then
  echo "==> Локально изменён scripts/gemma_panel.sh — stash только этого файла"
  git stash push -m "gemma_panel safe-pull $(date -Iseconds 2>/dev/null || date)" -- scripts/gemma_panel.sh
  stash=1
fi

echo "==> git pull"
if ! git pull; then
  echo "git pull завершился с ошибкой."
  if [[ "$stash" -eq 1 ]]; then
    echo "Вернуть отложенный файл: git stash list | head -1 ; git stash pop"
  fi
  exit 1
fi

if [[ "$stash" -eq 1 ]]; then
  echo "==> git stash pop (если конфликт — откройте scripts/gemma_panel.sh и разрулите маркеры)"
  if ! git stash pop; then
    echo ""
    echo "Конфликт при stash pop. Варианты:"
    echo "  1) Вручную поправить scripts/gemma_panel.sh, затем: git add scripts/gemma_panel.sh && git stash drop"
    echo "  2) Взять версию из репозитория и потерять локальные правки в панели:"
    echo "       git checkout -- scripts/gemma_panel.sh && git stash drop"
    echo "  Лучше не править панель на сервере — используйте scripts/gemma_panel.local.conf"
    exit 1
  fi
fi

echo "==> OK: репозиторий обновлён."
chmod +x scripts/gemma_panel.sh 2>/dev/null || true
chmod +x scripts/gemma_collect_diagnostics.sh scripts/gemma_git_pull_safe.sh scripts/gemma_host_setup.sh 2>/dev/null || true
