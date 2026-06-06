"""Утечки JSON-схем инструментов и внутреннего контекста в ответ пользователю."""
from __future__ import annotations

import re


# Фрагмент описания Schedule / OpenAPI в тексте ответа (инцидент VPS 2026-05-23).
_TOOL_SCHEMA_LEAK_RE = re.compile(
    r'(?i)"description"\s*:\s*"[^"]{0,200}(?:xlsx|xls\b|расписан|пригород|suburban|электрич)'
    r'|"\s*Выдаёт\s+ссылки[^"]{0,300}(?:xlsx|xls\b|расписан|пригород)'
    r'|"\s*parameters\s*"\s*:\s*\{\s*"type"\s*:\s*"object"'
    r'|"enum"\s*:\s*\[\s*"Минск\s*-\s*'
    r'|Schedule\.suburban_rail_schedule_links'
    r'|Admin\.(?:CheckDatabase|ReadLogFile|Connect)'
    r'|^\s*[-*]?\s*tools\s*:\s*\['
    r'|"name"\s*:\s*"Admin\.'
)
_INTERNAL_PROMPT_CTX_RE = re.compile(
    r"(?i)(user_active_context|micro_emotion_style|self_diagnostic|blended_style|"
    r"style_hints|predictive_emotion|last_assistant_full|"
    r"verbosity['\"]?\s*:\s*['\"]concise)"
)
_JSON_CTX_BLOB_RE = re.compile(
    r"(?i)'\s*:\s*\{'verbosity'|\"\s*:\s*\{\"verbosity\""
)


def looks_like_tool_schema_leak(text: str) -> bool:
    s = (text or "").strip()
    if not s or len(s) < 24:
        return False
    if _TOOL_SCHEMA_LEAK_RE.search(s):
        return True
    if _INTERNAL_PROMPT_CTX_RE.search(s) and _JSON_CTX_BLOB_RE.search(s):
        return True
    if s.count('":') >= 6 and '"type"' in s and ("enum" in s or "properties" in s):
        if not re.search(r"(?m)^```|```$|^def\s+\w+", s):
            return True
    return False


def strip_tool_schema_leak(text: str) -> str:
    """Оставить только текст до первой явной утечки схемы/контекста."""
    s = (text or "").strip()
    if not s or not looks_like_tool_schema_leak(s):
        return s
    cut_at = len(s)
    for pat in (
        r'(?i)"description"\s*:\s*"',
        r'(?i)"\s*Выдаёт\s+ссылки',
        r'(?i)"parameters"\s*:\s*\{',
        r'(?i)"enum"\s*:\s*\[',
        r"(?i)user_active_context\s*:",
        r"(?i)'\s*:\s*\{'verbosity'",
    ):
        m = re.search(pat, s)
        if m:
            cut_at = min(cut_at, m.start())
    head = s[:cut_at].strip().rstrip('",')
    if not head or looks_like_tool_schema_leak(head):
        return ""
    if len(head) >= 40:
        return head
    return ""
