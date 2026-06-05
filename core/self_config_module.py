"""
Self-config module для brain (auto-register via core.tools).

Позволяет LLM отвечать на вопросы о себе:
- SelfConfig.status — сводка: профиль, модель, аптайм
- SelfConfig.config_get — чтение ключа конфигурации
- SelfConfig.metrics — текущие метрики (мониторинг)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


class SelfConfigModule:
    """Инструменты само-конфигурации агента (авто-регистрация в core.tools)."""

    def __init__(self) -> None:
        self._start_ts = time.time()

    def status(self) -> Dict[str, Any]:
        """
        Базовый статус агента: профиль, модель, аптайм.
        """
        return {
            "ok": True,
            "uptime_sec": int(time.time() - self._start_ts),
            "model": os.getenv("PRIMARY_MODEL", "deepseek/deepseek-v4-flash"),
            "config_path": ".env",
        }

    def config_get(self, key: str = "") -> Dict[str, Any]:
        """
        Прочитать значение ключа конфигурации (env или .env).
        Возвращает значение или ошибку если ключ не найден.

        args:
          key — имя переменной окружения
        """
        k = (key or "").strip().upper()
        if not k:
            return {"ok": False, "error": "key required"}
        val = os.getenv(k)
        if val is None:
            return {"ok": False, "error": f"key '{k}' not found"}
        return {"ok": True, "key": k, "value": val}

    def metrics(self) -> Dict[str, Any]:
        """
        Текущие метрики из monitoring.MONITOR.
        Возвращает счётчики и их значения.
        """
        try:
            from core.monitoring import MONITOR

            snapshot = MONITOR.snapshot()
            return {"ok": True, "counters": snapshot.get("counters", {})}
        except Exception as e:
            return {"ok": False, "error": str(e)}
