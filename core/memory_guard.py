"""
Memory-safety guard 2.0: prevents persisting inferred facts without
explicit user confirmation. Blocks auto-save of sensitive fields
(country, city, age, birth_date, interests, occupation) unless
source == "user_input" and confirmed is True.

Logs attempted writes of inferred facts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

MEMORY_GUARD_VERSION = "2.0.0"

SENSITIVE_FIELDS: Set[str] = {
    "country",
    "city",
    "age",
    "birth_date",
    "interests",
    "occupation",
}

_error_counter: Dict[str, int] = {}
AUTO_RESET_THRESHOLD = 3


def can_persist_user_fact(source: str, confirmed: bool) -> bool:
    """Only persist user facts that were explicitly stated by the user
    AND explicitly confirmed.
    Requires source == "user_input" and confirmed is True."""
    if not confirmed:
        return False
    if source != "user_input":
        return False
    return True


def can_persist_sensitive(field: str, source: str, confirmed: bool) -> bool:
    """Guard for sensitive fields: country, city, age, birth_date, interests, occupation.
    Requires source == "user_input" and confirmed is True."""
    if field not in SENSITIVE_FIELDS:
        return True
    return source == "user_input" and confirmed is True


def log_inferred_attempt(field: str, value: Any, source: str) -> None:
    """Log a rejected attempt to persist an inferred fact."""
    logger.info(
        "memory_guard: blocked inferred fact field=%s value=%s source=%s",
        field, value, source,
    )


def record_error(category: str) -> int:
    """Increment error counter for a category. Returns current count."""
    _error_counter[category] = _error_counter.get(category, 0) + 1
    return _error_counter[category]


def should_auto_reset() -> bool:
    """Return True if 3+ memory-guard errors occurred consecutively,
    triggering context reset."""
    total = sum(_error_counter.values())
    if total >= AUTO_RESET_THRESHOLD:
        _error_counter.clear()
        logger.warning("memory_guard: auto-reset triggered after %d+ consecutive errors", total)
        return True
    return False


def reset_error_counters() -> None:
    _error_counter.clear()
