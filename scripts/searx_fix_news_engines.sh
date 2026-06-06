#!/usr/bin/env bash
# SearXNG: отключить мёртвые движки (google news, DDG), включить bing/yandex.
# Запуск на сервере: sudo bash scripts/searx_fix_news_engines.sh
set -euo pipefail
SETTINGS="${SEARXNG_SETTINGS_PATH:-/etc/searxng/settings.yml}"
if [[ ! -f "$SETTINGS" ]]; then
  echo "No settings at $SETTINGS" >&2
  exit 1
fi
cp -a "$SETTINGS" "${SETTINGS}.bak-gemma-engines-$(date -u +%Y%m%dT%H%M%SZ)"
python3 <<'PY'
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

path = Path("/etc/searxng/settings.yml")
DISABLE = {
    "google news",
    "duckduckgo",
    "duckduckgo news",
    "duckduckgo images",
    "duckduckgo videos",
}
ENABLE = {"bing", "google", "yandex"}

d = yaml.safe_load(path.read_text(encoding="utf-8"))
changed: list[str] = []
for eng in d.get("engines", []):
    name = str(eng.get("name") or "")
    if name in DISABLE:
        if eng.get("disabled") is not True:
            eng["disabled"] = True
            changed.append(f"off {name}")
    elif name in ENABLE:
        if eng.get("disabled") is True:
            eng["disabled"] = False
            changed.append(f"on {name}")

if not changed:
    print("no engine changes needed")
else:
    print("changed:", ", ".join(changed))

bak = path.with_name(
    path.name + ".bak-gemma-yaml-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)
shutil.copy2(path, bak)
path.write_text(
    yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print("backup:", bak)
print("patched", path)
PY
systemctl restart searxng
sleep 2
code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/" || echo "000")
echo "searxng HTTP $code"
