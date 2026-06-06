"""Сводные ops-метрики для /admin_self: 24h лог + с boot (без LLM)."""
from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _p50(vals: List[float]) -> Optional[int]:
    if not vals:
        return None
    return int(round(statistics.median(vals)))


def _p95(vals: List[float]) -> Optional[int]:
    if not vals:
        return None
    s = sorted(vals)
    idx = int(0.95 * (len(s) - 1))
    return int(round(s[idx]))


def resolve_llm_usage_path(project_root: Path) -> Path:
    raw = (os.getenv("GEMMA_LLM_USAGE_PATH") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else project_root / p
    candidates = [
        project_root / "data/runtime/llm_usage.jsonl",
        project_root / "data/llm_usage.jsonl",
    ]
    best = candidates[0]
    best_size = -1
    for c in candidates:
        if c.is_file():
            sz = c.stat().st_size
            if sz > best_size:
                best = c
                best_size = sz
    return best


def resolve_turns_path(project_root: Path) -> Path:
    raw = (os.getenv("GEMMA_TURNS_LOG_PATH") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else project_root / p
    return project_root / "data/runtime/turns.jsonl"


def _is_brain_row(row: Dict[str, Any]) -> bool:
    tag = str(row.get("telemetry_tag") or row.get("tag") or "")
    kind = str(row.get("telemetry_kind") or "")
    return tag.startswith("brain") or kind == "brain"


def _row_latency_ms(row: Dict[str, Any]) -> Optional[float]:
    raw = row.get("latency_ms")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def _recent_limit(row: Dict[str, Any]) -> Optional[int]:
    try:
        lim = int(row.get("brain_recent_limit") or 0)
    except (TypeError, ValueError):
        return None
    return lim if lim > 0 else None


def summarize_llm_usage_window(
    project_root: Path,
    *,
    hours: float = 24.0,
) -> Dict[str, Any]:
    path = resolve_llm_usage_path(project_root)
    out: Dict[str, Any] = {
        "path": str(path),
        "hours": hours,
        "available": False,
        "brain_rows": 0,
        "llm_ok": 0,
        "llm_fail": 0,
        "brain_latency_p50_ms": None,
        "brain_latency_p95_ms": None,
        "all_latency_p95_ms": None,
        "kv_hit_pct": None,
        "cached_token_pct": None,
        "recent_brain_counts": {},
    }
    if not path.is_file():
        return out

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.5, hours))
    brain_latencies: List[float] = []
    all_latencies: List[float] = []
    hits = misses = 0
    cached_sum = prompt_sum = 0
    recent_counts: Dict[str, int] = {}

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
        if ts is not None:
            ts = _ensure_utc(ts)
            if ts < cutoff:
                continue

        ok = bool(row.get("ok", True))
        if ok:
            out["llm_ok"] += 1
        else:
            out["llm_fail"] += 1

        lat = _row_latency_ms(row)
        if lat is not None and ok:
            all_latencies.append(lat)

        if _is_brain_row(row):
            out["brain_rows"] += 1
            if lat is not None and ok:
                brain_latencies.append(lat)
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
            lim = _recent_limit(row)
            if lim is not None:
                key = str(lim)
                recent_counts[key] = recent_counts.get(key, 0) + 1

    total = hits + misses
    out["available"] = out["brain_rows"] > 0 or out["llm_ok"] > 0 or out["llm_fail"] > 0
    out["brain_latency_p50_ms"] = _p50(brain_latencies)
    out["brain_latency_p95_ms"] = _p95(brain_latencies)
    out["all_latency_p95_ms"] = _p95(all_latencies)
    if total:
        out["kv_hit_pct"] = round(100.0 * hits / total, 1)
    if prompt_sum:
        out["cached_token_pct"] = round(100.0 * cached_sum / prompt_sum, 1)
    out["recent_brain_counts"] = dict(sorted(recent_counts.items(), key=lambda x: int(x[0])))
    return out


def summarize_turns_window(
    project_root: Path,
    *,
    hours: float = 24.0,
) -> Dict[str, Any]:
    path = resolve_turns_path(project_root)
    out: Dict[str, Any] = {
        "path": str(path),
        "hours": hours,
        "available": False,
        "turns": 0,
        "issues": 0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
    }
    if not path.is_file():
        return out

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.5, hours))
    latencies: List[float] = []

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
        ts = _parse_ts(row.get("ts"))
        if ts is not None:
            ts = _ensure_utc(ts)
            if ts < cutoff:
                continue
        out["turns"] += 1
        if row.get("issues"):
            out["issues"] += 1
        lat = _row_latency_ms(row)
        if lat is not None:
            latencies.append(lat)

    out["available"] = out["turns"] > 0
    out["latency_p50_ms"] = _p50(latencies)
    out["latency_p95_ms"] = _p95(latencies)
    return out


def live_metrics_since_boot() -> Dict[str, Any]:
    """In-memory OBS/MONITOR с момента старта процесса бота."""
    out: Dict[str, Any] = {
        "available": False,
        "telegram_p95_ms": None,
        "openrouter_p95_ms": None,
        "llm_ok": 0,
        "llm_fail": 0,
        "input_messages": 0,
        "telegram_samples": 0,
    }
    try:
        from core.monitoring import MONITOR
        from core.observability import OBS

        cnt = MONITOR.counters
        tg_key = "telegram_pipeline"
        or_key = "openrouter_completion_ms"
        tg_vals = list(OBS.latencies_ms.get(tg_key, []))
        out["available"] = bool(tg_vals) or int(cnt.get("openrouter_completion_ok_total") or 0) > 0
        out["telegram_p95_ms"] = int(round(OBS.p95(tg_key))) if tg_vals else None
        out["openrouter_p95_ms"] = int(round(OBS.p95(or_key))) if OBS.latencies_ms.get(or_key) else None
        out["llm_ok"] = int(cnt.get("openrouter_completion_ok_total") or 0)
        out["llm_fail"] = int(cnt.get("openrouter_completion_fail_total") or 0)
        out["input_messages"] = int(cnt.get("input_messages_total") or 0)
        out["telegram_samples"] = len(tg_vals)
    except Exception:
        pass
    return out


def format_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def format_recent_ab_counts(counts: Dict[str, int]) -> str:
    if not counts:
        return "—"
    parts = [f"r{k}={v}" for k, v in counts.items()]
    return " ".join(parts[:6])


def collect_admin_self_metrics(project_root: Path, *, hours: float = 24.0) -> Dict[str, Any]:
    return {
        "llm_24h": summarize_llm_usage_window(project_root, hours=hours),
        "turns_24h": summarize_turns_window(project_root, hours=hours),
        "live": live_metrics_since_boot(),
    }
