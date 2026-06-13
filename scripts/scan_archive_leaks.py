#!/usr/bin/env python3
"""
Скан message_archive на утечки промпта/секретов во входящих и исходящих.

  python scripts/scan_archive_leaks.py --root .
  python scripts/scan_archive_leaks.py --root /opt/gemma_agent --json-out data/benchmarks/archive_leaks.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.text_leak_scan import scan_text_leaks


def _archive_dir(root: Path) -> Path:
    base = (root / "data" / "behavior" / "message_archive").resolve()
    return base


def scan_archives(root: Path) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    files_scanned = 0
    messages_scanned = 0
    scan_dirs: List[Path] = []
    try:
        from core.data_paths import behavior_dir, message_archive_dir

        scan_dirs = [message_archive_dir(), behavior_dir()]
    except Exception:
        scan_dirs = [
            (root / "data" / "behavior" / "message_archive").resolve(),
            (root / "data" / "users" / "behavior" / "message_archive").resolve(),
            (root / "data" / "behavior").resolve(),
            (root / "data" / "users" / "behavior").resolve(),
        ]
    seen_files: set[str] = set()

    def _scan_items(fp: Path, items: List[Any]) -> None:
        nonlocal messages_scanned
        for i, m in enumerate(items):
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or m.get("content") or "")
            if not text.strip():
                continue
            messages_scanned += 1
            role = str(m.get("role") or "assistant").lower()
            leaks = scan_text_leaks(
                text, role="user" if role in ("user", "human") else "assistant"
            )
            if leaks:
                findings.append(
                    {
                        "file": str(fp.relative_to(root)),
                        "index": i,
                        "role": role,
                        "text_len": len(text),
                        "leak_codes": [str(lk.get("code")) for lk in leaks],
                    }
                )

    for adir in scan_dirs:
        if not adir.is_dir():
            continue
        for fp in sorted(adir.glob("*.json")):
            key = str(fp.resolve())
            if key in seen_files:
                continue
            seen_files.add(key)
            files_scanned += 1
            try:
                doc = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                rm = doc.get("recent_messages")
                if isinstance(rm, list) and rm:
                    _scan_items(fp, rm)
            items = doc if isinstance(doc, list) else doc.get("messages") or doc.get("items") or []
            if isinstance(doc, dict) and not items:
                items = [v for v in doc.values() if isinstance(v, dict) and v.get("text")]
            if items:
                _scan_items(fp, items if isinstance(items, list) else [])
    by_code = Counter()
    for f in findings:
        for lk in f.get("leaks") or []:
            by_code[str(lk.get("code"))] += 1
    adir = _archive_dir(root)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "archive_dir": str(adir),
        "scan_dirs": [str(d) for d in scan_dirs if d.is_dir()],
        "files_scanned": files_scanned,
        "messages_scanned": messages_scanned,
        "findings_count": len(findings),
        "by_code": dict(by_code),
        "findings": findings[:500],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    from core.sensitive_export import (
        scan_counts_payload,
        scan_summary_log_line,
        write_scan_report_json,
    )

    raw = scan_archives(root)
    counts = scan_counts_payload(raw)
    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = root / out
        write_scan_report_json(out, raw)
        print(f"Wrote {out}")
    print(
        scan_summary_log_line(
            files=counts["files_scanned"],
            messages=counts["messages_scanned"],
            leaks=counts["findings_count"],
        )
    )
    return 1 if counts["findings_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
