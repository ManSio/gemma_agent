from __future__ import annotations

from typing import Any


def redact_public_path(value: Any) -> str:
    """
    Скрывает чувствительные абсолютные пути в пользовательских/операторских ответах.
    """
    s = str(value if value is not None else "")
    s = s.replace("/opt/gemma_agent/", "./")
    s = s.replace("/opt/gemma_agent", ".")
    return s

