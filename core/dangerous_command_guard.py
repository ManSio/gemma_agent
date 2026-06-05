"""
Dangerous Command Guard — детектор опасных shell-команд с подтверждением.

Режимы (DANGEROUS_COMMAND_MODE):
  - off  : guard выключен (поведение как сейчас)
  - log  : логировать подозрительные вызовы, НЕ блокировать (default)
  - block: блокировать опасные инструменты, возвращать ошибку

Инструменты считаются опасными если:
  1. Их имя начинается с префикса из DANGEROUS_TOOL_PREFIXES (env, через запятую).
  2. ИЛИ аргументы содержат shell-метасимволы (; | ` $ () {} && ||).
  3. ИЛИ аргументы содержат чувствительные пути (см. _SENSITIVE_PATH_PATTERNS).

Для mode=block можно настроить allowlist:
  DANGEROUS_COMMAND_ALLOWLIST — список tool_name, разрешённых даже в block-режиме.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Префиксы инструментов, считающихся опасными по умолчанию ──
_DEFAULT_DANGEROUS_PREFIXES: List[str] = [
    "SelfDeployment.",
    "VoiceModule.",
]

# ── Паттерны shell-инъекции в аргументах ──
_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"[;|&$`]"),         # точка с запятой, пайп, фон, подстановка, backtick
    re.compile(r"\$\([^)]+\)"),     # $(...)
    re.compile(r"`[^`]+`"),         # backtick-инъекция
    re.compile(r"\{\s*[^{}]+\s*\}"),  # shell brace expansion (грубо)
]

# ── Чувствительные пути в аргументах ──
_SENSITIVE_PATH_PATTERNS: List[re.Pattern] = [
    re.compile(r"/etc/"),
    re.compile(r"/proc/"),
    re.compile(r"/sys/"),
    re.compile(r"~/.ssh"),
    re.compile(r"/root/"),
    re.compile(r"/var/log/auth"),
]

_DANGEROUS_TOOL_PREFIXES: Optional[List[str]] = None
_DANGEROUS_COMMAND_ALLOWLIST: Optional[set[str]] = None


def _get_dangerous_prefixes() -> List[str]:
    global _DANGEROUS_TOOL_PREFIXES
    if _DANGEROUS_TOOL_PREFIXES is not None:
        return _DANGEROUS_TOOL_PREFIXES
    raw = os.getenv("DANGEROUS_TOOL_PREFIXES", "").strip()
    if raw:
        _DANGEROUS_TOOL_PREFIXES = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        _DANGEROUS_TOOL_PREFIXES = list(_DEFAULT_DANGEROUS_PREFIXES)
    return _DANGEROUS_TOOL_PREFIXES


def _get_allowlist() -> set[str]:
    global _DANGEROUS_COMMAND_ALLOWLIST
    if _DANGEROUS_COMMAND_ALLOWLIST is not None:
        return _DANGEROUS_COMMAND_ALLOWLIST
    raw = os.getenv("DANGEROUS_COMMAND_ALLOWLIST", "").strip()
    if raw:
        _DANGEROUS_COMMAND_ALLOWLIST = {x.strip() for x in raw.split(",") if x.strip()}
    else:
        _DANGEROUS_COMMAND_ALLOWLIST = set()
    return _DANGEROUS_COMMAND_ALLOWLIST


def _guard_mode() -> str:
    raw = os.getenv("DANGEROUS_COMMAND_MODE", "log").strip().lower()
    return raw if raw in {"off", "log", "block"} else "log"


def _tool_is_dangerous_by_name(tool_name: str) -> bool:
    prefixes = _get_dangerous_prefixes()
    for p in prefixes:
        if tool_name.startswith(p):
            return True
    return False


def _args_have_injection(args_dict: Dict[str, Any]) -> Optional[str]:
    """Проверяет строковые аргументы на shell-инъекцию. Возвращает описание или None."""
    for key, val in args_dict.items():
        if not isinstance(val, str):
            continue
        for p in _INJECTION_PATTERNS:
            m = p.search(val)
            if m:
                return f"arg '{key}' содержит shell-метасимвол: {m.group()!r}"
    return None


def _args_have_sensitive_path(args_dict: Dict[str, Any]) -> Optional[str]:
    """Проверяет аргументы на чувствительные пути (только для опасных инструментов)."""
    for key, val in args_dict.items():
        if not isinstance(val, str):
            continue
        for p in _SENSITIVE_PATH_PATTERNS:
            if p.search(val.strip()):
                return f"arg '{key}' указывает на чувствительный путь: {val[:80]}"
    return None


def check_dangerous_tool_call(tool_name: str, kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Проверить вызов инструмента на опасность.

    Returns:
      None — безопасно (пропустить).
      dict с полями reason и (опционально) injection_detail — если опасность обнаружена.
    """
    mode = _guard_mode()
    if mode == "off":
        return None

    # Allowlist имеет приоритет
    if tool_name in _get_allowlist():
        return None

    # Проверка по имени
    if _tool_is_dangerous_by_name(tool_name):
        # Дополнительно: проверить аргументы на чувствительные пути
        sensitive = _args_have_sensitive_path(kwargs)
        if sensitive:
            logger.warning("[dangerous_command] sensitive path in %s: %s (mode=%s)", tool_name, sensitive, mode)
            if mode == "block":
                return {"reason": f"tool '{tool_name}' blocked: {sensitive}"}
            # log mode: warn but allow
        else:
            if mode == "block":
                return {"reason": f"tool '{tool_name}' is in dangerous prefixes and DANGEROUS_COMMAND_MODE=block"}
            logger.warning("[dangerous_command] blocked by name: %s (mode=%s)", tool_name, mode)
            logger.warning("[dangerous_command]   kwargs: %s", _truncate_kwargs(kwargs))
        if mode == "log":
            return None  # log mode: warn but allow
        return {"reason": f"tool '{tool_name}' blocked by dangerous guard"}

    # Проверка аргументов на инъекцию
    injection = _args_have_injection(kwargs)
    if injection:
        logger.warning("[dangerous_command] injection in %s: %s (mode=%s)", tool_name, injection, mode)
        logger.warning("[dangerous_command]   full kwargs: %s", _truncate_kwargs(kwargs))
        if mode == "log":
            return None
        return {"reason": f"tool '{tool_name}' blocked: {injection}"}

    return None


def _truncate_kwargs(kwargs: Dict[str, Any], max_len: int = 400) -> str:
    import json
    try:
        s = json.dumps(kwargs, ensure_ascii=False, default=str)
        if len(s) > max_len:
            s = s[: max_len - 20] + "...(truncated)"
        return s
    except Exception:
        return str(kwargs)[:max_len]
