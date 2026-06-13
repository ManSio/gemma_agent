#!/usr/bin/env python3
"""Снимок метрик кэша и задержек: llm_usage/turns (24h) + MONITOR/OBS с boot."""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core.admin_ops_metrics import collect_admin_self_metrics, resolve_turns_path


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Разобрать ISO timestamp из turns/metrics."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _read_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    """Построчно читать JSONL."""
    if not path.is_file():
        return
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


def _p95(vals: List[float]) -> Optional[int]:
    """p95 в миллисекундах."""
    if not vals:
        return None
    s = sorted(vals)
    idx = int(0.95 * (len(s) - 1))
    return int(round(s[idx]))


def _p50(vals: List[float]) -> Optional[int]:
    """Медиана в миллисекундах."""
    if not vals:
        return None
    return int(round(statistics.median(vals)))


def _latest_metrics_counters(root: Path) -> Dict[str, Any]:
    """Последний снимок MONITOR из metrics_timeseries.jsonl."""
    path = root / "data/runtime/metrics_timeseries.jsonl"
    out: Dict[str, Any] = {"path": str(path), "available": False, "ts": None, "cache_counters": {}}
    if not path.is_file():
        return out
    last: Optional[Dict[str, Any]] = None
    for row in _read_jsonl(path):
        if row.get("counters"):
            last = row
    if not last:
        return out
    cnt = last.get("counters") if isinstance(last.get("counters"), dict) else {}
    out["available"] = True
    out["ts"] = last.get("ts")
    out["cache_counters"] = _cache_counters(cnt)
    reuse_h = int(cnt.get("openrouter_prompt_reuse_hits_total") or 0)
    reuse_m = int(cnt.get("openrouter_prompt_reuse_misses_total") or 0)
    tot = reuse_h + reuse_m
    out["prompt_reuse_hit_rate_pct"] = round(100.0 * reuse_h / tot, 1) if tot else None
    out["brain_response_cache_hit_total"] = int(cnt.get("brain_response_cache_hit_total") or 0)
    out["brain_prompt_cache_hit_total"] = int(cnt.get("brain_prompt_cache_hit_total") or 0)
    out["openrouter_cached_read_tokens"] = int(cnt.get("openrouter_prompt_cache_read_tokens_total") or 0)
    return out


def _stage_ms_window(root: Path, *, hours: float = 24.0) -> Dict[str, Any]:
    """Агрегат stage_ms из turns.jsonl за окно."""
    path = resolve_turns_path(root)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.5, hours))
    buckets: Dict[str, List[float]] = {}
    turns = 0
    with_stage = 0
    for row in _read_jsonl(path):
        ts = _parse_ts(row.get("ts"))
        if ts is not None and ts < cutoff:
            continue
        if row.get("type") == "scenario":
            continue
        turns += 1
        sm = row.get("stage_ms")
        if not isinstance(sm, dict):
            continue
        with_stage += 1
        for k, v in sm.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv < 0:
                continue
            buckets.setdefault(str(k), []).append(fv)
    p95_map = {k: _p95(v) for k, v in buckets.items()}
    p50_map = {k: _p50(v) for k, v in buckets.items()}
    return {
        "path": str(path),
        "hours": hours,
        "turns": turns,
        "turns_with_stage_ms": with_stage,
        "stage_p50_ms": p50_map,
        "stage_p95_ms": p95_map,
    }


def _cache_counters(counters: Dict[str, int]) -> Dict[str, int]:
    """Счётчики MONITOR, связанные с кэшем и reuse."""
    keys = sorted(
        k
        for k in counters
        if any(x in k.lower() for x in ("cache", "reuse", "kv", "prompt_cache"))
    )
    return {k: int(counters[k]) for k in keys}


def collect_snapshot(root: Path, *, hours: float = 24.0) -> Dict[str, Any]:
    """Собрать сводку cache/latency для ops."""
    admin = collect_admin_self_metrics(root, hours=hours)
    try:
        from core.monitoring import MONITOR
        from core.observability import OBS

        snap = MONITOR.snapshot()
        obs = OBS.snapshot()
        cnt = snap.get("counters") if isinstance(snap.get("counters"), dict) else {}
        live = dict(admin.get("live") if isinstance(admin.get("live"), dict) else {})
        live["cache_counters"] = _cache_counters(cnt if isinstance(cnt, dict) else {})
        live["latency_p95_ms"] = {
            str(k): float(v) for k, v in (obs.get("latency_p95_ms") or {}).items()
        }
        live["monitor_counters_top"] = dict(
            sorted(
                ((k, int(v)) for k, v in (cnt or {}).items()),
                key=lambda x: -abs(x[1]),
            )[:25]
        )
        admin["live"] = live
    except Exception as e:
        admin["live_error"] = str(e)
    metrics_ts = _latest_metrics_counters(root)
    stage_ms = _stage_ms_window(root, hours=hours)
    return {
        "hours": hours,
        "root": str(root.resolve()),
        "admin_metrics": admin,
        "metrics_timeseries_latest": metrics_ts,
        "turn_stage_ms": stage_ms,
        "summary": _build_summary(admin, metrics_ts, stage_ms),
    }


def _build_summary(
    admin: Dict[str, Any],
    metrics_ts: Dict[str, Any],
    stage_ms: Dict[str, Any],
) -> Dict[str, Any]:
    """Компактная выжимка для отчёта."""
    llm = admin.get("llm_24h") if isinstance(admin.get("llm_24h"), dict) else {}
    turns = admin.get("turns_24h") if isinstance(admin.get("turns_24h"), dict) else {}
    live = admin.get("live") if isinstance(admin.get("live"), dict) else {}
    cache = live.get("cache_counters") if isinstance(live.get("cache_counters"), dict) else {}
    reuse_hits = int(cache.get("openrouter_prompt_reuse_hits_total", 0))
    reuse_miss = int(cache.get("openrouter_prompt_reuse_misses_total", 0))
    reuse_total = reuse_hits + reuse_miss
    st_p95 = stage_ms.get("stage_p95_ms") if isinstance(stage_ms.get("stage_p95_ms"), dict) else {}
    return {
        "llm_brain_rows_24h": llm.get("brain_rows"),
        "kv_hit_pct_24h": llm.get("kv_hit_pct"),
        "cached_token_pct_24h": llm.get("cached_token_pct"),
        "brain_latency_p50_ms_24h": llm.get("brain_latency_p50_ms"),
        "brain_latency_p95_ms_24h": llm.get("brain_latency_p95_ms"),
        "turns_24h": turns.get("turns"),
        "turn_latency_p95_ms_24h": turns.get("latency_p95_ms"),
        "telegram_p95_ms_boot": live.get("telegram_p95_ms"),
        "openrouter_p95_ms_boot": live.get("openrouter_p95_ms"),
        "prompt_reuse_hit_rate_boot_pct": round(100.0 * reuse_hits / reuse_total, 1) if reuse_total else None,
        "brain_response_cache_hits_boot": cache.get("brain_response_cache_hit_total"),
        "brain_prompt_cache_hits_boot": cache.get("brain_prompt_cache_hit_total"),
        "metrics_ts_at": metrics_ts.get("ts"),
        "prompt_reuse_hit_rate_metrics_ts_pct": metrics_ts.get("prompt_reuse_hit_rate_pct"),
        "brain_response_cache_metrics_ts": metrics_ts.get("brain_response_cache_hit_total"),
        "openrouter_cached_read_tokens_metrics_ts": metrics_ts.get("openrouter_cached_read_tokens"),
        "turn_total_p95_ms": st_p95.get("total"),
        "turn_exec_modules_p95_ms": st_p95.get("exec_modules_done"),
        "turn_plan_p95_ms": st_p95.get("plan_done"),
        "turns_with_stage_ms": stage_ms.get("turns_with_stage_ms"),
    }


def _render_ru(summary: Dict[str, Any], admin: Dict[str, Any], report: Dict[str, Any]) -> str:
    """Краткий текстовый отчёт."""
    lines: List[str] = [
        "## Кэш и задержки (снимок)",
        "",
        "### 24h (llm_usage.jsonl + turns.jsonl)",
        f"- Brain LLM вызовов: **{summary.get('llm_brain_rows_24h', '—')}**",
        f"- KV cache hit (строки с cached_prompt_tokens>0): **{summary.get('kv_hit_pct_24h', '—')}%**",
        f"- Доля cached prompt tokens: **{summary.get('cached_token_pct_24h', '—')}%**",
        f"- Brain latency p50 / p95: **{summary.get('brain_latency_p50_ms_24h', '—')}** / **{summary.get('brain_latency_p95_ms_24h', '—')}** ms",
        f"- Telegram turns: **{summary.get('turns_24h', '—')}**, p95 ответа: **{summary.get('turn_latency_p95_ms_24h', '—')}** ms",
        "",
        "### С момента перезапуска бота (in-memory, если скрипт в процессе бота)",
        f"- Telegram pipeline p95: **{summary.get('telegram_p95_ms_boot', '—')}** ms",
        f"- OpenRouter completion p95: **{summary.get('openrouter_p95_ms_boot', '—')}** ms",
        f"- OpenRouter prompt reuse hit rate: **{summary.get('prompt_reuse_hit_rate_boot_pct', '—')}%**",
        f"- brain_response_cache_hit: **{summary.get('brain_response_cache_hits_boot', 0)}**",
        f"- brain_prompt_cache_hit: **{summary.get('brain_prompt_cache_hits_boot', 0)}**",
        "",
        "### metrics_timeseries (последний снимок MONITOR на диске)",
        f"- Снимок: **{summary.get('metrics_ts_at', '—')}**",
        f"- OpenRouter prompt reuse hit rate: **{summary.get('prompt_reuse_hit_rate_metrics_ts_pct', '—')}%**",
        f"- brain_response_cache_hit (накопительно): **{summary.get('brain_response_cache_metrics_ts', '—')}**",
        f"- openrouter cached read tokens: **{summary.get('openrouter_cached_read_tokens_metrics_ts', '—')}**",
        "",
        "### Задержки по стадиям pipeline (turns.stage_ms, 24h)",
        f"- Ходов со stage_ms: **{summary.get('turns_with_stage_ms', '—')}**",
        f"- p95 total / exec_modules / plan: **{summary.get('turn_total_p95_ms', '—')}** / **{summary.get('turn_exec_modules_p95_ms', '—')}** / **{summary.get('turn_plan_p95_ms', '—')}** ms",
    ]
    live = admin.get("live") if isinstance(admin.get("live"), dict) else {}
    lat_boot = live.get("latency_p95_ms") if isinstance(live.get("latency_p95_ms"), dict) else {}
    if lat_boot:
        lines.extend(["", "### OBS p95 по стадиям (boot)", ""])
        for k, v in sorted(lat_boot.items(), key=lambda x: -float(x[1])):
            lines.append(f"- `{k}`: **{int(round(float(v)))}** ms")
    cache_boot = live.get("cache_counters") if isinstance(live.get("cache_counters"), dict) else {}
    if cache_boot:
        lines.extend(["", "### Счётчики кэша (boot)", ""])
        for k, v in sorted(cache_boot.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: **{v}**")
    stage = report.get("turn_stage_ms") if isinstance(report.get("turn_stage_ms"), dict) else {}
    st_p95 = stage.get("stage_p95_ms") if isinstance(stage.get("stage_p95_ms"), dict) else {}
    if st_p95:
        lines.extend(["", "### Все стадии p95 (turns)", ""])
        for k, v in sorted(st_p95.items(), key=lambda x: -(x[1] or 0)):
            if v is not None:
                lines.append(f"- `{k}`: **{v}** ms")
    mts = report.get("metrics_timeseries_latest") if isinstance(report.get("metrics_timeseries_latest"), dict) else {}
    mcache = mts.get("cache_counters") if isinstance(mts.get("cache_counters"), dict) else {}
    if mcache:
        lines.extend(["", "### Кэш-счётчики (metrics_timeseries)", ""])
        for k, v in sorted(mcache.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: **{v}**")
    return "\n".join(lines) + "\n"


def main() -> int:
    """CLI: вывести снимок и опционально сохранить JSON/Markdown."""
    ap = argparse.ArgumentParser(description="Снимок cache/latency метрик")
    ap.add_argument("--root", default=".", help="Корень gemma_bot")
    ap.add_argument("--hours", type=float, default=24.0, help="Окно llm/turns")
    ap.add_argument("--json", default="", help="Путь для JSON")
    ap.add_argument("--md", default="", help="Путь для markdown")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    report = collect_snapshot(root, hours=args.hours)
    text = _render_ru(report["summary"], report["admin_metrics"], report)
    print(text)
    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {p}")
    if args.md:
        p = Path(args.md)
        if not p.is_absolute():
            p = root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
