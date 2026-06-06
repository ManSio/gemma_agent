"""
Эмбеддинги для RAG через OpenRouter (тот же ключ, что и для чата).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional

import aiohttp

logger = logging.getLogger(__name__)

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"

_embedding_session: Optional[aiohttp.ClientSession] = None
_embedding_session_lock = asyncio.Lock()


async def _get_embedding_session() -> aiohttp.ClientSession:
    global _embedding_session
    if _embedding_session is None or _embedding_session.closed:
        async with _embedding_session_lock:
            if _embedding_session is None or _embedding_session.closed:
                to_total = max(30.0, min(float(os.getenv("OPENROUTER_HTTP_TIMEOUT", "120")), 600.0))
                _embedding_session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=to_total),
                )
    return _embedding_session


async def close_embedding_session() -> None:
    global _embedding_session
    if _embedding_session is not None and not _embedding_session.closed:
        await _embedding_session.close()


def embedding_model() -> str:
    return (os.getenv("QDRANT_EMBEDDING_MODEL") or "openai/text-embedding-3-small").strip()


async def embed_texts(texts: List[str], *, api_key: Optional[str] = None) -> Optional[List[List[float]]]:
    """Асинхронно получить векторы; при ошибке — None."""
    key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        logger.warning("embed_texts: OPENROUTER_API_KEY не задан")
        return None
    clean = [t if isinstance(t, str) else str(t) for t in texts if (t if isinstance(t, str) else str(t)).strip()]
    if not clean:
        return []
    model = embedding_model()
    try:
        session = await _get_embedding_session()
        async with session.post(
            OPENROUTER_EMBEDDINGS_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "input": clean},
        ) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:400]
                logger.warning("OpenRouter embeddings HTTP %s: %s", resp.status, body)
                return None
            data = await resp.json()
        rows = data.get("data") or []
        if len(rows) != len(clean):
            logger.warning("embeddings: ожидали %s векторов, пришло %s", len(clean), len(rows))
        out: List[List[float]] = []
        for item in sorted(rows, key=lambda x: int(x.get("index", 0))):
            emb = item.get("embedding")
            if isinstance(emb, list):
                out.append([float(x) for x in emb])
        return out if len(out) == len(clean) else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("OpenRouter embeddings request failed: %s", e)
        return None


def embed_texts_sync(texts: List[str], *, api_key: Optional[str] = None) -> Optional[List[List[float]]]:
    """Синхронная обёртка для обратной совместимости."""
    return asyncio.run(embed_texts(texts, api_key=api_key))
