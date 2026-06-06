#!/usr/bin/env python3
"""
C6 (UPGRADE_PLAN): сравнение нагрузки recent 10 vs 12 по llm_usage.jsonl и turns.jsonl.

Не меняет env — только отчёт для решения владельца:
  BRAIN_STANDARD_RECENT_COUNT=10 (prod) vs 12 (эксперимент на LAN).

Запуск:
  python scripts/analyze_brain_recent_ab.py
  python scripts/analyze_brain_recent_ab.py --days 14 --profile standard
  python scripts/analyze_brain_recent_ab.py --turns-path data/runtime/turns.jsonl
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


def _analyze_llm_usage(path: Path, *, cutoff: datetime, profile_filter: str) -> Dict[str, Any]:
    by_tag: DefaultDict[str, List[int]] = defaultdict(list)
    by_kind: DefaultDict[str, List[int]] = defaultdict(list)
    n_rows = 0
    n_brain = 0

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
            if profile_filter and prof != profile_filter.strip().lower():
                continue
            pt = row.get("prompt_tokens") or row.get("input_tokens")
            try:
                pt_i = int(pt)
            except (TypeError, ValueError):
                continue
            n_rows += 1
            tag = str(row.get("telemetry_tag") or row.get("tag") or "?").strip()
            kind = str(row.get("telemetry_kind") or "").strip()
            by_tag[tag.split(":")[0]].append(pt_i)
            if kind:
                by_kind[kind].append(pt_i)
            if tag.startswith("brain") or kind == "brain":
                n_brain += 1
                by_tag["__brain_all__"].append(pt_i)

    return {"n_rows": n_rows, "n_brain": n_brain, "by_tag": dict(by_tag), "by_kind": dict(by_kind)}


def _analyze_llm_by_recent_limit(
    path: Path,
    *,
    cutoff: datetime,
    tag_prefix: str = "brain",
    profile_filter: str = "",
) -> Dict[int, List[int]]:
    """C6: brain_recent_limit из llm_usage (telemetry_extra в pipeline)."""
    by_recent: DefaultDict[int, List[int]] = defaultdict(list)
    prof_want = profile_filter.strip().lower()
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
            tag = str(row.get("telemetry_tag") or row.get("tag") or "").strip()
            if tag_prefix and not tag.startswith(tag_prefix):
                continue
            if prof_want:
                bp = str(row.get("brain_profile") or row.get("profile") or "").strip().lower()
                if bp and bp != prof_want:
                    continue
            lim = row.get("brain_recent_limit")
            try:
                lim_i = int(lim)
            except (TypeError, ValueError):
                lim_i = 0
            try:
                pt_i = int(row.get("prompt_tokens") or row.get("prompt_tokens_est") or 0)
            except (TypeError, ValueError):
                pt_i = 0
            if pt_i <= 0:
                continue
            by_recent[lim_i].append(pt_i)
    return dict(by_recent)


def _analyze_turns(path: Path, *, cutoff: datetime, profile_filter: str) -> Dict[str, Any]:
    """Группировка prompt_tokens_est по brain_recent_limit (после деплоя телеметрии)."""
    by_recent: DefaultDict[int, List[int]] = defaultdict(list)
    n = 0
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
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts and ts < cutoff:
                continue
            prof = str(row.get("profile") or "").strip().lower()
            if profile_filter and prof != profile_filter.strip().lower():
                continue
            pt = row.get("prompt_tokens_est")
            try:
                pt_i = int(pt)
            except (TypeError, ValueError):
                continue
            if pt_i <= 0:
                continue
            lim = row.get("brain_recent_limit")
            try:
                lim_i = int(lim)
            except (TypeError, ValueError):
                lim_i = 0
            n += 1
            by_recent[lim_i].append(pt_i)
    return {"n": n, "by_recent": dict(by_recent)}


def _load_repo_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass


def _report_pack(
    *,
    llm_path: Path,
    turns_path: Path,
    cutoff: datetime,
    days: int,
    profile_filter: str,
) -> Dict[str, Any]:
    std_recent = os.getenv("BRAIN_STANDARD_RECENT_COUNT", "10")
    short_recent = os.getenv("BRAIN_SHORT_RECENT_COUNT", "10")
    report: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "env": {
            "BRAIN_STANDARD_RECENT_COUNT": std_recent,
            "BRAIN_SHORT_RECENT_COUNT": short_recent,
        },
        "llm_usage_path": str(llm_path),
        "turns_path": str(turns_path),
    }
    if llm_path.is_file():
        pack = _analyze_llm_usage(llm_path, cutoff=cutoff, profile_filter=profile_filter)
        by_recent_llm = _analyze_llm_by_recent_limit(llm_path, cutoff=cutoff)
        by_std = _analyze_llm_by_recent_limit(
            llm_path,
            cutoff=cutoff,
            tag_prefix="brain_first",
            profile_filter="standard",
        )
        report["llm_usage"] = {
            "n_rows": pack["n_rows"],
            "n_brain": pack["n_brain"],
            "by_recent_limit": {
                str(k or "unset"): {"n": len(v), "p50": _p50(v)} for k, v in by_recent_llm.items()
            },
            "brain_first_standard": {
                str(k or "unset"): {"n": len(v), "p50": _p50(v)} for k, v in by_std.items()
            },
        }
        if 10 in by_recent_llm and 12 in by_recent_llm:
            report["llm_usage"]["delta_p50_12_minus_10"] = (
                _p50(by_recent_llm[12]) - _p50(by_recent_llm[10])
            )
    if turns_path.is_file():
        tpack = _analyze_turns(turns_path, cutoff=cutoff, profile_filter=profile_filter)
        by_recent = tpack.get("by_recent") or {}
        report["turns"] = {
            "n_with_tokens": tpack["n"],
            "by_recent_limit": {
                str(k or "unset"): {"n": len(v), "p50": _p50(v)} for k, v in by_recent.items()
            },
        }
        if 10 in by_recent and 12 in by_recent:
            report["turns"]["delta_p50_12_minus_10"] = _p50(by_recent[12]) - _p50(by_recent[10])
    return report


def main() -> int:
    _load_repo_env()
    ap = argparse.ArgumentParser(description="Brain recent A/B — prompt tokens from logs")
    ap.add_argument("--path", default="", help="llm_usage.jsonl (default data/runtime/llm_usage.jsonl)")
    ap.add_argument("--turns-path", default="", help="turns.jsonl (default GEMMA_TURNS_LOG_PATH)")
    ap.add_argument("--days", type=int, default=7, help="окно в днях")
    ap.add_argument("--profile", default="", help="фильтр profile в записи (опц.)")
    ap.add_argument("--json-out", default="", help="сохранить отчёт JSON (C6 baseline)")
    args = ap.parse_args()

    llm_path = Path(args.path or os.getenv("LLM_USAGE_PATH") or _default_llm_usage_path())
    turns_path = Path(
        args.turns_path
        or os.getenv("GEMMA_TURNS_LOG_PATH")
        or ROOT / "data/runtime/turns.jsonl"
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))

    report = _report_pack(
        llm_path=llm_path,
        turns_path=turns_path,
        cutoff=cutoff,
        days=args.days,
        profile_filter=args.profile,
    )
    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {out}")

    std_recent = report["env"]["BRAIN_STANDARD_RECENT_COUNT"]
    short_recent = report["env"]["BRAIN_SHORT_RECENT_COUNT"]

    print(f"Окно: последние {args.days} дн.")
    print(f"Текущий env (локально): BRAIN_STANDARD_RECENT_COUNT={std_recent} BRAIN_SHORT_RECENT_COUNT={short_recent}")
    print()

    pack: Dict[str, Any] = {}
    if llm_path.is_file():
        pack = _analyze_llm_usage(llm_path, cutoff=cutoff, profile_filter=args.profile)
        print(f"=== llm_usage: {llm_path} ===")
        print(f"Записей с токенами: {pack['n_rows']}; brain-вызовов: {pack['n_brain']}")
        print()
        print("Медиана prompt_tokens по telemetry_tag (top brain):")
        by_tag = pack.get("by_tag") or {}
        brain_tags = sorted(
            ((k, _p50(v), len(v)) for k, v in by_tag.items() if k.startswith("brain") or k == "__brain_all__"),
            key=lambda x: -x[2],
        )
        for tag, med, cnt in brain_tags[:12]:
            print(f"  {tag:28} n={cnt:5}  p50={med:6}")
        print()
        print("По telemetry_kind:")
        for kind, med, cnt in sorted(
            ((k, _p50(v), len(v)) for k, v in (pack.get("by_kind") or {}).items()),
            key=lambda x: -x[2],
        )[:10]:
            print(f"  {kind:20} n={cnt:5}  p50={med:6}")
        by_recent_llm = _analyze_llm_by_recent_limit(llm_path, cutoff=cutoff)
        if by_recent_llm:
            print("Медиана prompt_tokens по brain_recent_limit (llm_usage, brain-вызовы):")
            for lim in sorted(by_recent_llm.keys()):
                vals = by_recent_llm[lim]
                label = str(lim) if lim else "(не задано)"
                print(f"  recent={label:6} n={len(vals):5}  p50={_p50(vals):6}")
            if 10 in by_recent_llm and 12 in by_recent_llm:
                d = _p50(by_recent_llm[12]) - _p50(by_recent_llm[10])
                print(f"  Δ p50 (12−10): {d:+d} tok (llm_usage)")
        by_std = _analyze_llm_by_recent_limit(
            llm_path,
            cutoff=cutoff,
            tag_prefix="brain_first",
            profile_filter="standard",
        )
        if by_std:
            print("brain_first + profile=standard:")
            for lim in sorted(by_std.keys()):
                vals = by_std[lim]
                label = str(lim) if lim else "(не задано)"
                print(f"  recent={label:6} n={len(vals):5}  p50={_p50(vals):6}")
        print()
    else:
        print(f"Нет llm_usage: {llm_path}")

    if turns_path.is_file():
        tpack = _analyze_turns(turns_path, cutoff=cutoff, profile_filter=args.profile)
        print(f"=== turns: {turns_path} ===")
        print(f"Ходов с prompt_tokens_est>0: {tpack['n']}")
        by_recent = tpack.get("by_recent") or {}
        if by_recent:
            print("Медиана prompt_tokens_est по brain_recent_limit:")
            for lim in sorted(by_recent.keys()):
                vals = by_recent[lim]
                label = str(lim) if lim else "(не задано)"
                print(f"  recent={label:6} n={len(vals):5}  p50={_p50(vals):6}")
            if 10 in by_recent and 12 in by_recent:
                d = _p50(by_recent[12]) - _p50(by_recent[10])
                print(f"  Δ p50 (12−10): {d:+d} tokens_est")
        else:
            print("  brain_recent_limit ещё нет в логах — после деплоя telemetry bridge нужны brain-ходы.")
            if pack.get("n_brain"):
                med = _p50((pack.get("by_tag") or {}).get("__brain_all__") or [])
                if med:
                    print(
                        f"  (прокси llm_usage __brain_all__ p50={med} tok — "
                        f"сравнение 10 vs 12 только по turns с brain_recent_limit)"
                    )
        print()
    else:
        print(f"Нет turns: {turns_path}")

    print(
        "A/B: на LAN выставить BRAIN_STANDARD_RECENT_COUNT=12, через 3–7 дней "
        "сравнить p50 по brain_recent_limit; не поднимать на VPS без отчёта в DEV_DIARY."
    )
    print("KV: python scripts/analyze_kv_session_metrics.py --days", args.days)
    return 0 if llm_path.is_file() or turns_path.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
