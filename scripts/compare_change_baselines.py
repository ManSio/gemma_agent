#!/usr/bin/env python3
"""
Сравнение двух снимков capture_metrics_baseline.py (до/после правки).

  python scripts/compare_change_baselines.py --before g1-pre --after g1-post
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent


def _load(root: Path, label: str) -> Dict[str, Any]:
    p = root / "data" / "benchmarks" / "baselines" / f"{label}.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8"))


def _delta(a: Optional[float], b: Optional[float]) -> str:
    if a is None or b is None:
        return "—"
    try:
        d = float(b) - float(a)
        return f"{d:+.1f}"
    except (TypeError, ValueError):
        return "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args()
    root = Path(args.root).resolve()
    try:
        before = _load(root, args.before.strip())
        after = _load(root, args.after.strip())
    except FileNotFoundError as e:
        print("Missing baseline:", e, file=sys.stderr)
        return 1

    def g(snap: Dict[str, Any], *keys: str) -> Any:
        cur: Any = snap
        for k in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    rows = [
        ("git", g(before, "git_head"), g(after, "git_head"), "—"),
        ("kv_hit_%", g(before, "kv", "kv_hit_pct"), g(after, "kv", "kv_hit_pct"), _delta(
            g(before, "kv", "kv_hit_pct"), g(after, "kv", "kv_hit_pct")
        )),
        ("unknown_tag_%", g(before, "telemetry_tags", "unknown_tag_pct"), g(after, "telemetry_tags", "unknown_tag_pct"), _delta(
            g(before, "telemetry_tags", "unknown_tag_pct"), g(after, "telemetry_tags", "unknown_tag_pct")
        )),
        ("agent_p95_ms", g(before, "metrics_period", "agent_p95_ms"), g(after, "metrics_period", "agent_p95_ms"), _delta(
            g(before, "metrics_period", "agent_p95_ms"), g(after, "metrics_period", "agent_p95_ms")
        )),
        ("llm_p95_ms", g(before, "metrics_period", "llm_p95_ms"), g(after, "metrics_period", "llm_p95_ms"), _delta(
            g(before, "metrics_period", "llm_p95_ms"), g(after, "metrics_period", "llm_p95_ms")
        )),
        ("llm_share_%", g(before, "metrics_period", "llm_share_pct"), g(after, "metrics_period", "llm_share_pct"), _delta(
            g(before, "metrics_period", "llm_share_pct"), g(after, "metrics_period", "llm_share_pct")
        )),
    ]
    br_b = (g(before, "c6_ab", "llm_usage", "by_recent_limit") or {})
    br_a = (g(after, "c6_ab", "llm_usage", "by_recent_limit") or {})
    for lim in ("10", "12"):
        cell_b = br_b.get(lim) if isinstance(br_b, dict) else None
        cell_a = br_a.get(lim) if isinstance(br_a, dict) else None
        pb = cell_b.get("p50") if isinstance(cell_b, dict) else None
        pa = cell_a.get("p50") if isinstance(cell_a, dict) else None
        if pb is not None or pa is not None:
            rows.append((f"brain_p50_recent_{lim}", pb, pa, _delta(pb, pa)))

    print(f"Before: {args.before}  ({before.get('captured_at')})  note: {before.get('note') or '—'}")
    print(f"After:  {args.after}  ({after.get('captured_at')})  note: {after.get('note') or '—'}")
    print()
    print(f"{'metric':<22} {'before':>12} {'after':>12} {'delta':>10}")
    print("-" * 60)
    for name, b, a, d in rows:
        print(f"{name:<22} {str(b):>12} {str(a):>12} {d:>10}")

    verdict_lines: list[str] = []
    uk_b = g(before, "telemetry_tags", "unknown_tag_pct")
    uk_a = g(after, "telemetry_tags", "unknown_tag_pct")
    if uk_b is not None and uk_a is not None and float(uk_a) < float(uk_b):
        verdict_lines.append("telemetry: меньше «?» — лучше наблюдаемость")
    kv_b = g(before, "kv", "kv_hit_pct")
    kv_a = g(after, "kv", "kv_hit_pct")
    if kv_b is not None and kv_a is not None and float(kv_a) > float(kv_b):
        verdict_lines.append("KV: hit rate вырос — цель Q2 ближе")
    ap95_b = g(before, "metrics_period", "agent_p95_ms")
    ap95_a = g(after, "metrics_period", "agent_p95_ms")
    if ap95_b is not None and ap95_a is not None and float(ap95_a) < float(ap95_b):
        verdict_lines.append("latency: agent p95 снизился")

    print()
    if verdict_lines:
        print("Сигналы улучшения:")
        for ln in verdict_lines:
            print("  ·", ln)
    else:
        print("Явного улучшения по таблице нет — проверьте окно дней, трафик и гипотезу.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
