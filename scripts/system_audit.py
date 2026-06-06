#!/usr/bin/env python3
"""
Быстрый аудит отзывчивости и слабых мест в логах (локально или на сервере).

  python scripts/system_audit.py
  python scripts/system_audit.py --data-dir /opt/gemma_agent/data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_jsonl_tail(path: Path, max_lines: int = 5000) -> list[dict]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    for ln in lines[-max_lines:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = min(len(s) - 1, int(len(s) * 0.95))
    return float(s[i])


def _llm_usage_path(data: Path) -> Path:
    """Как analyze_kv_session_metrics: runtime, затем корень data/."""
    p1 = data / "runtime" / "llm_usage.jsonl"
    if p1.is_file():
        return p1
    return data / "llm_usage.jsonl"


def _runtime_errors_path(data: Path) -> Path:
    """Часть деплоев пишет в data/runtime/, часть — в data/."""
    p1 = data / "runtime_errors.jsonl"
    if p1.is_file():
        return p1
    p2 = data / "runtime" / "runtime_errors.jsonl"
    return p2


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemma Agent system audit")
    ap.add_argument("--data-dir", default=os.getenv("GEMMA_DATA_DIR", str(ROOT / "data")))
    args = ap.parse_args()
    data = Path(args.data_dir)

    print("=== Gemma system audit ===")
    print(f"data_dir: {data}")
    print(f"ts_utc: {datetime.now(timezone.utc).isoformat()}\n")

    # LLM latency
    usage_path = _llm_usage_path(data)
    rows = _read_jsonl_tail(usage_path, 3000)
    latencies = []
    errors = 0
    models = Counter()
    for r in rows:
        ms = r.get("latency_ms") or r.get("duration_ms")
        if isinstance(ms, (int, float)) and ms > 0:
            latencies.append(float(ms))
        if r.get("error") or r.get("ok") is False:
            errors += 1
        m = r.get("model") or r.get("model_id")
        if m:
            models[str(m)] += 1
    print(f"--- LLM ({usage_path.relative_to(data) if usage_path.is_file() else usage_path.name} tail) ---")
    print(f"  path: {usage_path}")
    print(f"  records: {len(rows)}  errors: {errors}")
    if latencies:
        print(f"  p50_ms: {sorted(latencies)[len(latencies)//2]:.0f}  p95_ms: {_p95(latencies):.0f}  max_ms: {max(latencies):.0f}")
    if models:
        print("  top models:", models.most_common(5))

    # Route risk / CDC
    rr_path = data / "runtime" / "route_risk.jsonl"
    rr = _read_jsonl_tail(rr_path, 2000)
    rr_intents = Counter(str(x.get("intent") or "?") for x in rr[-500:])
    print("\n--- route_risk (recent intents) ---")
    print(f"  lines_tail: {len(rr)}  recent_intents: {rr_intents.most_common(8)}")

    cdc_path = data / "runtime" / "cdc_turn_outcomes.jsonl"
    cdc = _read_jsonl_tail(cdc_path, 2000)
    bad = sum(1 for x in cdc if str(x.get("outcome") or "").lower() in {"bad", "fail", "error", "clarify"})
    print("\n--- CDC turns ---")
    print(f"  tail: {len(cdc)}  bad-ish: {bad}")

    # Runtime errors
    err_path = _runtime_errors_path(data)
    errs = _read_jsonl_tail(err_path, 500)
    err_types = Counter(str(x.get("type") or x.get("error_type") or "?") for x in errs)
    print("\n--- runtime_errors ---")
    print(f"  path: {err_path}")
    print(f"  tail: {len(errs)}  types: {err_types.most_common(8)}")

    # Metrics timeseries (if present)
    mt_path = data / "runtime" / "metrics_timeseries.jsonl"
    mt = _read_jsonl_tail(mt_path, 500)
    if mt:
        last = mt[-1]
        print("\n--- metrics_timeseries (last row keys) ---")
        print(f"  keys: {list(last.keys())[:12]}")

    # Recommendations
    print("\n--- Recommendations ---")
    recs = []
    if latencies and _p95(latencies) > 25000:
        recs.append("p95 LLM > 25s: проверьте OPENROUTER_MODEL, BRAIN_FAST_PATH, кеш LLM_CACHE")
    if errors and len(rows) and errors / max(len(rows), 1) > 0.15:
        recs.append(">15% ошибок в llm_usage: смотрите runtime_errors.jsonl и /admin_pulse")
    if bad and len(cdc) and bad / max(len(cdc), 1) > 0.4:
        recs.append("много bad CDC: /admin_reputation, ROUTE_RISK_CLUSTER_AUTO_LESSON, heuristic_fixes")
    if not recs:
        recs.append("критичных порогов не найдено; для деталей: /admin_efficiency, /admin_learning_digest")
    for r in recs:
        print(f"  • {r}")

    print("\nTelegram: /admin_pulse · /admin_efficiency · /admin_learning_digest")
    print("Deploy: bash scripts/gemma_panel.sh update  (см. docs/GEMMA_PANEL.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
