"""Ссылка на оркестратор для HTTP API (избегаем циклических импортов)."""
from __future__ import annotations

from typing import Any, Optional

_orchestrator: Optional[Any] = None


def set_orchestrator(orchestrator: Any) -> None:
    global _orchestrator
    _orchestrator = orchestrator


def get_orchestrator() -> Optional[Any]:
    return _orchestrator
