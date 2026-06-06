#!/usr/bin/env python3
"""
Сводка бэклога владельца (P1–P3) + инженерные метрики — без «бутафории».

Запуск:
  python scripts/backlog_status_report.py
  python scripts/backlog_status_report.py --days 7
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> int:
    print(f"\n>>> {' '.join(cmd)}\n")
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    days = max(1, args.days)

    print("=== Gemma backlog status (owner P1–P3) ===\n")
    print("P1 C6 A/B recent count — см. scripts/analyze_brain_recent_ab.py")
    print("P2 KV session — см. scripts/analyze_kv_session_metrics.py")
    print("P3 pre-LLM intents — core/intent_heuristics.detect_text_intent + slash commands")
    print("Slots — core/dialogue_slots.py (DIALOGUE_SLOTS_ENABLED=true)\n")

    rc = 0
    ab = ROOT / "scripts" / "analyze_brain_recent_ab.py"
    if ab.is_file():
        rc |= _run([sys.executable, str(ab), "--days", str(days)])
    kv = ROOT / "scripts" / "analyze_kv_session_metrics.py"
    if kv.is_file():
        rc |= _run([sys.executable, str(kv), "--days", str(days)])

    print("\n=== Ручное закрытие (только владелец) ===")
    print("- REFORM_S9_ACCEPTANCE_TRACKER_RU.md — ≥9/10 в Telegram")
    print("- C6: 3–7 дней LAN с BRAIN_STANDARD_RECENT_COUNT=12 → решение по отчёту AB")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
