#!/usr/bin/env python3
"""
Снимок метрик «до/после» одной итерации (гипотеза + цифры, не «на авось»).

  python scripts/capture_metrics_baseline.py --label g1-news-rss-pre --note "до деплоя G1"
  python scripts/capture_metrics_baseline.py --label g1-news-rss-post --note "через 3 дня после деплоя"

Пишет:
  data/benchmarks/baselines/<label>.json
  data/benchmarks/change_baselines.jsonl  (одна строка на запуск)

Обновляет docs/METRICS_PERIODS_RU.md и дописывает metrics_snapshots.jsonl (как metrics_period_report).

Сравнение двух меток:
  python scripts/compare_change_baselines.py --before g1-news-rss-pre --after g1-news-rss-post
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git_head(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


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


def _default_llm_usage_path() -> Path:
    """Предпочитаем файл с данными (на deploy-host часто data/llm_usage.jsonl, не runtime/)."""
    candidates = [ROOT / "data/runtime/llm_usage.jsonl", ROOT / "data/llm_usage.jsonl"]
    best = candidates[-1]
    best_size = -1
    for p in candidates:
        if p.is_file():
            sz = p.stat().st_size
            if sz > best_size:
                best = p
                best_size = sz
    return best


def _kv_summary(days: int) -> Dict[str, Any]:
    path = _default_llm_usage_path()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    hits = misses = 0
    prompt_sum = cached_sum = 0
    n_brain = 0
    if not path.is_file():
        return {"path": str(path), "error": "missing"}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
        if ts and ts < cutoff:
            continue
        tag = str(row.get("telemetry_tag") or row.get("tag") or "")
        if tag and not (tag.startswith("brain") or tag in ("router_classifier",)):
            if "brain" not in str(row.get("telemetry_kind") or ""):
                continue
        n_brain += 1
        try:
            cpt_i = max(0, int(row.get("cached_prompt_tokens") or 0))
        except (TypeError, ValueError):
            cpt_i = 0
        try:
            pt_i = max(0, int(row.get("prompt_tokens") or row.get("input_tokens") or 0))
        except (TypeError, ValueError):
            pt_i = 0
        prompt_sum += pt_i
        cached_sum += cpt_i
        if cpt_i > 0:
            hits += 1
        else:
            misses += 1
    total = hits + misses
    return {
        "path": str(path),
        "days": days,
        "brain_rows": n_brain,
        "kv_hit_pct": round(100.0 * hits / total, 1) if total else None,
        "cached_token_share_pct": round(100.0 * cached_sum / prompt_sum, 1) if prompt_sum else None,
    }


def _telemetry_unknown_pct(days: int) -> Dict[str, Any]:
    path = _default_llm_usage_path()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    total = unknown = 0
    if not path.is_file():
        return {"path": str(path), "error": "missing"}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(row.get("ts") or row.get("timestamp"))
        if ts and ts < cutoff:
            continue
        total += 1
        tag = str(row.get("telemetry_tag") or row.get("tag") or "").strip()
        if not tag or tag == "?":
            unknown += 1
    return {
        "path": str(path),
        "days": days,
        "rows": total,
        "unknown_tag_pct": round(100.0 * unknown / total, 1) if total else None,
    }


def _latest_metrics_summary(json_path: Path) -> Dict[str, Any]:
    if not json_path.is_file():
        return {}
    try:
        rep = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: Dict[str, Any] = {
        "generated_at": rep.get("generated_at"),
        "llm_records": rep.get("llm_records"),
    }
    for row in reversed(rep.get("daily") or []):
        if not isinstance(row, dict):
            continue
        pl = row.get("pipeline") or {}
        ag = row.get("agent") or {}
        lm = row.get("llm") or {}
        if pl.get("llm_share_of_turn_pct") is not None or ag.get("latency_ms_p95"):
            out["last_day"] = row.get("day")
            out["agent_p95_ms"] = ag.get("latency_ms_p95")
            out["llm_p95_ms"] = lm.get("latency_ms_p95")
            out["llm_share_pct"] = pl.get("llm_share_of_turn_pct")
            out["brain_first_tok_p50"] = (lm.get("by_tag") or {}).get("brain_first", {}).get("prompt_tokens_p50")
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Baseline метрик для сравнения до/после правки")
    ap.add_argument("--label", required=True, help="уникальная метка, напр. g1-pre / c6-lan-week1")
    ap.add_argument("--note", default="", help="гипотеза или комментарий")
    ap.add_argument("--root", default=str(ROOT), help="корень репо / сервера")
    ap.add_argument("--days", type=int, default=7, help="окно для KV / C6 / telemetry")
    ap.add_argument("--skip-period-report", action="store_true", help="не обновлять METRICS_PERIODS_RU.md")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    label = (args.label or "").strip()
    if not label or "/" in label or "\\" in label:
        print("Invalid --label", file=sys.stderr)
        return 2

    baselines_dir = root / "data" / "benchmarks" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    out_path = baselines_dir / f"{label}.json"

    if not args.skip_period_report:
        cmd = [
            sys.executable,
            str(root / "scripts" / "metrics_period_report.py"),
            "--root",
            str(root),
            "--json",
            str(root / "data" / "benchmarks" / "metrics_periods_latest.json"),
            "--history",
            str(root / "data" / "benchmarks" / "metrics_snapshots.jsonl"),
            "--out",
            str(root / "docs" / "METRICS_PERIODS_RU.md"),
        ]
        subprocess.run(cmd, cwd=str(root), check=False)

    import importlib.util

    ab_path = root / "scripts" / "analyze_brain_recent_ab.py"
    spec = importlib.util.spec_from_file_location("analyze_brain_recent_ab", ab_path)
    if spec is None or spec.loader is None:
        print("Cannot load analyze_brain_recent_ab.py", file=sys.stderr)
        return 1
    ab_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab_mod)
    _report_pack = ab_mod._report_pack

    llm_path = _default_llm_usage_path()
    turns_path = root / "data" / "runtime" / "turns.jsonl"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    c6 = _report_pack(
        llm_path=llm_path if llm_path.is_file() else root / "data" / "llm_usage.jsonl",
        turns_path=turns_path,
        cutoff=cutoff,
        days=args.days,
        profile_filter="",
    )

    snapshot: Dict[str, Any] = {
        "schema": "change_baseline_v1",
        "label": label,
        "note": (args.note or "").strip(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(root),
        "root": str(root),
        "days_window": args.days,
        "metrics_period": _latest_metrics_summary(root / "data" / "benchmarks" / "metrics_periods_latest.json"),
        "c6_ab": c6,
        "kv": _kv_summary(args.days),
        "telemetry_tags": _telemetry_unknown_pct(args.days),
    }

    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    jsonl_path = root / "data" / "benchmarks" / "change_baselines.jsonl"
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "label": label,
                    "captured_at": snapshot["captured_at"],
                    "git_head": snapshot["git_head"],
                    "note": snapshot["note"],
                    "path": str(out_path.relative_to(root)).replace("\\", "/"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    print(f"Baseline: {out_path}")
    mp = snapshot.get("metrics_period") or {}
    kv = snapshot.get("kv") or {}
    print(
        f"  git={snapshot.get('git_head')}  llm_records={mp.get('llm_records')}  "
        f"kv_hit%={kv.get('kv_hit_pct')}  unknown_tag%={(snapshot.get('telemetry_tags') or {}).get('unknown_tag_pct')}"
    )
    print("Compare: python scripts/compare_change_baselines.py --before <label> --after <label>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
