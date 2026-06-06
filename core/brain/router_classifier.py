"""Tiered classifier: bypass -> LRU -> LLM -> heuristic (online).
Пассивное обучение поверх для сбора данных.

Архитектура:

  classify(text, context)  <- онлайн
    +-- bypass: ultra-short (<15 chars, no "?") -> short (0ms)
    +-- LRU-кеш (sha256, 1024 entry, TTL 14d) -> hit -> return
    +-- LLM (liquid/lfm-2.5-1.2b-instruct:free, ~1s) -> JSON -> LRU.put
    +-- fallback: эвристика (agent_pack.determine_profile)

  passive_learn(text, heuristic_profile, llm)  <- fire-and-forget после ответа
    +-- LLM -> JSON -> raw JSONL (append, ротация 100k)
    +-- frequency analysis (из raw JSONL) -> permanent

  lru_storage:
    - LRU на диске (data/router_lru_cache.json, atomic write, TTL 14d)
    - permanent (data/router_permanent.json, разогрев LRU при старте)

Метрики:
  - router_source (bypass|lru|llm|heuristic)
  - router_latency_ms
  - router_confidence
  - passive_llm_latency_ms
  - passive_disagreements (счётчик расхождений)
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

# -- Профили --
PROFILE_SHORT = "short"
PROFILE_STANDARD = "standard"
PROFILE_DEEP = "deep"
PROFILE_QUICK_EXPLAIN = "quick_explain"
PROFILE_NEWS_BRIEF = "news_brief"
PROFILE_DEEP_ANALYSIS = "deep_analysis"
PROFILE_CREATIVE = "creative"
PROFILE_TASK_EXECUTOR = "task_executor"

def _all_profiles() -> frozenset:
    """Все профили из profile_registry.
    ВНИМАНИЕ: Не добавляй from core.brain.router_classifier import ... в profile_registry — цикл."""
    from core.brain.profile_registry import all_profile_names
    return frozenset(all_profile_names())


_ALL_PROFILES = _all_profiles()  # профили из profile_registry

# -- Маппинг русских вариантов профилей -> английские (liquid 1.2B пишет по-русски) --
_RU_TO_EN_PROFILE: Dict[str, str] = {
    "short": "short",
    "standard": "standard",
    "deep": "deep",
    "quick_explain": "quick_explain",
    "news_brief": "news_brief",
    "deep_analysis": "deep_analysis",
    "creative": "creative",
    "task_executor": "task_executor",
    # Русские варианты (что может вернуть 1.2B модель)
    "приветствие": "short",
    "привет": "short",
    "разговор": "standard",
    "вопрос": "quick_explain",
    "объяснение": "quick_explain",
    "почему": "quick_explain",
    "сложный": "deep",
    "сравнение": "deep",
    "код": "deep",
    "программирование": "deep",
    "команда": "task_executor",
    "задача": "task_executor",
    "творчество": "creative",
    "напиши": "creative",
    "новости": "news_brief",
    "просмотр": "task_executor",
    "просоводный": "standard",
    "перевод": "translation",
    "закон": "legal",
    "код": "code_generation",
    "отладка": "code_debug",
    "математика": "math_solve",
    "учёба": "education",
    "справка": "command_help",
    "документ": "document_qa",
}

# -- Batch-детектор (много вопросов/строк в одном сообщении) --
_BATCH_QUESTION_THRESHOLD = 3   # ≥3 запроса → batch

_CMD_VERBS = frozenset({
    "напиши", "расскажи", "спланируй", "объясни", "сделай",
    "переведи", "посчитай", "суммаризируй", "найди", "спроектируй",
    "приготовь", "придумай", "покажи", "создай", "напишите",
    "проверь", "протестируй", "дополни", "измени", "почини",
    "настрой", "установи", "запусти", "оптимизируй",
    "сравни", "выбери", "отсортируй", "сгруппируй",
    "подытожь", "перечисли", "опиши", "проанализируй",
    "рассчитай", "вычисли", "преобразуй",
})


def _count_requests(lines: list[str]) -> int:
    count = 0
    for line in lines:
        if "?" in line:
            count += 1
        elif line.lower().startswith(tuple(_CMD_VERBS)):
            count += 1
    return count

# -- Константы --
_SHORT_TEXT_BYPASS_LEN = 15
_LRU_MAX_SIZE = 1024
_METRICS_WINDOW = 50
_LRU_TTL_DAYS = 14
_LRU_TTL_SEC = _LRU_TTL_DAYS * 86400

# -- Пути --
_LRU_CACHE_PATH = os.getenv("ROUTER_CACHE_PATH", "data/router_lru_cache.json")
_RAW_LOG_PATH = os.getenv("ROUTER_RAW_LOG", "data/router_passive_raw.jsonl")
_PERMANENT_PATH = os.getenv("ROUTER_PERMANENT_PATH", "data/router_permanent.json")
_RAW_LOG_MAX_LINES = int(os.getenv("ROUTER_RAW_LOG_MAX_LINES", "100000"))

_LRU_SAVE_INTERVAL = 30  # сек между синхронизациями на диск
_last_lru_save: float = 0

# -- Мастер-включатель роутера --
_ROUTER_ENABLED = os.getenv("ROUTER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

# -- Модели --
_ROUTER_LLM_MODEL = os.getenv("ROUTER_LLM_MODEL", "liquid/lfm-2.5-1.2b-instruct:free")
_ROUTER_LLM_RETRY_MAX = int(os.getenv("ROUTER_LLM_RETRY_MAX", "2"))
_ROUTER_LLM_TIMEOUT_SEC = float(os.getenv("ROUTER_LLM_TIMEOUT_SEC", "8.0"))

_PASSIVE_ENABLED = os.getenv("ROUTER_PASSIVE_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
_PASSIVE_LLM_MODEL = os.getenv("ROUTER_PASSIVE_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")
_PASSIVE_TIMEOUT_SEC = float(os.getenv("ROUTER_PASSIVE_TIMEOUT_SEC", "8.0"))

# -- Для периодической частотной обертки raw JSONL --
_last_frequency_sweep: float = 0
_FREQUENCY_SWEEP_INTERVAL = 600  # сек, раз в 10 минут
_FREQUENCY_PROMOTE_THRESHOLD = 3
_FREQUENCY_PERMANENT_THRESHOLD = 10
_raw_log_rotation_counter: int = 0


def _router_system_prompt() -> str:
    from core.brain.profile_registry import router_profiles_catalog
    catalog = router_profiles_catalog()
    return f"""Classify the user request. Reply with ONLY valid JSON, no extra text.

Example:
User: why is the sky blue
{{"profile":"quick_explain","need_search":false,"need_memory":false,"reasoning_depth":"shallow","confidence":0.95}}

Profiles (pick exactly one name):
{catalog}

Rules:
- Use profile name exactly as listed (snake_case English).
- need_search=true if web facts required; need_memory if user refers to past chat.
- reasoning_depth: shallow | nested | deep
- Dialogue context is provided after User text — use it to detect conflicts
  (tone=angry/testing), memory tests (memory_ref), correction loops.
  For conflict/correction: prefer "short" or "standard" with need_memory.
  For memory_ref: prefer "standard" or "deep" with need_memory=true.
  For new_topic: classify the new topic independently of previous context."""


@dataclass
class ClassificationResult:
    profile: str
    confidence: float
    source: str        # "bypass" | "lru" | "llm" | "heuristic"
    need_search: bool = False
    need_memory: bool = False
    reasoning_depth: str = "shallow"
    latency_ms: float = 0.0


# =====================================================================
# LRU-кеш с TTL, атомарный save, загрузка permanent при старте
# =====================================================================

_LRU_CACHE: OrderedDict[str, ClassificationResult] = OrderedDict()
_PERMANENT: Dict[str, ClassificationResult] = {}  # то, что навсегда


def _ts() -> float:
    return time.time()


def _lru_serializable() -> Dict[str, Any]:
    """Сериализовать кеш для сохранения на диск. Пропускает expired."""
    _evict_expired()
    out: Dict[str, Any] = {}
    for key, val in _LRU_CACHE.items():
        entry_ts = getattr(val, "_ts", None) or _ts()
        out[key] = {
            "profile": val.profile,
            "confidence": val.confidence,
            "source": val.source,
            "need_search": val.need_search,
            "need_memory": val.need_memory,
            "reasoning_depth": val.reasoning_depth,
            "ts": entry_ts,
        }
    return out


def _evict_expired() -> int:
    """Удалить записи, которые прожили дольше TTL. Вернуть число удалённых."""
    now = _ts()
    expired_keys = []
    # Проверяем, есть ли ts в записях (могли загрузиться без ts из старого формата)
    # Для старых записей без ts считаем их свежими (только что загруженными)
    for key in list(_LRU_CACHE.keys()):
        entry = _LRU_CACHE[key]
        entry_ts = getattr(entry, "_ts", None)
        if entry_ts is not None and now - entry_ts > _LRU_TTL_SEC:
            expired_keys.append(key)
    for k in expired_keys:
        del _LRU_CACHE[k]
    if expired_keys:
        logger.debug("[router] TTL evicted %d entries", len(expired_keys))
        MONITOR.inc("router_lru_ttl_evicted", len(expired_keys))
    return len(expired_keys)


def _lru_atomic_save(path: str, data: Dict[str, Any]) -> None:
    """Атомарная запись: .tmp + os.replace."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("[router] atomic write error: %s", e)


def _lru_save() -> None:
    """Сохранить LRU-кеш на диск (атомарно, с TTL-фильтром)."""
    try:
        data = _lru_serializable()
        _lru_atomic_save(_LRU_CACHE_PATH, data)
    except Exception as e:
        logger.debug("[router] lru save error: %s", e)


def _lru_load() -> int:
    """Загрузить LRU-кеш с диска. Возвращает число записей."""
    if not os.path.isfile(_LRU_CACHE_PATH):
        return 0
    try:
        with open(_LRU_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return 0
        count = 0
        now = _ts()
        for key, val in data.items():
            if not isinstance(val, dict) or val.get("profile") not in _ALL_PROFILES:
                continue
            # Проверка TTL
            entry_ts = val.get("ts")
            if isinstance(entry_ts, (int, float)) and now - entry_ts > _LRU_TTL_SEC:
                continue
            cr = ClassificationResult(
                profile=val["profile"],
                confidence=float(val.get("confidence", 0.6)),
                source="lru",
                need_search=bool(val.get("need_search", False)),
                need_memory=bool(val.get("need_memory", False)),
                reasoning_depth=str(val.get("reasoning_depth", "shallow")),
            )
            cr._ts = entry_ts or now
            _LRU_CACHE[key] = cr
            count += 1
        while len(_LRU_CACHE) > _LRU_MAX_SIZE:
            _LRU_CACHE.popitem(last=False)
        logger.info("[router] loaded %d entries from LRU cache", count)
        return count
    except Exception as e:
        logger.debug("[router] lru load error: %s", e)
        return 0


def _lru_maybe_save() -> None:
    """Сохранить на диск если прошло _LRU_SAVE_INTERVAL."""
    global _last_lru_save
    now = time.monotonic()
    if now - _last_lru_save >= _LRU_SAVE_INTERVAL:
        _last_lru_save = now
        _lru_save()


def lru_key(text: str) -> str:
    return hashlib.sha256(text.lower().encode()).hexdigest()


def _router_lru_include_topic() -> bool:
    raw = os.getenv("ROUTER_LRU_INCLUDE_TOPIC", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _lru_context_key(text: str, context: Optional[Dict[str, Any]] = None) -> str:
    """Контекстно-зависимый LRU-ключ.

    Комбинирует текст + сигнатуру диалогового контекста (DSV).
    Один и тот же текст в спорном и обычном диалоге получает разные ключи.
    """
    base = lru_key(text)
    if not isinstance(context, dict):
        return base
    parts = [base]
    if _router_lru_include_topic():
        tt = context.get("topic_tracking")
        topic = (
            str(tt.get("current") or "").strip().lower()[:48]
            if isinstance(tt, dict)
            else ""
        )
        if topic:
            parts.append(f"topic:{topic}")
    try:
        from core.brain.dialogue_context import build_dsv

        dsv = build_dsv(context)
        sig = dsv.context_signature()
        if sig != "normal":
            parts.append(sig)
    except Exception:
        pass
    if len(parts) == 1:
        return base
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _lru_get(text: str, context: Optional[Dict[str, Any]] = None) -> Optional[ClassificationResult]:
    key = _lru_context_key(text, context)
    if key not in _LRU_CACHE:
        # Fallback: пробуем базовый ключ (обратная совместимость)
        if context is not None and key != lru_key(text):
            return _lru_get(text, context=None)
        return None
    entry = _LRU_CACHE[key]
    entry_ts = getattr(entry, "_ts", None)
    if entry_ts is not None and _ts() - entry_ts > _LRU_TTL_SEC:
        del _LRU_CACHE[key]
        MONITOR.inc("router_lru_ttl_evicted")
        return None
    _LRU_CACHE.move_to_end(key)
    return entry


def _lru_put(text: str, result: ClassificationResult, context: Optional[Dict[str, Any]] = None) -> None:
    key = _lru_context_key(text, context)
    result._ts = _ts()
    _LRU_CACHE[key] = result
    _LRU_CACHE.move_to_end(key)
    while len(_LRU_CACHE) > _LRU_MAX_SIZE:
        _LRU_CACHE.popitem(last=False)
    _lru_maybe_save()


def _lru_clear() -> int:
    n = len(_LRU_CACHE)
    _LRU_CACHE.clear()
    _lru_save()
    return n


def lru_size() -> int:
    return len(_LRU_CACHE)


# =====================================================================
# Permanent storage (то, что навсегда, загружается в LRU при старте)
# =====================================================================

def _permanent_load() -> int:
    """Загрузить permanent-записи в LRU-кеш при старте. Вернуть число."""
    if not os.path.isfile(_PERMANENT_PATH):
        return 0
    try:
        with open(_PERMANENT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return 0
        count = 0
        now = _ts()
        for key, val in data.items():
            if not isinstance(val, dict) or val.get("profile") not in _ALL_PROFILES:
                continue
            cr = ClassificationResult(
                profile=val["profile"],
                confidence=float(val.get("confidence", 0.85)),
                source="lru",
                need_search=bool(val.get("need_search", False)),
                need_memory=bool(val.get("need_memory", False)),
                reasoning_depth=str(val.get("reasoning_depth", "shallow")),
            )
            cr._ts = now  # permanent всегда свежий
            _PERMANENT[key] = cr
            # Также кладём в LRU
            if key not in _LRU_CACHE:
                cr2 = ClassificationResult(
                    profile=val["profile"],
                    confidence=float(val.get("confidence", 0.85)),
                    source="lru",
                    need_search=bool(val.get("need_search", False)),
                    need_memory=bool(val.get("need_memory", False)),
                    reasoning_depth=str(val.get("reasoning_depth", "shallow")),
                )
                cr2._ts = now
                _LRU_CACHE[key] = cr2
                count += 1
        while len(_LRU_CACHE) > _LRU_MAX_SIZE:
            _LRU_CACHE.popitem(last=False)
        logger.info("[router] loaded %d permanent entries", count)
        return count
    except Exception as e:
        logger.debug("[router] permanent load error: %s", e)
        return 0


def _permanent_save() -> None:
    """Сохранить permanent-записи на диск."""
    try:
        out: Dict[str, Any] = {}
        for key, val in _PERMANENT.items():
            out[key] = {
                "profile": val.profile,
                "confidence": val.confidence,
                "need_search": val.need_search,
                "need_memory": val.need_memory,
                "reasoning_depth": val.reasoning_depth,
                "promoted_ts": _ts(),
            }
        _lru_atomic_save(_PERMANENT_PATH, out)
    except Exception as e:
        logger.debug("[router] permanent save error: %s", e)


def _promote_to_permanent(key: str) -> None:
    """Продвинуть запись из LRU в permanent."""
    entry = _LRU_CACHE.get(key)
    if entry is None:
        return
    _PERMANENT[key] = entry
    _permanent_save()
    MONITOR.inc("router_promoted_total")
    logger.info("[router] promoted to permanent: profile=%s", entry.profile)


def permanent_size() -> int:
    return len(_PERMANENT)


# =====================================================================
# Raw JSONL (сырой лог всех passive_learn результатов)
# =====================================================================

def _append_raw_log(text: str, heuristic_profile: str, llm_result: Optional[Dict[str, Any]],
                    elapsed_ms: float, outcome: str,
                    dsv_signature: str = "") -> None:
    """Дописать одну строку в raw JSONL с ротацией при превышении лимита."""
    global _raw_log_rotation_counter
    try:
        os.makedirs(os.path.dirname(_RAW_LOG_PATH) or ".", exist_ok=True)
        record = {
            "ts": _ts(),
            "text_hash": lru_key(text),
            "text_preview": text[:120],
            "heuristic_profile": heuristic_profile,
            "llm_profile": llm_result.get("profile") if llm_result else None,
            "llm_confidence": llm_result.get("confidence") if llm_result else None,
            "outcome": outcome,  # "agree" | "disagree" | "error" | "timeout"
            "latency_ms": round(elapsed_ms, 1),
            "dsv_signature": dsv_signature or "",
        }
        with open(_RAW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        # Проверка ротации (каждую 50-ю запись)
        _raw_log_rotation_counter += 1
        if _raw_log_rotation_counter >= 50:
            _raw_log_rotation_counter = 0
            _trim_raw_log()
    except Exception as e:
        logger.debug("[router] raw log write error: %s", e)


def _trim_raw_log() -> None:
    """Обрезать raw JSONL до _RAW_LOG_MAX_LINES строк (хвост)."""
    if not os.path.isfile(_RAW_LOG_PATH):
        return
    try:
        with open(_RAW_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _RAW_LOG_MAX_LINES:
            return
        tail = lines[-_RAW_LOG_MAX_LINES:]
        with open(_RAW_LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(tail)
    except Exception as e:
        logger.debug("[router] trim raw log error: %s", e)


def raw_log_size() -> int:
    """Вернуть число строк в raw JSONL."""
    if not os.path.isfile(_RAW_LOG_PATH):
        return 0
    try:
        with open(_RAW_LOG_PATH, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def reset_raw_log() -> None:
    """Очистить raw JSONL (админ)."""
    try:
        if os.path.isfile(_RAW_LOG_PATH):
            os.remove(_RAW_LOG_PATH)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
# =====================================================================
# Frequency analysis (сканирование raw JSONL, промоция частого)
# =====================================================================

async def _frequency_sweep() -> int:
    """Сканировать raw JSONL, подсчитать частоту, продвинуть частое в permanent.
    Вызывается периодически (раз в _FREQUENCY_SWEEP_INTERVAL).
    Возвращает число продвинутых записей.
    """
    global _last_frequency_sweep
    now = _ts()
    if now - _last_frequency_sweep < _FREQUENCY_SWEEP_INTERVAL:
        return 0
    _last_frequency_sweep = now
    if not os.path.isfile(_RAW_LOG_PATH):
        return 0
    try:
        counter: Counter = Counter()
        with open(_RAW_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text_hash = rec.get("text_hash")
                if text_hash:
                    counter[text_hash] += 1
    except Exception as e:
        logger.debug("[router] frequency sweep error: %s", e)
        return 0
    promoted = 0
    for text_hash, count in counter.items():
        if count >= _FREQUENCY_PERMANENT_THRESHOLD and text_hash not in _PERMANENT:
            # Ищем запись с таким хэшем в LRU
            if text_hash in _LRU_CACHE:
                _promote_to_permanent(text_hash)
                promoted += 1
        elif count >= _FREQUENCY_PROMOTE_THRESHOLD and text_hash not in _PERMANENT:
            # Продвигаем в LRU, если ещё нет
            # (запись может быть в LRU через _lru_put из classify)
            pass  # Уже должно быть в LRU через _lru_put
    if promoted:
        logger.info("[router] frequency sweep promoted %d entries", promoted)
    return promoted


def trigger_frequency_sweep() -> None:
    """Принудительный сброс счётчика (для админа или тестов)."""
    global _last_frequency_sweep
    _last_frequency_sweep = 0


# =====================================================================
# Метрики онлайн
# =====================================================================

_metrics: List[Dict[str, Any]] = []


def _record_metric(source: str, latency_ms: float, confidence: float, profile: str) -> float:
    """Записать метрику и скорректировать confidence по ProfileReinforcement."""
    adjusted = confidence
    try:
        from core.self_improvement import get_profile_reinforcement
        pr = get_profile_reinforcement()
        score = pr.get_profile_score(profile)
        # Если профиль плохо себя показал — снижаем уверенность
        if score < 0.4:
            adjusted = confidence * 0.5
        elif score < 0.6:
            adjusted = confidence * 0.8
    except Exception as e:
        logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
    _metrics.append({
        "ts": time.time(),
        "source": source,
        "latency_ms": latency_ms,
        "confidence": adjusted,
        "profile": profile,
    })
    while len(_metrics) > _METRICS_WINDOW:
        _metrics.pop(0)
    MONITOR.inc("router_latency_ms_sum", int(latency_ms))
    MONITOR.inc("router_confidence_sum", int(adjusted * 100))
    MONITOR.inc(f"router_source_{source}")
    return adjusted


def router_metrics() -> Dict[str, Any]:
    if not _metrics:
        return {
            "samples": 0,
            "lru_size": lru_size(),
            "permanent_size": permanent_size(),
            "raw_log_size": raw_log_size(),
        }
    sources: Dict[str, int] = {}
    latencies: List[float] = []
    confidences: List[float] = []
    profiles: Dict[str, int] = {}
    for m in _metrics:
        sources[m["source"]] = sources.get(m["source"], 0) + 1
        latencies.append(m["latency_ms"])
        confidences.append(m["confidence"])
        profiles[m["profile"]] = profiles.get(m["profile"], 0) + 1
    latencies.sort()
    confidences.sort()
    n = len(latencies)
    return {
        "samples": n,
        "sources": sources,
        "latency_ms_median": latencies[n // 2] if n else 0,
        "latency_ms_p95": latencies[int(n * 0.95)] if n else 0,
        "confidence_median": confidences[n // 2] if n else 0,
        "profiles": profiles,
        "lru_size": lru_size(),
        "permanent_size": permanent_size(),
        "raw_log_size": raw_log_size(),
    }


def reset_metrics() -> None:
    _metrics.clear()
    _lru_clear()


# =====================================================================
# Bypass
# =====================================================================

def _bypass_short(text: str, context: Optional[Dict[str, Any]] = None) -> Optional[ClassificationResult]:
    txt = (text or "").strip()
    low = txt.lower()
    # Короткая реплика внутри диалога — не режим «1–3 слова».
    if isinstance(context, dict):
        try:
            from core.behavior_store import _is_short_topic_followup
            from core.prompt_routing import (
                recent_dialogue_has_substance,
                text_looks_dialog_followup_cue,
            )

            rd = context.get("recent_dialogue") or context.get("recent_messages") or []
            tt = context.get("topic_tracking")
            cur_topic = (
                str(tt.get("current") or "").strip()
                if isinstance(tt, dict)
                else ""
            )
            if _is_short_topic_followup(txt) or text_looks_dialog_followup_cue(txt):
                return None
            if len(cur_topic) >= 12 and recent_dialogue_has_substance(rd):
                return None
        except Exception as e:
            logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
    if any(
        w in low
        for w in (
            "бесполезн",
            "не помог",
            "не то",
            "не понял",
            "trace",
            "проверь",
            "проверить",
        )
    ):
        return None
    if len(txt) < _SHORT_TEXT_BYPASS_LEN and "?" not in txt:
        _memory_triggers = {"помнишь", "напомни", "вспомни", "память",
                            "запомни", "не забудь", "забыл", "забудешь",
                            "какое слов", "какие слов",
                            "запоминал", "запомнил"}
        if any(t in txt.lower() for t in _memory_triggers):
            return None

        # Не байпасить континуацию (продолжи, дальше, ещё, continue, more)
        _continue_triggers = {"продолжи", "продолжай", "дальше", "ещё", "еще",
                              "далее", "continue", "more", "next", "давай дальше"}
        if any(t in txt.lower() for t in _continue_triggers):
            return None

        try:
            from core.brain.code_empty_recovery import thread_awaits_code_body

            if thread_awaits_code_body(txt, context):
                return None
        except Exception as e:
            logger.debug("router bypass code thread: %s", e)

        # Не байпасить запросы о себе — они требуют user_facts или инструментов архива
        _self_triggers = {"сколько мне", "мое имя", "моё имя", "мои данные",
                          "мои записи", "кто я", "мой возраст",
                          "что ты знаешь обо мне", "что обо мне",
                          "кодовое слов", "кодово", "кодов",
                          "кто тебя", "кто создал", "кто автор", "кто разработчик",
                          "кто сделал", "кто написал"}
        if any(t in txt.lower() for t in _self_triggers):
            return None

        _explain_triggers = {"почему", "зачем", "отчего", "как работает", "объясни"}
        if any(t in txt.lower() for t in _explain_triggers):
            return None

        # Не байпасить при конфликтном контексте
        if isinstance(context, dict):
            try:
                from core.brain.dialogue_context import build_dsv

                dsv = build_dsv(context)
                if dsv.conflict_escalation > 0 or dsv.correction_loop:
                    return None
            except Exception as e:
                logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
        try:
            from core.heuristic_context_gate import should_run_shortcut

            _gr = should_run_shortcut(
                "router_bypass_short",
                txt,
                planner_context=context if isinstance(context, dict) else None,
                ultra_short_text=True,
            )
            if not _gr.allowed:
                return None
        except Exception as e:
            logger.debug("router bypass gate: %s", e)
        return ClassificationResult(
            profile=PROFILE_SHORT,
            confidence=0.99,
            source="bypass",
        )
    return None


# =====================================================================
# Fallback: эвристика
# =====================================================================

def _is_reference_paste(user_text: str) -> bool:
    """
    Длинная вставка статьи/инструкции без прямого вопроса к боту — не batch.
    Типичный кейс: пользователь присылает готовый текст и ждёт реакции/сравнения.
    """
    txt = (user_text or "").strip()
    if len(txt) < 800:
        return False
    first = txt.split("\n", 1)[0].strip()
    if first.endswith("?"):
        return False
    low = txt.lower()
    article_markers = (
        "надеюсь, эта информация",
        "если останутся вопросы",
        "можно ответить кратко",
        "поэтому на вопрос",
        "вот ключевые",
        "пошаговая инструкция",
    )
    if any(m in low for m in article_markers):
        return True
    lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
    if len(lines) >= 6 and len(txt) > 2000:
        avg_len = len(txt) / max(len(lines), 1)
        if avg_len > 65 and "?" not in txt[-400:]:
            return True
    return False


def _detect_batch(user_text: str) -> bool:
    """Определить, содержит ли сообщение пакет из множества команд/вопросов.

    Условия (по порядку проверки):
      1. ≥4 строк с '?'                → вопросы
      2. ≥6 строк без длинных строк    → список команд/дел
      3. 1 строка, ≥8 запятых/точек с запятой  → список в строку
      4. 1 строка, ≥4 вхождений `\\d+\\.` → нумерованный список

    Исключения (НЕ batch):
      - Любая строка длиннее 150 символов и без '?' → код/проза/статья
      - Меньше 30 символов всего
      - Одна задача с нумерованными подпунктами (тессеракт, тест)
    """
    if not user_text or len(user_text) < 10:
        return False

    if _is_reference_paste(user_text):
        return False

    try:
        from core.batch_continuation import is_unified_problem

        if is_unified_problem(user_text):
            return False
    except Exception as e:
        logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
    lines = [l.strip() for l in user_text.split("\n") if l.strip()]
    if not lines:
        return False

    # Guard: длинные строки без '?' — это код, проза, пересланная статья, НЕ batch
    has_long_line = False
    for l in lines:
        if len(l) > 150 and "?" not in l:
            has_long_line = True
            break

    # Если ≥3 строк начинаются с цифры и точки — это нумерованный список, а не проза
    numbered_lines = sum(1 for l in lines if re.match(r'^\d+[\.\)]\s*\S', l))

    n = len(lines)
    request_count = _count_requests(lines)

    if has_long_line and numbered_lines < 3:
        if request_count >= _BATCH_QUESTION_THRESHOLD:
            return True
        return False

    if request_count >= _BATCH_QUESTION_THRESHOLD:
        return True
    # 2. Много коротких строк — список команд/дел
    if n >= 6:
        return True

    # 3. Одна строка — список через запятую или точку с запятой
    if n == 1:
        txt = user_text.strip()
        comma_count = txt.count(",") + txt.count(";")
        if comma_count >= 8:
            return True
        # 5. Нумерованный список в одну строку (1. купить 2. сделать ...)
        numbered = len(re.findall(r'\d+\.\s', txt))
        if numbered >= 4:
            return True

    return False


def _count_requests(lines: list[str]) -> int:
    count = 0
    for line in lines:
        if "?" in line:
            count += 1
        elif line.lower().startswith(tuple(_CMD_VERBS)):
            count += 1
    return count


def _heuristic_fallback(
    user_text: str,
    active_goal_ids: Optional[List[str]] = None,
    intent_complexity: float = 0.0,
    context: Optional[Dict[str, Any]] = None,
) -> ClassificationResult:
    # Сначала проверяем DSV — контекст диалога
    if isinstance(context, dict):
        from core.brain.dialogue_context import build_dsv

        dsv = build_dsv(context)
        # Конфликт >= 2: короткий ответ с извинением и верификацией
        if dsv.conflict_escalation >= 2:
            return ClassificationResult(
                profile=PROFILE_SHORT,
                confidence=0.75,
                source="heuristic",
                need_memory=True,
                reasoning_depth="shallow",
            )
        # Цикл исправлений: нужна верификация фактов
        if dsv.correction_loop:
            return ClassificationResult(
                profile=PROFILE_STANDARD,
                confidence=0.7,
                source="heuristic",
                need_memory=True,
                reasoning_depth="nested",
            )
        # Запрос к памяти
        if dsv.memory_referenced:
            return ClassificationResult(
                profile=PROFILE_STANDARD,
                confidence=0.65,
                source="heuristic",
                need_memory=True,
                reasoning_depth="shallow",
            )
        # Новая тема — не даём short, пусть LLM решит
        if dsv.topic_change:
            return ClassificationResult(
                profile=PROFILE_STANDARD,
                confidence=0.6,
                source="heuristic",
            )

    from core.brain.agent_pack import determine_profile
    profile = determine_profile(
        user_text=user_text,
        active_goal_ids=active_goal_ids or [],
        intent_complexity=intent_complexity,
        context=context,
    )
    profile = _guard_heuristic_profile_on_prose(profile, user_text, context)
    return ClassificationResult(
        profile=profile,
        confidence=0.6,
        source="heuristic",
    )


_RISKY_HEURISTIC_PROFILES = frozenset(
    {
        "math_solve",
        "code_debug",
        "legal",
        "translation",
        "data_analysis",
        "code_generation",
        "news_brief",
        "research",
        "summarization",
        "troubleshooting",
        "planning",
    }
)


def _router_prose_guard_heuristic_enabled() -> bool:
    raw = os.getenv("ROUTER_PROSE_GUARD_HEURISTIC", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _guard_heuristic_profile_on_prose(
    profile: str,
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Не отдавать keyword-fallback профиль на длинной prose (P1)."""
    if not _router_prose_guard_heuristic_enabled():
        return profile
    p = (profile or "standard").strip()
    if p not in _RISKY_HEURISTIC_PROFILES:
        return profile
    try:
        from core.heuristic_context_gate import should_run_shortcut
        from core.heuristic_shortcuts_registry import get_rule

        gr = should_run_shortcut(
            "router_heuristic_prose_guard",
            user_text,
            planner_context=context if isinstance(context, dict) else None,
        )
        if gr.allowed:
            return profile
        rule = get_rule("router_heuristic_prose_guard") or {}
        fb = str(rule.get("fallback") or "quick_explain").strip() or "quick_explain"
        from core.brain.profile_registry import is_valid_profile

        return fb if is_valid_profile(fb) else "quick_explain"
    except Exception as e:
        logger.debug("prose guard heuristic profile: %s", e)
    return profile


# =====================================================================
# LLM-роутер (активный контур)
# =====================================================================

def _router_session_id(context: Optional[Dict[str, Any]]) -> str:
    """Стабильный X-Session-Id для router LLM (system prompt одинаковый → cache)."""
    if not isinstance(context, dict):
        return ""
    sid = str(context.get("llm_session_id") or "").strip()
    if sid:
        return sid
    uid = str(context.get("user_id") or "").strip()
    if uid and uid not in {"unknown", "anon", ""}:
        return f"u-{uid}.router"
    return ""


async def _call_llm_router(
    llm: Any,
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[ClassificationResult]:
    """Вызвать LLM для классификации запроса.

    Теперь передаёт DialogueStateVector — компактный контекст диалога.
    """
    if not _ROUTER_ENABLED:
        return None

    dsv = None
    ctx_lines = ""
    if isinstance(context, dict):
        from core.brain.dialogue_context import build_dsv

        dsv = build_dsv(context)
        ctx_lines = "\n" + dsv.to_prompt()

    prompt_text = (
        f"User: {user_text[:500]}{ctx_lines}\n"
        f"JSON:"
    )
    router_session = _router_session_id(context)
    for attempt in range(_ROUTER_LLM_RETRY_MAX + 1):
        try:
            start = time.monotonic()
            result = await asyncio.wait_for(
                llm.generate(
                    prompt=prompt_text,
                    model=_ROUTER_LLM_MODEL,
                    system_prompt=_router_system_prompt(),
                    max_tokens=150,
                    temperature=0.1,
                    telemetry_kind="router_llm",
                    telemetry_tag="router_classifier",
                    session_id=router_session,
                    conversation_id=router_session,
                ),
                timeout=_ROUTER_LLM_TIMEOUT_SEC,
            )
            elapsed = (time.monotonic() - start) * 1000
            if result.get("error"):
                logger.warning("[router] LLM error (attempt %d): %s", attempt, result["error"])
                continue
            content = str(result.get("content") or "").strip()
            parsed = _parse_llm_response(content)
            if parsed:
                parsed.latency_ms = elapsed
                parsed.source = "llm"
                return parsed
            logger.warning("[router] unparseable (attempt %d): %s", attempt, content[:200])
        except asyncio.TimeoutError:
            logger.warning("[router] timeout (attempt %d)", attempt)
        except Exception as e:
            logger.warning("[router] error (attempt %d): %s", attempt, e)
    return None


def _map_unknown_profile(profile_raw: str) -> Optional[str]:
    """Сопоставить неизвестное имя профиля с ближайшим известным."""
    if not profile_raw:
        return None
    # Прямые синонимы
    _synonyms = {
        "task_summary": "standard",
        "task_completion": "task_executor",
        "task_planning": "task_executor",
        "simple_qa": "quick_explain",
        "general_chat": "standard",
        "general_query": "standard",
        "factual": "quick_explain",
        "factual_qa": "quick_explain",
        "opinion": "creative",
        "analysis": "deep_analysis",
        "research": "deep_analysis",
        "summary": "standard",
        "summarize": "standard",
        "debugging": "code_generation",
        "code": "code_generation",
        "translation": "translation",
        "translate": "translation",
        "traducción": "translation",
        "traduccion": "translation",
        "traduction": "translation",
        "übersetzung": "translation",
        "ubersetzung": "translation",
        "math": "math_solve",
        "help": "command_help",
        "document": "document_qa",
        "document_analysis": "document_qa",
        "planning": "planning",
        "troubleshooting": "troubleshoot",
        "tutorial": "tutorial",
        "roleplay": "roleplay",
        "debate": "debate",
        "data_analysis": "data_analysis",
        "recommendation": "recommendation",
        "brainstorm": "brainstorm",
        "legal": "legal",
        "legal_qa": "legal",
        "education": "education",
        "learning": "education",
        "code_review": "code_review",
    }
    mapped = _synonyms.get(profile_raw)
    if mapped in _ALL_PROFILES:
        logger.debug("[router] mapped unknown profile=%s -> %s", profile_raw, mapped)
        return mapped
    return None


def _parse_llm_response(content: str) -> Optional[ClassificationResult]:
    """Парсит JSON-ответ LLM. Fallback -- поиск имени профиля в тексте."""
    if not content or not content.strip():
        logger.debug("[router] empty LLM response")
        return None

    # 1. Парсинг JSON
    json_start = content.find("{")
    json_end = content.rfind("}")
    raw = ""
    if json_start != -1 and json_end != -1:
        raw = content[json_start:json_end + 1]
    data = None
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                fixed = raw.replace("'", '"')
                data = json.loads(fixed)
            except json.JSONDecodeError:
                data = None
    if data:
        profile_raw = str(data.get("profile") or "").strip().lower()
        profile = _RU_TO_EN_PROFILE.get(profile_raw, profile_raw)
        if profile not in _ALL_PROFILES:
            logger.warning(
                "[router] LLM returned unknown profile=%s — mapping to nearest known",
                profile_raw,
            )
            profile_mapped = _map_unknown_profile(profile_raw)
            if profile_mapped:
                profile = profile_mapped
            else:
                logger.warning("[router] no fallback for profile=%s, falling to text search", profile_raw)
                data = None  # Принудительно идём в текстовый fallback
        if data:
            try:
                confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.5)))
            except (TypeError, ValueError):
                confidence = 0.5
            depth = str(data.get("reasoning_depth") or "shallow").strip().lower()
            if depth not in ("shallow", "nested", "deep"):
                depth = "shallow"
            return ClassificationResult(
                profile=profile,
                confidence=confidence,
                source="llm",
                need_search=bool(data.get("need_search", False)),
                need_memory=bool(data.get("need_memory", False)),
                reasoning_depth=depth,
            )

    # 2. Fallback: ищем имя профиля в тексте ответа
    low = content.lower()
    profile_order = sorted(_ALL_PROFILES, key=len, reverse=True)
    for p in profile_order:
        if p in low:
            logger.debug("[router] fallback parsed profile=%s from: %.100s", p, content)
            return ClassificationResult(
                profile=p,
                confidence=0.6,
                source="llm",
                need_search=False,
                need_memory=False,
                reasoning_depth="shallow",
            )

    logger.warning("[router] unable to parse LLM response: %.200s", content)
    return None


# =====================================================================
# Публичный API: онлайн-классификация
# =====================================================================

async def classify(
    *,
    user_text: str,
    llm: Any,
    active_goal_ids: Optional[List[str]] = None,
    intent_complexity: float = 0.0,
    context: Optional[Dict[str, Any]] = None,
) -> ClassificationResult:
    """
    Онлайн-классификация: bypass -> LRU -> LLM -> эвристика.
    """
    start = time.monotonic()

    # 1. Bypass
    result = _bypass_short(user_text, context)
    if result:
        result.latency_ms = (time.monotonic() - start) * 1000
        _record_metric(result.source, result.latency_ms, result.confidence, result.profile)
        return result

    # 1a. Детерминированный preflight (статьи, URL, длинные вставки)
    try:
        from core.brain.profile_route_guard import (
            clamp_profile as _clamp_profile,
            preflight_profile as _preflight_profile,
        )

        _pre_prof = _preflight_profile(user_text)
        if _pre_prof and _pre_prof in _ALL_PROFILES:
            result = ClassificationResult(
                profile=_pre_prof,
                confidence=0.97,
                source="preflight",
                need_search=_pre_prof in ("summarization", "research", "document_qa"),
                need_memory=False,
                reasoning_depth="nested" if _pre_prof in ("research", "deep_analysis") else "shallow",
            )
            result.latency_ms = (time.monotonic() - start) * 1000
            _record_metric(result.source, result.latency_ms, result.confidence, result.profile)
            return result
    except Exception as e:
        logger.debug("[router] preflight: %s", e)

    # 1b. Batch-детектор (много вопросов/команд в одном сообщении)
    if _detect_batch(user_text):
        _batch_ok = True
        try:
            from core.heuristic_context_gate import should_run_shortcut

            _gr_batch = should_run_shortcut(
                "batch_detector",
                user_text,
                planner_context=context if isinstance(context, dict) else None,
            )
            _batch_ok = _gr_batch.allowed
        except Exception as e:
            logger.debug("batch_detector gate: %s", e)
        if _batch_ok:
            result = ClassificationResult(
                profile="batch",
                confidence=0.85,
                source="batch_detector",
                need_search=False,
                need_memory=False,
                reasoning_depth="nested",
            )
            result.latency_ms = (time.monotonic() - start) * 1000
            _record_metric(result.source, result.latency_ms, result.confidence, result.profile)
            return result

    # 2. LRU-кеш (контекстно-зависимый)
    result = _lru_get(user_text, context)
    if result:
        try:
            from core.brain.profile_route_guard import clamp_profile as _clamp_profile

            result.profile = _clamp_profile(
                result.profile,
                user_text,
                router_confidence=float(result.confidence or 0.0),
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
        result.latency_ms = (time.monotonic() - start) * 1000
        _record_metric(result.source, result.latency_ms, result.confidence, result.profile)
        return result

    # 3. LLM-роутер
    result = await _call_llm_router(llm, user_text, context)
    if result:
        try:
            from core.brain.profile_route_guard import clamp_profile as _clamp_profile

            result.profile = _clamp_profile(
                result.profile,
                user_text,
                router_confidence=float(result.confidence or 0.0),
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
        _lru_put(user_text, result, context)
        result.latency_ms = (time.monotonic() - start) * 1000
        _record_metric(result.source, result.latency_ms, result.confidence, result.profile)
        return result

    # 4. Fallback: эвристика
    result = _heuristic_fallback(
        user_text=user_text,
        active_goal_ids=active_goal_ids,
        intent_complexity=intent_complexity,
        context=context,
    )
    try:
        from core.brain.profile_route_guard import clamp_profile as _clamp_profile

        result.profile = _clamp_profile(
            result.profile,
            user_text,
            router_confidence=float(result.confidence or 0.0),
        )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
    result.latency_ms = (time.monotonic() - start) * 1000
    _record_metric(result.source, result.latency_ms, result.confidence, result.profile)
    return result


# =====================================================================
# Пассивное обучение (LLM, fire-and-forget)
# =====================================================================

def _parse_passive_json(content: str) -> Optional[Dict[str, Any]]:
    json_start = content.find("{")
    json_end = content.rfind("}")
    if json_start == -1 or json_end == -1:
        return None
    raw = content[json_start:json_end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    profile = str(data.get("profile") or "").strip().lower()
    normalized = _RU_TO_EN_PROFILE.get(profile, profile)
    if normalized not in _ALL_PROFILES:
        return None
    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    depth = str(data.get("reasoning_depth") or "shallow").strip()
    if depth not in ("shallow", "nested", "deep"):
        depth = "shallow"
    return {
        "profile": normalized,
        "confidence": confidence,
        "reasoning_depth": depth,
    }


async def passive_learn(
    *,
    user_text: str,
    heuristic_profile: str,
    llm: Any,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Фоновый вызов LLM для сбора эталонов.
    Результат пишется в raw JSONL. Высокоуверенные несогласия LLM также
    сохраняются в LRU-кеш, чтобы улучшить онлайн-роутинг.
    """
    if not _ROUTER_ENABLED or not _PASSIVE_ENABLED:
        return
    if not user_text or len(user_text.strip()) < 5:
        return
    if heuristic_profile == PROFILE_SHORT:
        return
    text = user_text.strip()[:500]

    start = time.monotonic()
    llm_result = None
    outcome = "error"

    try:
        passive_session = _router_session_id(context)
        result = await asyncio.wait_for(
            llm.generate(
                prompt=f"User: {text}\nJSON:",
                model=_PASSIVE_LLM_MODEL,
                system_prompt=_router_system_prompt(),
                max_tokens=100,
                temperature=0.1,
                telemetry_kind="router_passive",
                telemetry_tag="router_passive",
                session_id=passive_session,
                conversation_id=passive_session,
            ),
            timeout=_PASSIVE_TIMEOUT_SEC,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        MONITOR.inc("passive_llm_latency_ms_sum", int(elapsed_ms))

        if result.get("error"):
            logger.debug("[passive] LLM error: %s", result["error"])
            outcome = "error"
        else:
            content = str(result.get("content") or "").strip()
            parsed = _parse_passive_json(content)
            if parsed:
                llm_result = parsed
                llm_profile = parsed["profile"]
                llm_confidence = parsed["confidence"]
                if llm_profile != heuristic_profile:
                    outcome = "disagree"
                    MONITOR.inc("passive_disagreements_total")
                    logger.info(
                        "[passive] DISAGREE heuristic=%s llm=%s conf=%.2f text=%s",
                        heuristic_profile, llm_profile, llm_confidence,
                        text[:80],
                    )
                    # Если LLM уверена (>=0.7) — сохраняем её мнение в LRU-кеш
                    if llm_confidence >= 0.7:
                        cr = ClassificationResult(
                            profile=llm_profile,
                            confidence=llm_confidence,
                            source="heuristic",  # Источник технически пассивный, но используем heuristic
                            need_search=bool(parsed.get("reasoning_depth") in ("nested", "deep")),
                            need_memory=False,
                            reasoning_depth=str(parsed.get("reasoning_depth", "shallow")),
                        )
                        _lru_put(user_text, cr, context)
                        MONITOR.inc("passive_lru_update_total")
                        logger.info(
                            "[passive] stored in LRU: profile=%s conf=%.2f",
                            llm_profile, llm_confidence,
                        )
                else:
                    outcome = "agree"
                    MONITOR.inc("passive_agreements_total")
                    logger.debug(
                        "[passive] AGREE profile=%s conf=%.2f lat=%.0fms",
                        llm_profile, llm_confidence, elapsed_ms,
                    )
            else:
                outcome = "unparseable"
                logger.debug("[passive] LLM unparseable: %s", content[:200])
    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - start) * 1000
        MONITOR.inc("passive_timeout_total")
        logger.debug("[passive] timeout (%.1fs)", _PASSIVE_TIMEOUT_SEC)
        outcome = "timeout"
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        MONITOR.inc("passive_error_total")
        logger.debug("[passive] error: %s", e)

    # Всегда пишем в raw JSONL (с сигнатурой DSV)
    dsv_sig = ""
    if isinstance(context, dict):
        try:
            from core.brain.dialogue_context import build_dsv
            dsv_sig = build_dsv(context).context_signature()
        except Exception as e:
            logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
    _append_raw_log(text, heuristic_profile, llm_result, elapsed_ms, outcome, dsv_sig)

    # Фоновый frequency sweep (раз в 10 минут)
    try:
        asyncio.ensure_future(_frequency_sweep())
    except Exception as e:
        logger.debug('%s optional failed: %s', 'router_classifier', e, exc_info=True)
# =====================================================================
# Инициализация при старте
# =====================================================================

_lru_load()
_permanent_load()
atexit.register(_lru_save)
atexit.register(_permanent_save)
