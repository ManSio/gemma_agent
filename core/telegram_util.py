from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import os
import re
from typing import Any, List, Optional

import bleach
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

logger = logging.getLogger(__name__)

# Telegram HTML parse mode supported tags
TELEGRAM_ALLOWED_TAGS = [
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre",
    "a",
    "tg-spoiler", "span",
    "tg-emoji",
]
TELEGRAM_ALLOWED_ATTRS = {
    "a": ["href"],
    "span": ["class"],
    "tg-emoji": ["emoji-id"],
}


def sanitize_html(text: str) -> str:
    """Remove problematic tags (<i>, <blockquote>) and clean invalid HTML before sending to Telegram."""
    text = re.sub(r"</?i[^>]*>", "", text)
    text = re.sub(r"</?blockquote[^>]*>", "", text)
    allowed_tags = ["b", "strong", "u", "code", "pre", "a"]
    return bleach.clean(text, tags=allowed_tags, strip=True)


def _sanitize_telegram_html(text: str) -> str:
    """Strip unsupported tags (blockquote) and clean invalid HTML before sending to Telegram."""
    return sanitize_html(text)


def _is_stale_callback_query_error(exc: BaseException) -> bool:
    em = str(exc).lower()
    return (
        "query is too old" in em
        or "response timeout expired" in em
        or "query id is invalid" in em
    )


async def safe_callback_answer(callback: Any, *args: Any, **kwargs: Any) -> bool:
    """
    answerCallbackQuery без падения на просроченном query (рестарт бота, >~10 с до ответа).
    Возвращает False, если Telegram отклонил ответ.
    """
    try:
        await callback.answer(*args, **kwargs)
        return True
    except TelegramBadRequest as e:
        if _is_stale_callback_query_error(e):
            logger.debug("callback.answer skipped (stale or invalid query): %s", e)
            return False
        raise

# Запас до лимита Telegram 4096
DEFAULT_CHUNK = 4000

_TELEGRAM_SEND_RETRIES = max(1, int(os.getenv("TELEGRAM_SEND_RETRIES", "3")))


def _telegram_send_retry_base_sec() -> float:
    from core.number_parse import parse_env_float

    return parse_env_float("TELEGRAM_SEND_RETRY_BASE_SEC", 1.0)


def soft_truncate_plain(text: str, max_len: int) -> str:
    """
    Усечь plain-текст до max_len символов, по возможности не рвать слово (как _clip_soft в prompt_pack).
    """
    if max_len <= 0:
        return ""
    t = text or ""
    if len(t) <= max_len:
        return t
    suffix = "…"
    suf_len = len(suffix)
    max_body = max_len - suf_len
    if max_body < 8:
        return t[:max(0, max_body)] + suffix
    prefix = t[:max_body]
    min_keep = max(8, min(max_body // 3, 400))
    min_keep = min(min_keep, max_body)
    sp = prefix.rfind(" ")
    if sp >= min_keep:
        return prefix[:sp].rstrip() + suffix
    return prefix.rstrip() + suffix


def _html_to_plain_fallback(text: str) -> str:
    """Если Telegram отклонил HTML — убираем теги и entity-проблемы."""
    plain = re.sub(r"<[^>]+>", "", text or "")
    plain = html_module.unescape(plain).strip()
    out = soft_truncate_plain(plain, 4090) if plain else ""
    return out or "Ответ не удалось отформатировать."


async def answer_with_retry(
    message: Any,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    attempts: int = _TELEGRAM_SEND_RETRIES,
    **answer_kwargs: Any,
) -> None:
    """Отправка ответа с повторами при сетевых таймаутах (медленный канал / api.telegram.org)."""
    kwargs_send: dict[str, Any] = dict(answer_kwargs)
    if parse_mode is not None:
        kwargs_send["parse_mode"] = parse_mode
    effective_pm = kwargs_send.get("parse_mode")
    # Sanitize HTML before sending (strip blockquote, clean invalid tags)
    if effective_pm == "HTML":
        text = _sanitize_telegram_html(text)

    delay = _telegram_send_retry_base_sec()
    last: Exception | None = None
    for i in range(attempts):
        try:
            await message.answer(text, **kwargs_send)
            return
        except TelegramBadRequest as e:
            err_s = str(e).lower()
            logger.warning("telegram bad request (parse_mode=%s): %s", effective_pm, e)
            # "message to be replied not found" — удалённое/устаревшее сообщение, не рейзим
            if "message to be replied not found" in err_s:
                return
            # Сбой разметки (blockquote, незакрытые теги) или любой HTML-режим — plain fallback.
            try_plain = bool(
                effective_pm
                or "entity" in err_s
                or "entities" in err_s
                or "parse" in err_s
                or "blockquote" in err_s
                or "end tag" in err_s
            )
            if try_plain:
                try:
                    fb = dict(answer_kwargs)
                    fb.pop("parse_mode", None)
                    await message.answer(_html_to_plain_fallback(text), **fb)
                    return
                except TelegramBadRequest as e2:
                    logger.warning("telegram fallback plain also failed: %s", e2)
            raise
        except TelegramNetworkError as e:
            last = e
            logger.warning(
                "telegram answer failed (%s/%s): %s",
                i + 1,
                attempts,
                e,
            )
            if i + 1 < attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 10.0)
    if last:
        raise last


def chunk_text(text: str, limit: int = DEFAULT_CHUNK) -> List[str]:
    """
    Делит текст на части ≤ limit для Telegram. Старается рвать по \\n\\n / \\n / пробелу,
    а не посередине слова (как фиксированные шаги по limit).
    """
    if limit < 500:
        limit = 500
    n = len(text)
    if n <= limit:
        return [text]

    max_hdr = 40  # «… часть N/M⏎» — верхняя оценка длины префикса
    bodies: List[str] = []
    pos = 0

    while pos < n:
        first = not bodies
        room = limit if first else max(80, limit - max_hdr)
        room = min(room, n - pos)
        end = min(pos + room, n)
        if end >= n:
            bodies.append(text[pos:n])
            break

        window = text[pos:end]
        min_keep = max(24, len(window) // 12)
        cut_rel = len(window)
        for sep in ("\n\n", "\n", "\r"):
            ix = window.rfind(sep)
            if ix >= min_keep:
                cut_rel = ix + len(sep)
                break
        else:
            sp = window.rfind(" ")
            if sp >= min_keep:
                cut_rel = sp + 1

        if cut_rel <= 0:
            cut_rel = len(window)
        piece = text[pos : pos + cut_rel]
        if not piece:
            piece = text[pos : pos + 1]
        bodies.append(piece)
        pos += len(piece)

    total = len(bodies)
    if total <= 1:
        return bodies
    out: List[str] = [bodies[0]]
    for i in range(1, total):
        out.append(f"… часть {i + 1}/{total}\n{bodies[i]}")
    return out


async def reply_text_chunks(
    message: Any,
    text: str,
    *,
    limit: int = DEFAULT_CHUNK,
    reply_markup: Any = None,
    **answer_kwargs: Any,
) -> None:
    """Несколько сообщений для длинного plain-текста (лимит Telegram 4096). reply_markup — только на последней части."""
    parts = chunk_text(text, limit)
    last_i = len(parts) - 1
    for i, part in enumerate(parts):
        kw = dict(answer_kwargs)
        if reply_markup is not None and i == last_i:
            kw["reply_markup"] = reply_markup
        await answer_with_retry(message, part, **kw)


async def reply_html_chunks(
    message: Any,
    text: str,
    *,
    limit: int = 4000,
    reply_markup: Any = None,
) -> None:
    from core.telegram_ui import split_html_message

    parts = split_html_message(text, limit=limit)
    last_i = len(parts) - 1
    for i, part in enumerate(parts):
        kw: dict[str, Any] = {}
        if reply_markup is not None and i == last_i:
            kw["reply_markup"] = reply_markup
        await answer_with_retry(message, part, parse_mode="HTML", **kw)


async def reply_code_plain_chunks(
    message: Any,
    plain: str,
    *,
    limit: int = DEFAULT_CHUNK,
) -> None:
    """Длинный текст: несколько сообщений, каждое — один блок кода (<pre>), как reply_json_chunks."""
    from core.telegram_ui import code_block_html

    inner = max(400, min(limit, 3900) - 20)
    for part in chunk_text(plain, limit=inner):
        await answer_with_retry(message, code_block_html(part), parse_mode="HTML")


async def reply_json_chunks(
    message: Any,
    data: Any,
    *,
    limit: int = DEFAULT_CHUNK,
    ensure_ascii: bool = False,
    indent: int = 2,
) -> None:
    """JSON в Telegram — блок кода (<pre>), аналог ``` … ```."""
    from core.telegram_ui import code_block_html

    raw = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent, default=str)
    inner = max(400, min(limit, 3900) - 20)
    for part in chunk_text(raw, limit=inner):
        await answer_with_retry(message, code_block_html(part), parse_mode="HTML")
