#!/usr/bin/env python3
"""CLI для core.memory_ops_report (D2)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from core.memory_ops_report import build_memory_ops_report

    ap = argparse.ArgumentParser(description="Memory Ops read-only report")
    ap.add_argument("--user-id", default="", help="Optional behavior session user_id")
    ap.add_argument("--turns", type=int, default=25, help="Last N turns")
    ap.add_argument("--memory-limit", type=int, default=5, help="Tail per JSONL memory file")
    ap.add_argument("--misses-tail", type=int, default=200)
    args = ap.parse_args()
    try:
        print(
            build_memory_ops_report(
                user_id=args.user_id,
                turns_limit=args.turns,
                memory_limit=args.memory_limit,
                misses_tail=args.misses_tail,
                include_cli_hint=True,
            )
        )
    except Exception as e:
        print(f"memory_ops_report error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
