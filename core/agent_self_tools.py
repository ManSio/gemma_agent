"""
Self‑management tools — агент может запрашивать свою конфигурацию,
метрики и статус. Аналог spoon‑bot self_config / self_metrics / self_status.

Все инструменты работают в read‑only, только на запрос LLM.
Данные компактные, влезают в контекст.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_TOOL_JSON_CHARS = 40_000


class AgentSelfToolsModule:
    """Инструменты самодиагностики и самоуправления агента."""

    BRAIN_LITE_INCLUDE = True

    def _get_orchestrator(self) -> Optional[Any]:
        try:
            from core.runtime_diagnostic_module import _orchestrator as orch
            return orch
        except Exception:
            return None

    # ── self_config_get ──

    async def self_config_get(
        self,
        key: str = "",
    ) -> Dict[str, Any]:
        """
        Прочитать конфигурацию агента. Без key — все публичные настройки.
        key — путь через точку: 'brain.collapse_enabled', 'token_efficiency.budget.hard_limit_tokens'.
        Возвращает Dict с найденным значением или все настройки компактно.
        """
        try:
            from core.config_manager import get_config, AppConfig
            cfg = get_config()
            if isinstance(cfg, AppConfig):
                raw = _appconfig_to_dict(cfg)
            else:
                raw = _object_to_dict(cfg)
        except Exception as e:
            return {"error": f"config unavailable: {e}"}

        result = {"ok": True, "config_version": os.getenv("CONFIG_VERSION", "unknown")}

        if key:
            if key == "config_version":
                result["value"] = result["config_version"]
            else:
                value = _deep_get(raw, key)
                if value is None:
                    return {"ok": False, "error": f"key '{key}' not found"}
                result["key"] = key
                result["value"] = _compact_value(value)
        else:
            result["config"] = _compact_config(raw)

        return _trim(result)

    # ── self_metrics ──

    async def self_metrics(
        self,
        hours: int = 1,
    ) -> Dict[str, Any]:
        """
        Метрики агента: счётчики вызовов, ошибок, инструментов и т.д.
        hours — сколько часов истории (по умолч. 1, макс 168).
        """
        try:
            from core.monitoring import MONITOR
            snap = MONITOR.snapshot()
            history = MONITOR.get_history(hours=min(hours, 168))
        except Exception as e:
            return {"error": f"monitor unavailable: {e}"}

        out: Dict[str, Any] = {
            "ok": True,
            "snapshot": {
                "counters": dict(snap.get("counters", {})),
                "uptime_hint_sec": snap.get("uptime_hint_sec", 0),
            },
            "history_snapshots": len(history),
        }
        return _trim(out)

    # ── self_status ──

    async def self_status(self) -> Dict[str, Any]:
        """
        Общий статус агента: аптайм, версия, инструменты, нагрузка.
        Компактная сводка для быстрой проверки.
        """
        from core.brain.constants import BRAIN_CORE_VERSION

        orchestrator = self._get_orchestrator()
        uptime_sec = 0.0
        boot_ts = ""
        memory_rss = 0
        tools_loaded = 0

        try:
            from core.boot_timeline import process_uptime_seconds

            uptime_sec = process_uptime_seconds()
        except Exception as e:
            logger.debug('%s optional failed: %s', 'agent_self_tools', e, exc_info=True)
        if orchestrator:
            try:
                if hasattr(orchestrator, "_boot_start_time"):
                    boot_ts = str(getattr(orchestrator, "_boot_start_time", ""))
                    bt = getattr(orchestrator, "_boot_start_time", None)
                    if isinstance(bt, (int, float)):
                        uptime_sec = max(uptime_sec, time.time() - float(bt))
                tools_loaded = len(getattr(orchestrator, "tools_info", {}) or {})
            except Exception as e:
                logger.debug('%s optional failed: %s', 'agent_self_tools', e, exc_info=True)
        if tools_loaded <= 0:
            try:
                from core.tools import list_tools

                tools_loaded = len(list_tools())
            except Exception as e:
                logger.debug('%s optional failed: %s', 'agent_self_tools', e, exc_info=True)
        try:
            import psutil
            proc = psutil.Process()
            memory_rss = proc.memory_info().rss
        except Exception as e:
            logger.debug('%s optional failed: %s', 'agent_self_tools', e, exc_info=True)
        try:
            from core.monitoring import MONITOR
            counters = dict(MONITOR.snapshot().get("counters", {}))
            top_counters = dict(sorted(counters.items(), key=lambda x: -abs(x[1]))[:20])
        except Exception:
            top_counters = {}

        return _trim({
            "ok": True,
            "version": {
                "brain": str(BRAIN_CORE_VERSION),
                "compactor": "1.0.0",
                "tools_loaded": tools_loaded,
            },
            "uptime_sec": int(uptime_sec),
            "boot_timestamp": boot_ts,
            "memory_rss_bytes": memory_rss,
            "top_counters": top_counters,
        })


# ── helpers ──


def _appconfig_to_dict(cfg: Any) -> Dict[str, Any]:
    """Convert AppConfig to plain dict for JSON."""
    d: Dict[str, Any] = {}
    for attr in dir(cfg):
        if attr.startswith("_"):
            continue
        try:
            v = getattr(cfg, attr)
            if callable(v):
                continue
            d[attr] = _compact_value(v)
        except Exception:
            continue
    return d


def _object_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return {k: _compact_value(v) for k, v in obj.items()}
    d: Dict[str, Any] = {}
    for attr in dir(obj):
        if attr.startswith("_"):
            continue
        try:
            v = getattr(obj, attr)
            if callable(v):
                continue
            d[attr] = _compact_value(v)
        except Exception:
            continue
    return d


def _deep_get(d: Dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    current: Any = d
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
        elif hasattr(current, p):
            current = getattr(current, p)
        else:
            return None
        if current is None:
            return None
    return current


def _compact_value(v: Any) -> Any:
    if isinstance(v, (int, float, bool, type(None))):
        return v
    if isinstance(v, str):
        if len(v) > 200:
            return v[:200] + "..."
        return v
    if isinstance(v, dict):
        return _compact_config(v)
    if isinstance(v, (list, tuple)):
        if len(v) > 20:
            return {"_count": len(v), "_first": [str(x)[:80] for x in v[:3]], "_hint": "truncated"}
        return [str(x)[:80] for x in v]
    s = str(v)
    if len(s) > 200:
        return s[:200] + "..."
    return s


def _compact_config(d: Dict[str, Any], max_items: int = 40) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if len(out) >= max_items:
            out["_omitted"] = f"показано {max_items} из {len(d)}"
            break
        out[k] = _compact_value(v)
    return out


def _trim(d: Dict[str, Any]) -> Dict[str, Any]:
    """Урезать JSON до лимита."""
    raw = json.dumps(d, ensure_ascii=False, default=str)
    if len(raw) <= _MAX_TOOL_JSON_CHARS:
        return d
    d["_truncated"] = True
    d["_approx_chars"] = len(raw)
    return d
