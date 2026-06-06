"""Узкий путь для перевода: без skill translator и без мета-ответов про TOOL_CALL."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

_TRANSLATE_TRIGGERS = (
    "переведи",
    "translate",
    "перевод ",
    "translation",
    "translat",
    "скажи по-",
    "скажи по ",
    "say in ",
)

_LANG_INLINE_RE = re.compile(
    r"(?i)(?:"
    r"(?:по[-\s]?|на\s+)(?:английск|русск|немецк|французск|украинск|белорусск|испанск)|"
    r"(?:скажи\s+)?по[-\s]?(?:английск|русск|немецк|французск|украинск|белорусск|испанск)|"
    r"\b(?:английск|русск|немецк|французск|украинск|белорусск|испанск)\w*\s*:"
    r"|\b(?:english|german|french|russian|spanish|deutsch|français|español)\s*:"
    r")"
)

_LANG_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"на\s+английск", "en"),
    (r"to\s+english", "en"),
    (r"на\s+русск", "ru"),
    (r"to\s+russian", "ru"),
    (r"на\s+белорусск", "be"),
    (r"на\s+украинск", "uk"),
    (r"на\s+немецк", "de"),
    (r"на\s+испанск", "es"),
    (r"to\s+spanish", "es"),
    (r"на\s+французск", "fr"),
    (r"по[-\s]?английск", "en"),
    (r"по[-\s]?русск", "ru"),
    (r"по[-\s]?немецк", "de"),
    (r"по[-\s]?французск", "fr"),
    (r"по[-\s]?украинск", "uk"),
    (r"по[-\s]?белорусск", "be"),
    (r"по[-\s]?испанск", "es"),
    (r"скажи\s+по[-\s]?английск", "en"),
    (r"скажи\s+по[-\s]?французск", "fr"),
    (r"скажи\s+по[-\s]?немецк", "de"),
    (r"скажи\s+по[-\s]?испанск", "es"),
    (r"\benglish\s*:", "en"),
    (r"\bgerman\s*:", "de"),
    (r"\bfrench\s*:", "fr"),
    (r"\bspanish\s*:", "es"),
    (r"\bнемецк\w*\s*:", "de"),
    (r"\bфранцузск\w*\s*:", "fr"),
    (r"\bиспанск\w*\s*:", "es"),
    (r"\bанглийск\w*\s*:", "en"),
)


def is_translation_turn(user_text: str, *, brain_profile: str = "") -> bool:
    if (brain_profile or "").strip() == "translation":
        return True
    low = (user_text or "").lower()
    if any(t in low for t in _TRANSLATE_TRIGGERS):
        return True
    return bool(_LANG_INLINE_RE.search(user_text or ""))


def _detect_target_lang(low: str) -> Optional[str]:
    for pat, lang in _LANG_PATTERNS:
        if re.search(pat, low):
            return lang
    return None


def _extract_fragment(segment: str) -> str:
    raw = (segment or "").strip()
    if not raw:
        return ""
    m = re.search(r"['\"](.+?)['\"]", raw)
    if m:
        return m.group(1).strip()
    m2 = re.search(
        r"(?:переведи|translate|перевод|скажи)\s*(?:на\s+\w+|по[-\s]?\w+)?\s*:?\s*(.+)$",
        raw,
        re.IGNORECASE,
    )
    if m2:
        return m2.group(1).strip().strip("'\"")
    m3 = re.search(
        r"(?:скажи\s+)?по[-\s]?\w+\s*['\"](.+?)['\"]",
        raw,
        re.IGNORECASE,
    )
    if m3:
        return m3.group(1).strip()
    return raw


def _split_translation_segments(raw: str) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return []
    lines = re.split(r"[\n\r]+", text)
    segments: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^[·•\-]\s*", "", line).strip()
        if line:
            segments.append(line)
    return segments if len(segments) > 1 else [text]


def parse_translation_request(user_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Извлечь целевой язык и фрагмент для перевода.
    Возвращает (target_lang_hint, source_fragment).
    """
    reqs = parse_translation_requests(user_text)
    if not reqs:
        return None, None
    return reqs[0]


def parse_translation_requests(user_text: str) -> List[Tuple[Optional[str], str]]:
    """Несколько переводов в одном сообщении (строки / «·»)."""
    segments = _split_translation_segments(user_text)
    out: List[Tuple[Optional[str], str]] = []
    for seg in segments:
        low = seg.lower()
        tgt = _detect_target_lang(low)
        frag = _extract_fragment(seg)
        if frag:
            out.append((tgt, frag))
    return out


def translation_external_hint(user_text: str) -> str:
    reqs = parse_translation_requests(user_text)
    if not reqs:
        tgt, frag = parse_translation_request(user_text)
        reqs = [(tgt, frag)] if frag else []
    parts: List[str] = []
    for i, (tgt, frag) in enumerate(reqs, 1):
        lang_line = f"Целевой язык: {tgt}.\n" if tgt else ""
        frag_line = f"Текст: «{frag}».\n" if frag else ""
        parts.append(f"{i}. {lang_line}{frag_line}".strip())
    block = "\n".join(parts) if parts else ""
    return (
        "Режим перевода.\n"
        f"{block}\n"
        "Выведи **только** готовый перевод — без преамбулы, без «Примечание», "
        "без упоминания TOOL_CALL, инструментов и внутренних правил."
    )
