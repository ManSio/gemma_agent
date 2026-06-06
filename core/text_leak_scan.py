"""
Сканирование текста на утечки промпта, внутренних меток, секретов.
Используется в agent_test, turn_chain_audit, scan_archive_leaks.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Промпт / XML / orchestrator
_PROMPT_LEAK_RE = re.compile(
    r"(?i)(<rule\s+name=|priority\s*=\s*[\"']override|системный блок закончился|"
    r"</rule>|<description>\s*запрещ|теперь ответь пользователю|tool_call:|TOOL_CALL\s*:|"
    r"available tools\s*\(|ArithmeticTool|Style:\s*|Tools:\s*|external_hint|"
    r"ephemeral_lessons|route_risk_hint|planner_reason|brain_first|"
    r"message_archive|recent_messages\s*:|operator_corrections|"
    r"SelfProgramming|self_programming|document_intake|file_context|"
    r"tool_routing_hint|blended_style_stable)",
)

# Ключи / токены (не полный secret scan — только явные паттерны)
_SECRET_RE = re.compile(
    r"(?i)(sk-or-[a-z0-9]{8,}|api[_-]?key\s*[:=]\s*['\"]?[a-z0-9]{12,}|"
    r"OPENROUTER_API_KEY|Bearer\s+sk-)",
)

# Внутренние env / пути хоста
_INTERNAL_RE = re.compile(
    r"(?i)(/opt/gemma_agent|GEMMA_PROJECT_ROOT|\.env\.example|"
    r"TELEGRAM_PIPELINE_|BRAIN_KV_PROFILE)",
)


def scan_text_leaks(text: str, *, role: str = "assistant") -> List[Dict[str, str]]:
    """Список находок: [{code, snippet}, ...]."""
    s = (text or "").strip()
    if not s:
        return []
    out: List[Dict[str, str]] = []
    for code, pat in (
        ("prompt_markup_leak", _PROMPT_LEAK_RE),
        ("secret_like", _SECRET_RE),
        ("internal_path_or_env", _INTERNAL_RE),
    ):
        m = pat.search(s)
        if m:
            start = max(0, m.start() - 20)
            out.append({"code": code, "snippet": s[start : m.end() + 40][:120]})

    try:
        from core.brain.response_finalize import looks_like_prompt_instruction_leak

        if looks_like_prompt_instruction_leak(s):
            out.append({"code": "instruction_leak", "snippet": s[:120]})
    except Exception:
        pass

    try:
        from core.brain.schema_leak_strip import looks_like_tool_schema_leak

        if looks_like_tool_schema_leak(s):
            out.append({"code": "tool_schema_leak", "snippet": s[:120]})
    except Exception:
        pass

    try:
        from core.brain.text_helpers import looks_like_tool_execution_report_leak, looks_like_tool_list_leak

        if looks_like_tool_execution_report_leak(s) or looks_like_tool_list_leak(s):
            out.append({"code": "tool_execution_report_leak", "snippet": s[:120]})
    except Exception:
        pass

    try:
        from core.brain.code_empty_recovery import looks_like_internal_code_monologue

        if looks_like_internal_code_monologue(s):
            out.append({"code": "internal_code_monologue", "snippet": s[:120]})
    except Exception:
        pass

    if role == "user" and _SECRET_RE.search(s):
        if not any(x["code"] == "secret_like" for x in out):
            out.append({"code": "secret_like", "snippet": s[:80]})
    return out


_BLOCKING_CODES = frozenset(
    {
        "secret_like",
        "instruction_leak",
        "tool_schema_leak",
        "tool_execution_report_leak",
        "prompt_markup_leak",
        "internal_path_or_env",
        "internal_code_monologue",
    }
)


def primary_blocking_leak_code(text: str, *, role: str = "assistant") -> Optional[str]:
    """Первый блокирующий код утечки (приоритет: секреты → instruction → schema)."""
    leaks = scan_text_leaks(text, role=role)
    if not leaks:
        return None
    codes = {str(x.get("code") or "") for x in leaks}
    for preferred in (
        "secret_like",
        "instruction_leak",
        "tool_schema_leak",
        "tool_execution_report_leak",
        "internal_code_monologue",
        "prompt_markup_leak",
        "internal_path_or_env",
    ):
        if preferred in codes:
            return preferred
    return str(leaks[0].get("code") or "") or None


def has_blocking_leak(text: str, *, role: str = "assistant") -> bool:
    return primary_blocking_leak_code(text, role=role) is not None


def outbound_has_blocking_leak(text: str, *, role: str = "assistant") -> bool:
    """Alias для pre_send / agent_test / turn_chain."""
    return has_blocking_leak(text, role=role)
