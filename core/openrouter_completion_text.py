"""
Извлечение текста из ответа POST /api/v1/chat/completions (OpenRouter).

Схема ответа нормализована под OpenAI Chat API; у отдельных провайдеров бывают
расширения (reasoning) и редкий вариант NonChatChoice с полем text на choice.
См. https://openrouter.ai/docs/api/reference/overview

По умолчанию поля reasoning / reasoning_content НЕ подставляются в пользовательский ответ,
если message.content непустой. Если content пустой (часто у DeepSeek через OpenRouter),
опционально подставляем укороченный reasoning: OPENROUTER_REASONING_FALLBACK_IF_EMPTY.
Полное раскрытие рассуждений: OPENROUTER_EXPOSE_REASONING=true.
"""
from __future__ import annotations

import os
import re
from typing import Any, List

_THINK_BLOCK = re.compile(r"(?is)<think>.*?</think>")
_THINK_ORPHAN_TAG = re.compile(r"(?is)<think>|</think>")


def _truthy_expose_reasoning() -> bool:
    from core.runtime_telegram_settings import effective_bool

    return effective_bool("OPENROUTER_EXPOSE_REASONING", default=False)


def _truthy(name: str, default: bool = False) -> bool:
    from core.runtime_telegram_settings import effective_bool

    return effective_bool(name, default=default)


def _reasoning_fallback_max_chars() -> int:
    try:
        return max(0, int((os.getenv("OPENROUTER_REASONING_FALLBACK_MAX_CHARS") or "12000").strip() or "12000"))
    except ValueError:
        return 12000


def _reasoning_fallback_models_skip() -> bool:
    """Не подставлять reasoning для моделей с огромным CoT."""
    return _truthy("OPENROUTER_REASONING_FALLBACK_SKIP_R1", True)


def _model_skips_reasoning_fallback(model_slug: str) -> bool:
    m = (model_slug or "").lower()
    if not _reasoning_fallback_models_skip():
        return False
    if "deepseek-r1" in m or "/r1" in m or m.endswith("r1"):
        return True
    return False


def _strip_think_noise(s: str) -> str:
    t = _THINK_BLOCK.sub("", s or "")
    t = _THINK_ORPHAN_TAG.sub("", t)
    return t.strip()


def _reasoning_looks_like_json_leak(s: str) -> bool:
    """CoT/reasoning с сырым JSON — не показывать пользователю."""
    t = (s or "").strip()
    if not t:
        return False
    if re.search(r'"\.\.\."\s*\}\]|"\.\.\.".*\}\]', t):
        return True
    if re.search(r"(?i)сокращённо до \d+\s*символ", t):
        return True
    if '"role"' in t and '"content"' in t:
        return True
    if t.startswith(("{", "[")) and t.count('":') >= 3:
        return True
    return False


def _placeholder_only_content(s: str) -> bool:
    """Разделители вроде -------- без текста — считаем пустым content."""
    t = (s or "").strip()
    if not t:
        return True
    if re.fullmatch(r"[-_=.\s│─—]+", t):
        return True
    return False


def user_facing_completion_text(choice: Any, *, requested_model: str = "") -> str:
    """
    Текст для пользователя: сначала обычный content; при пустом — укороченный reasoning
    (если включено и модель не в чёрном списке CoT).
    """
    primary = text_from_completion_choice(choice)
    if primary.strip() and not _placeholder_only_content(primary):
        return primary
    if _placeholder_only_content(primary):
        primary = ""
    if not _truthy("OPENROUTER_REASONING_FALLBACK_IF_EMPTY", True):
        return ""
    if _model_skips_reasoning_fallback(requested_model):
        return ""
    alt = text_from_completion_choice(choice, include_reasoning=True)
    if not alt.strip():
        return ""
    alt = _strip_think_noise(alt)
    if not alt.strip():
        return ""
    if _reasoning_looks_like_json_leak(alt):
        return ""
    try:
        from core.brain.code_empty_recovery import looks_like_internal_code_monologue

        if looks_like_internal_code_monologue(alt):
            return ""
    except Exception:
        pass
    cap = _reasoning_fallback_max_chars()
    if cap and len(alt) > cap:
        alt = alt[: cap - 1] + "…"
    if _reasoning_looks_like_json_leak(alt):
        return ""
    return alt.strip()


def _normalize_message_content(raw: Any) -> str:
    """message.content: строка или массив частей {type: text, text: ...}."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: List[str] = []
        for part in raw:
            if isinstance(part, str) and part.strip():
                parts.append(part.strip())
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    t = part.get("text")
                    if t is not None and str(t).strip():
                        parts.append(str(t).strip())
        return " ".join(parts).strip()
    return str(raw).strip()


def text_from_completion_choice(choice: Any, *, include_reasoning: bool = False) -> str:
    """
    Текст из одного элемента choices[] нестримингового ответа.
    Порядок: message.content (включая multipart), reasoning*, затем choice.text.

    include_reasoning=True — для служебных проверок (connectivity), где пустой content
    и только reasoning всё же означает «модель ответила».
    """
    if not isinstance(choice, dict):
        return ""
    msg = choice.get("message")
    if isinstance(msg, dict):
        content = _normalize_message_content(msg.get("content"))
        if content:
            return content
        if include_reasoning or _truthy_expose_reasoning():
            for key in ("reasoning", "reasoning_content"):
                alt = msg.get(key)
                if alt is not None and str(alt).strip():
                    return str(alt).strip()
    raw_text = choice.get("text")
    if raw_text is not None and str(raw_text).strip():
        return str(raw_text).strip()
    return ""
