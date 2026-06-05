#!/usr/bin/env bash
# Однократно от root/deploy: владелец и базовые права на дерево проекта.
# Запуск бота сам создаёт data/* и chmod каталогов (см. RUNTIME_* в .env).
set -euo pipefail
TARGET="${1:-/opt/gemma_agent}"
OWNER="${2:-gemma:gemma}"
if [[ ! -d "$TARGET" ]]; then
  echo "Нет каталога: $TARGET" >&2
  exit 1
fi
chown -R "$OWNER" "$TARGET"
find "$TARGET" -type d -exec chmod 755 {} \;
find "$TARGET" -type f -exec chmod 644 {} \;
if [[ -f "$TARGET/.env" ]]; then
  chmod 600 "$TARGET/.env"
fi
echo "OK: $TARGET -> $OWNER (dirs 755, files 644, .env 600)"
