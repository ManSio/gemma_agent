#!/usr/bin/env bash
# Перезапуск Docker SearXNG после правки infra/searxng/settings.yml
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra/searxng"
docker compose restart searxng
sleep 4
code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/search?q=test&format=json" || echo "000")
echo "searxng json probe HTTP $code"
