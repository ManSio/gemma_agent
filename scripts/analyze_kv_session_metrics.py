#!/usr/bin/env python3
"""
Поток C (UPGRADE_PLAN): агрегат KV prompt-cache hit rate из llm_usage.jsonl.

Цель плана: >30% ходов с cached_prompt_tokens>0 в одной теме (epoch).
Не меняет env — только отчёт.

Запуск:
  python scripts/analyze_kv_session_metrics.py
  python scripts/analyze_kv_session_metrics.py --days 14 --path /opt/gemma_agent/data/runtime/llm_usage.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_llm_usage_path() -> Path:
    for rel in ("data/runtime/llm_usage.jsonl", "data/llm_usage.jsonl"):
        p = ROOT / rel
        if p.is_file():
            return p
    return ROOT / "data/runtime/llm_usage.jsonl"


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


def _p50(vals: List[int]) -> int:
    if not vals:
        return 0
    return int(statistics.median(vals))


def main() -> int:
    ap = argparse.ArgumentParser(description="KV session cache metrics from llm_usage")
    ap.add_argument("--path", default="", help="llm_usage.jsonl")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--profile", default="", help="фильтр profile (опц.)")
    args = ap.parse_args()

    path = Path(args.path or os.getenv("LLM_USAGE_PATH") or _default_llm_usage_path())
    if not path.is_file():
        print(f"Нет файла: {path}")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    n = 0
    n_brain = 0
    hits = 0
    misses = 0
    cached_sum = 0
    prompt_sum = 0
    by_profile: DefaultDict[str, List[int]] = defaultdict(list)
    by_session: DefaultDict[str, Dict[str, int]] = defaultdict(lambda: {"hits": 0, "total": 0})

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
            ts = _parse_ts(row.get("ts") or row.get("timestamp"))
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts and ts < cutoff:
                continue
            prof = str(row.get("profile") or row.get("brain_profile") or "").strip().lower()
            if args.profile and prof != args.profile.strip().lower():
                continue
            tag = str(row.get("telemetry_tag") or row.get("tag") or "")
            kind = str(row.get("telemetry_kind") or "")
            if not (tag.startswith("brain") or kind == "brain"):
                continue
            n_brain += 1
            n += 1
            cpt = row.get("cached_prompt_tokens")
            try:
                cpt_i = max(0, int(cpt or 0))
            except (TypeError, ValueError):
                cpt_i = 0
            pt = row.get("prompt_tokens") or row.get("input_tokens")
            try:
                pt_i = max(0, int(pt or 0))
            except (TypeError, ValueError):
                pt_i = 0
            prompt_sum += pt_i
            cached_sum += cpt_i
            if cpt_i > 0:
                hits += 1
            else:
                misses += 1
            if prof:
                by_profile[prof].append(cpt_i)
            sid = str(row.get("session_id") or row.get("kv_session_id") or "").strip()
            if sid:
                by_session[sid]["total"] += 1
                if cpt_i > 0:
                    by_session[sid]["hits"] += 1

    total = hits + misses
    hit_rate = (100.0 * hits / total) if total else 0.0
    coverage = (100.0 * cached_sum / prompt_sum) if prompt_sum else 0.0

    print(f"Файл: {path}")
    print(f"Окно: {args.days} дн.; brain-записей: {n_brain}")
    print(f"Ходы с cache hit (cached_prompt_tokens>0): {hits}/{total} = {hit_rate:.1f}%")
    print(f"Доля cached/prompt tokens: {coverage:.1f}% (сумма {cached_sum}/{prompt_sum})")
    print(f"Цель плана Q2: hit_rate >30% в личке на одной теме (epoch)")
    print()
    if by_profile:
        print("По profile (доля ходов с cache>0):")
        for prof, vals in sorted(by_profile.items(), key=lambda x: -len(x[1]))[:8]:
            h = sum(1 for v in vals if v > 0)
            t = len(vals)
            print(f"  {prof:20} hits={h:4}/{t:4}  ({100.0*h/t:.1f}%)" if t else f"  {prof}")
    print()
    sess_rates: List[float] = []
    for sid, st in by_session.items():
        if st["total"] >= 3:
            sess_rates.append(100.0 * st["hits"] / st["total"])
    if sess_rates:
        print(
            f"Сессии с ≥3 brain-ходами: n={len(sess_rates)}  "
            f"median hit_rate={_p50([int(x) for x in sess_rates])}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
