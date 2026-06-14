#!/usr/bin/env python3
"""Replay thread routing from ops_trace / turns.jsonl (structural, optional LLM)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


_ROOT = _project_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_jsonl(path: Path, *, limit: int = 0) -> List[Dict[str, Any]]:
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
        if isinstance(row, dict):
            rows.append(row)
    if limit > 0:
        return rows[-limit:]
    return rows


def _replay_structural(trace_row: Dict[str, Any]) -> Dict[str, Any]:
    """Structural replay: TurnMeaning + shortcut registry без LLM."""
    from core.turn_meaning import resolve_turn_meaning_structural
    from core.short_circuit_registry import lookup_short_circuit

    user_text = str(trace_row.get("user_text") or trace_row.get("user_excerpt") or "")
    recent = trace_row.get("recent_before") or trace_row.get("recent_dialogue") or []
    ctx: Dict[str, Any] = {"recent_dialogue": recent if isinstance(recent, list) else []}
    meaning = resolve_turn_meaning_structural(user_text, ctx)
    sc = str(trace_row.get("short_circuit") or trace_row.get("planner_bypass") or "")
    ent = lookup_short_circuit(sc) if sc else None
    return {
        "trace_id": str(trace_row.get("trace_id") or "")[:64],
        "user_text_head": user_text[:80],
        "referent": meaning.referent,
        "thread_action": meaning.thread_action,
        "short_circuit": sc or None,
        "registry_lane": (ent or {}).get("lane"),
        "recorded_lane": trace_row.get("lane"),
        "recorded_referent": trace_row.get("referent"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry."""
    ap = argparse.ArgumentParser(description="Replay turn thread from ops_trace or turns.jsonl")
    ap.add_argument(
        "--ops-trace",
        default=str(_project_root() / "data" / "runtime" / "ops_trace.jsonl"),
    )
    ap.add_argument(
        "--turns",
        default=str(_project_root() / "data" / "runtime" / "turns.jsonl"),
    )
    ap.add_argument("--trace-id", default="", help="Single trace_id to replay")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--regression", action="store_true", help="Run bundled 20-case regression suite")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.regression:
        from core.turn_regression import run_regression_suite

        rep = run_regression_suite()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"regression total={rep['total']} passed={rep['passed']} failed={rep['failed']}")
            for row in rep.get("results") or []:
                if not row.get("ok"):
                    print(f"  FAIL {row.get('id')}: {', '.join(row.get('mismatches') or [])}")
        return 1 if rep.get("failed") else 0

    rows = _load_jsonl(Path(args.ops_trace), limit=args.limit * 3)
    if not rows:
        rows = _load_jsonl(Path(args.turns), limit=args.limit * 3)
    if args.trace_id:
        tid = args.trace_id.strip()
        rows = [r for r in rows if str(r.get("trace_id") or "") == tid]
    rows = rows[-args.limit :]

    out: List[Dict[str, Any]] = []
    mismatches = 0
    for row in rows:
        rep = _replay_structural(row)
        ref_ok = not rep.get("recorded_referent") or rep.get("referent") == rep.get("recorded_referent")
        if not ref_ok:
            mismatches += 1
        rep["referent_match"] = ref_ok
        out.append(rep)

    if args.json:
        print(json.dumps({"replayed": len(out), "mismatches": mismatches, "rows": out}, ensure_ascii=False, indent=2))
    else:
        print(f"replayed={len(out)} referent_mismatches={mismatches}")
        for r in out:
            print(
                f"  {r.get('trace_id','-')[:12]} "
                f"act={r.get('thread_action')} ref={r.get('referent')} "
                f"sc={r.get('short_circuit') or '-'} match={r.get('referent_match')}"
            )
    return 1 if mismatches > len(out) // 2 and out else 0


if __name__ == "__main__":
    sys.exit(main())
