#!/usr/bin/env python3
"""
Телеметрия модулей/профилей из turns.jsonl — кто реально использовался за N дней.

Запуск:
  python scripts/analyze_module_hits.py
  python scripts/analyze_module_hits.py --root /opt/gemma_agent --days 7
  python scripts/analyze_module_hits.py --json-out data/benchmarks/module_hits.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CATALOG = ROOT / "config" / "modules_catalog.json"


def _default_turns_path(root: Path) -> Path:
    for rel in ("data/runtime/turns.jsonl", "data/turns.jsonl"):
        p = root / rel
        if p.is_file():
            return p
    return root / "data/runtime/turns.jsonl"


def _parse_ts(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _load_jsonl(path: Path, *, cutoff: datetime) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = _parse_ts(row.get("ts"))
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts is not None and ts < cutoff:
                continue
            out.append(row)
    return out


def _load_catalog() -> Tuple[Dict[str, str], List[str]]:
    """module_name -> tier, default_denylist names."""
    if not CATALOG.is_file():
        return {}, []
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    tiers: Dict[str, str] = {}
    for name, meta in (data.get("modules") or {}).items():
        if isinstance(meta, dict):
            tiers[str(name)] = str(meta.get("tier") or "?")
    deny = [str(x) for x in (data.get("default_denylist") or [])]
    return tiers, deny


def _top(counter: Counter, n: int = 15) -> List[Tuple[str, int]]:
    return [(k, v) for k, v in counter.most_common(n) if k]


def _report_pack(
    turns_path: Path,
    *,
    cutoff: datetime,
    days: int,
) -> Dict[str, Any]:
    rows = _load_jsonl(turns_path, cutoff=cutoff)
    tiers, default_deny = _load_catalog()

    mod_c: Counter = Counter()
    prof_c: Counter = Counter()
    planner_c: Counter = Counter()
    tool_c: Counter = Counter()
    shortcut_c: Counter = Counter()
    skill_c: Counter = Counter()

    for row in rows:
        m = str(row.get("module") or "").strip()
        if m:
            mod_c[m] += 1
        p = str(row.get("profile") or row.get("router_profile") or "").strip()
        if p:
            prof_c[p] += 1
        pr = str(row.get("planner_reason") or row.get("planner_bypass") or "").strip()
        if pr:
            planner_c[pr] += 1
        lt = str(row.get("last_tool") or "").strip()
        if lt:
            tool_c[lt] += 1
        sc = str(row.get("shortcut_rule_id") or "").strip()
        if sc:
            shortcut_c[sc] += 1
        sk = str(row.get("skill") or "").strip()
        if sk:
            skill_c[sk] += 1

    tier_a = [n for n, t in tiers.items() if t == "A"]
    tier_b = [n for n, t in tiers.items() if t == "B"]
    zero_a = sorted(n for n in tier_a if mod_c.get(n, 0) == 0)
    zero_b = sorted(n for n in tier_b if mod_c.get(n, 0) == 0)

    return {
        "window_days": days,
        "turns_path": str(turns_path),
        "turns_in_window": len(rows),
        "modules_top": _top(mod_c),
        "profiles_top": _top(prof_c),
        "planner_top": _top(planner_c, 20),
        "last_tool_top": _top(tool_c, 20),
        "shortcut_rule_top": _top(shortcut_c, 20),
        "skill_top": _top(skill_c, 15),
        "catalog_tiers": len(tiers),
        "zero_hit_tier_a": zero_a,
        "zero_hit_tier_b": zero_b,
        "default_denylist_count": len(default_deny),
    }


def _render_text(report: Dict[str, Any]) -> str:
    lines = [
        f"Module hits — окно {report.get('window_days')}d",
        f"turns: {report.get('turns_in_window')} ({report.get('turns_path')})",
        "",
    ]

    def _block(title: str, items: List[Tuple[str, int]]) -> None:
        lines.append(title)
        if not items:
            lines.append("  (нет данных)")
        else:
            for name, cnt in items:
                lines.append(f"  {cnt:5d}  {name}")
        lines.append("")

    _block("module (turns.module):", report.get("modules_top") or [])
    _block("profile:", report.get("profiles_top") or [])
    _block("planner_reason / bypass:", report.get("planner_top") or [])
    _block("last_tool:", report.get("last_tool_top") or [])
    _block("shortcut_rule_id:", report.get("shortcut_rule_top") or [])

    za = report.get("zero_hit_tier_a") or []
    zb = report.get("zero_hit_tier_b") or []
    if za or zb:
        lines.append("zero hits (по полю module — не brain profile):")
        if za:
            lines.append(f"  tier A ({len(za)}): " + ", ".join(za[:12]) + ("…" if len(za) > 12 else ""))
        if zb:
            lines.append(f"  tier B ({len(zb)}): " + ", ".join(zb[:12]) + ("…" if len(zb) > 12 else ""))
        lines.append("  (многие A/B идут через brain, не module — смотри profiles_top)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Module/profile hits from turns.jsonl")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--turns", type=Path, default=None)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    root = args.root.resolve()
    turns_path = args.turns or _default_turns_path(root)
    days = max(1, min(int(args.days), 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    report = _report_pack(turns_path, cutoff=cutoff, days=days)
    text = _render_text(report)
    print(text)

    if args.json_out:
        out = args.json_out
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
