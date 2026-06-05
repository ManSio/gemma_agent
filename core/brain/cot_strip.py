"""Снятие утечек chain-of-thought из текста ответа модели.

Фильтрует два класса утечек:
1. CoT-утечки (chain-of-thought) — модель «думает вслух»:
   - Мыслительные маркеры: "we need to", "пользователь написал", "у меня есть инструмент" и т.д.
   - Сильные маркеры (_COT_LEAK_STRONG) снижают порог срабатывания до 200 символов.
2. Format-leak утечки — модель выводит внутренние format-инструкции:
   - _shape=short_answer, response_shape=comparison — format-selector artifacts
   - user_request_type=, внешний хинт — утечка internal hint-блоков в ответ
   - Фильтруются на уровне абзацев через _paragraph_is_format_leak()

Логика:
- Если TOOL_CALL найден — отрезается только leaky-префикс
- Если текст разбит на абзацы — ищет последние осмысленные абзацы (с кириллицей или короткие)
- Если текст сплошной (1 абзац) — ищет последние осмысленные строки
- Если весь текст — format leak — возвращает пустую строку
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# OpenRouter / reasoning models: CoT в content или reasoning (см. openrouter_completion_text).
_THINK_BLOCK_RE = re.compile(r"(?is)<think>.*?</think>")
_THINK_ORPHAN_TAG_RE = re.compile(r"(?is)<think>|</think>")

# ── Sanitizer counter: сколько сообщений удалено из истории ──
_SANITIZE_COUNTER: int = 0

_COT_LEAK_MARKERS_EN = (
    "we need to ",
    "given the instruction",
    "according to policy",
    "the user says",
    "the user is asking",
    "the user wants",
    "let's decide",
    "maybe we should",
    "i need to decide",
    "we have to ",
    "skill_output shows",
    "selected_skill =",
    "the available tools",
    "i have tools",
    "looking at the tools",
    "ok thought process",
    "let me parse",
    "choosing two different",
    "first, let me",
)
_COT_LEAK_MARKERS_RU = (
    "мы сейчас общаемся",
    "пользователь написал",
    "пользователь пишет:",
    "пользователь снова",
    "пользователь спрашивает",
    "пользователь выражает",
    "пользователь просит",
    "пользователь хочет",
    "пользователь повторяет",
    "в контексте:",
    "нужно понять, что именно",
    "согласно политике",
    "согласно инструкции",
    "инструменты selfprogramming",
    "selfprogramming.",
    "у меня есть инструмент",
    "доступные инструменты",
    "в истории диалога",
    "в истории есть",
    "memory_facts",
    "recent_dialogue",
    "видимо, он имеет в виду",
    "это не совсем то",
    "возможно, стоит использовать",
    "однако для",
    "посмотрим на доступные",
    "контекст показывает",
    "контекст:",
    "мне нужно ответить",
    "надо ответить",
    "мы находимся в",
    "нужно ответить на три",
    "нужно ответить на",
    "вспомним:",
    "это первое",
    "второй вопрос:",
    "очень кратко",
    "мысленно перебираю",
    "мысленно ",
    "по контексту видно",
    "пользователь сказал",
    "внешний хинт",
    "внешний хинт подсказывает",
    "user_request_type=",
)
_COT_LEAK_STRONG = (
    "memory_facts",
    "recent_dialogue",
    "selfprogramming.",
    "доступные инструменты:",
    "у меня есть инструменты",
    "_shape=",
    "response_shape=",
)
_COT_META_LINE_PREFIXES = (
    "пользователь ",
    "нужно ",
    "у меня ",
    "согласно ",
    "возможно",
    "однако ",
    "если ",
    "когда ",
    "для этого",
    "посмотрим",
    "инструмент",
    "selfprogramming",
    "memory_facts",
    "recent_dialogue",
    "доступные инструмент",
    "согласно инструкции",
    "анализ разговора",
    "платформа может",
)


def text_has_cyrillic(s: str) -> bool:
    return any("\u0400" <= c <= "\u04ff" for c in (s or ""))


def _paragraph_is_format_leak(para: str) -> bool:
    """Проверяет, не является ли абзац утечкой внутреннего формата (response_shape, _shape, tool_call-теги и т.д.)."""
    low = para.lower()
    # _shape= и response_shape= в контексте внутренней инструкции
    if "_shape=" in low and para.count("_shape=") >= 2:
        return True
    if "response_shape=" in low and para.count("response_shape=") >= 2:
        return True
    # user_request_type= в контексте инструкции модели
    if "user_request_type=" in low and len(para) > 200:
        return True
    # Внешний хинт / external_hint — утечка внутренней инструкции
    if "внешний хинт" in low:
        return True
    # REASONING: / reasoning: как начало абзаца — утечка chain-of-thought
    if re.match(r"^\s*(рассуждение|reasoning)\s*[:\-–—]", low):
        return True
    # AUTO_REASONING_PLUGIN_REPORT — служебный блок
    if "auto_reasoning_plugin_report" in low:
        return True
    # Классификаторский JSON — начинается с {"profile":...
    stripped = para.strip()
    if stripped.startswith("{") and ("\"profile\":" in low or "\"intent\":" in low):
        return True
    if "available tools" in low and ("admin," in low or "universalsearch" in low):
        return True
    if "системное сообщение перед диалогом" in low:
        return True
    if "примечание:" in low and "tool_call" in low and "перевод" in low:
        return True
    if low.strip().startswith("tools:") and '"name"' in low and "arithmetictool" in low:
        return True
    if "blended_style_stable" in low:
        return True
    if low.strip().startswith("style:") and ("{" in para or "blended_style" in low):
        return True
    if low.strip().startswith("user message:") or "\nuser message:" in low:
        return True
    if low.strip().startswith("сообщение пользователя:"):
        return True
    if "document_intake" in low and "пользователь" in low:
        return True
    if "file_context" in low and ("прикладывать" in low or "вложен" in low):
        return True
    if "вызванн" in low and "инструмент" in low and "опирайся" in low:
        return True
    if "text_layer_empty" in low or "access=denied" in low:
        return True
    return False


def _tail_starts_numbered_list(s: str) -> bool:
    """Первый непустой ряд абзаца — нумерованный пункт (1. …), типично продолжение после вводной)."""
    first_line = (s or "").strip().split("\n", 1)[0].strip()
    return bool(re.match(r"^\d+\.", first_line))


def _tail_looks_sentence_incomplete(s: str) -> bool:
    """
    Короткий финальный абзац без нормального завершения предложения — часто обрыв модели,
    а не осмысленный ответ; тогда сохраняем предыдущий (обычно более длинный) абзац.
    """
    t = (s or "").strip()
    if not t:
        return True
    if len(t) >= 220:
        return False
    last = t[-1]
    if last in ".!?…。！？":
        if len(t) < 96 and last == "…":
            return True
        return False
    return True


def strip_provider_think_tags(text: str) -> str:
    """Снять <think> и осиротевшие открывающие/закрывающие теги."""
    t = (text or "").strip()
    if not t:
        return ""
    t = _THINK_BLOCK_RE.sub("", t)
    t = _THINK_ORPHAN_TAG_RE.sub("", t)
    return t.strip()


def strip_leaked_cot(
    text: str,
    *,
    extra_markers_en: Tuple[str, ...] = (),
    extra_markers_ru: Tuple[str, ...] = (),
) -> str:
    """
    Убирает типичный «внутренний разбор» модели, если он попал в content (утечки в Telegram).
    Не трогает строки с TOOL_CALL, кроме префикса перед маркером.
    """
    markers_en = _COT_LEAK_MARKERS_EN + tuple(extra_markers_en)
    markers_ru = _COT_LEAK_MARKERS_RU + tuple(extra_markers_ru)
    t = strip_provider_think_tags(text)
    if not t:
        return ""
    if re.search(r"(?m)^```|```$|^def\s+\w+|^class\s+\w+|^import\s+\w+", t):
        return t
    low = t.lower()
    looks_leaky = any(m in low for m in markers_en) or any(m in low for m in markers_ru)
    strong = any(s in low for s in _COT_LEAK_STRONG)
    min_len = 200 if strong else 320
    if len(t) < min_len and not looks_leaky:
        return t

    tool_rest = ""
    if "TOOL_CALL:" in t:
        i = t.index("TOOL_CALL:")
        prefix, tool_rest = t[:i].strip(), t[i:].strip()
        if len(prefix) < 200:
            return t
        head = prefix.lower()
        leaky_prefix = any(m in head for m in markers_en) or any(m in head for m in markers_ru)
        if leaky_prefix:
            return tool_rest
        return t

    if not looks_leaky:
        return t

    parts = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
    if len(parts) < 2:
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        if len(lines) >= 8:
            for start in range(len(lines) - 1, -1, -1):
                ln = lines[start]
                if len(ln) < 45:
                    continue
                llow = ln.lower()
                if any(llow.startswith(p) or p in llow[: min(72, len(llow))] for p in _COT_META_LINE_PREFIXES):
                    continue
                tail = "\n".join(lines[start:])
                if len(tail) >= 80 and not _paragraph_is_format_leak(tail):
                    return tail
        if _paragraph_is_format_leak(t):
            return ""
        return t
    matched: List[str] = []
    for candidate in reversed(parts[-6:]):
        cl = candidate.lower()
        if len(candidate) < 40:
            continue
        if cl.startswith("we need to") or cl.startswith("given the ") or cl.startswith("the user "):
            continue
        if "we need to" in cl and len(candidate) > 250:
            continue
        if "пользователь пишет" in cl:
            continue
        if _paragraph_is_format_leak(candidate):
            continue
        if text_has_cyrillic(candidate) or len(candidate) < 700:
            matched.append(candidate)
    if not matched:
        return parts[-1]
    tail = matched[0]
    forward = list(reversed(matched))
    if len(matched) >= 2:
        prev_blk = matched[1]
        if _tail_looks_sentence_incomplete(tail) and len(prev_blk) > max(320, 6 * max(len(tail), 1)):
            return "\n\n".join(forward[:-1])
        # Вводная + нумерованный список в разных абзацах — вернуть оба, иначе ответ без вводной строки списка.
        if (
            _tail_starts_numbered_list(tail)
            and text_has_cyrillic(prev_blk)
            and not _tail_starts_numbered_list(prev_blk)
        ):
            return "\n\n".join(forward)
    return tail


def sanitize_dialogue(messages: list) -> list:
    """Очищает историю диалога от сообщений с format-утечками.

    Проходит по каждому сообщению, извлекает текст (dict.text или str),
    проверяет через _paragraph_is_format_leak(). Мусорные сообщения
    удаляются целиком. Считает количество удалённых сообщений в глобальный
    счётчик _SANITIZE_COUNTER.

    Args:
        messages: список сообщений (dict с ключом 'text' или str).

    Returns:
        Очищенный список (порядок сохранён).
    """
    global _SANITIZE_COUNTER
    cleaned: list = []
    removed = 0
    for msg in messages:
        text = msg.get("text", "") if isinstance(msg, dict) else str(msg)
        if _paragraph_is_format_leak(text):
            removed += 1
            continue
        cleaned.append(msg)
    if removed:
        _SANITIZE_COUNTER += removed
        logger.info("[sanitizer] removed %d toxic messages from dialogue", removed)
    return cleaned


def sanitize_external_hint(hint: str) -> str:
    """Удаляет из external_hint мусорные инструкции, попавшие из ответов модели."""
    toxic_patterns = [
        "Запрос обзора возможностей/инструментов/диагностики",
        "внешний хинт подсказывает",
        "user_request_type=",
        "_shape=short_answer",
        "response_shape=short_answer",
    ]
    for pattern in toxic_patterns:
        hint = hint.replace(pattern, "")
    return hint.strip()


def sanitize_counter_reset() -> None:
    """Сброс счётчика sanitizer (для тестов / админ-команды)."""
    global _SANITIZE_COUNTER
    _SANITIZE_COUNTER = 0


def sanitize_counter() -> int:
    """Текущее количество удалённых sanitizer'ом сообщений."""
    return _SANITIZE_COUNTER
