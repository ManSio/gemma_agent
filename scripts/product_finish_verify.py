#!/usr/bin/env python3
"""Проверка закрытия PRODUCT_FINISH: pytest + ключевые env."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _env_ok(key: str, expected: str) -> bool:
    return (os.getenv(key) or "").strip().lower() == expected.lower()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    tests = [
        "tests/test_geo_location_reply.py",
        "tests/test_fallback_direct_reply.py",
        "tests/test_user_correction_bus.py",
        "tests/test_turn_observer_and_corrections.py",
        "tests/test_dialogue_lane.py",
        "tests/test_external_apis_clients.py",
        "tests/test_product_behavior.py",
    ]
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *tests],
        cwd=str(root),
    )
    if r.returncode != 0:
        print("[FAIL] pytest product_finish subset")
        return r.returncode
    print("[OK] pytest product_finish subset")
    want = {
        "MCE_AUTO_APPLY": "false",
        "BRAIN_DIRECT_DIALOG_ENABLED": "true",
        "TELEGRAM_PIPELINE_PRIVATE_PARALLEL": "1",
    }
    missing = [k for k, v in want.items() if not _env_ok(k, v)]
    if missing and (root / ".env").is_file():
        print(f"[WARN] в .env не совпадают (запустите ensure_product_finish_env.py): {', '.join(missing)}")
    elif missing:
        print(f"[WARN] env не заданы: {', '.join(missing)}")
    else:
        print("[OK] ключевые env")
    print("[OK] product_finish_verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
