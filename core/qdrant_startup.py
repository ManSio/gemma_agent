"""Проверка Qdrant при старте процесса (коллекции и доступность API)."""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

from core.env_flags import env_truthy

logger = logging.getLogger(__name__)

_DEFAULT_COLLECTIONS: Tuple[str, ...] = (
    "gemma_classifier_cache",
    "gemma_lessons_cache",
)


def qdrant_startup_strict_enabled() -> bool:
    """True — падать при недоступном Qdrant; False — только warning (runtime LRU fallback)."""
    return env_truthy("QDRANT_STARTUP_STRICT", default=True)


def require_qdrant_env() -> Tuple[str, str]:
    """Вернуть (url, api_key) или ValueError если переменные не заданы."""
    qdrant_url = (os.getenv("QDRANT_URL") or "").strip()
    qdrant_key = (os.getenv("QDRANT_API_KEY") or "").strip()
    if not qdrant_url:
        raise ValueError("QDRANT_URL is required — set in .env or environment")
    if not qdrant_key:
        raise ValueError("QDRANT_API_KEY is required — set in .env or environment")
    return qdrant_url, qdrant_key


def ensure_qdrant_collections(
    qdrant_url: str,
    qdrant_key: str,
    collection_names: List[str] | None = None,
) -> None:
    """Проверить API Qdrant и создать отсутствующие коллекции."""
    from core.qdrant_http import Distance, QdrantHTTP, VectorParams

    names = list(collection_names or _DEFAULT_COLLECTIONS)
    qdrant = QdrantHTTP(url=qdrant_url, api_key=qdrant_key)
    result = qdrant.get_collections()
    existing = {c.name for c in result.collections}
    for name in names:
        if name in existing:
            continue
        qdrant.create_collection(
            name,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )
        logger.info("[guardian] created missing Qdrant collection: %s", name)


def ensure_qdrant_at_startup() -> None:
    """Обязательные env + ping Qdrant; при QDRANT_STARTUP_STRICT=true — fail-fast."""
    qdrant_url, qdrant_key = require_qdrant_env()
    try:
        ensure_qdrant_collections(qdrant_url, qdrant_key)
    except Exception as e:
        if qdrant_startup_strict_enabled():
            raise RuntimeError(f"Qdrant startup check failed: {e}") from e
        logger.warning("[guardian] Qdrant check skipped (QDRANT_STARTUP_STRICT=false): %s", e)
