#!/usr/bin/env python3
"""Объединённый offline-отчёт: policy memory + reliability horizon.

Запуск:
  python scripts/research_agent_metrics.py
  python scripts/research_agent_metrics.py --days 14
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research.policy_memory import load_gemma_profiles, run_matrix  # noqa: E402
from core.research.reliability_horizon import compute_horizon_report  # noqa: E402


def _default_turns_path() -> Path:
    for rel in ("data/runtime/turns.jsonl", "data/turns.jsonl"):
        p = ROOT / rel
        if p.is_file():
            return p
    return ROOT / "data/runtime/turns.jsonl"


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Combined agent research metrics")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json-out", default="data/benchmarks/agent_research_latest.json")
    args = ap.parse_args()

    turns_path = Path(os.getenv("GEMMA_TURNS_LOG_PATH") or _default_turns_path())
    combined = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "policy_memory": run_matrix(profiles=load_gemma_profiles()),
        "reliability_horizon": compute_horizon_report(turns_path, days=args.days),
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    interp = combined["reliability_horizon"].get("interpretation", "")
    try:
        print(interp)
    except UnicodeEncodeError:
        print(interp.encode("ascii", errors="replace").decode("ascii"))
    v = combined["policy_memory"].get("verdict", {})
    print(
        f"Policy memory: slots>trim1 {v.get('slots_beats_trim1_scenarios')}/{v.get('slots_beats_trim1_total')}, "
        f"wrong_transfer_ok={v.get('wrong_transfer_ok')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
