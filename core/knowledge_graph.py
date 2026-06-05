"""
Knowledge Graph — превращает плоские user_facts в граф сущностей + отношений.

Когда Qdrant включён:
  - Сущности хранятся как векторизованные точки в коллекции 'gemma_knowledge_graph'
  - Связи между сущностями — в том же наборе точек с полем relation_type
  - Поиск: семантический по эмбеддингам через Qdrant

Без Qdrant:
  - Плоский JSONL-файл data/runtime/knowledge_graph.jsonl
  - Поиск: полнотекстовый (грубый, но рабочий)

Все инструменты read/write через TOOL_CALL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_KG_LOCK = threading.Lock()

# ── Конфигурация ──

KG_COLLECTION = "gemma_knowledge_graph"
KG_VECTOR_SIZE = 768  # стандартный размер эмбеддинга

# Лимиты для flat-режима
_FLAT_MAX_ENTRIES = 2000
_FLAT_SEARCH_CHARS = 8000


def _qdrant_url() -> str:
    return (os.getenv("QDRANT_URL") or "").strip()


def _flat_path() -> str:
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "knowledge_graph.jsonl")


def _kg_enabled() -> bool:
    return bool((os.getenv("KNOWLEDGE_GRAPH_ENABLED") or "true").strip().lower() in {"1", "true", "yes", "on"})


def _use_qdrant() -> bool:
    return bool(_qdrant_url())


# ── Сущность ──


def _entity_id(name: str, entity_type: str) -> int:
    """Стабильный int-идентификатор для Qdrant (64‑бит, unsigned)."""
    raw = f"{entity_type}::{name.strip()}".lower()
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)


def _serialize(entity_type: str, name: str,
               properties: Optional[Dict[str, Any]] = None,
               relations: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "entity_type": entity_type,
        "name": name,
        "properties": properties or {},
        "relations": relations or [],
        "ts": time.time(),
    }


def _text_for_embedding(e: Dict[str, Any]) -> str:
    parts = [f"{e['entity_type']}: {e['name']}"]
    for k, v in (e.get("properties") or {}).items():
        if isinstance(v, str):
            parts.append(f"{k}: {v}")
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k}: {', '.join(str(x)[:60] for x in v[:5])}")
    for r in (e.get("relations") or []):
        rel = r.get("relation", "related_to")
        target = r.get("target_name", "")
        target_type = r.get("target_type", "")
        parts.append(f"-> {rel} -> ({target_type}) {target}")
    return ". ".join(parts)


# ── Qdrant-режим ──


def _qdrant_collection_exists(client) -> bool:
    try:
        names = {c.name for c in client.get_collections().collections}
        return KG_COLLECTION in names
    except Exception:
        return False


def _qdrant_ensure_collection(client) -> bool:
    try:
        if _qdrant_collection_exists(client):
            return True
        from core.qdrant_http import Distance, VectorParams
        client.create_collection(
            collection_name=KG_COLLECTION,
            vectors_config=VectorParams(size=KG_VECTOR_SIZE, distance=Distance.COSINE),
        )
        return True
    except Exception as e:
        logger.warning("[kg] qdrant ensure collection: %s", e)
        return False


def _qdrant_save(client, entity_id: int, embedding: List[float], payload: Dict[str, Any]) -> bool:
    try:
        from core.qdrant_http import models
        client.upsert_points(
            collection_name=KG_COLLECTION,
            points=[models.PointStruct(id=entity_id, vector=embedding, payload=payload)],
        )
        return True
    except Exception as e:
        logger.warning("[kg] qdrant save: %s", e)
        return False


def _qdrant_delete(client, entity_id: int) -> bool:
    try:
        client.delete_points(collection_name=KG_COLLECTION, points=[entity_id])
        return True
    except Exception as e:
        logger.warning("[kg] qdrant delete: %s", e)
        return False


def _qdrant_search(client, embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
    try:
        results = client.search_points(
            collection_name=KG_COLLECTION,
            vector=embedding,
            limit=limit,
            with_payload=True,
        )
        out: List[Dict[str, Any]] = []
        for r in results:
            payload = getattr(r, "payload", None) or {}
            score = getattr(r, "score", 0.0)
            out.append({
                "entity_type": payload.get("entity_type", ""),
                "name": payload.get("name", ""),
                "properties": dict(payload.get("properties", {})),
                "relations": list(payload.get("relations", [])),
                "score": round(score, 4),
            })
        return out
    except Exception as e:
        logger.warning("[kg] qdrant search: %s", e)
        return []


# ── Flat-режим (без Qdrant) ──


def _flat_load_all() -> Dict[str, Dict[str, Any]]:
    path = _flat_path()
    if not os.path.isfile(path):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("eid"):
                    out[rec["eid"]] = rec
    except OSError:
        pass
    return out


def _flat_save_all(entries: Dict[str, Dict[str, Any]]) -> None:
    path = _flat_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # trim if over limit
    if len(entries) > _FLAT_MAX_ENTRIES:
        sorted_items = sorted(entries.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
        entries = dict(sorted_items[:_FLAT_MAX_ENTRIES])
    try:
        with _KG_LOCK:
            with open(path, "w", encoding="utf-8") as f:
                for eid, rec in entries.items():
                    f.write(json.dumps({"eid": eid, **rec}, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("[kg] flat save: %s", e)


def _flat_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    entries = _flat_load_all()
    query_low = query.lower()
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for eid, rec in entries.items():
        text = _text_for_embedding(rec).lower()
        score = 0.0
        if query_low in text:
            score = len(query_low) / max(len(text), 1)
            # boost if name matches exactly
            if query_low == rec.get("name", "").lower():
                score += 0.5
            # boost if entity_type matches
            if query_low == rec.get("entity_type", "").lower():
                score += 0.3
        if score > 0:
            scored.append((score, rec))
    scored.sort(key=lambda x: -x[0])
    return [{k: v for k, v in rec.items() if k != "eid"} for _, rec in scored[:limit]]


# ── Unified interface ──


def _get_client():
    if not _use_qdrant():
        return None
    try:
        from core.qdrant_http import QdrantHTTP
        return QdrantHTTP(url=_qdrant_url(), api_key=os.getenv("QDRANT_API_KEY") or None)
    except Exception:
        return None


async def _save_entity(
    entity_type: str,
    name: str,
    properties: Optional[Dict[str, Any]] = None,
    relations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    eid = _entity_id(name, entity_type)
    entity = _serialize(entity_type, name, properties, relations)

    # Always save to flat (mirror for reliability)
    entries = _flat_load_all()
    entries[eid] = entity
    _flat_save_all(entries)
    mode = "flat"

    if _use_qdrant():
        client = _get_client()
        if client and _qdrant_ensure_collection(client):
            try:
                text = _text_for_embedding(entity)
                from core.rag_embeddings import embed_texts
                emb_list = await embed_texts([text])
                if emb_list and emb_list[0]:
                    payload = {**entity, "eid": eid}
                    _qdrant_save(client, eid, emb_list[0], payload)
                    mode = "qdrant"
            except Exception as e:
                logger.warning("[kg] qdrant save failed, keeping flat mirror: %s", e)

    return {"ok": True, "eid": eid, "entity_type": entity_type, "name": name, "mode": mode}


async def _search_entities(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    if _use_qdrant():
        client = _get_client()
        if client and _qdrant_collection_exists(client):
            try:
                from core.rag_embeddings import embed_texts
                emb_list = await embed_texts([query])
                if emb_list and emb_list[0]:
                    results = _qdrant_search(client, emb_list[0], limit=limit)
                    if results:
                        return results
            except Exception as e:
                logger.debug('%s optional failed: %s', 'knowledge_graph', e, exc_info=True)
    return _flat_search(query, limit=limit)


# ── TOOL_CALL-инструменты ──


class KnowledgeGraphModule:
    """Инструменты для работы с графом знаний (entity_save / entity_search / entity_delete / entity_relate)."""

    BRAIN_LITE_INCLUDE = True

    async def entity_save(
        self,
        entity_type: str,
        name: str,
        properties: str = "",
    ) -> Dict[str, Any]:
        """
        Сохранить сущность в граф знаний.
        entity_type: category (person, place, event, thing, concept, book, fact)
        name: уникальное имя сущности
        properties: JSON-строка с дополнительными полями (опционально)
        """
        entity_type = (entity_type or "").strip().lower()
        name = (name or "").strip()
        if not entity_type or len(entity_type) > 40:
            return {"ok": False, "error": "entity_type required, max 40 chars"}
        if not name:
            return {"ok": False, "error": "name required"}

        props: Dict[str, Any] = {}
        if properties and isinstance(properties, str):
            try:
                parsed = json.loads(properties)
                if isinstance(parsed, dict):
                    props = parsed
            except json.JSONDecodeError:
                props = {"_raw": properties[:500]}

        return await _save_entity(entity_type, name, props)

    async def entity_relate(
        self,
        name: str,
        target_name: str,
        relation: str = "related_to",
        target_type: str = "",
    ) -> Dict[str, Any]:
        """
        Создать связь между двумя сущностями.
        name: имя исходной сущности
        target_name: имя целевой сущности
        relation: тип связи (lives_in, works_at, knows, owns, part_of, located_in, created_by)
        target_type: тип целевой сущности (если нужно для поиска)
        """
        name = (name or "").strip()
        target_name = (target_name or "").strip()
        relation = (relation or "related_to").strip().lower()
        if not name or not target_name:
            return {"ok": False, "error": "name and target_name required"}

        # Загружаем существующую запись и добавляем связь
        entries = _flat_load_all()
        source_entry = None
        source_eid = None
        source_type = "unknown"
        for eid, rec in entries.items():
            if rec.get("name", "").lower() == name.lower():
                source_entry = rec
                source_eid = eid
                source_type = rec.get("entity_type", "unknown")
                break

        rels = list(source_entry.get("relations", [])) if source_entry else []
        rels.append({
            "relation": relation,
            "target_name": target_name,
            "target_type": (target_type or "").strip()[:40],
        })

        # _save_entity сохранит и в flat, и в Qdrant
        return await _save_entity(
            source_type,
            name,
            source_entry.get("properties") if source_entry else {},
            rels,
        )

    async def entity_search(
        self,
        query: str,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """
        Поиск сущностей в графе знаний по тексту.
        query: текст для поиска
        limit: макс. результатов (1-20)
        """
        query = (query or "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        limit = max(1, min(limit, 20))
        try:
            results = await _search_entities(query, limit=limit)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        return {
            "ok": True,
            "query": query,
            "count": len(results),
            "results": results,
        }

    async def entity_delete(
        self,
        name: str,
        entity_type: str = "",
    ) -> Dict[str, Any]:
        """
        Удалить сущность из графа знаний.
        name: имя сущности
        entity_type: тип (для точной идентификации)
        """
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}

        deleted = False
        if _use_qdrant():
            client = _get_client()
            if client and _qdrant_collection_exists(client):
                eid = _entity_id(name, entity_type or "unknown")
                _qdrant_delete(client, eid)
                deleted = True

        # Flat: удаляем все записи с таким name
        entries = _flat_load_all()
        to_del = []
        for eid, rec in entries.items():
            if rec.get("name", "").lower() == name.lower():
                if not entity_type or rec.get("entity_type", "").lower() == entity_type.lower():
                    to_del.append(eid)
        for eid in to_del:
            del entries[eid]
            deleted = True
        if to_del:
            _flat_save_all(entries)

        if not deleted:
            return {"ok": False, "error": f"entity '{name}' not found", "mode": "flat"}

        return {"ok": True, "name": name, "deleted_count": len(to_del)}
