"""User-message prompt-injection hardening before LLM calls."""
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from core.untrusted_content_sanitize import line_looks_like_prompt_injection

_FILTERED_LINE = "[сообщение отфильтровано: похоже на попытку подмены инструкций]"


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def guard_user_message(text: str) -> Tuple[str, Dict[str, Any]]:
    """
    Strip obvious injection lines from user text.
    Full jailbreak blocking stays in pipeline_early_guards (no LLM path).
    """
    meta: Dict[str, Any] = {"stripped_lines": 0, "enabled": False}
    if not _env_flag("PROMPT_INJECTION_GUARD_ENABLED", default=True):
        return text, meta
    if not (text or "").strip():
        return text, meta

    meta["enabled"] = True
    out_lines = []
    for line in text.splitlines():
        if line_looks_like_prompt_injection(line):
            out_lines.append(_FILTERED_LINE)
            meta["stripped_lines"] = int(meta["stripped_lines"]) + 1
        else:
            out_lines.append(line)
    return "\n".join(out_lines), meta
