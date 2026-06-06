"""
«Быстрый мозг» — классификатор запросов через Qdrant (task_profiles).

Архитектура:
1. Qdrant-коллекция task_profiles с эталонами успешных запросов.
2. classify_query(user_text) — поиск ближайшего эталона (score >= 0.85).
3. save_successful_query(...) — сохранение/обновление эталонов.
4. dedup_and_cleanup() — обслуживание коллекции (раз в сутки).

Когда Qdrant не настроен — используется in-memory LRU-кэш (текстовый хэш).
save_successful_query при отсутствии Qdrant сохраняет в LRU, чтобы следующие
похожие запросы попадали в кэш.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.rag_embeddings import embed_texts

logger = logging.getLogger(__name__)

COLLECTION = "task_profiles"
VECTOR_SIZE = 1536
DISTANCE = "Cosine"
SIMILARITY_THRESHOLD = 0.85
DEDUP_THRESHOLD = 0.95
INITIAL_SUCCESS_COUNT = 5
MIN_SUCCESS_COUNT = 3

ClassifierResult = Dict[str, str]  # {"profile": ..., "need_memory": ..., "need_verify": ...}

# ── In-memory fallback LRU (используется, когда Qdrant не настроен) ──
_LRU_CACHE: OrderedDict[str, ClassifierResult] = OrderedDict()
_LRU_MAX_SIZE = 512

def _lru_key(text: str) -> str:
    return hashlib.sha256(text.lower().encode()).hexdigest()

def _lru_get(text: str) -> Optional[ClassifierResult]:
    key = _lru_key(text)
    if key in _LRU_CACHE:
        _LRU_CACHE.move_to_end(key)
        return _LRU_CACHE[key]
    return None

def _lru_put(text: str, result: ClassifierResult) -> None:
    key = _lru_key(text)
    _LRU_CACHE[key] = result
    _LRU_CACHE.move_to_end(key)
    while len(_LRU_CACHE) > _LRU_MAX_SIZE:
        _LRU_CACHE.popitem(last=False)


def _qdrant_configured() -> bool:
    return bool((os.getenv("QDRANT_URL") or "").strip())


def _new_point_id(text: str) -> int:
    return abs(hash(text)) % (10 ** 12)


# ── Утилиты для работы с Qdrant ──


def _get_client():
    from core.qdrant_http import QdrantHTTP
    url = (os.getenv("QDRANT_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("QDRANT_API_KEY") or "").strip() or None
    return QdrantHTTP(url=url, api_key=api_key)


def _collection_exists(client) -> bool:
    names = {c.name for c in client.get_collections().collections}
    return COLLECTION in names


# ── 1. Создание коллекции ──


async def ensure_collection() -> bool:
    """Создать коллекцию task_profiles если её нет. Безопасно — не пересоздаёт."""
    if not _qdrant_configured():
        return False
    try:
        client = _get_client()
        if _collection_exists(client):
            return True
        from core.qdrant_http import Distance, VectorParams
        ok = client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        if ok:
            logger.info("[classifier] collection '%s' created", COLLECTION)
        return ok
    except Exception as e:
        logger.debug("[classifier] ensure_collection error: %s", e)
        return False


# ── 2. Холодный старт ──


async def cold_start() -> int:
    """Заполнить коллекцию эталонами из начального набора.

    Использует хардкодные эталоны для cold start (логи парсить ненадёжно).
    В процессе работы эталоны обновляются через save_successful_query.
    Возвращает число добавленных эталонов.
    """
    if not _qdrant_configured():
        return 0
    await ensure_collection()
    client = _get_client()
    if not _collection_exists(client):
        return 0

    samples = _cold_start_samples()
    if not samples:
        logger.info("[classifier] cold_start: no samples found")
        return 0

    # Проверяем, есть ли уже данные в коллекции
    existing = client.scroll(collection_name=COLLECTION, limit=1)
    if existing.get("points"):
        logger.info("[classifier] cold_start: collection already has data, skipping")
        return 0

    added = 0
    for idx, (text, profile, tools_count, need_memory, need_verify) in enumerate(samples):
        if not text or len(text.strip()) < 5:
            continue
        embedding = await embed_texts([text.strip()])
        if not embedding:
            continue
        vector = embedding[0] if isinstance(embedding, list) else embedding
        point_id = idx + 1  # простые последовательные ID
        client.upsert(
            collection_name=COLLECTION,
            points=[
                type("PointStruct", (), {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "user_text": text.strip()[:500],
                        "profile": profile,
                        "tools_count": tools_count,
                        "need_memory": str(need_memory).lower(),
                        "need_verify": str(need_verify).lower(),
                        "success_count": INITIAL_SUCCESS_COUNT,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                })()
            ],
        )
        added += 1

    logger.info("[classifier] cold_start: %d samples added", added)
    return added


def _cold_start_samples() -> List[tuple]:
    """Начальные эталоны для холодного старта."""
    return [
        ("Привет, как дела?", "short", 0, False, False),
        ("Расскажи про погоду", "standard", 1, False, False),
        ("Что такое квантовая физика?", "standard", 0, False, False),
        ("Напиши программу на Python", "deep", 2, True, True),
        ("Сравни два подхода к разработке", "deep", 0, True, True),
        ("Какая сегодня дата?", "short", 0, False, False),
        ("Помнишь, что мы обсуждали вчера?", "standard", 1, True, False),
        ("Рассчитай стоимость проекта", "deep", 1, True, True),
        ("Спланируй мою неделю", "deep_analysis", 2, True, True),
        ("Что нового?", "short", 0, False, False),
        ("Объясни теорию относительности", "quick_explain", 0, False, False),
        ("Как работает нейронная сеть?", "quick_explain", 0, False, True),
        ("Открой ссылку", "task_executor", 1, False, False),
        ("Переведи текст на английский", "translation", 0, False, False),
        ("Проверь код на ошибки", "code_review", 0, False, True),
        ("Что говорит закон о шуме в ночное время", "legal", 2, False, True),
        ("Реши уравнение 2x+5=15", "math_solve", 1, False, False),
        ("Сделай краткое резюме текста", "summarization", 0, False, False),
        ("Какие команды есть у бота", "command_help", 0, False, False),
        ("Напомни мне купить молоко", "task_executor", 1, True, False),
        ("Сравни цены на авиабилеты", "deep_analysis", 2, False, True),
        ("Расскажи анекдот", "short", 0, False, False),
        ("Помоги с домашним заданием", "deep_analysis", 1, True, True),
        ("Что ты умеешь?", "standard", 0, False, False),
        ("Как настроить Docker?", "deep_analysis", 1, True, True),
        ("Напиши письмо", "creative", 1, False, False),
        ("Сделай анализ рынка", "deep_analysis", 2, True, True),
        ("Привет", "short", 0, False, False),
        ("Пока", "short", 0, False, False),
        ("Спасибо", "short", 0, False, False),
        ("Что пишут в новостях?", "news_brief", 2, False, False),
        ("Последние новости Беларуси", "news_brief", 2, False, False),
        ("Напиши стихотворение про осень", "creative", 1, False, False),
        ("Придумай сценарий для видео", "creative", 2, False, False),
        ("Выполни команду /time", "task_executor", 0, False, False),
        ("Поставь напоминание на завтра", "task_executor", 1, True, False),
        ("Создай задачу в списке дел", "task_executor", 1, True, False),
        # ── Полный спектр профилей profile_registry ──
        ("Напиши функцию на Python", "code_generation", 0, False, False),
        ("Не компилируется код, ошибка: undefined reference", "code_debug", 0, False, True),
        ("Найди в документе что сказано про налоги", "document_qa", 1, False, False),
        ("Составь план поездки в Минск на 3 дня", "planning", 1, False, True),
        ("Разбери тему квантовых вычислений", "research", 2, True, True),
        ("Помоги с проблемой: бот не отвечает после обновления", "troubleshooting", 1, False, False),
        ("Расскажи как настроить Docker Compose", "tutorial", 0, False, False),
        ("Ты пират, отвечай как пират", "roleplay", 0, False, False),
        ("Аргументируй за и против использования ИИ в образовании", "debate", 2, True, True),
        ("Проанализируй данные продаж за квартал", "data_analysis", 1, True, True),
        ("Посоветуй ноутбук для программиста до 1500$", "recommendation", 2, True, False),
        ("Предложи идеи для стартапа в EdTech", "brainstorm", 0, False, False),
        ("Объясни тему теоремы Пифагора для 7 класса", "education", 0, False, False),
        ("Сделай краткое резюме статьи про изменения в налоговом кодексе", "summarization", 0, False, False),
        ("Переведи на белорусский: Добры дзень, як справы?", "translation", 0, False, False),
        ("Проверь код на уязвимости SQL-инъекции", "code_review", 0, False, True),
        ("Посчитай 15% от 84000 рублей", "math_solve", 1, False, False),
        ("Какие команды есть для работы с плагинами?", "command_help", 0, False, False),
        ("Что говорит статья 12 КоАП РБ о нарушении тишины", "legal", 2, True, True),
    ]


def _sanitize_classifier_result(result: ClassifierResult) -> Optional[ClassifierResult]:
    from core.brain.profile_registry import normalize_profile, is_valid_profile

    if not result:
        return None
    p = normalize_profile(str(result.get("profile") or ""))
    if not is_valid_profile(p):
        return None
    out = dict(result)
    out["profile"] = p
    return out


# ── 3. classify_query ──


async def classify_query(user_text: str) -> Optional[ClassifierResult]:
    """Поиск ближайшего эталона в Qdrant или in-memory LRU.

    Возвращает payload эталона {'profile', 'need_memory', 'need_verify', 'tools_count'}
    если score >= 0.85 (Qdrant) или найден точный текстовый хеш (LRU).
    Иначе None (fallback на эвристики).
    При недоступности Qdrant — поиск в LRU.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    # In-memory LRU fallback (используется, когда Qdrant не настроен)
    cached = _lru_get(text)
    if cached is not None:
        sanitized = _sanitize_classifier_result(cached)
        if sanitized:
            logger.debug("[classifier] LRU hit profile=%s", sanitized.get("profile"))
            return sanitized
        return None

    if not _qdrant_configured():
        return None
    client = _get_client()
    if not _collection_exists(client):
        return None

    embedding = await embed_texts([text])
    if not embedding:
        return None
    vector = embedding[0] if isinstance(embedding, list) else embedding

    try:
        hits = client.search(
            collection_name=COLLECTION,
            query_vector=vector,
            limit=1,
            score_threshold=SIMILARITY_THRESHOLD,
            with_vector=True,
        )
    except Exception as e:
        logger.debug("[classifier] Qdrant search error: %s", e)
        return None

    if not hits or hits[0].score < SIMILARITY_THRESHOLD:
        return None

    payload = hits[0].payload or {}
    if not all(k in payload for k in ("profile", "need_memory", "need_verify")):
        return None

    # Увеличить success_count
    _increment_success_count(client, hits[0].id, hits[0].vector, payload)

    result: ClassifierResult = {
        "profile": str(payload["profile"]),
        "need_memory": str(payload["need_memory"]),
        "need_verify": str(payload["need_verify"]),
        "tools_count": str(payload.get("tools_count", "0")),
    }
    result = _sanitize_classifier_result(result)
    if not result:
        return None
    logger.debug("[classifier] Qdrant hit score=%.3f profile=%s", hits[0].score, result["profile"])
    _lru_put(text, result)
    return result


def _increment_success_count(client, point_id: int, vector: List[float], payload: dict) -> None:
    """Увеличить счётчик успешных срабатываний эталона."""
    from core.qdrant_http import PointStruct
    current = int(payload.get("success_count", 0))
    payload["success_count"] = current + 1
    try:
        client.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'classifier', e, exc_info=True)
# ── 4. save_successful_query ──


async def save_successful_query(
    user_text: str,
    profile: str,
    tools_count: int = 0,
    need_memory: bool = False,
    need_verify: bool = False,
) -> bool:
    """Сохранить успешный запрос как эталон.

    - Если запрос короче 20 символов — не сохранять (приветствия).
    - Если Qdrant не настроен — сохранить в in-memory LRU.
    - Если похожий эталон уже есть (score > 0.95) — увеличить success_count.
    - Иначе создать новый эталон с success_count=1.
    """
    text = (user_text or "").strip()
    if len(text) < 20:
        return False

    from core.brain.profile_registry import normalize_profile

    profile = normalize_profile(profile)

    result: ClassifierResult = {
        "profile": profile,
        "need_memory": str(need_memory).lower(),
        "need_verify": str(need_verify).lower(),
        "tools_count": str(tools_count),
    }

    # Fallback: in-memory LRU при отсутствии Qdrant
    if not _qdrant_configured():
        _lru_put(text, result)
        logger.debug("[classifier] LRU saved profile=%s len=%d", profile, len(text))
        return True

    client = _get_client()
    if not _collection_exists(client):
        await ensure_collection()

    embedding = await embed_texts([text])
    if not embedding:
        return False
    vector = embedding[0] if isinstance(embedding, list) else embedding

    # Проверить, есть ли уже похожий эталон
    try:
        hits = client.search(
            collection_name=COLLECTION,
            query_vector=vector,
            limit=1,
            score_threshold=DEDUP_THRESHOLD,
        )
    except Exception:
        hits = []

    if hits and hits[0].score >= DEDUP_THRESHOLD:
        # Обновить существующий
        payload = dict(hits[0].payload or {})
        current_count = int(payload.get("success_count", 0))
        payload["success_count"] = current_count + 1
        payload["profile"] = profile
        payload["tools_count"] = tools_count
        payload["need_memory"] = str(need_memory).lower()
        payload["need_verify"] = str(need_verify).lower()
        payload["ts"] = datetime.now(timezone.utc).isoformat()
        try:
            from core.qdrant_http import PointStruct
            client.upsert(
                collection_name=COLLECTION,
                points=[PointStruct(id=hits[0].id, vector=vector, payload=payload)],
            )
            logger.debug("[classifier] updated existing etalon (id=%s, score=%.3f, count=%d)",
                         hits[0].id, hits[0].score, current_count + 1)
            _lru_put(text, result)
            return True
        except Exception as e:
            logger.debug("[classifier] update etalon error: %s", e)
            return False

    # Создать новый эталон
    point_id = _new_point_id(text)
    try:
        from core.qdrant_http import PointStruct
        client.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "user_text": text[:500],
                        "profile": profile,
                        "tools_count": tools_count,
                        "need_memory": str(need_memory).lower(),
                        "need_verify": str(need_verify).lower(),
                        "success_count": 1,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                )
            ],
        )
        logger.debug("[classifier] saved new etalon id=%s profile=%s", point_id, profile)
        _lru_put(text, result)
        return True
    except Exception as e:
        logger.debug("[classifier] save etalon error: %s", e)
        return False


# ── 5. dedup_and_cleanup ──


def dedup_and_cleanup() -> int:
    """Обслуживание коллекции task_profiles.

    - Удалить эталоны с success_count < 3 (мусор).
    - Объединить эталоны с score > 0.95 и одинаковым payload (дубликаты).
    Вызывать раз в сутки. Возвращает число удалённых/объединённых точек.
    """
    if not _qdrant_configured():
        return 0

    client = _get_client()
    if not _collection_exists(client):
        return 0

    removed = 0

    # 1. Собрать все точки
    all_points = _scroll_all_points(client)
    if not all_points:
        return 0

    # 2. Удалить мусор (success_count < 3)
    to_delete = []
    for p in all_points:
        sc = int((p.get("payload") or {}).get("success_count", 0))
        if sc < MIN_SUCCESS_COUNT:
            to_delete.append(p.get("id"))
    if to_delete:
        from core.qdrant_http import PointIdsList
        client.delete(collection_name=COLLECTION, points_selector=PointIdsList(points=to_delete))
        removed += len(to_delete)
        logger.info("[classifier] dedup: removed %d low-quality etalons (success_count < %d)",
                    len(to_delete), MIN_SUCCESS_COUNT)
        # Refresh list
        all_points = [p for p in _scroll_all_points(client) if p.get("id") not in to_delete]

    # 3. Объединить дубликаты (похожие векторы)
    if len(all_points) < 2:
        return removed

    merged_ids = set()
    for i in range(len(all_points)):
        if all_points[i].get("id") in merged_ids:
            continue
        vec_i = all_points[i].get("vector") or []
        if not vec_i:
            continue
        for j in range(i + 1, len(all_points)):
            if all_points[j].get("id") in merged_ids:
                continue
            vec_j = all_points[j].get("vector") or []
            if not vec_j:
                continue
            sim = _cosine_similarity(vec_i, vec_j)
            if sim >= DEDUP_THRESHOLD:
                payload_i = all_points[i].get("payload") or {}
                payload_j = all_points[j].get("payload") or {}
                # Объединяем success_count
                total_count = (int(payload_i.get("success_count", 0))
                               + int(payload_j.get("success_count", 0)))
                payload_i["success_count"] = total_count
                payload_i["ts"] = datetime.now(timezone.utc).isoformat()
                # Обновляем первую точку с суммой
                from core.qdrant_http import PointStruct
                client.upsert(
                    collection_name=COLLECTION,
                    points=[
                        PointStruct(
                            id=all_points[i].get("id"),
                            vector=vec_i,
                            payload=payload_i,
                        )
                    ],
                )
                # Помечаем вторую на удаление
                merged_ids.add(all_points[j].get("id"))

    if merged_ids:
        from core.qdrant_http import PointIdsList
        client.delete(
            collection_name=COLLECTION,
            points_selector=PointIdsList(points=list(merged_ids)),
        )
        removed += len(merged_ids)
        logger.info("[classifier] dedup: merged %d duplicate etalons", len(merged_ids))

    return removed


def _scroll_all_points(client, limit: int = 100) -> List[dict]:
    """Прочитать все точки из коллекции через scroll с пагинацией."""
    all_points = []
    offset = None
    while True:
        result = client.scroll(collection_name=COLLECTION, limit=limit, offset=offset)
        points = result.get("points") or []
        if not points:
            break
        for p in points:
            if isinstance(p, dict):
                all_points.append(p)
        next_offset = result.get("next_page_offset")
        if next_offset is None:
            break
        offset = next_offset
    return all_points


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity между двумя векторами."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
