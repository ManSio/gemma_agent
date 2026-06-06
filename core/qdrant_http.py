"""
Лёгкая HTTP-обёртка для Qdrant REST API.
Заменяет qdrant-client — только HTTP/REST, без numpy.

Модели (Distance, VectorParams, PointStruct, Filter и т.д.)
реализованы как duck-typing-совместимые классы,
чтобы код, использующий qdrant_client, мог просто заменить импорт.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ── Duck-typed модели, совместимые с qdrant_client.models ──


class Distance:
    """Замена qdrant_client.models.Distance."""
    COSINE = "Cosine"
    DOT = "Dot"
    EUCLID = "Euclid"


class VectorParams:
    """Замена qdrant_client.models.VectorParams."""

    def __init__(self, size: int, distance: str) -> None:
        self.size = size
        self.distance = distance


class PointStruct:
    """Замена qdrant_client.models.PointStruct."""

    def __init__(self, id: int, vector: List[float], payload: Optional[dict] = None) -> None:
        self.id = id
        self.vector = vector
        self.payload = payload or {}


class Filter:
    """Замена qdrant_client.models.Filter."""

    def __init__(self, must: Optional[List[Any]] = None, should: Optional[List[Any]] = None,
                 must_not: Optional[List[Any]] = None) -> None:
        self.must = must or []
        self.should = should or []
        self.must_not = must_not or []


class MatchValue:
    """Замена qdrant_client.models.MatchValue."""

    def __init__(self, value: Any) -> None:
        self.value = value


class FieldCondition:
    """Замена qdrant_client.models.FieldCondition."""

    def __init__(self, key: str, match: Optional[Any] = None, range: Optional[Any] = None) -> None:
        self.key = key
        self.match = match
        self.range = range


class FilterSelector:
    """Замена qdrant_client.models.FilterSelector."""

    def __init__(self, filter: Optional[Any] = None) -> None:
        self.filter = filter


class PointIdsList:
    """Замена qdrant_client.models.PointIdsList."""

    def __init__(self, points: List[int]) -> None:
        self.points = points


class ScoredPoint:
    """Duck-typed результат поиска, совместимый с qdrant_client.ScoredPoint."""

    def __init__(self, id: int, score: float, payload: dict, vector: Optional[List[float]] = None) -> None:
        self.id = id
        self.score = score
        self.payload = payload
        self.vector = vector


class _QdrantCollectionDescription:
    """Описание коллекции."""

    def __init__(self, name: str) -> None:
        self.name = name


class _QdrantCollectionsResult:
    """Результат get_collections()."""

    def __init__(self, names: List[str]) -> None:
        self.collections = [_QdrantCollectionDescription(n) for n in names]


def _api_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    path = path.lstrip("/")
    return f"{base}/{path}"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


class QdrantHTTP:
    """HTTP-клиент для облачного Qdrant. Не требует numpy/qdrant-client."""

    def __init__(self, url: str, api_key: Optional[str] = None) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._session = _session()

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._api_key:
            h["api-key"] = self._api_key
        return h

    def _request(self, method: str, path: str, json_body: Any = None) -> Any:
        url = _api_url(self._url, path)
        resp = self._session.request(
            method, url, headers=self._headers(), json=json_body, timeout=30
        )
        if resp.status_code >= 400:
            logger.debug("Qdrant HTTP %s %s -> %s %s", method, url, resp.status_code, resp.text[:200])
            resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    # ── Collections ──

    def get_collections(self) -> _QdrantCollectionsResult:
        """Вернуть список коллекций."""
        result = self._request("GET", "/collections")
        names = [c["name"] for c in result.get("collections", [])]
        return _QdrantCollectionsResult(names)

    def create_collection(
        self,
        collection_name: str,
        vectors_config: Any,
    ) -> bool:
        """Создать коллекцию. vectors_config должен иметь .size и .distance."""
        size = vectors_config.size
        distance = vectors_config.distance
        body: Dict[str, Any] = {
            "vectors": {
                "size": size,
                "distance": distance,
            }
        }
        try:
            self._request("PUT", f"/collections/{collection_name}", body)
            return True
        except Exception as e:
            logger.warning("Qdrant create_collection failed: %s", e)
            return False

    # ── Points ──

    def upsert(
        self,
        collection_name: str,
        points: List[Any],
    ) -> bool:
        """Upsert точки. points — список объектов с .id, .vector, .payload."""
        pts = []
        for p in points:
            pt: Dict[str, Any] = {
                "id": p.id,
                "vector": p.vector,
            }
            if hasattr(p, "payload") and p.payload:
                pt["payload"] = p.payload
            pts.append(pt)
        body = {"points": pts}
        try:
            self._request("PUT", f"/collections/{collection_name}/points", body)
            return True
        except Exception as e:
            logger.warning("Qdrant upsert failed: %s", e)
            return False

    def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 10,
        query_filter: Optional[Any] = None,
        score_threshold: Optional[float] = None,
        with_vector: bool = False,
    ) -> List[ScoredPoint]:
        """Поиск ближайших векторов.

        query_filter — опциональный Filter-подобный объект.
        with_vector — если True, возвращает векторы в результате.
        """
        body: Dict[str, Any] = {
            "vector": query_vector,
            "limit": limit,
        }
        if score_threshold is not None:
            body["score_threshold"] = score_threshold
        if query_filter is not None:
            body["filter"] = _filter_to_dict(query_filter)
        if with_vector:
            body["with_vector"] = True
        try:
            result = self._request("POST", f"/collections/{collection_name}/points/search", body)
            hits = []
            for h in result:
                hits.append(
                    ScoredPoint(
                        id=h.get("id", 0),
                        score=h.get("score", 0.0),
                        payload=h.get("payload") or {},
                        vector=h.get("vector") if with_vector else None,
                    )
                )
            return hits
        except Exception as e:
            logger.debug("Qdrant search error: %s", e)
            return []

    def delete(
        self,
        collection_name: str,
        points_selector: Any,
    ) -> bool:
        """Удаление точек. points_selector — FilterSelector или PointIdsList."""
        body = _selector_to_dict(points_selector)
        try:
            self._request("POST", f"/collections/{collection_name}/points/delete", body)
            return True
        except Exception as e:
            logger.debug("Qdrant delete error: %s", e)
            return False

    def scroll(
        self,
        collection_name: str,
        limit: int = 100,
        offset: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Прочитать точки через scroll API. Возвращает {"points": [...], "next_page_offset": ...}."""
        body: Dict[str, Any] = {"limit": limit}
        if offset is not None:
            body["offset"] = offset
        try:
            result = self._request("POST", f"/collections/{collection_name}/points/scroll", body)
            return result
        except Exception as e:
            logger.debug("Qdrant scroll error: %s", e)
            return {"points": []}


# ── Алиас для drop-in замены QdrantClient → QdrantHTTP ──
QdrantClient = QdrantHTTP
models = type("models", (), {
    "Distance": Distance,
    "VectorParams": VectorParams,
    "PointStruct": PointStruct,
    "Filter": Filter,
    "MatchValue": MatchValue,
    "FieldCondition": FieldCondition,
    "FilterSelector": FilterSelector,
    "PointIdsList": PointIdsList,
})()


# ── Утилиты конвертации моделей → dict для REST API ──


def _filter_to_dict(f: Any) -> Dict[str, Any]:
    """Конвертировать Filter в dict для REST API."""
    result: Dict[str, Any] = {}
    for field in ("must", "should", "must_not"):
        conds = getattr(f, field, None)
        if conds:
            result[field] = [_condition_to_dict(c) for c in conds]
    return result


def _condition_to_dict(c: Any) -> Dict[str, Any]:
    """Конвертировать FieldCondition/MatchValue в dict."""
    key = c.key if hasattr(c, "key") else ""
    match_val = None
    if hasattr(c, "match"):
        m = c.match
        if hasattr(m, "value"):
            match_val = m.value
        elif hasattr(m, "text"):
            match_val = m.text
    if match_val is not None:
        return {
            "key": key,
            "match": {"value": match_val if not isinstance(match_val, str) else match_val},
        }
    if hasattr(c, "range"):
        r = c.range
        rng: Dict[str, Any] = {}
        for k in ("gte", "gt", "lte", "lt"):
            v = getattr(r, k, None)
            if v is not None:
                rng[k] = v
        if rng:
            return {"key": key, "range": rng}
    return {"key": key, "match": {"value": str(c)}}


def _selector_to_dict(s: Any) -> Dict[str, Any]:
    """Конвертировать points_selector в dict для REST API."""
    # PointIdsList
    if hasattr(s, "points"):
        return {"points": list(s.points)}
    # FilterSelector
    if hasattr(s, "filter"):
        return {"filter": _filter_to_dict(s.filter)}
    return {}
