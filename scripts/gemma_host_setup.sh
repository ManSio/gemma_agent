#!/usr/bin/env bash
# Первичная настройка на Linux-хосте: права на запуск скриптов, шаблон local.conf, опционально владелец data/.
# Пароль sudo вводится стандартно в запросе sudo (мы НЕ храним пароль в скрипте).
#
#   bash scripts/gemma_host_setup.sh [BOT_DIR]
#
# Владелец data/ поправить автоматически (нужен sudo):
#   GEMMA_FIX_DATA_OWNER=1 bash scripts/gemma_host_setup.sh
set -o pipefail
set -u

BOT_DIR="${1:-${GEMMA_BOT_DIR:-/opt/gemma_agent}}"
BOT_DIR="$(cd "$BOT_DIR" 2>/dev/null && pwd || echo "$BOT_DIR")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Gemma host setup"
echo "  BOT_DIR=$BOT_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

need_sudo() {
  if [[ "${EUID:-0}" -ne 0 ]]; then
    echo ""
    echo "Нужны права sudo (для chown data/, если включено). Введите пароль по запросу:"
    sudo -v || { echo "sudo отменён."; exit 1; }
  fi
}

chmod +x "$SCRIPT_DIR/gemma_panel.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/gemma_collect_diagnostics.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/gemma_git_pull_safe.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/gemma_host_setup.sh" 2>/dev/null || true
echo "✓ chmod +x для скриптов в $SCRIPT_DIR"

LOCAL_EX="$SCRIPT_DIR/gemma_panel.local.conf.example"
LOCAL="$SCRIPT_DIR/gemma_panel.local.conf"
if [[ -f "$LOCAL_EX" && ! -f "$LOCAL" ]]; then
  cp -n "$LOCAL_EX" "$LOCAL"
  echo "✓ Создан $LOCAL (подправьте пути при необходимости)"
elif [[ -f "$LOCAL" ]]; then
  echo "· $LOCAL уже есть — не трогаю"
fi

if [[ "${GEMMA_FIX_DATA_OWNER:-}" == "1" ]] || [[ "${GEMMA_FIX_DATA_OWNER:-}" == "true" ]]; then
  need_sudo
  OWNER="${SUDO_USER:-${USER:-}}"
  if [[ -z "$OWNER" ]]; then
    echo "Не удалось определить пользователя (SUDO_USER/USER). Задайте: GEMMA_DATA_OWNER=имя"
    OWNER="${GEMMA_DATA_OWNER:-}"
  fi
  if [[ -n "$OWNER" && -d "$BOT_DIR/data" ]]; then
    sudo chown -R "$OWNER:$OWNER" "$BOT_DIR/data"
    echo "✓ chown -R $OWNER:$OWNER $BOT_DIR/data"
  else
    echo "! Пропуск chown: нет OWNER или нет $BOT_DIR/data"
  fi
else
  echo "· Владелец data/ не менялся (для авто-исправления: GEMMA_FIX_DATA_OWNER=1 bash $0 ...)"
fi

mkdir -p "$BOT_DIR/data/diagnostics" 2>/dev/null || true

echo ""
echo "Обновление кода без конфликта с панелью:"
echo "  bash $SCRIPT_DIR/gemma_git_pull_safe.sh \"$BOT_DIR\""
echo "  или сбросить только локальные правки панели:"
echo "  bash $SCRIPT_DIR/gemma_git_pull_safe.sh \"$BOT_DIR\" --discard-panel"
echo ""
echo "Диагностика для чата/поддержки:"
echo "  bash $SCRIPT_DIR/gemma_collect_diagnostics.sh \"$BOT_DIR\""
echo ""

if [[ -x "$SCRIPT_DIR/gemma_collect_diagnostics.sh" ]]; then
  bash "$SCRIPT_DIR/gemma_collect_diagnostics.sh" "$BOT_DIR" || true
fi
