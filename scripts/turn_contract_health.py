#!/usr/bin/env python3
"""Health-check TurnContract gates + fingerprint stall alert (Phase 0.4)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_turn_rows(path: Path, *, limit: int = 1000) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("type") not in ("scenario", "pre_send"):
            rows.append(row)
    if limit > 0:
        return rows[-limit:]
    return rows


def gate0_referent_fingerprint(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gate 0: доля ходов с referent + fingerprint coverage."""
    total = len(rows)
    if not total:
        return {"ok": False, "reason": "no_rows", "total": 0}
    with_ref = sum(1 for r in rows if str(r.get("referent") or "").strip())
    recent = sum(1 for r in rows if str(r.get("recent_fingerprint") or "").strip())
    with_fp = sum(1 for r in rows if str(r.get("recent_fingerprint") or r.get("fp") or "").strip())
    ref_pct = round(100.0 * with_ref / total, 1)
    recent_pct = round(100.0 * recent / total, 1)
    fp_pct = round(100.0 * with_fp / total, 1)
    ok = ref_pct >= 90.0 and fp_pct >= 90.0
    return {
        "ok": ok,
        "total": total,
        "referent_pct": ref_pct,
        "recent_fingerprint_pct": recent_pct,
        "fingerprint_pct": fp_pct,
        "fp_fallback_rows": with_fp - recent,
    }


def gate1_issues(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gate 1 proxy: issues rate + anti_echo hits."""
    total = len(rows)
    issues = sum(1 for r in rows if r.get("issues"))
    anti_echo = sum(
        1
        for r in rows
        if any("anti_echo" in str(x).lower() for x in (r.get("issues") or []))
        or "anti_echo" in str(r.get("detail") or "").lower()
    )
    iss_pct = round(100.0 * issues / total, 1) if total else 0.0
    return {
        "ok": iss_pct < 15.0,
        "total": total,
        "issues_pct": iss_pct,
        "anti_echo_rows": anti_echo,
    }


def gate2_drift(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gate 2 proxy: turn_hash_drift rate."""
    total = len(rows)
    drift = sum(1 for r in rows if r.get("turn_hash_drift"))
    drift_pct = round(100.0 * drift / total, 1) if total else 0.0
    return {"ok": drift_pct < 20.0, "total": total, "drift_pct": drift_pct}


def main(argv: List[str] | None = None) -> int:
    """CLI entry."""
    ap = argparse.ArgumentParser(description="TurnContract health gates + fingerprint stall")
    ap.add_argument("--turns", default="", help="Path to turns.jsonl")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--regression", action="store_true", help="Also run structural regression 20")
    args = ap.parse_args(argv)

    from core.turn_fingerprint_alert import scan_fingerprint_stalls
    from core.turn_observer import log_path

    turns_path = Path(args.turns) if args.turns else log_path()
    rows = _load_turn_rows(turns_path, limit=args.limit)
    stalls = scan_fingerprint_stalls(path=turns_path, limit=args.limit)

    report: Dict[str, Any] = {
        "turns_path": str(turns_path),
        "rows": len(rows),
        "gate0": gate0_referent_fingerprint(rows),
        "gate1": gate1_issues(rows),
        "gate2": gate2_drift(rows),
        "fingerprint_stalls": stalls,
        "fingerprint_stall_ok": not stalls,
    }

    if args.regression:
        from core.turn_regression import run_regression_suite

        reg = run_regression_suite()
        report["regression"] = {
            "ok": reg.get("failed", 1) == 0,
            "total": reg.get("total"),
            "failed": reg.get("failed"),
        }

    all_ok = (
        report["gate0"].get("ok")
        and report["gate1"].get("ok")
        and report["gate2"].get("ok")
        and report["fingerprint_stall_ok"]
        and (not args.regression or report.get("regression", {}).get("ok"))
    )
    report["ok"] = bool(all_ok)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"turns={report['rows']} path={turns_path}")
        g0 = report["gate0"]
        print(
            f"gate0 referent={g0.get('referent_pct')}% "
            f"recent_fp={g0.get('recent_fingerprint_pct')}% "
            f"fp={g0.get('fingerprint_pct')}% ok={g0.get('ok')}"
        )
        g1 = report["gate1"]
        print(f"gate1 issues={g1.get('issues_pct')}% ok={g1.get('ok')}")
        g2 = report["gate2"]
        print(f"gate2 drift={g2.get('drift_pct')}% ok={g2.get('ok')}")
        print(f"fingerprint_stalls={len(stalls)} ok={report['fingerprint_stall_ok']}")
        if stalls:
            for a in stalls[:5]:
                print(
                    f"  STALL chat={a.get('chat_key')} fp={a.get('fingerprint')} "
                    f"turns={a.get('turns')} span_min={a.get('span_minutes')}"
                )
        if args.regression:
            rg = report.get("regression") or {}
            print(f"regression {rg.get('total')} failed={rg.get('failed')} ok={rg.get('ok')}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
