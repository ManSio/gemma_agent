#!/usr/bin/env python3
"""METR-style reliability horizon из turns.jsonl (read-only).

Аналог METR 50%-time horizon: здесь — 50% сессий с N успешными ходами подряд.

Запуск:
  python scripts/agent_reliability_horizon.py
  python scripts/agent_reliability_horizon.py --days 14 --json-out data/benchmarks/horizon_latest.json
  python scripts/agent_reliability_horizon.py --user-id 123456
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research.reliability_horizon import compute_horizon_report  # noqa: E402


def _default_turns_path() -> Path:
    for rel in ("data/runtime/turns.jsonl", "data/turns.jsonl"):
        p = ROOT / rel
        if p.is_file():
            return p
    return ROOT / "data/runtime/turns.jsonl"


def _format_human(report: dict) -> str:
    lines = [
        "=== Agent reliability horizon (METR-analogue) ===",
        f"Окно: {report.get('window_days')} дн., сессии: {report.get('sessions_n')}",
        f"Горизонт ходов (50% сессий): {report.get('horizon_turns_50pct')}",
        f"Медиана длительности лучшей серии: {report.get('horizon_streak_minutes_median')} мин",
        f"Max streak p50/p90: {report.get('session_max_streak', {})}",
        f"Outcomes: {report.get('outcome_counts')}",
        f"Article thread OK%: {report.get('article_thread')}",
        report.get("interpretation", ""),
    ]
    return "\n".join(str(x) for x in lines if x)


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Reliability horizon from turns.jsonl")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--turns-path", default="")
    ap.add_argument("--user-id", default="")
    ap.add_argument("--session-gap-min", type=int, default=0, help="0 = env AGENT_RELIABILITY_SESSION_GAP_MIN")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--quiet", action="store_true", help="только JSON при --json-out")
    args = ap.parse_args()

    turns_path = Path(
        args.turns_path or os.getenv("GEMMA_TURNS_LOG_PATH") or _default_turns_path()
    )
    gap = args.session_gap_min or None
    report = compute_horizon_report(
        turns_path,
        days=args.days,
        session_gap_minutes=gap,
        user_id_filter=args.user_id,
    )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"Wrote {out}")

    if not args.quiet or not args.json_out:
        print(_format_human(report))
        if args.json_out and not args.quiet:
            print(f"\n(JSON: {args.json_out})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
