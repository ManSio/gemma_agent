#!/usr/bin/env python3
"""E4: доля llm_usage записей с telemetry_tag за окно (не «?» в админке)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_ts(raw) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--path", default="")
    args = ap.parse_args()
    for rel in ("data/runtime/llm_usage.jsonl", "data/llm_usage.jsonl"):
        p = ROOT / rel
        if p.is_file():
            path = p
            break
    else:
        path = Path(args.path or ROOT / "data/runtime/llm_usage.jsonl")
    if args.path:
        path = Path(args.path)
    if not path.is_file():
        print(f"Нет файла: {path}")
        return 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    n = 0
    tagged = 0
    by_tag: Counter[str] = Counter()
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(row.get("ts"))
            if ts and ts < cutoff:
                continue
            n += 1
            tag = str(row.get("telemetry_tag") or row.get("tag") or "").strip()
            if tag and tag not in {"?", "unknown"}:
                tagged += 1
                by_tag[tag.split(":")[0]] += 1
    pct = (100.0 * tagged / n) if n else 0.0
    print(f"Файл: {path}")
    print(f"Окно: {args.days} дн.; записей: {n}; с tag: {tagged} ({pct:.1f}%)")
    for tag, cnt in by_tag.most_common(12):
        print(f"  {tag:28} {cnt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
