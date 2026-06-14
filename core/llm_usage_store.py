"""
Персистентный журнал вызовов OpenRouter для /admin_llm_usage (токены, cost, виды).

Файл: GEMMA_LLM_USAGE_PATH или data/runtime/llm_usage.jsonl (рядом с ERROR_ANALYSIS_DIR).
Отключить запись: GEMMA_LLM_USAGE_PERSIST=false
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, TypedDict

from core.report_timezone import get_report_tz

# ── News logging schema ──────────────────────────────────────────────

class NewsSourceLog(TypedDict):
    """Один источник в логе генерации новостного ответа."""
    url: str
    domain: str
    fetch_method: str  # "rss" | "web_search" | "urlfetch"
    fetch_success: bool
    text_length: int
    parsing_confidence: float  # 0.0–1.0

class NewsGenerationLog(TypedDict, total=False):
    """Схема записи в llm_usage.jsonl для новостных ответов."""
    type: str  # "news_generation"
    timestamp: str  # ISO 8601 UTC
    user_id: str
    query: str  # что пользователь спросил (truncated 500)
    sources: List[NewsSourceLog]
    reply: str  # первые 500 символов ответа
    llm_model: str
    self_verify_run: bool
    self_verify_result: str  # "ok" | "fix:..." | "N/A"
    fetch_methods_used: List[str]
    total_sources: int
    avg_confidence: float
    trusted_domain_count: int


def news_generation_log(
    *,
    user_id: str = "",
    query: str = "",
    sources: Optional[List[Dict[str, Any]]] = None,
    reply: str = "",
    llm_model: str = "",
    self_verify_run: bool = False,
    self_verify_result: str = "N/A",
    consistency_checked: bool = False,
    consistency_ok: bool = True,
    consistency_conflicts_count: int = 0,
    consistency_recommendation: str = "safe",
) -> Dict[str, Any]:
    """Собрать строку лога новостного ответа."""
    from datetime import datetime, timezone

    src_list: List[Dict[str, Any]] = []
    total_conf = 0.0
    trusted = 0
    methods: set = set()
    for s in (sources or []):
        if isinstance(s, dict):
            src_list.append({
                "url": str(s.get("url", ""))[:300],
                "domain": str(s.get("domain", "")),
                "fetch_method": str(s.get("fetch_method", "unknown")),
                "fetch_success": bool(s.get("fetch_success", True)),
                "text_length": int(s.get("text_length", 0)),
                "parsing_confidence": float(s.get("parsing_confidence", 0.0)),
            })
            total_conf += float(s.get("parsing_confidence", 0.0))
            methods.add(str(s.get("fetch_method", "unknown")))
            if s.get("domain") in _TRUSTED_DOMAINS:
                trusted += 1
    n = len(src_list) or 1
    return {
        "type": "news_generation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": str(user_id)[:100],
        "query": (query or "")[:500],
        "sources": src_list,
        "reply": (reply or "")[:500],
        "llm_model": llm_model,
        "self_verify_run": self_verify_run,
        "self_verify_result": self_verify_result,
        "consistency_checked": bool(consistency_checked),
        "consistency_ok": bool(consistency_ok),
        "consistency_conflicts_count": int(consistency_conflicts_count),
        "consistency_recommendation": str(consistency_recommendation or "safe")[:80],
        "fetch_methods_used": sorted(methods),
        "total_sources": len(src_list),
        "avg_confidence": round(total_conf / n, 3),
        "trusted_domain_count": trusted,
    }

_TRUSTED_DOMAINS = frozenset({
    "reuters.com", "bbc.com", "bbc.co.uk", "ap.org", "apnews.com",
    "tass.ru", "interfax.ru", "kommersant.ru", "rbc.ru", "ria.ru",
    "unian.ua", "pravda.com.ua",
})

logger = logging.getLogger(__name__)

_USAGE_LOCK = threading.Lock()


def _sanitize_row_for_persistence(row: Dict[str, Any]) -> Dict[str, Any]:
    """Whitelist fields before llm_usage.jsonl append (CodeQL clear-text guard)."""
    from core.sensitive_export import llm_usage_row_for_disk

    return llm_usage_row_for_disk(row if isinstance(row, dict) else {})


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_log_path() -> str:
    raw = (os.getenv("GEMMA_LLM_USAGE_PATH") or "").strip()
    if raw:
        return raw
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    return os.path.join(base, "llm_usage.jsonl")


def log_path() -> str:
    return _default_log_path()


def append_record(row: Dict[str, Any]) -> None:
    if not _truthy("GEMMA_LLM_USAGE_PERSIST", True):
        return
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        from core.sensitive_export import write_llm_usage_jsonl

        with _USAGE_LOCK:
            write_llm_usage_jsonl(path, row)
    except OSError as e:
        logger.warning("llm_usage_store append failed: %s", e)


def reset_records() -> Dict[str, Any]:
    """
    Очистка персистентного журнала llm_usage.jsonl.
    Не трогает runtime-счётчики MONITOR.
    """
    path = log_path()
    existed = os.path.isfile(path)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        return {"ok": True, "log_path": path, "existed": existed}
    except OSError as e:
        return {"ok": False, "log_path": path, "existed": existed, "error": str(e)}


def _parse_ts(row: Dict[str, Any]) -> Optional[datetime]:
    ts = row.get("ts")
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _local_date_key(dt: datetime) -> str:
    return _ensure_utc(dt).astimezone(get_report_tz()).date().isoformat()


def format_row_ts_for_report(row: Dict[str, Any]) -> str:
    """Время строки журнала LLM для отображения в зоне отчёта."""
    from core.report_timezone import OPERATOR_DATETIME_FMT

    dt = _parse_ts(row)
    if dt is None:
        return str(row.get("ts", ""))[:22]
    loc = _ensure_utc(dt).astimezone(get_report_tz()).replace(microsecond=0)
    return loc.strftime(OPERATOR_DATETIME_FMT)


def recent_rows(*, days: float = 30.0) -> List[Dict[str, Any]]:
    """Записи за последние `days` дней (для сортировки в /admin_llm_usage)."""
    data = load_records()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: List[Dict[str, Any]] = []
    for r in data:
        dt = _parse_ts(r)
        if dt is not None and dt < cutoff:
            continue
        out.append(r)
    return out


def load_records(*, max_lines: int = 80000) -> List[Dict[str, Any]]:
    path = log_path()
    if not os.path.isfile(path):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
                if len(rows) >= max_lines:
                    break
    except OSError:
        return []
    return rows


def aggregate_usage(
    *,
    days: float = 30.0,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Сводка по журналу за последние `days` дней."""
    data = rows if rows is not None else load_records()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered: List[Dict[str, Any]] = []
    for r in data:
        dt = _parse_ts(r)
        if dt is not None and dt < cutoff:
            continue
        filtered.append(r)

    n_ok = sum(1 for r in filtered if r.get("ok"))
    n_fail = sum(1 for r in filtered if not r.get("ok"))
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    cost_sum = 0.0
    paid_n = 0
    free_n = 0
    by_kind: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"n": 0, "tokens": 0, "cost": 0.0})
    # последние 7 календарных дней в зоне отчёта — токены за день (для полоски)
    day_buckets: Dict[str, int] = defaultdict(int)
    day_cost: Dict[str, float] = defaultdict(float)

    for r in filtered:
        if not r.get("ok"):
            continue
        tt = int(r.get("total_tokens") or 0)
        pt = int(r.get("prompt_tokens") or 0)
        ct = int(r.get("completion_tokens") or 0)
        total_tokens += tt if tt else pt + ct
        prompt_tokens += pt
        completion_tokens += ct
        c = r.get("cost")
        cf = 0.0
        if c is not None:
            try:
                cf = float(c)
            except (TypeError, ValueError):
                cf = 0.0
        cost_sum += cf
        if cf > 1e-12:
            paid_n += 1
        else:
            free_n += 1
        kind = str(r.get("kind") or "chat")
        by_kind[kind]["n"] += 1
        by_kind[kind]["tokens"] += tt if tt else pt + ct
        by_kind[kind]["cost"] += cf
        dt = _parse_ts(r)
        if dt:
            dkey = _local_date_key(dt)
            day_buckets[dkey] += tt if tt else pt + ct
            day_cost[dkey] += cf

    # последние 7 календарных дней в зоне отчёта
    today_local = datetime.now(timezone.utc).astimezone(get_report_tz()).date()
    spark_days: List[str] = [(today_local - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    spark_tokens = [day_buckets.get(d, 0) for d in spark_days]
    spark_cost = [day_cost.get(d, 0.0) for d in spark_days]

    span_days = max(1e-6, min(days, 365.0))
    daily_avg_cost = cost_sum / span_days
    monthly_est_cost = daily_avg_cost * 30.0
    daily_avg_tokens = (total_tokens / span_days) if filtered else 0.0
    monthly_est_tokens = daily_avg_tokens * 30.0

    return {
        "period_days": days,
        "window_records": len(filtered),
        "completions_ok": n_ok,
        "completions_fail": n_fail,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "avg_tokens_per_ok": (total_tokens / n_ok) if n_ok else 0.0,
        "cost_sum": cost_sum,
        "paid_completions": paid_n,
        "free_completions": free_n,
        "by_kind": dict(by_kind),
        "sparkline_days": spark_days,
        "sparkline_tokens": spark_tokens,
        "sparkline_cost": spark_cost,
        "daily_avg_cost": daily_avg_cost,
        "monthly_est_cost": monthly_est_cost,
        "daily_avg_tokens": daily_avg_tokens,
        "monthly_est_tokens": monthly_est_tokens,
        "log_path": log_path(),
    }


def unicode_sparkline(values: List[float], *, width: int = 7) -> str:
    """Простая полоска ▁▂▃▄▅▆▇ по нормализации max в серии."""
    if not values:
        return "—"
    vals = list(values)[-width:]
    mx = max(vals) or 1e-9
    chars = "▁▂▃▄▅▆▇█"
    out = []
    for v in vals:
        idx = min(len(chars) - 1, int((v / mx) * (len(chars) - 1)))
        out.append(chars[idx])
    return "".join(out)


def sorted_records(
    rows: List[Dict[str, Any]],
    *,
    sort: str = "date",
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """sort: date | cost | tokens"""
    lim = max(5, min(limit, 200))
    ok_rows = [r for r in rows if isinstance(r, dict)]
    if sort == "cost":
        ok_rows.sort(key=lambda r: float(r.get("cost") or 0.0), reverse=True)
    elif sort == "tokens":
        ok_rows.sort(key=lambda r: int(r.get("total_tokens") or 0), reverse=True)
    else:
        ok_rows.sort(key=lambda r: _parse_ts(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return ok_rows[:lim]
