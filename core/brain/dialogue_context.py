"""
DialogueStateVector (DSV) — компактное представление диалогового контекста
для роутера (~100 токенов).

Используется в router_classifier.py для:
  - LLM-промпта (передаётся как "Dialogue context:" строка)
  - Эвристического fallback (детектор конфликтов, тестов памяти)
  - Контекстно-зависимого LRU-ключа

Архитектура:
  build_dsv(context) -> DialogueStateVector
    +-- извлекает recent_dialogue, dialogue_state
    +-- классифицирует тон пользователя (regex + эвристики)
    +-- определяет конфликтную эскалацию
    +-- определяет смену темы
    +-> to_prompt() -> строка для LLM-роутера
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# -- Регулярки для детекции тона и намерений --

_CONFLICT_PATTERNS = re.compile(
    r"(?i)(ты\s+(галюционируешь|врёшь|ошибся|неправ|обманываешь|"
    r"не\s+понял|не\s+то|не\s+так|перепутал|"
    r"глючишь|тормозишь|фигню\s+несёшь)|"
    r"что\s+ты\s+(врёшь|несёшь|говоришь)|"
    r"исправь|поправь|неверно|не\s+правильно|ошибк|"
    r"ты\s+не\s+прав)",
)

_MEMORY_PATTERNS = re.compile(
    r"(?i)(помнишь|напомни|вспомни|память|"
    r"запомни|не\s+забудь|забыл|забудешь|"
    r"какое\s+слов[ао]|какие\s+слов|"
    r"запоминал|запомнил|просил\s+запомнит|"
    r"что\s+я\s+просил|что\s+ты\s+помнишь|"
    r"покажи\s+(мои\s+)?заметк|мои\s+факт|"
    r"что\s+(обо\s+)?мне\s+известн|"
    r"найди\s+в\s+(переписк|архив|моих\s+документ))",
)

_CORRECTION_PATTERNS = re.compile(
    r"(?i)(нет[,\s]|не\s+(то|так|верно|правильно)|"
    r"я\s+же\s+говор|повтор|"
    r"я\s+сказал|я\s+писал)",
)

_CONFUSED_PATTERNS = re.compile(
    r"(?i)(не\s+пон(ял|ятн)|не\s+поним|"
    r"что\s+это\s+знач|объясн|"
    r"зачем\s+ты|почему\s+ты)",
)

_TOPIC_CHANGE_PATTERNS = re.compile(
    r"(?i)(а\s+что\s+насчёт|давай\s+друг|"
    r"сменим\s+тему|не\s+про\s+то|"
    r"кстати\s+о|а\s+вообще)",
)

_MEMORY_TRIGGER_WORDS = frozenset({
    "помнишь", "напомни", "вспомни", "память",
    "запомни", "не забудь", "забыл", "забудешь",
    "какое слов", "какие слов",
    "запоминал", "запомнил", "просил запомнить",
    "что я просил", "покажи заметк",
})


@dataclass
class DialogueStateVector:
    """Компактное представление диалогового контекста (~100 токенов).

    Все поля — примитивы для передачи в LLM-роутер и эвристики.
    """

    turn_count: int = 0
    """Количество реплик в диалоге."""

    last_bot_intent: str = ""
    """intent последней реплики бота (из dialogue_state.last_intent)."""

    last_bot_summary: str = ""
    """Краткая суть последнего ответа бота (первые 40 токенов)."""

    user_tone: str = "neutral"
    """positive | neutral | angry | confused | testing."""

    conflict_escalation: int = 0
    """0=нет, 1=первое несогласие, 2=повтор, 3+=спор."""

    topic_change: bool = False
    """True если пользователь явно сменил тему."""

    memory_referenced: bool = False
    """True если пользователь ссылается на память."""

    correction_loop: bool = False
    """True если пользователь поправляет бота повторно."""

    last_assistant_excerpt: str = ""
    """Последняя реплика ассистента (первые 200 символов), если есть."""

    previous_user_excerpt: str = ""
    """Предыдущая реплика пользователя (первые 200 символов), если есть."""

    def to_prompt(self) -> str:
        """Скомпоновать строку для LLM-роутера.

        Возвращает ~100 токенов с ключевой информацией о диалоге.
        """
        parts = []
        parts.append(f"turns={self.turn_count}")
        if self.user_tone != "neutral":
            parts.append(f"tone={self.user_tone}")
        if self.conflict_escalation > 0:
            parts.append(f"conflict_lvl={self.conflict_escalation}")
        if self.memory_referenced:
            parts.append("memory_ref")
        if self.correction_loop:
            parts.append("correction_loop")
        if self.topic_change:
            parts.append("new_topic")
        if self.last_assistant_excerpt:
            parts.append(f"last_assistant: {self.last_assistant_excerpt[:150]}")
        return " | ".join(parts)

    def context_signature(self) -> str:
        """Компактная сигнатура контекста для LRU-ключа.

        Возвращает строку вида "conflict:2|memory:1|tone:angry".
        """
        tags = []
        if self.conflict_escalation > 0:
            tags.append(f"conflict:{self.conflict_escalation}")
        if self.correction_loop:
            tags.append("loop")
        if self.memory_referenced:
            tags.append("memory")
        if self.user_tone != "neutral":
            tags.append(f"tone:{self.user_tone}")
        return "|".join(tags) if tags else "normal"


def _extract_role_content(turn: Any) -> tuple[str, str]:
    """Извлечь (role, content) из реплики диалога.

    Поддерживает dict вида {"role": "user", "content": "..."}
    и строковые форматы.
    """
    if isinstance(turn, dict):
        role = str(turn.get("role", "user")).lower()
        content = str(turn.get("content") or turn.get("text") or "")
        return role, content[:500]
    if isinstance(turn, str):
        return "user", turn[:500]
    return "user", str(turn)[:500]


def _classify_tone(user_text: str) -> str:
    """Определить тон пользователя по тексту запроса."""
    if _CONFLICT_PATTERNS.search(user_text):
        return "angry"
    if _CORRECTION_PATTERNS.search(user_text):
        return "testing"
    if _CONFUSED_PATTERNS.search(user_text):
        return "confused"
    if any(w in user_text.lower() for w in _MEMORY_TRIGGER_WORDS):
        return "testing"
    return "neutral"


def _count_escalation(recent_dialogue: list, current_tone: str) -> int:
    """Подсчитать уровень эскалации конфликта.

    Анализирует recent_dialogue на наличие конфликтных паттернов.
    """
    if current_tone == "angry":
        level = 1
    elif current_tone == "testing":
        level = 1
    else:
        return 0

    # Считаем предыдущие конфликтные реплики пользователя
    conflict_count = 0
    for turn in recent_dialogue[:-1]:  # исключаем текущую
        role, content = _extract_role_content(turn)
        if role == "user" and _CONFLICT_PATTERNS.search(content):
            conflict_count += 1

    # Проверяем, были ли исправления от пользователя
    correction_count = 0
    for turn in recent_dialogue[:-1]:
        role, content = _extract_role_content(turn)
        if role == "user" and _CORRECTION_PATTERNS.search(content):
            correction_count += 1

    level += conflict_count + correction_count
    return min(level, 5)  # кап на 5


def _detect_topic_change(recent_dialogue: list, user_text: str) -> bool:
    """Определить, сменил ли пользователь тему."""
    if _TOPIC_CHANGE_PATTERNS.search(user_text):
        return True
    return False


def _mentions_memory(user_text: str) -> bool:
    """Проверить, ссылается ли пользователь на память."""
    if _MEMORY_PATTERNS.search(user_text):
        return True
    return bool(_MEMORY_TRIGGER_WORDS.intersection(user_text.lower().split()))


def _is_correction_loop(recent_dialogue: list, user_text: str) -> bool:
    """Определить цикл исправлений.

    True если пользователь поправлял бота >=2 раз подряд.
    """
    if not _CORRECTION_PATTERNS.search(user_text):
        return False

    corrections = 0
    for turn in reversed(recent_dialogue[:-3]):  # последние 3 реплики
        role, content = _extract_role_content(turn)
        if role == "user" and _CORRECTION_PATTERNS.search(content):
            corrections += 1
        elif role == "assistant":
            pass  # не сбрасываем — считаем连续的 исправления
        if corrections >= 2:
            return True
    return False


def build_dsv(context: Optional[Dict[str, Any]]) -> DialogueStateVector:
    """Построить DSV из полного контекста диалога.

    Вызывается из роутера при каждом запросе. Работает за <0.1ms.
    """
    if not isinstance(context, dict):
        return DialogueStateVector()

    # -- Извлекаем recent_dialogue --
    rd = context.get("recent_dialogue") or context.get("recent_messages")
    if not isinstance(rd, list):
        rd = []

    # -- Извлекаем dialogue_state --
    ds = context.get("dialogue_state")
    if not isinstance(ds, dict):
        ds = {}

    turn_count = len(rd)
    last_intent = str(ds.get("last_intent") or "")

    # -- Последняя реплика пользователя --
    user_text = str(context.get("user_text") or context.get("text") or "")
    if not user_text:
        # Если user_text не передали, берём из recent_dialogue
        for turn in reversed(rd):
            role, content = _extract_role_content(turn)
            if role == "user" and content:
                user_text = content
                break

    # -- Классифицируем тон --
    user_tone = _classify_tone(user_text)

    # -- Последняя реплика ассистента --
    last_assistant = ""
    previous_user = ""
    for turn in reversed(rd):
        role, content = _extract_role_content(turn)
        if role == "assistant" and not last_assistant:
            last_assistant = content[:500]
        elif role in ("user", "") and not previous_user and content and content != user_text:
            previous_user = content[:500]

    # -- Детекция --
    conflict_escalation = _count_escalation(rd, user_tone)
    topic_change = _detect_topic_change(rd, user_text)
    memory_referenced = _mentions_memory(user_text)
    correction_loop = _is_correction_loop(rd, user_text)

    return DialogueStateVector(
        turn_count=turn_count,
        last_bot_intent=last_intent,
        last_bot_summary=_summarize_bot_response(last_assistant),
        user_tone=user_tone,
        conflict_escalation=conflict_escalation,
        topic_change=topic_change,
        memory_referenced=memory_referenced,
        correction_loop=correction_loop,
        last_assistant_excerpt=last_assistant[:200],
        previous_user_excerpt=previous_user[:200],
    )


def _summarize_bot_response(content: str) -> str:
    """Сжать ответ бота до 40 токенов.

    Берёт первую значащую строку или начало текста.
    """
    if not content:
        return ""
    # Первая непустая строка
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("TOOL_CALL"):
            return stripped[:120]
    # Если всё TOOL_CALL — возвращаем начало
    return content[:120]
