"""Финальная очистка текста перед отправкой пользователю в Telegram."""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Optional

from core.brain.cot_strip import strip_leaked_cot
from core.brain.text_helpers import strip_leaked_tool_call_markup, safe_text


logger = logging.getLogger(__name__)

def _looks_like_garbage_json(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    try:
        from core.brain.schema_leak_strip import looks_like_tool_schema_leak

        if looks_like_tool_schema_leak(s):
            return True
    except Exception as e:
        logger.debug("schema_leak check: %s", e)
    if re.search(r"(?i)сокращённо до \d+\s*символ", s):
        return True
    if re.search(r'"\.\.\."\s*\}\]|"\.\.\.".*\}\]', s):
        return True
    if '"role"' in s and '"content"' in s and len(s) < 1200:
        return True
    if s.startswith("{") and '"' in s and ":" in s:
        return True
    if s.startswith("[") and ("{" in s or (len(s) > 40 and not re.search(r"[а-яё]", s, re.I))):
        return True
    if s.count('":') >= 4 and "{" in s and len(s) < 900 and not re.search(r"(?i)[а-яё]{12,}", s):
        return True
    return False

# Утечки reasoning_layer / tool debug в ответ
_LEAK_LINE_RE = re.compile(
    r"(?im)^\s*("
    r"last_operation|last_tool_result|last_tool_ok|last_tool_error|"
    r"_text\s*=|reasoning\s*:|auto_reasoning"
    r")\b.*$"
)
_LEAK_INLINE_RE = re.compile(
    r"(?i)(last_operation\s*[=:]|last_tool_result\s*[=:]|_text\s*=\s*[\d.]+)"
)
# Мета-утечки: модель пересказывает инструкцию вместо перевода
_META_TOOL_NOTE_RE = re.compile(
    r"(?im)^\s*(примечание:.*tool_call|"
    r"для перевода.*не вызывай.*tool_call|"
    r"не вызывай никаких tool_call.*перевод|"
    r"available tools|системное сообщение|tool_names:|tools_full_index)"
    r".*$"
)
_PROMPT_LEAK_PARA_RE = re.compile(
    r"(?is)(available tools\s*\(|системное сообщение перед диалогом|"
    r"названия в русской локали|admin,\s*adu|"
    r"given the current context|current function call history|"
    r"правила безопасности:|operator_rules|blended_style_stable|"
    r'args_schema|"description"\s*:\s*|tools:\s*\{|'
    r"<rule\s+name=|priority\s*=\s*[\"']override|системный блок закончился|"
    r"<description>\s*запрещ|"
    r"^Rules:\s*\n?\s*<operator_rules)"
)
_COT_GARBAGE_LINE_RE = re.compile(
    r"(?im)^\s*("
    r"style:\s*|predictive:|internal notes:|last_assistant_full:|"
    r"user message text:|possible corrections:|"
    r'"description"\s*:|args_schema\s*:|'
    r"knowledgegraph\.query|booksrag\.get_answer"
    r")\b.*$"
)
# Модель пересказала инструкции промпта вместо ответа (часто на code_generation).
_INSTRUCTION_LEAK_SIGNALS = (
    re.compile(r"(?i)document_intake"),
    re.compile(r"(?i)file_context"),
    re.compile(r"(?i)text_layer_empty"),
    re.compile(r"(?i)access\s*=\s*denied"),
    re.compile(r"(?i)вызванн\w*\s+инструмент"),
    re.compile(r"(?i)опирайся\s+на\s+их\s+результат"),
    re.compile(r"(?i)пользователь\s+также\s+может\s+прикладывать"),
    re.compile(r"(?i)не\s+пытайся\s+выдать\s+пустые\s+данные"),
    re.compile(r"(?i)блок\s+context\s+ниже"),
    re.compile(r"(?i)external_hint"),
    re.compile(r"(?i)tool_routing_hint"),
    re.compile(r"(?i)agent_inst\s*:"),
    re.compile(r"(?i)<rule\s+name="),
    re.compile(r"(?i)системный блок закончился"),
    re.compile(r"(?i)priority\s*=\s*[\"']override"),
    re.compile(r"(?i)selfprogramming"),
    re.compile(r"(?i)self_programming"),
    re.compile(r"(?i)мы\s+в\s+режиме\s+code_generation"),
    re.compile(r"(?i)task_outline\s*:"),
    re.compile(r"(?i)^\s*[-*]?\s*task_outline\b"),
    re.compile(r"(?i)^\s*[-*]?\s*photo_count\s*:"),
    re.compile(r"(?i)photo_count\s*:\s*\d"),
)
# Модель пересказала формат TOOL_CALL / external_hint вместо ответа (часто code_generation).
_TOOL_INSTRUCTION_ECHO_RE = re.compile(
    r"(?i)(^|\n)\s*инструкция\s*:|"
    r"дай\s+строго\s+один\s+ответ|"
    r"один\s+valid\s+json|"
    r"без\s+обрамления\s+json|"
    r"не\s+придумывай\s+инструмент|"
    r"строго\s+формат\s+выше|"
    r"формат\s+выше\s+и\s+один"
)
_PERSONA_CONTRACT_LEAK_RE = re.compile(
    r"(?i)(?:^|[\s.])_length\s*:\s*['\"]?(?:short|medium|long)|"
    r"response_tone\s*:|_format\s*:\s*plain|normalize_user_facing|"
    r"(?:^|\n)\s*User message:\s*$"
)
_THINKING_BLOCK_RE = re.compile(r"(?is)<thinking\b[^>]*>.*?</thinking>\s*")
_RULE_BLOCK_RE = re.compile(r"(?is)<rule\b[^>]*>.*?</rule>\s*")
_NOW_ANSWER_USER_LINE_RE = re.compile(
    r"(?im)^\s*(теперь ответь пользователю|now answer the user)\b.*$\n?",
)


@lru_cache(maxsize=1)
def _env_leak_substrings() -> tuple[str, ...]:
    """FINALIZE_LEAK_STRIP_PATTERNS — подстроки через «;» (Claude: strip-set как данные, не хардкод)."""
    raw = (os.getenv("FINALIZE_LEAK_STRIP_PATTERNS") or "").strip()
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.split(";") if p.strip())


def _line_has_env_leak_pattern(line: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    low = line.lower()
    return any(p.lower() in low for p in patterns)


def looks_like_prompt_instruction_leak(text: str) -> bool:
    """Весь ответ — служебная инструкция, а не ответ пользователю."""
    s = (text or "").strip()
    if not s or len(s) > 2800:
        return False
    if _PERSONA_CONTRACT_LEAK_RE.search(s):
        return True
    if _TOOL_INSTRUCTION_ECHO_RE.search(s):
        if not re.search(r"(?m)^```|^def\s+\w+|^class\s+\w+", s):
            return True
    if re.search(r"(?i)TOOL_CALL", s) and not s.strip().startswith("TOOL_CALL:"):
        if re.search(
            r"(?i)(valid\s+json|не\s+придумывай\s+инструмент|один\s+ответ|"
            r"без\s+markdown|формат\s+выше)",
            s,
        ):
            return True
    hits = sum(1 for pat in _INSTRUCTION_LEAK_SIGNALS if pat.search(s))
    if hits >= 2:
        return True
    if hits == 1 and len(s) < 520:
        return True
    if re.search(r"(?i)document_intake", s) and not re.search(
        r"(?m)^```|```$|^def\s+\w+|^class\s+\w+", s
    ):
        return True
    return False


def finalize_user_reply(
    text: str,
    *,
    user_text: str = "",
    extra_markers_en: Optional[tuple] = None,
    extra_markers_ru: Optional[tuple] = None,
) -> str:
    """Единая точка: think-теги, CoT, tool markup, JSON-мусор, debug-утечки."""
    from core.brain.user_facing_contract import normalize_user_facing_text

    return normalize_user_facing_text(
        text,
        user_text=user_text,
        extra_markers_en=extra_markers_en,
        extra_markers_ru=extra_markers_ru,
    ).text


def _finalize_user_reply_legacy(
    text: str,
    *,
    user_text: str = "",
    extra_markers_en: Optional[tuple] = None,
    extra_markers_ru: Optional[tuple] = None,
) -> str:
    """Внутренняя реализация strip/leak (вызывается из user_facing_contract)."""
    s = safe_text(text)
    if not s:
        return ""
    s = _THINKING_BLOCK_RE.sub("", s)
    s = _RULE_BLOCK_RE.sub("", s)
    s = _NOW_ANSWER_USER_LINE_RE.sub("", s).strip()
    try:
        from core.brain.text_helpers import looks_like_leaked_tool_call_leak

        if looks_like_leaked_tool_call_leak(s):
            return ""
    except Exception as e:
        logger.debug("tool_call leak check: %s", e)
    s = strip_leaked_cot(
        s,
        extra_markers_en=extra_markers_en or (),
        extra_markers_ru=extra_markers_ru or (),
    )
    s = strip_leaked_tool_call_markup(s)
    try:
        from core.brain.schema_leak_strip import strip_tool_schema_leak

        s = strip_tool_schema_leak(s)
    except Exception as e:
        logger.debug("strip_tool_schema_leak: %s", e)
    if looks_like_prompt_instruction_leak(s):
        return ""
    if _PROMPT_LEAK_PARA_RE.search(s):
        parts = _PROMPT_LEAK_PARA_RE.split(s)
        s = parts[-1].strip() if parts else ""
    _env_pats = _env_leak_substrings()
    lines = []
    for line in s.split("\n"):
        if _line_has_env_leak_pattern(line, _env_pats):
            continue
        if _LEAK_LINE_RE.search(line):
            continue
        if _COT_GARBAGE_LINE_RE.search(line):
            continue
        if _META_TOOL_NOTE_RE.search(line):
            continue
        if _LEAK_INLINE_RE.search(line):
            line = _LEAK_INLINE_RE.sub("", line).strip()
        if line.strip():
            lines.append(line)
    s = "\n".join(lines).strip()
    if _looks_like_garbage_json(s):
        return ""
    try:
        from core.brain.translation_path import is_translation_turn, parse_translation_request
        from core.brain.translation_reply import sanitize_translation_reply

        if is_translation_turn(user_text):
            tgt, frag = parse_translation_request(user_text)
            cleaned = sanitize_translation_reply(
                s, user_text=user_text, target_lang=tgt, source_fragment=frag
            )
            if cleaned.strip():
                return cleaned
    except Exception as e:
        logger.debug('%s optional failed: %s', 'response_finalize', e, exc_info=True)
    try:
        from core.brain.text_helpers import task_fact_profile

        tf = task_fact_profile(user_text or "", {}, [])
        if tf.get("is_news"):
            from core.telegram_output_guard import trim_hallucinated_news_bullets

            s = trim_hallucinated_news_bullets(s)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'response_finalize', e, exc_info=True)
    return s
