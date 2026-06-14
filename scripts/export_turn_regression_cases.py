#!/usr/bin/env python3
"""Экспорт prod-ходов из turns.jsonl в regression cases (Phase 3.3 prod)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_rows(path: Path, *, limit: int) -> List[Dict[str, Any]]:
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
    return rows[-limit:] if limit > 0 else rows


def row_to_case(row: Dict[str, Any], *, idx: int) -> Dict[str, Any]:
    """Преобразовать turns.jsonl row в regression case."""
    user_text = str(row.get("user_excerpt") or row.get("user_text") or "")
    case: Dict[str, Any] = {
        "id": f"prod_{idx}_{str(row.get('trace_id') or '')[:8]}",
        "user_text": user_text,
        "profile": str(row.get("profile") or ""),
        "short_circuit": str(row.get("short_circuit") or row.get("planner_bypass") or ""),
        "source": "turns.jsonl",
        "trace_id": str(row.get("trace_id") or "")[:64],
    }
    expect: Dict[str, str] = {}
    if row.get("referent"):
        expect["referent"] = str(row.get("referent"))
    if row.get("lane"):
        expect["lane"] = str(row.get("lane"))
    if row.get("thread_action"):
        expect["thread_action"] = str(row.get("thread_action"))
    if expect:
        case["expect"] = expect
    return case


def main(argv: List[str] | None = None) -> int:
    """CLI entry."""
    ap = argparse.ArgumentParser(description="Export turns.jsonl rows to regression JSON")
    ap.add_argument("--turns", default="")
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "turn_regression_prod.json"))
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)

    from core.turn_observer import log_path

    src = Path(args.turns) if args.turns else log_path()
    rows = _load_rows(src, limit=args.limit)
    cases = [row_to_case(r, idx=i) for i, r in enumerate(rows)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"exported={len(cases)} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
