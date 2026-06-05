"""Public build: spatial design disabled."""
from __future__ import annotations
from typing import Any, Optional


def wants_spatial_design_intent(
    _text: str,
    *,
    file_context: Optional[dict] = None,
    persisted: Optional[dict] = None,
) -> bool:
    return False
