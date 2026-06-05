"""Public build: law search disabled."""
from __future__ import annotations
from typing import Any, Optional

async def prefetch_law_for_brain(*_a: Any, **_k: Any) -> Optional[str]:
    return None

def wants_belarus_decree_prefetch(_text: str) -> bool:
    return False
