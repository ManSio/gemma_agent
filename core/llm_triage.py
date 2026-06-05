"""
LLM Triage — анализ срабатываний healers через LLM и генерация рекомендаций.

Поток:
1. Healer срабатывает → bus.emit("healer.action")
2. TriageCollector подхватывает → аккумулирует
3. По команде /admin_bug_heal_triage → LLM анализирует контекст →
   выдаёт рекомендации (что делать: env, команды, патчи)
4. /admin_bug_heal_list — просмотр
5. /admin_bug_heal_apply <id> — отметка "применено"

Хранилище: data/runtime/llm_triage_recommendations.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

from core.event_bus import bus

logger = logging.getLogger(__name__)

# ─── Хранилище рекомендаций ──────────────────────────────────────────────

_TRIAGE_STORE: List[Dict[str, Any]] = []
_LOCK = Lock()
_MAX_STORE = int(os.getenv("LLM_TRIAGE_MAX_RECOMMENDATIONS", "50"))
_ENABLED = (os.getenv("LLM_TRIAGE_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"})


def _store_path() -> Path:
    p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
    return p.resolve() / "llm_triage_recommendations.json"


def _save_store() -> None:
    try:
        _store_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_store_path(), "w", encoding="utf-8") as f:
            json.dump(_TRIAGE_STORE, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.debug("llm_triage save error: %s", exc)


def _load_store() -> None:
    global _TRIAGE_STORE
    p = _store_path()
    if not p.is_file():
        return
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            _TRIAGE_STORE = data[-_MAX_STORE:]
    except Exception as exc:
        logger.debug("llm_triage load error: %s", exc)


# Загружаем при импорте
_load_store()


# ─── TriageCollector — подписчик на healer.action ────────────────────────

class TriageCollector:
    """
    Аккумулирует healer.action события и при достижении лимита
    или по команде запускает LLM-триаж.

    Не блокирует основной поток — все действия fire-and-forget.
    """

    def __init__(self) -> None:
        self._pending_events: List[Dict[str, Any]] = []
        self._flush_lock = Lock()
        self._max_before_flush = int(os.getenv("LLM_TRIAGE_AUTOFLUSH_COUNT", "3"))

    async def __call__(self, payload: Dict[str, Any]) -> None:
        if not _ENABLED:
            return
        if _should_skip_triage_event(payload):
            return
        with self._flush_lock:
            self._pending_events.append(dict(payload))
            count = len(self._pending_events)
        if count >= self._max_before_flush and self._max_before_flush > 0:
            # Авто-триаж при N срабатываниях
            await self._flush()

    async def _flush(self) -> None:
        """Запустить LLM-триаж по накопленным событиям (с блокировкой)."""
        with self._flush_lock:
            events = list(self._pending_events)
            self._pending_events.clear()
        if not events:
            return
        try:
            await self._run_triage(events)
        except Exception as exc:
            logger.warning("llm_triage auto-flush error: %s", exc)

    @staticmethod
    async def _run_triage(events: List[Dict[str, Any]]) -> Optional[str]:
        """Собрать контекст, вызвать LLM, сохранить рекомендацию."""
        events = [e for e in events if not _should_skip_triage_event(e)]
        if not events:
            return None
        context = _build_triage_context(events)
        recommendation_id = _next_id()
        analysis = await _call_llm_for_triage(context, recommendation_id)
        if not analysis:
            return None
        rec = {
            "id": recommendation_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "events": events,
            "analysis": analysis.get("analysis", ""),
            "steps": analysis.get("steps", []),
            "priority": analysis.get("priority", "medium"),
            "status": "pending",
            "applied_at": None,
        }
        with _LOCK:
            _TRIAGE_STORE.append(rec)
            if len(_TRIAGE_STORE) > _MAX_STORE:
                _TRIAGE_STORE[:] = _TRIAGE_STORE[-_MAX_STORE:]
            _save_store()
        logger.info("llm_triage: stored recommendation id=%s priority=%s", recommendation_id, rec["priority"])
        return recommendation_id

    def pending_count(self) -> int:
        with self._flush_lock:
            return len(self._pending_events)

    def clear_pending(self) -> int:
        with self._flush_lock:
            n = len(self._pending_events)
            self._pending_events.clear()
        return n


# ─── TriageCollector instance ────────────────────────────────────────────

_collector = TriageCollector()
_INSTALLED = False


def install_triage() -> None:
    """Идемпотентная регистрация подписчика."""
    global _INSTALLED
    if _INSTALLED:
        return
    bus.subscribe_async("healer.action", _collector)
    _INSTALLED = True
    logger.info("llm_triage: installed TriageCollector (autoflush=%d)", _collector._max_before_flush)


def get_collector() -> TriageCollector:
    return _collector


# ─── Построение контекста для LLM ────────────────────────────────────────

def _build_triage_context(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Собрать: события + последние N событий шины + ключевые MONITOR-счётчики."""
    ctx: Dict[str, Any] = {
        "trigger_events": events,
    }

    # Последние события шины
    try:
        recent = bus.history(n=20)
        ctx["recent_bus_events"] = [
            {
                "event_type": e.event_type,
                "ts": str(e.data.get("ts", ""))[:19],
                "healer": e.data.get("healer"),
                "action": e.data.get("action"),
                "reason": (e.data.get("reason") or "")[:200],
            }
            for e in recent
            if e.event_type in ("healer.action", "module.failed", "module.executed")
            and not (
                e.event_type == "healer.action"
                and isinstance(e.data, dict)
                and _should_skip_triage_event(e.data)
            )
        ]
    except Exception:
        ctx["recent_bus_events"] = []

    # MONITOR counters
    try:
        from core.monitoring import MONITOR
        mon = MONITOR.snapshot()
        counters = mon.get("counters", {})
        if isinstance(counters, dict):
            ctx["monitor_counters"] = {
                k: counters[k]
                for k in (
                    "module_exec_ok_total", "module_exec_fail_total",
                    "openrouter_completion_ok_total", "openrouter_completion_fail_total",
                    "input_messages_total", "openrouter_cost_credits_nanos_total",
                )
                if k in counters
            }
    except Exception:
        ctx["monitor_counters"] = {}

    # OBS p95
    try:
        from core.observability import OBS
        lats = getattr(OBS, "latencies_ms", {})
        if isinstance(lats, dict):
            ctx["latency_p95"] = {
                k: v.get("p95") if isinstance(v, dict) else None
                for k, v in lats.items()
            }
    except Exception:
        ctx["latency_p95"] = {}

    return ctx


# ─── LLM-вызов ───────────────────────────────────────────────────────────


def _should_skip_triage_event(payload: Dict[str, Any]) -> bool:
    """Не триажить самореференцию MCE (иначе петля healer → triage → LLM)."""
    if not isinstance(payload, dict):
        return False
    if str(payload.get("healer") or "") != "MetaCognitiveEngine":
        return False
    action = str(payload.get("action") or "")
    if action in {
        "tighten_healer_thresholds",
        "drift_detected",
        "suggest_faster_model",
    }:
        return True
    return False


def _parse_triage_json(content: str) -> Dict[str, Any]:
    """Устойчивый разбор JSON от triage-модели."""
    s = (content or "").strip()
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    for candidate in (s,):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i, j = candidate.find("{"), candidate.rfind("}")
        if i >= 0 and j > i:
            chunk = candidate[i : j + 1]
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                chunk2 = re.sub(r",\s*}", "}", chunk)
                chunk2 = re.sub(r",\s*]", "]", chunk2)
                try:
                    obj = json.loads(chunk2)
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
    analysis_m = re.search(
        r'"analysis"\s*:\s*"((?:[^"\\]|\\.)*)"',
        content or "",
        re.DOTALL,
    )
    priority_m = re.search(r'"priority"\s*:\s*"(\w+)"', content or "", re.IGNORECASE)
    if analysis_m:
        analysis = analysis_m.group(1).replace('\\"', '"').replace("\\n", "\n")
        steps = re.findall(r'"((?:[^"\\]|\\.)*)"', content[analysis_m.end() : analysis_m.end() + 800])
        steps_clean = [s for s in steps if len(s) > 8 and not s.startswith("analysis")][:3]
        return {
            "analysis": analysis,
            "priority": (priority_m.group(1).lower() if priority_m else "medium"),
            "steps": steps_clean or ["Проверить логи healers и /admin_bug_heal_list"],
        }
    return {}


def _next_id() -> str:
    import secrets
    return secrets.token_hex(4)


def _system_prompt() -> str:
    return (
        "Ты ассистент по диагностике и лечению бота gemma_bot. "
        "Тебе передан JSON с событиями healers и состоянием системы.\n\n"
        "Задачи:\n"
        "1. Проанализируй что произошло (на русском, 2-3 предложения).\n"
        "2. Определи приоритет: critical / high / medium / low.\n"
        "3. Предложи 1-3 конкретных шага (steps). Каждый шаг — это:\n"
        "   - /admin_* команда (например /admin_plugin_disable module_name)\n"
        "   - .env переменная (например HEALER_MODULE_MAX_FAILURES=5)\n"
        "   - `restart container` если нужен рестарт\n\n"
        "Формат ответа — строгий JSON:\n"
        '{"analysis": "текст", "priority": "high", "steps": ["шаг 1", "шаг 2"]}\n\n'
        "Без markdown, без пояснений, ТОЛЬКО JSON."
    )


async def _call_llm_for_triage(
    context: Dict[str, Any],
    recommendation_id: str,
) -> Optional[Dict[str, Any]]:
    """Вызвать LLM с контекстом, распарсить JSON-ответ.

    Использует прямой вызов OpenRouter (минуя call_brain), так как
    call_brain проходит через полный конвейер агента (профили, инструменты,
    персону) и возвращает естественно-языковой ответ, из которого
    невозможно надёжно извлечь JSON-рекомендацию.
    """
    try:
        import json as _json

        raw = _json.dumps(context, ensure_ascii=False, default=str)
        max_chars = int(os.getenv("LLM_TRIAGE_MAX_CONTEXT_CHARS", "8000"))
        if len(raw) > max_chars:
            raw = raw[:max_chars] + "…(truncated)"

        user_prompt = (
            f"События healers и состояние системы (rekomendation_id={recommendation_id}):\n\n{raw}"
        )

        from core.openrouter_provider import get_openrouter_provider
        _triage_llm = get_openrouter_provider()
        model = os.getenv("LLM_TRIAGE_MODEL", "openai/gpt-4.1-nano")
        timeout_sec = float(os.getenv("LLM_TRIAGE_TIMEOUT_SEC", "30"))

        import asyncio
        result = await asyncio.wait_for(
            _triage_llm.generate(
                prompt=user_prompt,
                model=model,
                system_prompt=_system_prompt(),
                max_tokens=int(os.getenv("LLM_TRIAGE_MAX_TOKENS", "512")),
                temperature=0.1,
            ),
            timeout=timeout_sec,
        )

        if result.get("error"):
            logger.warning("llm_triage: LLM error: %s", result["error"])
            return None

        content = str(result.get("content") or "").strip()
        if not content:
            logger.warning("llm_triage: empty content from LLM")
            return None

        parsed = _parse_triage_json(content)
        if not parsed:
            raise ValueError("could not parse triage JSON")
        parsed.setdefault("analysis", "")
        parsed.setdefault("priority", "medium")
        parsed.setdefault("steps", [])
        return parsed
    except asyncio.TimeoutError:
        logger.warning("llm_triage: LLM timeout (%.0fs)", timeout_sec)
        return None
    except Exception as exc:
        logger.warning("llm_triage: LLM call failed: %s", exc)
        return None


# ─── Публичное API ──────────────────────────────────────────────────────

async def run_triage_now_async() -> Optional[str]:
    """Запустить триаж по накопленным событиям (async-версия)."""
    with _collector._flush_lock:
        events = list(_collector._pending_events)
        _collector._pending_events.clear()
    if not events:
        return None
    return await TriageCollector._run_triage(events)


def run_triage_now() -> Optional[str]:
    """Запустить триаж по накопленным событиям (синхронная обёртка)."""
    import asyncio
    return asyncio.run(run_triage_now_async())


def list_recommendations(
    status: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Список рекомендаций, опционально фильтр по статусу."""
    with _LOCK:
        pool = list(reversed(_TRIAGE_STORE))
    if status:
        pool = [r for r in pool if r.get("status") == status]
    return pool[:limit]


def get_recommendation(rec_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        for r in _TRIAGE_STORE:
            if r.get("id") == rec_id:
                return dict(r)
    return None


def apply_recommendation(rec_id: str) -> bool:
    """Отметить рекомендацию как применённую."""
    with _LOCK:
        for r in _TRIAGE_STORE:
            if r.get("id") == rec_id and r.get("status") == "pending":
                r["status"] = "applied"
                r["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _save_store()
                return True
    return False


def dismiss_recommendation(rec_id: str) -> bool:
    """Отклонить рекомендацию."""
    with _LOCK:
        for r in _TRIAGE_STORE:
            if r.get("id") == rec_id and r.get("status") == "pending":
                r["status"] = "dismissed"
                _save_store()
                return True
    return False


def snapshot() -> Dict[str, Any]:
    return {
        "enabled": _ENABLED,
        "installed": _INSTALLED,
        "pending_events": _collector.pending_count(),
        "recommendations_total": len(_TRIAGE_STORE),
        "autoflush_count": _collector._max_before_flush,
        "recommendations": [
            {
                "id": r.get("id"),
                "ts": r.get("ts"),
                "priority": r.get("priority"),
                "status": r.get("status"),
                "analysis": (r.get("analysis") or "")[:100],
            }
            for r in reversed(_TRIAGE_STORE[-10:])
        ],
    }
