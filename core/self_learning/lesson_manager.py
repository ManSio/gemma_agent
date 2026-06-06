"""Lesson Manager — persistence, retrieval, deduplication, and forgetting for SelfLearningEngine.

Backends:
- Primary: JSONL file (data/runtime/self_learning_lessons.jsonl) — source of truth.
- Secondary: Qdrant (gemma_lessons_cache collection) — semantic search.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.self_learning.models import Lesson

logger = logging.getLogger(__name__)

_LESSONS_LOCK = threading.Lock()

_DEFAULT_PATH: Optional[str] = None
_QDRANT_COLLECTION = "gemma_lessons_cache"
_INSTANCE: Optional["LessonManager"] = None


def _lessons_path() -> str:
    global _DEFAULT_PATH
    if _DEFAULT_PATH:
        return _DEFAULT_PATH
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    p = os.path.join(base, "self_learning_lessons.jsonl")
    _DEFAULT_PATH = p
    return p


def _self_learning_enabled() -> bool:
    raw = (os.getenv("SELF_LEARNING_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _retention_half_life() -> float:
    try:
        return max(1.0, float(os.getenv("SELF_LEARNING_RETENTION_HALF_LIFE_SEC", "604800").strip()))
    except (ValueError, TypeError):
        return 604800.0


def _forget_threshold() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("SELF_LEARNING_FORGET_THRESHOLD", "0.1").strip())))
    except (ValueError, TypeError):
        return 0.1


def _retire_score() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("SELF_LEARNING_RETIRE_SCORE", "0.2").strip())))
    except (ValueError, TypeError):
        return 0.2


def _consolidate_score() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("SELF_LEARNING_CON_SOLIDATE_SCORE", "0.9").strip())))
    except (ValueError, TypeError):
        return 0.9


def _stable_point_id(lesson_id: str) -> int:
    h = hash(lesson_id)
    return h % (2**63 - 1)


class LessonManager:
    """Manages the lifecycle of Lesson objects with JSONL + Qdrant backends."""

    @staticmethod
    def get_instance() -> LessonManager:
        global _INSTANCE
        if _INSTANCE is None:
            _INSTANCE = LessonManager()
        return _INSTANCE

    def __init__(self) -> None:
        self._qdrant_client: Any = None
        self._qdrant_url: str = ""
        self._qdrant_api_key: Optional[str] = None
        self._init_qdrant()

    def _init_qdrant(self) -> None:
        url = (os.getenv("QDRANT_URL") or "").strip().rstrip("/")
        if not url:
            return
        self._qdrant_url = url
        self._qdrant_api_key = (os.getenv("QDRANT_API_KEY") or "").strip() or None

    def _get_qdrant_client(self) -> Any:
        if not self._qdrant_url:
            return None
        if self._qdrant_client is None:
            from core.qdrant_http import QdrantHTTP

            kwargs: Dict[str, Any] = {"url": self._qdrant_url}
            if self._qdrant_api_key:
                kwargs["api_key"] = self._qdrant_api_key
            self._qdrant_client = QdrantHTTP(**kwargs)
        return self._qdrant_client

    def _ensure_collection(self, vector_size: int) -> bool:
        if not self._qdrant_url:
            return False
        client = self._get_qdrant_client()
        if client is None:
            return False
        try:
            from core.qdrant_http import Distance, VectorParams

            names = {c.name for c in client.get_collections().collections}
            if _QDRANT_COLLECTION not in names:
                client.create_collection(
                    collection_name=_QDRANT_COLLECTION,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
            return True
        except Exception as e:
            logger.warning("[self_learning] ensure_collection failed: %s", e)
            return False

    # ── CRUD ──

    async def store_lesson(self, lesson: Lesson) -> None:
        """Persist a lesson to JSONL and Qdrant."""
        if not _self_learning_enabled():
            logger.info("[self_learning] store_lesson skipped: SELF_LEARNING_ENABLED is false")
            return
        logger.info("[self_learning] store_lesson id=%s content=%.100s", lesson.id, lesson.content)
        self._append_jsonl(lesson)
        await self._upsert_qdrant(lesson)
        logger.info("[self_learning] store_lesson done id=%s", lesson.id)

    def _append_jsonl(self, lesson: Lesson) -> None:
        path = _lessons_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            row = lesson.to_dict()
            line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
            with _LESSONS_LOCK:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
            logger.info("[self_learning] appended lesson id=%s path=%s bytes=%d", lesson.id, path, len(line.encode("utf-8")))
        except OSError as e:
            logger.warning("[self_learning] append lesson error: %s", e)

    async def _upsert_qdrant(self, lesson: Lesson) -> None:
        if not self._qdrant_url:
            return
        client = self._get_qdrant_client()
        if client is None:
            return
        try:
            from core.rag_embeddings import embed_texts

            vecs = await embed_texts([lesson.content])
            if not vecs:
                return
            vector = vecs[0]
            if not self._ensure_collection(len(vector)):
                return

            from core.qdrant_http import PointStruct

            pid = _stable_point_id(lesson.id)
            client.upsert(
                collection_name=_QDRANT_COLLECTION,
                points=[
                    PointStruct(
                        id=pid,
                        vector=vector,
                        payload={
                            "lesson_id": lesson.id,
                            "content": lesson.content[:8000],
                            "category": lesson.category,
                            "status": lesson.status,
                        },
                    )
                ],
            )
        except Exception as e:
            logger.debug("[self_learning] qdrant upsert error: %s", e)

    def get_lesson_by_id(self, lesson_id: str) -> Optional[Lesson]:
        """Fetch a single lesson by ID from the JSONL file."""
        path = _lessons_path()
        if not os.path.isfile(path):
            return None
        try:
            with _LESSONS_LOCK:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("id") == lesson_id:
                            return Lesson.from_dict(row)
        except OSError:
            pass
        return None

    def update_lesson(self, lesson: Lesson) -> None:
        """Update a lesson in the JSONL (rewrite entire file)."""
        path = _lessons_path()
        if not os.path.isfile(path):
            return
        all_lessons = self._load_all_raw()
        updated = False
        rows: List[Dict[str, Any]] = []
        for row in all_lessons:
            if row.get("id") == lesson.id:
                rows.append(lesson.to_dict())
                updated = True
            else:
                rows.append(row)
        if updated:
            self._write_all_raw(rows)

    def _load_all_raw(self) -> List[Dict[str, Any]]:
        path = _lessons_path()
        if not os.path.isfile(path):
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with _LESSONS_LOCK:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return rows

    def _write_all_raw(self, rows: List[Dict[str, Any]]) -> None:
        path = _lessons_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with _LESSONS_LOCK:
                with open(path, "w", encoding="utf-8") as f:
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("[self_learning] write_all error: %s", e)

    def load_active_lessons(self) -> List[Lesson]:
        """Load all lessons with status='active'."""
        rows = self._load_all_raw()
        lessons: List[Lesson] = []
        for row in rows:
            lesson = Lesson.from_dict(row)
            if lesson.status == "active":
                lessons.append(lesson)
        return lessons

    def load_all_lessons(self) -> List[Lesson]:
        """Load all lessons regardless of status."""
        rows = self._load_all_raw()
        return [Lesson.from_dict(row) for row in rows]

    def load_lessons(self) -> List[Lesson]:
        """Alias for load_all_lessons — called at startup to warm disk cache."""
        return self.load_all_lessons()

    # ── Semantic Search ──

    async def find_relevant_lessons(self, query: str, top_k: int = 3) -> List[Lesson]:
        """Find the most relevant active lessons for a query using Qdrant vector search.

        Falls back to keyword search if Qdrant is unavailable.
        Returns up to top_k lessons sorted by strength * effectiveness_score descending.
        """
        if not _self_learning_enabled():
            return []
        q = (query or "").strip()
        if not q:
            return []

        # Try vector search in Qdrant first
        qdrant_hits = await self._search_qdrant(q, top_k * 2)
        if not qdrant_hits:
            return self._keyword_search(q, top_k)

        # Fetch full Lesson objects by id
        lessons: List[Lesson] = []
        seen: set = set()
        for hit in qdrant_hits:
            lid = hit.get("lesson_id", "")
            if not lid or lid in seen:
                continue
            seen.add(lid)
            lesson = self.get_lesson_by_id(lid)
            if lesson and lesson.status == "active":
                lessons.append(lesson)

        lessons.sort(key=lambda x: x.strength * x.effectiveness_score, reverse=True)
        return lessons[:top_k]

    async def _search_qdrant(self, query: str, limit: int) -> List[Dict[str, Any]]:
        if not self._qdrant_url:
            return []
        client = self._get_qdrant_client()
        if client is None:
            return []
        try:
            from core.rag_embeddings import embed_texts

            vecs = await embed_texts([query])
            if not vecs:
                return []
            query_vector = vecs[0]
            hits = client.search(
                collection_name=_QDRANT_COLLECTION,
                query_vector=query_vector,
                limit=min(limit, 20),
            )
            out: List[Dict[str, Any]] = []
            for h in hits:
                pl = h.payload or {}
                lesson_id = pl.get("lesson_id", "")
                if not lesson_id:
                    continue
                out.append({
                    "lesson_id": lesson_id,
                    "score": float(h.score) if h.score is not None else 0.0,
                })
            return out
        except Exception as e:
            logger.debug("[self_learning] qdrant search error: %s", e)
            return []

    def _keyword_search(self, query: str, top_k: int) -> List[Lesson]:
        """Simple keyword overlap fallback when Qdrant is unavailable."""
        active = self.load_active_lessons()
        if not active:
            return []
        words = set((query or "").lower().split())
        scored: List[tuple] = []
        for lesson in active:
            lesson_words = set(lesson.content.lower().split())
            overlap = len(words & lesson_words)
            if overlap > 0:
                scored.append((overlap * lesson.strength * lesson.effectiveness_score, lesson))
        scored.sort(key=lambda x: -x[0])
        return [s[1] for s in scored[:top_k]]

    # ── Forgetting Curve ──

    def apply_forgetting_curve(self) -> int:
        """Apply the Ebbinghaus forgetting curve to all active lessons.

        Returns the number of lessons retired.
        """
        if not _self_learning_enabled():
            return 0
        half_life = _retention_half_life()
        threshold = _forget_threshold()
        now = datetime.now(timezone.utc)
        retired = 0

        rows = self._load_all_raw()
        changed = False
        new_rows: List[Dict[str, Any]] = []

        for row in rows:
            lesson = Lesson.from_dict(row)
            if lesson.status != "active":
                new_rows.append(row)
                continue
            try:
                last = datetime.fromisoformat(lesson.last_accessed_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                last = now
            elapsed = (now - last).total_seconds()
            if elapsed > 0:
                lesson.strength = lesson.strength * math.exp(-elapsed / half_life)
            if lesson.strength < threshold:
                lesson.status = "retired"
                retired += 1
                self._delete_from_qdrant(lesson.id)
                changed = True
            new_rows.append(lesson.to_dict())

        if changed:
            self._write_all_raw(new_rows)

        if retired:
            logger.info("[self_learning] forgetting curve retired %d lessons", retired)
        return retired

    def _delete_from_qdrant(self, lesson_id: str) -> None:
        if not self._qdrant_url:
            return
        client = self._get_qdrant_client()
        if client is None:
            return
        try:
            from core.qdrant_http import models

            pid = _stable_point_id(lesson_id)
            client.delete(
                collection_name=_QDRANT_COLLECTION,
                points_selector=models.PointIdsList(
                    points=[pid],
                ),
            )
        except Exception as e:
            logger.debug("[self_learning] qdrant delete error: %s", e)
