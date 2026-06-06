#!/usr/bin/env bash
# Dependency vulnerability scan (CI + local).
# aiohttp<3.14 is required by aiogram 3.28 — CVEs fixed in 3.14+; ignored until aiogram allows bump.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec pip-audit -r requirements.txt \
  --ignore-vuln CVE-2026-34993 \
  --ignore-vuln CVE-2026-47265
