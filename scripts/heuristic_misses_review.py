#!/usr/bin/env python3
"""Топ блокировок heuristic gate + черновик negative_patterns (C2)."""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _default_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "data" / "runtime" / "heuristic_misses.jsonl"


def _load_rows(path: Path, tail: int) -> List[Dict[str, Any]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: List[Dict[str, Any]] = []
    for line in lines[-max(1, tail) :]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _suggest_patterns(excerpts: List[str], *, max_patterns: int = 5) -> List[str]:
    """Короткие фразы из excerpt для ручного review (не авто-apply)."""
    out: List[str] = []
    for ex in excerpts:
        t = re.sub(r"\s+", " ", (ex or "").strip().lower())
        if len(t) < 12:
            continue
        # 4–6 слов с «рядом», «ошибк», «напомин» и т.п.
        for m in re.finditer(
            r"(?:\S+\s+){2,5}(?:рядом|ошибк|напомин|зуб|пломб|стать|лечен)",
            t,
        ):
            phrase = m.group(0).strip()
            if 8 <= len(phrase) <= 72 and phrase not in out:
                out.append(phrase)
            if len(out) >= max_patterns:
                return out
        words = t.split()
        if len(words) >= 4:
            phrase = " ".join(words[:5])
            if phrase not in out:
                out.append(phrase)
        if len(out) >= max_patterns:
            break
    return out[:max_patterns]


def _draft_local_json(by_rule: Dict[str, List[str]], version: int = 1) -> Dict[str, Any]:
    rules = []
    for rid, patterns in sorted(by_rule.items()):
        if not patterns:
            continue
        rules.append(
            {
                "id": rid,
                "negative_patterns": patterns,
                "_comment": "черновик из heuristic_misses — проверить вручную перед продом",
            }
        )
    return {"version": version, "_comment": "merge с heuristic_shortcuts.json; gitignore", "rules": rules}


def main() -> int:
    ap = argparse.ArgumentParser(description="Review heuristic gate misses")
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--tail", type=int, default=500, help="Last N lines to scan")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument(
        "--draft-local",
        action="store_true",
        help="Print draft config/heuristic_shortcuts.local.json to stdout",
    )
    ap.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="Min misses per rule_id for draft patterns",
    )
    args = ap.parse_args()
    path = args.path or _default_path()
    if not path.is_file():
        print(f"No file: {path}")
        return 0
    rows = _load_rows(path, args.tail)
    if not rows:
        print("Empty misses log")
        return 0
    by_rule = Counter(str(r.get("rule_id") or "?") for r in rows)
    by_reason = Counter(str(r.get("reason") or "?") for r in rows)
    print(f"=== heuristic_misses ({len(rows)} rows, tail={args.tail}) ===\n")
    print("By rule_id:")
    for rid, n in by_rule.most_common(args.top):
        print(f"  {n:4d}  {rid}")
    print("\nBy reason:")
    for reason, n in by_reason.most_common(args.top):
        print(f"  {n:4d}  {reason}")
    print("\nLast 5 excerpts:")
    for r in rows[-5:]:
        ex = str(r.get("text_excerpt") or "")[:120]
        print(f"  [{r.get('rule_id')}] {r.get('reason')}: {ex!r}")

    if args.draft_local:
        grouped: Dict[str, List[str]] = defaultdict(list)
        for r in rows:
            rid = str(r.get("rule_id") or "").strip()
            if not rid:
                continue
            grouped[rid].append(str(r.get("text_excerpt") or ""))
        draft_rules: Dict[str, List[str]] = {}
        for rid, count in by_rule.most_common():
            if count < args.min_count:
                continue
            patterns = _suggest_patterns(grouped.get(rid, []))
            if patterns:
                draft_rules[rid] = patterns
        doc = _draft_local_json(draft_rules)
        print("\n=== draft heuristic_shortcuts.local.json ===\n")
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
