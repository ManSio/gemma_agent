#!/usr/bin/env python3
"""
Онлайн-проверка Telegram + OpenRouter (таймаут 20 с по умолчанию).

    python scripts/check_connectivity.py
    CONNECTIVITY_CHECK_TIMEOUT_SEC=25 python scripts/check_connectivity.py

Загружает .env из корня проекта.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка Telegram + OpenRouter")
    ap.add_argument(
        "--http-probes",
        action="store_true",
        help="Добавить параллельные HTTP-замеры до openrouter/telegram/cloudflare",
    )
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    from core.connectivity_check import run_connectivity_checks

    report = asyncio.run(run_connectivity_checks(include_http_probes=args.http_probes))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for ln in report.get("lines") or []:
        if ln:
            print("—", ln)
    print("timeout_sec:", report.get("timeout_sec"))
    print("OK" if report.get("ok") else "FAIL")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
