#!/usr/bin/env bash
# Тюнинг нативного SearXNG (systemd): ru-RU, таймауты, включить yandex+bing.
set -euo pipefail
SETTINGS="${SEARXNG_SETTINGS_PATH:-/etc/searxng/settings.yml}"
if [[ ! -f "$SETTINGS" ]]; then
  echo "No settings at $SETTINGS" >&2
  exit 1
fi
cp -a "$SETTINGS" "${SETTINGS}.bak-gemma-$(date -u +%Y%m%dT%H%M%SZ)"
python3 <<'PY'
import re
import shutil
from pathlib import Path

path = Path("/etc/searxng/settings.yml")
text = path.read_text(encoding="utf-8")
orig = text

text = re.sub(r'^(\s*default_lang:\s*)"auto"', r'\1"ru-RU"', text, count=1, flags=re.M)
text = re.sub(r'^(\s*request_timeout:\s*)3\.0', r"\g<1>5.0", text, count=1, flags=re.M)
if "max_request_timeout:" not in text:
    text = re.sub(
        r"^(\s*request_timeout:\s*[\d.]+)\s*$",
        r"\1\n\1".replace("request_timeout", "max_request_timeout: 15.0").split("\n")[0]
        + "\n  max_request_timeout: 15.0",
        text,
        count=1,
        flags=re.M,
    )
else:
    text = re.sub(r"^(\s*max_request_timeout:\s*)[\d.]+", r"\g<1>15.0", text, count=1, flags=re.M)

for name in ("yandex", "bing"):
    pat = rf"(- name: {re.escape(name)}\n(?:    [^\n]+\n)*?    disabled: )true"
    text, n = re.subn(pat, r"\1false", text, count=1)
    if n == 0:
        print(f"warn: engine {name} block not patched")

if text == orig:
    print("warn: no changes applied")
path.write_text(text, encoding="utf-8")
print("OK: patched", path)
PY
systemctl restart searxng
sleep 2
code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/" || echo "000")
echo "searxng HTTP $code"
