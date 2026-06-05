#!/usr/bin/env python3
"""
PASS + чистая chain (нет leak/issues) из agent_test report → golden_corpus.

  python scripts/promote_golden_from_report.py --report data/testing/reports/full_audit.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.golden_promote import chain_passes_for_golden, golden_record_from_report_row


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip():
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", default="data/learning/golden_corpus.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    report = _ROOT / args.report if not Path(args.report).is_absolute() else Path(args.report)
    out = _ROOT / args.out
    rows = _read_jsonl(report)
    existing: Set[str] = set()
    if out.is_file():
        for r in _read_jsonl(out):
            existing.add(str(r.get("id") or ""))
    added = 0
    skipped = 0
    new_lines: List[str] = []
    for row in rows:
        cid = str(row.get("id") or "")
        if not cid or cid in existing:
            skipped += 1
            continue
        if not chain_passes_for_golden(row):
            skipped += 1
            continue
        ts = str(row.get("ts") or datetime.now(timezone.utc).isoformat())
        rec = golden_record_from_report_row(row, ts=ts)
        new_lines.append(json.dumps(rec, ensure_ascii=False))
        added += 1
    if not args.dry_run and new_lines:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            for ln in new_lines:
                f.write(ln + "\n")
    print(f"promote: added={added} skipped={skipped} out={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
