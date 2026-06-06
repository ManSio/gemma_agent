"""
Опциональный векторный индекс книг в Qdrant (при QDRANT_URL).
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

from core.rag_embeddings import embed_texts

logger = logging.getLogger(__name__)


def qdrant_configured() -> bool:
    return bool((os.getenv("QDRANT_URL") or "").strip())


def collection_name() -> str:
    return (os.getenv("QDRANT_COLLECTION") or "gemma_books_rag").strip() or "gemma_books_rag"


def chunk_book_text(text: str, *, max_chars: int = 520, overlap: int = 80) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_chars:
        return [t]
    chunks: List[str] = []
    start = 0
    while start < len(t):
        end = min(start + max_chars, len(t))
        piece = t[start:end]
        if end < len(t):
            cut = max(piece.rfind("\n"), piece.rfind(". "), piece.rfind(" "))
            if cut > max_chars // 3:
                piece = piece[: cut + 1].strip()
                end = start + len(piece)
        if piece:
            chunks.append(piece)
        if end >= len(t):
            break
        start = max(end - overlap, start + 1)
    return chunks[:200]


class QdrantBooksIndex:
    """Индексация и поиск чанков книг в Qdrant."""

    def __init__(self) -> None:
        self._url = (os.getenv("QDRANT_URL") or "").strip().rstrip("/")
        self._api_key = (os.getenv("QDRANT_API_KEY") or "").strip() or None
        self._collection = collection_name()
        self._client = None
        self._vector_size: Optional[int] = None

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def _get_client(self):
        if self._client is None:
            from core.qdrant_http import QdrantHTTP

            kwargs: Dict[str, Any] = {"url": self._url}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = QdrantHTTP(**kwargs)
        return self._client

    def _ensure_collection(self, vector_size: int) -> bool:
        try:
            from core.qdrant_http import Distance, VectorParams

            client = self._get_client()
            if client is None:
                return False
            names = {c.name for c in client.get_collections().collections}
            if self._collection not in names:
                client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
            self._vector_size = vector_size
            return True
        except Exception as e:
            logger.warning("Qdrant ensure_collection failed: %s", e)
            return False

    def delete_book_vectors(self, book_id: str) -> None:
        if not self.enabled:
            return
        try:
            from core.qdrant_http import models

            client = self._get_client()
            if client is None:
                return
            client.delete(
                collection_name=self._collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="book_id",
                                match=models.MatchValue(value=str(book_id)),
                            ),
                        ],
                    ),
                ),
            )
        except Exception as e:
            logger.debug("Qdrant delete_book_vectors: %s", e)

    async def upsert_book(self, book_id: str, title: str, content: str) -> bool:
        if not self.enabled:
            return False
        chunks = chunk_book_text(content)
        if not chunks:
            return True
        vectors = await embed_texts(chunks)
        if not vectors:
            return False
        dim = len(vectors[0])
        if not self._ensure_collection(dim):
            return False
        try:
            from core.qdrant_http import PointStruct

            self.delete_book_vectors(book_id)
            points: List[PointStruct] = []
            for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
                if len(vec) != dim:
                    continue
                pid = _stable_point_id(book_id, i)
                points.append(
                    PointStruct(
                        id=pid,
                        vector=vec,
                        payload={
                            "book_id": str(book_id),
                            "title": title,
                            "chunk_index": i,
                            "text": chunk[:8000],
                        },
                    )
                )
            if not points:
                return False
            client = self._get_client()
            client.upsert(collection_name=self._collection, points=points)
            return True
        except Exception as e:
            logger.warning("Qdrant upsert_book failed: %s", e)
            return False

    async def search(self, book_id: str, query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        q = (query or "").strip()
        if not q:
            return []
        vecs = await embed_texts([q])
        if not vecs:
            return []
        query_vector = vecs[0]
        try:
            from core.qdrant_http import models

            client = self._get_client()
            hits = client.search(
                collection_name=self._collection,
                query_vector=query_vector,
                limit=limit,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="book_id",
                            match=models.MatchValue(value=str(book_id)),
                        ),
                    ],
                ),
            )
            out: List[Dict[str, Any]] = []
            for h in hits:
                pl = h.payload or {}
                txt = pl.get("text") or ""
                if not txt:
                    continue
                out.append(
                    {
                        "chunk_id": pl.get("chunk_index"),
                        "content": txt,
                        "match_type": "vector",
                        "score": float(h.score) if h.score is not None else None,
                    }
                )
            return out
        except Exception as e:
            logger.warning("Qdrant search failed: %s", e)
            return []


def _stable_point_id(book_id: str, chunk_index: int) -> int:
    """Детерминированный int id для Qdrant (не зависит от PYTHONHASHSEED)."""
    h = hashlib.sha256(f"{book_id}:{chunk_index}".encode()).digest()
    return int.from_bytes(h[:8], "big", signed=False) % (2**63 - 1)
