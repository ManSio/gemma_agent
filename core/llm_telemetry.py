"""
Телеметрия вызовов OpenRouter: токены, cost из usage (если API вернул), задержка.

Счётчики — MONITOR (видны в /admin_stats, diagnostic_snapshot.monitoring).
Латентность — OBS.observe_latency(\"openrouter_completion_ms\") для p95 в снимке.

Переменные:
  GEMMA_LLM_AUDIT_LOG=true — одна строка INFO на каждый запрос (успех/ошибка).
  GEMMA_VERBOSE_CORE=true — root DEBUG + приглушение httpx/aiohttp (см. logging_setup).

См. https://openrouter.ai/docs/quickstart и поле usage в ответе completions.

Персистентный журнал (JSONL) для /admin_llm_usage: GEMMA_LLM_USAGE_PATH, GEMMA_LLM_USAGE_PERSIST.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.llm_usage_store import append_record
from core.monitoring import MONITOR
from core.observability import OBS

logger = logging.getLogger(__name__)


def _default_telemetry_tag() -> str:
    return (os.getenv("LLM_TELEMETRY_DEFAULT_TAG") or "openrouter_chat").strip() or "openrouter_chat"


def build_openrouter_telemetry(
    *,
    tag: Optional[str] = None,
    kind: Optional[str] = None,
    session_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
    stream: bool = False,
    vision: bool = False,
) -> Dict[str, Any]:
    """E4: единый блок tag/kind для llm_usage.jsonl."""
    from core.llm_tiered import telemetry_kind_from_tag

    base_tag = (tag or "").strip() or _default_telemetry_tag()
    if vision and base_tag in ("openrouter_chat", "openrouter"):
        base_tag = "openrouter_vision"
    base_kind = (kind or "").strip() or ("vision" if vision else telemetry_kind_from_tag(base_tag))
    out: Dict[str, Any] = {
        "telemetry_tag": base_tag,
        "telemetry_kind": base_kind,
        "tag": base_tag,
        "kind": base_kind,
    }
    if stream:
        out["stream"] = True
    if session_id:
        out["session_id"] = session_id
    if isinstance(extra, dict):
        for k, v in extra.items():
            if v is not None and v != "":
                out[str(k)] = v
    return out


def normalize_llm_usage_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Дублирует tag/kind в telemetry_* поля для админки и analyze-скриптов."""
    from core.llm_tiered import telemetry_kind_from_tag

    out = dict(row)
    tag = str(out.get("telemetry_tag") or out.get("tag") or "").strip()
    if not tag:
        tag = _default_telemetry_tag()
    kind = str(out.get("telemetry_kind") or out.get("kind") or "").strip()
    if not kind:
        kind = telemetry_kind_from_tag(tag)
    out["telemetry_tag"] = tag
    out["telemetry_kind"] = kind
    out["tag"] = tag
    out["kind"] = kind
    return out


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def usage_summary(usage: Any) -> Dict[str, Any]:
    """Выжимка usage из тела ответа OpenRouter (нормализовано под OpenAI schema)."""
    if not isinstance(usage, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if k in usage and usage[k] is not None:
            try:
                out[k] = int(usage[k])
            except (TypeError, ValueError):
                out[k] = usage[k]
    if usage.get("cost") is not None:
        try:
            out["cost"] = float(usage["cost"])
        except (TypeError, ValueError):
            out["cost"] = usage["cost"]
    cd = usage.get("cost_details")
    if isinstance(cd, dict):
        out["cost_details"] = cd
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        if ptd.get("cached_tokens") is not None:
            try:
                out["cached_prompt_tokens"] = int(ptd["cached_tokens"])
            except (TypeError, ValueError):
                out["cached_prompt_tokens"] = ptd.get("cached_tokens")
        if ptd.get("cache_write_tokens") is not None:
            try:
                out["cache_write_tokens"] = int(ptd["cache_write_tokens"])
            except (TypeError, ValueError):
                out["cache_write_tokens"] = ptd.get("cache_write_tokens")
    ctd = usage.get("completion_tokens_details")
    if isinstance(ctd, dict) and ctd.get("reasoning_tokens") is not None:
        out["reasoning_tokens"] = ctd.get("reasoning_tokens")
    return out


def record_openrouter_completion(
    *,
    ok: bool,
    requested_model: str,
    upstream_model: Optional[str],
    latency_ms: float,
    usage: Any,
    http_status: Optional[int] = None,
    error: Optional[str] = None,
    content_chars: int = 0,
    telemetry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Обновляет MONITOR/OBS, опционально пишет audit в лог.
    Возвращает нормализованный usage (пустой dict при ошибке парсинга).
    """
    summary = usage_summary(usage) if ok and usage else {}
    OBS.observe_latency("openrouter_completion_ms", float(latency_ms))

    if ok:
        MONITOR.inc("openrouter_completion_ok_total")
        pt = int(summary.get("prompt_tokens") or 0)
        ct = int(summary.get("completion_tokens") or 0)
        if pt:
            MONITOR.inc("openrouter_prompt_tokens_total", pt)
        if ct:
            MONITOR.inc("openrouter_completion_tokens_total", ct)
        cpt = summary.get("cached_prompt_tokens")
        if cpt is not None:
            try:
                n = int(cpt)
                if n > 0:
                    MONITOR.inc("openrouter_prompt_cache_read_tokens_total", n)
                    MONITOR.inc("openrouter_prompt_reuse_hits_total")
                else:
                    MONITOR.inc("openrouter_prompt_reuse_misses_total")
            except (TypeError, ValueError):
                pass
        cost = summary.get("cost")
        if cost is not None:
            try:
                c = float(cost)
                if c > 0:
                    MONITOR.inc("openrouter_paid_completions_total")
                # сумма в нано-кредитах (int), чтобы не терять float в счётчике
                MONITOR.inc("openrouter_cost_credits_nanos_total", max(0, int(round(c * 1e9))))
            except (TypeError, ValueError):
                pass
    else:
        MONITOR.inc("openrouter_completion_fail_total")

    payload: Dict[str, Any] = {
        "gemma_event": "openrouter_completion",
        "ok": ok,
        "requested_model": requested_model,
        "upstream_model": upstream_model,
        "latency_ms": int(round(latency_ms)),
        "http_status": http_status,
        "usage": summary,
        "content_chars": content_chars,
        "error": (error or "")[:400],
    }

    if telemetry and telemetry.get("cached"):
        payload["cached"] = True
        payload["cached_by"] = "llm_step_cache"

    # Emit событие на шину
    try:
        from core.event_bus import bus

        bus.emit_ff("openrouter.done", {
            "model": requested_model,
            "latency_ms": latency_ms,
            "ok": ok,
            "tokens_total": summary.get("total_tokens", 0),
            "cost": summary.get("cost", 0.0),
            "cached_tok": summary.get("cached_prompt_tokens", 0),
            "error": error[:200] if error else None,
        })
    except Exception as e:
        logger.debug('%s optional failed: %s', 'llm_telemetry', e, exc_info=True)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("llm_telemetry %s", json.dumps(payload, ensure_ascii=False, default=str))

    if _truthy("GEMMA_LLM_AUDIT_LOG"):
        if ok:
            _cached = summary.get("cached_prompt_tokens")
            _cwt = summary.get("cache_write_tokens")
            logger.info(
                "openrouter ok latency_ms=%s model=%s upstream=%s tokens_total=%s cost=%s chars=%s cached_tok=%s cache_write=%s",
                int(latency_ms),
                requested_model,
                upstream_model or "-",
                summary.get("total_tokens", "-"),
                summary.get("cost", "-"),
                content_chars,
                _cached if _cached is not None else "-",
                _cwt if _cwt is not None else "-",
                extra=payload,
            )
        else:
            logger.info(
                "openrouter FAIL http=%s latency_ms=%s model=%s err=%s",
                http_status,
                int(latency_ms),
                requested_model,
                (error or "")[:200],
                extra=payload,
            )

    row: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "requested_model": requested_model,
        "upstream_model": upstream_model,
        "latency_ms": int(round(latency_ms)),
        "http_status": http_status,
        "content_chars": content_chars,
        "error": (error or "")[:400],
        "prompt_tokens": summary.get("prompt_tokens"),
        "completion_tokens": summary.get("completion_tokens"),
        "total_tokens": summary.get("total_tokens"),
        "cost": summary.get("cost"),
        "cached_prompt_tokens": summary.get("cached_prompt_tokens"),
        "cache_write_tokens": summary.get("cache_write_tokens"),
    }
    if telemetry:
        for k, v in telemetry.items():
            if v is not None and v != "":
                row[k] = v
    row = normalize_llm_usage_row(row)
    append_record(row)

    return summary


def recent_calls_summary(minutes: int = 5) -> Dict[str, Any]:
    """Сводка по последним N минутам LLM-вызовов (для диагностики)."""
    try:
        from core.llm_usage_store import recent_rows

        rows = recent_rows(days=minutes / 1440.0)
    except Exception:
        return {}
    if not rows:
        return {"ok": 0, "fail": 0, "avg_latency_ms": 0}
    ok_n = sum(1 for r in rows if r.get("ok"))
    fail_n = sum(1 for r in rows if not r.get("ok"))
    lats = [r.get("latency_ms", 0) or 0 for r in rows if r.get("ok")]
    avg_lat = (sum(lats) / len(lats)) if lats else 0
    return {
        "ok": ok_n,
        "fail": fail_n,
        "avg_latency_ms": avg_lat,
    }
