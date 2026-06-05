"""Детерминированная арифметика «к тому числу +7» по последнему ответу бота."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, List, Optional, Sequence, Tuple

from core.arithmetic_tool_module import safe_eval_arithmetic

logger = logging.getLogger(__name__)

_REF_MATH_RE = re.compile(
    r"(?i)(?:к\s+)?(?:тому\s+)?(?:числ|ответу|результату|значению)"
    r"|(?:прибав|приплюс|добав|плюсуй|убав|вычт|отним|умнож|раздел|подел)"
)
_ONLY_NUMBER_RE = re.compile(r"(?i)только\s+числ|only\s+(?:a\s+)?number")
_ADD_RE = re.compile(r"(?i)(?:прибав(?:ь|ить)?|приплюс(?:ь|уй)?|добав(?:ь|ить)?|плюс(?:уй)?)\s*(\d+(?:[.,]\d+)?)")
_SUB_RE = re.compile(r"(?i)(?:убав(?:ь|ить)?|вычт(?:и|ь)|отним(?:и|ь)?|минус)\s*(\d+(?:[.,]\d+)?)")
_MUL_RE = re.compile(r"(?i)(?:умнож(?:ь|ить)?|×|x)\s*(\d+(?:[.,]\d+)?)")
_DIV_RE = re.compile(r"(?i)(?:раздел(?:и|ить)?|подел(?:и|ить)?|÷|/)\s*(\d+(?:[.,]\d+)?)")


def _env_truthy(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def referential_math_enabled() -> bool:
    return _env_truthy("REFERENTIAL_MATH_REPLY_ENABLED", default=True)


def looks_like_referential_math_request(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8 or len(t) > 220:
        return False
    return bool(_REF_MATH_RE.search(t))


def _parse_operand(text: str) -> Tuple[Optional[str], Optional[float]]:
    """op: add|sub|mul|div, operand."""
    t = text or ""
    for rx, op in ((_ADD_RE, "add"), (_SUB_RE, "sub"), (_MUL_RE, "mul"), (_DIV_RE, "div")):
        m = rx.search(t)
        if m:
            try:
                return op, float(m.group(1).replace(",", "."))
            except ValueError:
                return op, None
    return None, None


_CLARIFY_ANCHOR_RE = re.compile(
    r"(?i)(укажите\s+числ|какое\s+число|не\s+понял|уточните|повторите\s+числ)"
)


def _assistant_anchor_text(text: str) -> bool:
    t = (text or "").strip()
    if not t or _CLARIFY_ANCHOR_RE.search(t):
        return False
    return extract_anchor_number(t) is not None


def _last_assistant_text(recent_dialogue: Any) -> str:
    """Последняя реплика бота с числом-якорем (не clarify «укажите число»)."""
    if not recent_dialogue:
        return ""
    rows = list(recent_dialogue) if isinstance(recent_dialogue, (list, tuple)) else []
    candidates: List[str] = []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in ("assistant", "bot"):
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if not text:
            continue
        candidates.append(text)
        if _assistant_anchor_text(text):
            return text
    return candidates[0] if candidates else ""


def extract_anchor_number(assistant_text: str) -> Optional[float]:
    """Число из «только число» ответа или последнее число в короткой реплике."""
    t = (assistant_text or "").strip()
    if not t:
        return None
    if len(t) <= 32:
        m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*[\.\!]?\s*", t)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                pass
    nums = re.findall(r"\d+(?:[.,]\d+)?", t)
    if not nums:
        return None
    try:
        return float(nums[-1].replace(",", "."))
    except ValueError:
        return None


def _format_number(value: float, *, only_number: bool) -> str:
    if abs(value - round(value)) < 1e-9:
        s = str(int(round(value)))
    else:
        s = f"{value:g}"
    if only_number:
        return s
    return s


def try_referential_math_reply(
    user_text: str,
    *,
    recent_dialogue: Any = None,
) -> Optional[str]:
    if not referential_math_enabled():
        return None
    text = (user_text or "").strip()
    if not looks_like_referential_math_request(text):
        return None
    op, operand = _parse_operand(text)
    if not op or operand is None:
        return None
    base = extract_anchor_number(_last_assistant_text(recent_dialogue))
    if base is None:
        return None
    try:
        if op == "add":
            result = safe_eval_arithmetic(f"{base}+{operand}")
        elif op == "sub":
            result = safe_eval_arithmetic(f"{base}-{operand}")
        elif op == "mul":
            result = safe_eval_arithmetic(f"{base}*{operand}")
        else:
            if operand == 0:
                return None
            result = safe_eval_arithmetic(f"{base}/{operand}")
    except Exception as e:
        logger.debug("referential_math eval: %s", e)
        return None
    only_number = bool(_ONLY_NUMBER_RE.search(text))
    out = _format_number(float(result), only_number=only_number)
    logger.info(
        "referential_math_reply base=%s op=%s operand=%s -> %s",
        base,
        op,
        operand,
        out,
    )
    return out


def try_referential_math_reply_sync(
    user_text: str,
    *,
    recent_dialogue: Any = None,
) -> Optional[str]:
    return try_referential_math_reply(user_text, recent_dialogue=recent_dialogue)
