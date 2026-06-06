"""
Compact intent classifier — pure heuristic, no LLM.
4 fundamental intents: direct_action | direct_tool_action | goal | chitchat.
Auto-tool-resolution via TOOL_SYNONYMS (intent 2.0).
Dynamic Tool Selection 2.0 (Autonomy 3.0): tool relevance scoring.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SEMANTIC_INTENT_VERSION = "3.0.0"

TOOL_SYNONYMS: Dict[str, List[str]] = {
    "digital_twin": [
        "двойник",
        "цифровой двойник",
        "twin",
        "портрет",
        "цифровой портрет",
    ],
    "document_reader": [
        "прочитай документ",
        "открой документ",
        "покажи документ",
        "прочти документ",
        "зачитай документ",
        "вот документ",
        "что в документе",
        "что в файле",
    ],
    "corpus_search": [
        "найди в корпусе",
        "поиск по корпусу",
        "поищи в корпусе",
        "ищи в корпусе",
        "поиск в корпусе",
    ],
    "vision_ocr": [
        "ocr",
        "распознай текст",
        "прочитай с картинки",
        "распознай с картинки",
        "распознавание текста",
        "что на фото",
        "что на картинке",
        "что на изображении",
    ],
    "tts": [
        "озвучь",
        "tts",
        "озвучивание",
        "озвучка",
        "прочитай вслух",
        "скажи голосом",
        "озвучь текст",
    ],
    "download": [
        "скачай",
        "загрузи файл",
        "скачай файл",
        "download",
        "сохрани файл",
    ],
    "url_check": [
        "проверь ссылку",
        "проверь url",
        "проверь домен",
        "проверь сайт",
        "проверь адрес",
        "почему ссылка не работает",
        "ссылка не открывается",
    ],
}

CANONICAL_TOOL_KEYWORDS: Dict[str, str] = {
    "двойник": "digital_twin",
    "twin": "digital_twin",
    "портрет": "digital_twin",
    "ocr": "vision_ocr",
    "распознай": "vision_ocr",
    "tts": "tts",
    "озвуч": "tts",
    "скачай": "download",
    "download": "download",
    "корпус": "corpus_search",
}

DIRECT_ACTION_IMPERATIVES: Tuple[str, ...] = (
    "сделай", "сгенерируй", "создай", "нарисуй", "переведи",
    "исправь", "выполни", "построй", "оформи", "напиши",
    "собери", "сформируй", "дай diff", "почини", "запусти",
    "установи", "настрой", "разверни", "скопируй", "перемести",
    "удали", "добавь", "измени", "обнови", "отформатируй",
)

DIRECT_TOOL_ACTION_IMPERATIVES: Tuple[str, ...] = (
    "скачай", "проверь", "проанализируй", "tts", "озвучь",
    "проверь ссылку", "проверь текст", "проверь безопасность",
    "сохрани", "архивируй", "проверь файл", "загрузи",
    "выгрузи", "проверь домен", "проверь url",
    "проверь сообщение", "проверь контент",
)

GOAL_PHRASES: Tuple[str, ...] = (
    "хочу систему",
    "хочу",
    "нужно",
    "надо",
    "необходимо",
    "помоги построить",
    "разработай стратегию",
    "разработай план",
    "составь план",
    "спланируй",
    "продумай стратегию",
    "как мне",
    "как лучше",
    "как правильн",
    "сделай проект",
    "нужно реализовать",
    "помоги реализовать",
    "хочу реализовать",
    "создай проект",
    "помоги с проектом",
    "построй систему",
    "спроектируй",
    "архитектура",
    "оптимизируй проект",
)


def _detect(text: str, patterns: Tuple[str, ...]) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    for p in patterns:
        if p in low:
            return True
    return False


def _extract_after(text: str, patterns: Tuple[str, ...]) -> str:
    low = (text or "").strip().lower()
    for p in patterns:
        idx = low.find(p)
        if idx >= 0:
            after = low[idx + len(p):].strip()
            words = after.split()[:3]
            return " ".join(words) if words else p
    return ""


def normalize_tool_name(user_text: Optional[str]) -> Optional[str]:
    """
    Map user text to a canonical tool name via synonym dictionary.
    Pure-heuristic, no LLM.

    Returns canonical tool name (e.g. "digital_twin", "tts") or None.
    """
    if not user_text:
        return None
    low = user_text.strip().lower()
    if not low:
        return None

    for canonical, synonyms in TOOL_SYNONYMS.items():
        for phrase in synonyms:
            if phrase in low:
                logger.debug("normalize_tool_name: %s → %s", phrase, canonical)
                return canonical

    for keyword, canonical in CANONICAL_TOOL_KEYWORDS.items():
        if keyword in low:
            logger.debug("normalize_tool_name (keyword): %s → %s", keyword, canonical)
            return canonical

    return None


def classify_intent(user_text: Optional[str] = None) -> Dict[str, Any]:
    """
    Pure-heuristic intent classifier (no LLM).
    Returns: {intent, topic, should_call_tool, reason}
    """
    if not user_text:
        return {"intent": "chitchat", "topic": "", "should_call_tool": False, "reason": "empty"}

    # 0) auto-tool-resolution: synonym match → direct_tool_action
    _tool_name = normalize_tool_name(user_text)
    if _tool_name is not None:
        return {
            "intent": "direct_tool_action",
            "topic": _tool_name,
            "should_call_tool": True,
            "reason": "tool_synonym",
            "canonical_tool": _tool_name,
        }

    # 1) direct_tool_action
    if _detect(user_text, DIRECT_TOOL_ACTION_IMPERATIVES):
        return {
            "intent": "direct_tool_action",
            "topic": _extract_after(user_text, DIRECT_TOOL_ACTION_IMPERATIVES),
            "should_call_tool": True,
            "reason": "tool_imperative",
        }

    # 2) direct_action
    if _detect(user_text, DIRECT_ACTION_IMPERATIVES):
        return {
            "intent": "direct_action",
            "topic": _extract_after(user_text, DIRECT_ACTION_IMPERATIVES),
            "should_call_tool": False,
            "reason": "action_imperative",
        }

    # 3) goal
    if _detect(user_text, GOAL_PHRASES):
        return {
            "intent": "goal",
            "topic": _extract_after(user_text, GOAL_PHRASES),
            "should_call_tool": False,
            "reason": "goal_phrasing",
        }

    # 4) chitchat — default
    return {"intent": "chitchat", "topic": "", "should_call_tool": False, "reason": "default"}


_URL_RE: re.Pattern = re.compile(r"https?://[^\s\)\]>,;]+", re.IGNORECASE)

_NUMBER_RE: re.Pattern = re.compile(r"\b(\d+)\b")

_QUOTED_RE: re.Pattern = re.compile(r'"([^"]{1,500})"')

_JSON_RE: re.Pattern = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]")

_CODE_BLOCK_RE: re.Pattern = re.compile(r"```(?:[\w]*\n)?(.*?)```", re.DOTALL)

_ID_RE: re.Pattern = re.compile(r"\b(?:id|ID|идентификатор)\s*[:=]?\s*([A-Za-z0-9_-]{3,64})")

_FILENAME_RE: re.Pattern = re.compile(r"(?:файл|file)\s+([A-Za-zА-Яа-я0-9_\-\.]{1,255}(?:\.[A-Za-z0-9]{1,10}))", re.IGNORECASE)


def extract_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = _URL_RE.search(text)
    return m.group(0).rstrip(".,;:!?") if m else None


def extract_number(text: str) -> Optional[int]:
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    return int(m.group(1)) if m else None


def extract_quoted(text: str) -> Optional[str]:
    if not text:
        return None
    m = _QUOTED_RE.search(text)
    return m.group(1).strip() if m else None


def extract_json(text: str) -> Optional[str]:
    if not text:
        return None
    m = _JSON_RE.search(text)
    return m.group(0) if m else None


def extract_code_block(text: str) -> Optional[str]:
    if not text:
        return None
    m = _CODE_BLOCK_RE.search(text)
    return m.group(1).strip() if m else None


def extract_id(text: str) -> Optional[str]:
    if not text:
        return None
    m = _ID_RE.search(text)
    return m.group(1).strip() if m else None


def extract_filename(text: str) -> Optional[str]:
    if not text:
        return None
    m = _FILENAME_RE.search(text)
    return m.group(1).strip() if m else None


# ── Dynamic Tool Selection 2.0 (Autonomy 3.0) ──

_TOOL_RELEVANCE_PATTERNS: Dict[str, Dict[str, List[str]]] = {
    "document_reader": {
        "strong": ["документ", "файл", "file", "pdf", "docx", "txt", "читать"],
        "medium": ["открой", "посмотри", "прочитай", "что в"],
    },
    "vision_ocr": {
        "strong": ["фото", "картинка", "изображение", "скриншот", "ocr", "распознай"],
        "medium": ["что на", "посмотри на"],
    },
    "url_check": {
        "strong": ["ссылк", "url", "http", "сайт", "домен"],
        "medium": ["проверь", "не работает", "не открывается"],
    },
    "download": {
        "strong": ["скачай", "download", "загрузи"],
        "medium": ["сохрани", "получи файл"],
    },
    "corpus_search": {
        "strong": ["корпус", "поиск по", "найди в"],
        "medium": ["ищи", "поищи"],
    },
    "tts": {
        "strong": ["озвучь", "tts", "голос", "озвуч"],
        "medium": ["прочитай вслух", "скажи"],
    },
    "digital_twin": {
        "strong": ["двойник", "twin", "портрет", "цифровой"],
        "medium": ["профиль", "мой"],
    },
}


def score_tool_relevance(user_text: Optional[str], tool_name: str) -> int:
    """Score relevance of a tool to user text (0-5+ scale).

    0-1: нерелевантно
    1-3: возможно
    3-5: подходит
    5+: идеально
    """
    if not user_text or tool_name not in _TOOL_RELEVANCE_PATTERNS:
        return 0
    low = user_text.strip().lower()
    if not low:
        return 0
    score = 0
    patterns = _TOOL_RELEVANCE_PATTERNS[tool_name]
    for phrase in patterns.get("strong", []):
        if phrase in low:
            score += 3
    for phrase in patterns.get("medium", []):
        if phrase in low:
            score += 1
    return score


def select_best_tool(user_text: Optional[str], min_score: int = 3) -> Tuple[Optional[str], int]:
    """Select the best-matching tool by maximum relevance score.
    Returns (tool_name, score) or (None, 0).
    """
    best_tool: Optional[str] = None
    best_score = 0
    for tool_name in _TOOL_RELEVANCE_PATTERNS:
        s = score_tool_relevance(user_text, tool_name)
        if s > best_score:
            best_score = s
            best_tool = tool_name
    if best_score < min_score:
        return None, best_score
    return best_tool, best_score


def auto_select_tool(user_text: Optional[str]) -> Optional[str]:
    """Select tool even when user didn't explicitly request it.
    For implicit requests like "что на фото?", "почему ссылка не работает?".
    """
    tool, score = select_best_tool(user_text, min_score=2)
    return tool if score >= 2 else None
