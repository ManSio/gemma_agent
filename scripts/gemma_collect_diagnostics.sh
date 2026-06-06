#!/usr/bin/env bash
# Сбор сведений о хосте для отладки (без значений секретов из .env).
# Запуск: bash scripts/gemma_collect_diagnostics.sh [BOT_DIR]
# Выход: печать в stdout + файл data/diagnostics/gemma_host_collect_<date>.txt
set -o pipefail
set -u

BOT_DIR="${1:-${GEMMA_BOT_DIR:-/opt/gemma_agent}}"
BOT_DIR="$(cd "$BOT_DIR" 2>/dev/null && pwd || echo "$BOT_DIR")"
OUT_DIR="$BOT_DIR/data/diagnostics"
STAMP="$(date -u +"%Y%m%d_%H%M%SUTC")"
OUT="$OUT_DIR/gemma_host_collect_${STAMP}.txt"
mkdir -p "$OUT_DIR" 2>/dev/null || OUT="/tmp/gemma_host_collect_${STAMP}.txt"

sec() {
  echo ""
  echo "=== $1 ==="
  shift
  "$@" 2>&1 || echo "(команда завершилась с ошибкой — см. выше)"
}

{
  echo "gemma_collect_diagnostics.sh"
  echo "BOT_DIR=$BOT_DIR"
  echo "собрано UTC: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "whoami=$(whoami 2>/dev/null) uid=$(id -u 2>/dev/null) gid=$(id -g 2>/dev/null)"

  sec "uname" uname -a
  sec "hostnamectl" hostnamectl 2>/dev/null
  sec "df" df -h "$BOT_DIR" 2>/dev/null || df -h .

  if [[ -d "$BOT_DIR/.git" ]]; then
    sec "git HEAD" git -C "$BOT_DIR" rev-parse --short HEAD
    sec "git status" git -C "$BOT_DIR" status -sb
    sec "git remote" git -C "$BOT_DIR" remote -v
    sec "git diff gemma_panel (stat)" git -C "$BOT_DIR" diff --stat scripts/gemma_panel.sh 2>/dev/null
  else
    echo ""
    echo "=== git ==="
    echo "нет .git в $BOT_DIR"
  fi

  if [[ -f "$BOT_DIR/.env" ]]; then
    echo ""
    echo "=== .env: только имена переменных (значения не выводятся) ==="
    grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$BOT_DIR/.env" 2>/dev/null | sed 's/[[:space:]]*//;s/=.*//' | sort -u || true
  else
    echo ""
    echo "=== .env ==="
    echo "файл не найден"
  fi

  echo ""
  echo "=== панель и права ==="
  ls -la "$BOT_DIR/scripts/gemma_panel.sh" 2>/dev/null || echo "нет scripts/gemma_panel.sh"
  ls -ld "$BOT_DIR/data" 2>/dev/null || echo "нет data/"
  ls -la "$BOT_DIR/data/user_personas.json" 2>/dev/null || true

  sec "systemctl gemma_bot" systemctl show gemma_bot.service -p User -p Group -p MainPID -p ActiveState 2>/dev/null
  sec "systemctl is-active" systemctl is-active gemma_bot.service 2>/dev/null

  echo ""
  echo "=== процессы (фрагмент) ==="
  ps aux 2>/dev/null | grep -E '[m]ain\.py|[m]em0_server|[u]vicorn.*mem0' || echo "нет совпадений"

  echo ""
  echo "=== Mem0 HTTP (curl) ==="
  if command -v curl >/dev/null 2>&1; then
    for url in "http://127.0.0.1:8001/docs" "http://127.0.0.1:8001/openapi.json"; do
      code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null || echo err)"
      echo "$url -> HTTP $code"
    done
  else
    echo "curl нет"
  fi

  echo ""
  echo "=== хвост panel_nohup_bot.log (до 60 строк) ==="
  tail -n 60 "$BOT_DIR/panel_nohup_bot.log" 2>/dev/null || echo "нет panel_nohup_bot.log"

  echo ""
  echo "=== python ==="
  command -v python3 && python3 --version || true
  [[ -x "$BOT_DIR/venv/bin/python" ]] && "$BOT_DIR/venv/bin/python" --version || true

} | tee "$OUT"

echo >&2 ""
echo >&2 "Готово. Файл: $OUT"
echo >&2 "Прикрепите его или вставьте вывод в чат."
