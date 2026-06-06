#!/usr/bin/env python3
"""
Краткая сводка по накопленным снимкам metrics_snapshots.jsonl.

  python scripts/metrics_period_history.py
  python scripts/metrics_period_history.py --path data/benchmarks/metrics_snapshots.jsonl --last 10
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_snapshots(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="data/benchmarks/metrics_snapshots.jsonl")
    ap.add_argument("--last", type=int, default=20)
    args = ap.parse_args()
    path = Path(args.path)
    snaps = _load_snapshots(path)[-max(1, args.last) :]
    if not snaps:
        print("No snapshots at", path)
        print("Run: python scripts/metrics_period_report.py --history", path)
        return 1

    print(f"{'snapshot_id':<28} {'days':>5} {'llm':>6} {'agent_p95':>10} {'llm_p95':>8} {'llm%':>6} root")
    print("-" * 90)
    for s in snaps:
        rep = s.get("report") or {}
        summ = s.get("summary") or {}
        # последний день с pipeline correlation
        llm_share = None
        agent_p95 = None
        llm_p95 = None
        for row in reversed(rep.get("daily") or []):
            pl = row.get("pipeline") or {}
            ag = row.get("agent") or {}
            lm = row.get("llm") or {}
            if pl.get("llm_share_of_turn_pct") is not None and llm_share is None:
                llm_share = pl.get("llm_share_of_turn_pct")
            if ag.get("latency_ms_p95") and agent_p95 is None:
                agent_p95 = ag.get("latency_ms_p95")
            if lm.get("latency_ms_p95") and llm_p95 is None:
                llm_p95 = lm.get("latency_ms_p95")
        root = str(rep.get("root") or "")[-40:]
        print(
            f"{str(s.get('snapshot_id',''))[:28]:<28} "
            f"{summ.get('days',0):>5} "
            f"{summ.get('llm_records',0):>6} "
            f"{agent_p95 if agent_p95 is not None else '—':>10} "
            f"{llm_p95 if llm_p95 is not None else '—':>8} "
            f"{llm_share if llm_share is not None else '—':>6} "
            f"{root}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
