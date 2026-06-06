"""
Разбор чисел из .env и строк API: узкие/обычные пробелы, разделители тысяч.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Типографские пробелы и минус, которые ломают float()/int() без нормализации.
_NUM_SPACE = (
    "\u202f",  # narrow no-break space
    "\u00a0",  # no-break space
    "\u2009",  # thin space
    "\u2007",  # figure space
    "\u2008",  # punctuation space
)


def normalize_numeric_string(raw: Any) -> str:
    s = str(raw).strip()
    for ch in _NUM_SPACE:
        s = s.replace(ch, "")
    s = s.replace("\u2212", "-").replace(" ", "").replace(",", "").replace("_", "")
    return s


def parse_loose_float(raw: Any, default: float) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    txt = normalize_numeric_string(raw)
    if not txt:
        return float(default)
    try:
        return float(txt)
    except ValueError:
        logger.warning("Invalid numeric %r, fallback to %s", raw, default)
        return float(default)


def parse_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    txt = normalize_numeric_string(raw)
    if not txt:
        return float(default)
    try:
        return float(txt)
    except ValueError:
        logger.warning("Invalid %s=%r, fallback to %s", name, raw, default)
        return float(default)


def parse_loose_int(raw: Any, default: int) -> int:
    if isinstance(raw, bool):
        return int(default)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    txt = normalize_numeric_string(raw)
    if not txt:
        return int(default)
    try:
        return int(txt)
    except ValueError:
        try:
            return int(float(txt))
        except ValueError:
            logger.warning("Invalid int %r, fallback to %s", raw, default)
            return int(default)


def parse_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    return parse_loose_int(raw, default)
