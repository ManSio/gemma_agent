#!/usr/bin/env python3
"""Деактивировать legacy ephemeral lessons без anchor_user_q (prod cleanup)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.ephemeral_lessons import deactivate_legacy_generic_rating_lessons, snapshot_for_operator

    before = snapshot_for_operator()
    n = deactivate_legacy_generic_rating_lessons()
    after = snapshot_for_operator()
    print(f"[OK] deactivated={n} active_before={before.get('active_count')} active_after={after.get('active_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
