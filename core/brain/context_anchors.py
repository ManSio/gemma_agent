"""
Context Anchors — полная подсистема удержания нити диалога.

Компоненты:
  1. AnchorStore    — персистентное хранение сущностей между ходами (с затуханием)
  2. extract_entities() — NER из СЫРЫХ текстов (до compress, ничего не обрезано)
  3. coreference_detector() — поиск местоимений в ЛЮБОЙ позиции текста, не только в начале
  4. build_context_anchors_block() — сборка блока для промпта LLM

Интеграция в пайплайн:
  behavior_store.update_after_turn() → вызывает update_anchor_store() ДО compress
  → сущности пишутся в dialogue_state["anchor_entities"] → персистентны через ходы
  → prompt_modules.context_anchors читает оттуда + сканирует recent_dialogue на новое
  → last_assistant / previous_user — для LLM чтобы видеть "о чём была последняя реплика"
"""

from __future__ import annotations

import logging
import re
import os
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Константы ──
_MIN_ENTITY_LEN = 3
_MAX_ENTITIES = 10
_DECAY_RATE = 0.7  # каждый ход сущность умножается на decay; при < 0.3 удаляется
_INITIAL_WEIGHT = 1.0

# Стоп-слова, похожие на имена
_SKIP_WORDS: frozenset = frozenset({
    "вот", "этот", "это", "тот", "кто", "что", "как",
    "где", "когда", "почему", "зачем", "сколько",
    "мой", "твой", "его", "её", "их", "наш", "ваш",
    "весь", "каждый", "любой", "другой", "такой",
    "себя", "тебя", "меня", "вас", "нас", "них",
    "потом", "сейчас", "сегодня", "вчера", "завтра",
    "хорошо", "плохо", "круто", "классно", "нормально",
    "да", "нет", "ага", "ну", "ок", "окей",
    "просто", "вообще", "конечно", "ладно", "типа",
    "этот", "эта", "эти", "это", "тот", "та", "те",
    "привет", "здравствуй", "здравствуйте",
})

# Местоимения и отсылочные слова (для coreference)
_ANAPHORIC_WORDS: frozenset = frozenset({
    "он", "она", "оно", "они", "его", "её", "их", "ему", "ей",
    "им", "ними", "ним", "ней", "нём", "них", "нему", "неё",
    "это", "этого", "этому", "этим", "этом",
    "тот", "та", "те", "того", "тому", "тем",
    "такой", "такая", "такие", "такого", "такому",
    "мой", "твой", "наш", "ваш", "свой",
    "моего", "твоего", "нашего", "вашего",
    "который", "которая", "которое", "которые",
    "данный", "данная", "данные",
})

# ────────────────────────────────────────────────────────────
# 1. AnchorStore — персистентное хранилище сущностей
# ────────────────────────────────────────────────────────────


def update_anchor_store(
    existing: Optional[List[Any]],
    user_text: str,
    assistant_text: str,
    turn_index: int = 0,
    max_entities: int = _MAX_ENTITIES,
    decay_rate: float = _DECAY_RATE,
) -> List[str]:
    """Обновить хранилище якорных сущностей.

    Вызывается ИЗ behavior_store.update_after_turn() ДО compress_recent_dialogue,
    пока тексты ещё полные. Это Исправление Проблемы №5 (compress обрезает до extract).

    Алгоритм:
      1. Затухание: weight *= decay_rate; weight < 0.3 → удалить
      2. Извлечение новых сущностей из user_text и assistant_text
      3. Слияние: новые добавляются с weight=1.0
      4. Возврат: top N отсортированных по весу

    Args:
        existing: старое значение из dialogue_state["anchor_entities"] (список или None)
        user_text: сырой текст пользователя (ДО compress!)
        assistant_text: сырой текст ассистента
        turn_index: номер хода (для дебага)
        max_entities: макс сущностей в выдаче
        decay_rate: коэффициент затухания (0..1)

    Returns:
        Список сущностей для dialogue_state["anchor_entities"]
    """
    # Парсим существующие сущности с весами
    weights: Dict[str, float] = {}
    originals: Dict[str, str] = {}  # lowercase -> оригинальное написание
    if existing and isinstance(existing, list):
        for item in existing:
            if isinstance(item, str):
                low = item.lower()
                weights[low] = _INITIAL_WEIGHT
                if low not in originals:
                    originals[low] = item
            elif isinstance(item, dict) and "entity" in item:
                e = str(item["entity"]).strip()
                low = e.lower()
                w = float(item.get("weight", _INITIAL_WEIGHT))
                if low:
                    weights[low] = w
                    if low not in originals:
                        originals[low] = e

    # Затухание
    for k in list(weights):
        weights[k] *= decay_rate
        if weights[k] < 0.3:
            del weights[k]

    # Извлечение новых сущностей из сырых текстов (ДО compress)
    if user_text:
        for e in _extract_entities_from_one_text(user_text):
            low = e.lower()
            if low not in weights:
                weights[low] = _INITIAL_WEIGHT
                originals[low] = e
            else:
                weights[low] = min(weights[low] + 0.5, 2.0)  # реинфорс

    if assistant_text:
        for e in _extract_entities_from_one_text(assistant_text):
            low = e.lower()
            if low not in weights:
                weights[low] = _INITIAL_WEIGHT
                originals[low] = e
            else:
                weights[low] = min(weights[low] + 0.3, 2.0)  # ассистент — слабее

    # Сортируем по весу, берём топ-N
    sorted_entities = sorted(weights.items(), key=lambda x: -x[1])
    result = [originals.get(k, k) for k, _ in sorted_entities[:max_entities]]

    return result


def _extract_entities_from_one_text(text: str) -> List[str]:
    """Извлечь именованные сущности из одного текста.

    Работает с СЫРЫМ текстом (до обрезания). Находит:
    - Слова с заглавной буквы НЕ в начале предложения — почти наверняка имя/название
    - Первое слово, если оно не стоп-слово и следующее слово тоже с заглавной
      (паттерн «Имя Фамилия», где оба капитализированы)
    - Первое слово, если повторяется >= 2 раз
    """
    if not text:
        return []
    seen: Set[str] = set()
    entities: List[str] = []
    first_words: Dict[str, int] = {}

    words = text.split()
    for i, w in enumerate(words):
        clean = w.strip("«»\"'(),.!?:;—–-…”“„")
        if not clean or len(clean) < _MIN_ENTITY_LEN:
            continue
        if not clean[0].isupper() or not clean[0].isalpha():
            continue

        if i == 0:
            first_words[clean.lower()] = first_words.get(clean.lower(), 0) + 1
            # Если следующее слово тоже с заглавной — это «Имя Фамилия»
            if i + 1 < len(words):
                next_clean = words[i + 1].strip("«»\"'(),.!?:;—–-…”“„")
                if next_clean and len(next_clean) >= _MIN_ENTITY_LEN and next_clean[0].isupper():
                    low = clean.lower()
                    if low not in _SKIP_WORDS and low not in seen:
                        seen.add(low)
                        entities.append(clean)
        else:
            low = clean.lower()
            if low not in _SKIP_WORDS and low not in seen:
                seen.add(low)
                entities.append(clean)

    # Первые слова, встреченные >= 2 раз — вероятно имя
    for w_low, count in first_words.items():
        if count >= 2 and w_low not in seen:
            seen.add(w_low)
            entities.append(w_low.title())

    return entities


# ────────────────────────────────────────────────────────────
# 2. Детектор coreference — местоимения ВНУТРИ текста
# ────────────────────────────────────────────────────────────


def has_anaphora(text: str) -> bool:
    """Проверить, содержит ли текст отсылочные местоимения (coreference).

    В отличие от _is_likely_pronoun_anaphora() — ищет по ВСЕМУ тексту,
    а не только в начале. Это Исправление Проблемы №6.

    Примеры: "а что по поводу него?", "ты согласен с ним?", "после его заявления"
    """
    if not text:
        return False
    t = text.strip()
    if len(t) < 3:
        return False
    words = set(t.lower().split())
    return bool(words & _ANAPHORIC_WORDS)


# ────────────────────────────────────────────────────────────
# 3. Детектор начала с анафоры (для быстрой отсечки)
# ────────────────────────────────────────────────────────────


def _is_likely_pronoun_anaphora(text: str) -> bool:
    """Проверить, начинается ли текст с анафоры (быстрый regex)."""
    if not text:
        return False
    text = text.strip()
    if re.match(
        r"(?i)^(он[ао]?|они?|его|её|их|ему|ей|им|ним|ней|нём|"
        r"это[т]?|эта|эти|такой|такая|такие|"
        r"после\s+(этого|его|её|их)|"
        r"про\s+(это|него|неё|них)|"
        r"а\s+как\s+же|"
        r"а\s+что|но\s+|"
        r"аналогичн|так\s+же|так\s+ой\s+же)",
        text,
    ):
        return True
    word_count = len(text.split())
    if word_count <= 5 and any(
        w in text.lower().split()
        for w in {"он", "она", "оно", "они", "его", "её", "это", "этот",
                  "том", "тем", "нему", "ней", "них", "нём",
                  "ему", "ей", "им", "ним"}
    ):
        return True
    return False


def needs_anchors(user_text: str, dialogue: List[Dict[str, Any]]) -> bool:
    """Определить, нужны ли context_anchors в этом ходу.

    True если:
      - текст начинается с анафоры (быстрый regex)
      - ИЛИ содержит coreference в любом месте (местоимения)
      - ИЛИ в recent_dialogue есть именованные сущности

    Args:
        user_text: текущий запрос пользователя
        dialogue: recent_dialogue (может быть сжатый, но лучше до compress)
    """
    if not user_text or not dialogue:
        return False

    # Coreference — основной детектор
    if has_anaphora(user_text):
        return True

    # Быстрый старт-детектор
    if _is_likely_pronoun_anaphora(user_text):
        return True

    # Проверяем, есть ли сущности в recent (хотя бы 2 слова с заглавной)
    entity_count = 0
    max_scan = int(os.getenv("CONTEXT_ANCHORS_MAX_RECENT", "8"))
    for turn in dialogue[-max_scan:]:
        if not isinstance(turn, dict):
            continue
        txt = str(turn.get("text") or turn.get("content") or "")
        for w in txt.split():
            clean = w.strip("«»\"'(),.!?:;—–-…")
            if clean and len(clean) >= _MIN_ENTITY_LEN and clean[0].isupper() and clean[0].isalpha():
                entity_count += 1
                if entity_count >= 2:
                    return True

    return False


# ────────────────────────────────────────────────────────────
# 4. Доступ к сущностям (читает из store + сканирует recent)
# ────────────────────────────────────────────────────────────


def get_entities_for_prompt(
    anchor_entities: Optional[List[str]],
    recent_dialogue: List[Dict[str, Any]],
) -> List[str]:
    """Получить итоговый список сущностей для блока в промпте.

    Берёт персистентные anchor_entities из dialogue_state.
    Если их нет — извлекает из recent_dialogue на лету.
    """
    if anchor_entities and isinstance(anchor_entities, list) and len(anchor_entities) > 0:
        # Возвращаем только то, что ещё актуально (не стоп-слова)
        result = [e for e in anchor_entities if e.lower() not in _SKIP_WORDS]
        if result:
            return result[:8]

    # Fallback: сканируем dialogue в ширину (все сообщения, не только 4)
    return extract_entities_from_dialogue(recent_dialogue, max_recent=8)


def extract_entities_from_dialogue(
    dialogue: List[Dict[str, Any]],
    max_recent: int = 5,
) -> List[str]:
    """Извлечь сущности из диалога.

    Args:
        dialogue: список сообщений {role, text}
        max_recent: сколько последних сообщений сканировать
    """
    seen: Set[str] = set()
    entities: List[str] = []
    first_words: Dict[str, int] = {}

    messages = dialogue[-max_recent:] if dialogue else []
    for turn in messages:
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("text") or turn.get("content") or "")
        if not text.strip():
            continue

        words = text.split()
        for i, w in enumerate(words):
            clean = w.strip("«»\"'(),.!?:;—–-…")
            if not clean or len(clean) < _MIN_ENTITY_LEN:
                continue
            if not clean[0].isupper() or not clean[0].isalpha():
                continue

            if i == 0:
                first_words[clean.lower()] = first_words.get(clean.lower(), 0) + 1
                # Если следующее слово тоже с заглавной — «Имя Фамилия»
                if i + 1 < len(words):
                    next_clean = words[i + 1].strip("«»\"'(),.!?:;—–-…")
                    if next_clean and len(next_clean) >= _MIN_ENTITY_LEN and next_clean[0].isupper():
                        low = clean.lower()
                        if low not in _SKIP_WORDS and low not in seen:
                            seen.add(low)
                            entities.append(clean)
            else:
                low = clean.lower()
                if low not in _SKIP_WORDS and low not in seen:
                    seen.add(low)
                    entities.append(clean)

    for w_low, count in first_words.items():
        if count >= 2 and w_low not in seen:
            seen.add(w_low)
            entities.append(w_low.title())

    return entities[:_MAX_ENTITIES]


# ────────────────────────────────────────────────────────────
# 5. Сборка блока для промпта
# ────────────────────────────────────────────────────────────


def build_context_anchors_block(
    entities: List[str],
    last_assistant_excerpt: str,
    previous_user_excerpt: str,
    user_text: str,
) -> str:
    """Собрать строку для промпта LLM.

    Формат:
    ```
    - context_anchors: [Павел Дуров, Дубай, ОАЭ, Telegram]
    - last_assistant: Павел Дуров не впервые делает такие громкие заявления...
    - previous_user: Его потом пустят в евросоюз
    ```

    Если сущностей нет и excerpts нет — возвращает "".
    """
    parts: List[str] = []

    if entities:
        parts.append(f"- context_anchors: {entities}")

    if last_assistant_excerpt:
        parts.append(f"- last_assistant: {last_assistant_excerpt[:200]}")

    if previous_user_excerpt and previous_user_excerpt[:80] != user_text[:80]:
        parts.append(f"- previous_user: {previous_user_excerpt[:200]}")

    return "\n".join(parts) if parts else ""
