#!/usr/bin/env python3
"""
Разбор задержек: stage_ms в turns.jsonl + LLM по тегам (llm_usage.jsonl).

Показывает, где внутри хода тратится время (plan vs execute) и какие профили/теги
дают хвост p95.

Запуск:
  python scripts/analyze_stage_ms.py
  python scripts/analyze_stage_ms.py --root /opt/gemma_agent --days 7
  python scripts/analyze_stage_ms.py --slow-ms 30000 --top 15 --json-out report.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_turns_path(root: Path) -> Path:
    for rel in ("data/runtime/turns.jsonl", "data/turns.jsonl"):
        p = root / rel
        if p.is_file():
            return p
    return root / "data/runtime/turns.jsonl"


def _default_llm_path(root: Path) -> Path:
    env = (os.getenv("GEMMA_LLM_USAGE_PATH") or os.getenv("LLM_USAGE_PATH") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
    for rel in ("data/runtime/llm_usage.jsonl", "data/llm_usage.jsonl"):
        p = root / rel
        if p.is_file():
            return p
    return root / "data/llm_usage.jsonl"


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


def _p50(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    return float(statistics.median(vals))


def _p95(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return float(s[int(0.95 * (len(s) - 1))])


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
            ts = _parse_ts(row.get("ts") or row.get("timestamp"))
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts and ts < cutoff:
                continue
            out.append(row)
    return out


def _analyze_turns(
    rows: List[Dict[str, Any]],
    *,
    slow_ms: float,
    top_n: int,
) -> Dict[str, Any]:
    latencies: List[float] = []
    stage_acc: DefaultDict[str, List[float]] = defaultdict(list)
    profile_lat: DefaultDict[str, List[float]] = defaultdict(list)
    slow_samples: List[Dict[str, Any]] = []

    n_all = 0
    n_with_stage = 0

    for row in rows:
        if str(row.get("type") or "turn") == "scenario":
            continue
        n_all += 1
        lat = row.get("latency_ms")
        if not isinstance(lat, (int, float)) or lat <= 0:
            continue
        lat_f = float(lat)
        latencies.append(lat_f)
        prof = str(row.get("profile") or "(none)")
        profile_lat[prof].append(lat_f)

        sms = row.get("stage_ms")
        if isinstance(sms, dict) and sms:
            n_with_stage += 1
            for k, v in sms.items():
                if isinstance(v, (int, float)) and v >= 0:
                    stage_acc[str(k)].append(float(v))

        if lat_f >= slow_ms:
            slow_samples.append(
                {
                    "ts": row.get("ts"),
                    "latency_ms": int(lat_f),
                    "profile": prof,
                    "planner_reason": str(row.get("planner_reason") or "")[:80],
                    "planner_bypass": str(row.get("planner_bypass") or ""),
                    "module": str(row.get("module") or ""),
                    "user_excerpt": str(row.get("user_excerpt") or "")[:72],
                    "stage_ms": sms if isinstance(sms, dict) else None,
                }
            )

    slow_samples.sort(key=lambda x: -int(x.get("latency_ms") or 0))
    slow_samples = slow_samples[:top_n]

    stage_rows: List[Dict[str, Any]] = []
    for key, vals in stage_acc.items():
        if len(vals) < 2:
            continue
        stage_rows.append(
            {
                "stage": key,
                "n": len(vals),
                "median_ms": round(_p50(vals) or 0, 1),
                "p95_ms": round(_p95(vals) or 0, 1),
            }
        )
    stage_rows.sort(key=lambda x: (-x["p95_ms"], -x["median_ms"]))

    prof_rows: List[Dict[str, Any]] = []
    for prof, vals in profile_lat.items():
        if len(vals) < 2:
            continue
        prof_rows.append(
            {
                "profile": prof,
                "n": len(vals),
                "median_ms": round(_p50(vals) or 0, 1),
                "p95_ms": round(_p95(vals) or 0, 1),
            }
        )
    prof_rows.sort(key=lambda x: -x["p95_ms"])

    slow_profiles = Counter(s["profile"] for s in slow_samples)

    return {
        "turns_in_window": n_all,
        "turns_with_latency": len(latencies),
        "turns_with_stage_ms": n_with_stage,
        "stage_ms_coverage_pct": round(100.0 * n_with_stage / max(1, len(latencies)), 1),
        "latency_ms": {
            "median": round(_p50(latencies) or 0, 1),
            "p95": round(_p95(latencies) or 0, 1),
            "max": round(max(latencies), 1) if latencies else None,
        },
        "stage_ms": stage_rows,
        "by_profile": prof_rows[:12],
        "slow_turns": {
            "threshold_ms": int(slow_ms),
            "count": sum(1 for x in latencies if x >= slow_ms),
            "profiles_top": slow_profiles.most_common(10),
            "samples": slow_samples,
        },
    }


def _analyze_llm(rows: List[Dict[str, Any]], *, min_n: int = 5) -> List[Dict[str, Any]]:
    by_tag: DefaultDict[str, List[float]] = defaultdict(list)
    for row in rows:
        lat = row.get("latency_ms")
        if not isinstance(lat, (int, float)) or lat <= 0:
            continue
        tag = str(row.get("telemetry_tag") or row.get("tag") or "unknown").strip()
        by_tag[tag].append(float(lat))

    out: List[Dict[str, Any]] = []
    for tag, vals in by_tag.items():
        if len(vals) < min_n:
            continue
        out.append(
            {
                "tag": tag,
                "n": len(vals),
                "median_ms": round(_p50(vals) or 0, 1),
                "p95_ms": round(_p95(vals) or 0, 1),
            }
        )
    out.sort(key=lambda x: (-x["p95_ms"], -x["median_ms"]))
    return out[:20]


def _report_pack(
    *,
    turns_path: Path,
    llm_path: Path,
    cutoff: datetime,
    days: int,
    slow_ms: float,
    top_n: int,
) -> Dict[str, Any]:
    turn_rows = _load_jsonl(turns_path, cutoff=cutoff)
    llm_rows = _load_jsonl(llm_path, cutoff=cutoff)
    turns_part = _analyze_turns(turn_rows, slow_ms=slow_ms, top_n=top_n)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "root": str(turns_path.parent.parent),
        "turns_path": str(turns_path),
        "llm_path": str(llm_path),
        "turns": turns_part,
        "llm_by_tag": _analyze_llm(llm_rows),
    }


def _render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"=== Latency breakdown ({report.get('window_days')}d) ===")
    lines.append(f"turns: {report.get('turns_path')}")
    lines.append(f"llm:   {report.get('llm_path')}")
    t = report.get("turns") or {}
    lat = t.get("latency_ms") or {}
    lines.append(
        f"ходов с latency: {t.get('turns_with_latency')} / {t.get('turns_in_window')}  "
        f"stage_ms: {t.get('turns_with_stage_ms')} ({t.get('stage_ms_coverage_pct')}%)"
    )
    lines.append(
        f"latency_ms  med={lat.get('median')}  p95={lat.get('p95')}  max={lat.get('max')}"
    )

    lines.append("\n--- stage_ms (сегменты хода) ---")
    for row in t.get("stage_ms") or []:
        lines.append(
            f"  {row['stage']:22s} n={row['n']:4d}  med={row['median_ms']:8.0f}  p95={row['p95_ms']:8.0f}"
        )
    if not (t.get("stage_ms") or []):
        lines.append("  (нет stage_ms — мало ходов с телеметрией)")

    lines.append("\n--- профили (p95 latency) ---")
    for row in t.get("by_profile") or []:
        lines.append(
            f"  {row['profile']:18s} n={row['n']:4d}  med={row['median_ms']:8.0f}  p95={row['p95_ms']:8.0f}"
        )

    slow = t.get("slow_turns") or {}
    lines.append(f"\n--- медленные ходы (>={slow.get('threshold_ms')} ms): {slow.get('count')} ---")
    for prof, cnt in slow.get("profiles_top") or []:
        lines.append(f"  {prof}: {cnt}")
    for i, s in enumerate(slow.get("samples") or [], 1):
        lines.append(
            f"  #{i} {s.get('ts')}  {s.get('latency_ms')}ms  {s.get('profile')}  "
            f"{(s.get('user_excerpt') or '')[:48]}"
        )
        sms = s.get("stage_ms")
        if isinstance(sms, dict):
            top_seg = sorted(
                ((k, v) for k, v in sms.items() if k != "total" and isinstance(v, (int, float))),
                key=lambda x: -float(x[1]),
            )[:4]
            if top_seg:
                seg = " ".join(f"{k}={int(v)}ms" for k, v in top_seg)
                lines.append(f"      stage: {seg}")

    lines.append("\n--- LLM по тегам (p95) ---")
    for row in report.get("llm_by_tag") or []:
        lines.append(
            f"  {row['tag']:30s} n={row['n']:4d}  med={row['median_ms']:8.0f}  p95={row['p95_ms']:8.0f}"
        )

    lines.append("\nПодсказка: LATENCY_TRACE_LOG=slow в .env → сегменты в panel_nohup_bot.log")
    lines.append("grep 'latency trace=' panel_nohup_bot.log | tail -20")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Latency: stage_ms + LLM tags")
    ap.add_argument("--root", default=str(ROOT), help="корень репо / сервера")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--turns-path", default="")
    ap.add_argument("--llm-path", default="")
    ap.add_argument("--slow-ms", type=float, default=30000.0)
    ap.add_argument("--top", type=int, default=10, help="сколько медленных ходов в отчёт")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    turns_path = Path(args.turns_path) if args.turns_path else _default_turns_path(root)
    llm_path = Path(args.llm_path) if args.llm_path else _default_llm_path(root)
    if not turns_path.is_file():
        print(f"Нет файла: {turns_path}")
        return 1

    days = max(1, int(args.days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    report = _report_pack(
        turns_path=turns_path,
        llm_path=llm_path,
        cutoff=cutoff,
        days=days,
        slow_ms=float(args.slow_ms),
        top_n=max(1, int(args.top)),
    )
    text = _render_text(report)
    print(text)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
