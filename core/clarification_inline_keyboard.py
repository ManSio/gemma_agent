"""Inline-кнопки для уточнений (факты, гео/валюта) — сериализация в META telegram_inline_keyboard."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

# Короткие callback_data (лимит Telegram 64 байта UTF-8)
FACT_CFM_YES = "factcfm:y"
FACT_CFM_NO = "factcfm:n"


def merge_telegram_inline_rows(context: Dict[str, Any], rows: List[List[Dict[str, str]]]) -> None:
    """Добавить ряды клавиатуры в context['telegram_inline_keyboard']."""
    if not rows or not isinstance(context, dict):
        return
    from core.telegram_inline_meta import META_KEY

    cur = context.get(META_KEY)
    if isinstance(cur, list) and cur:
        context[META_KEY] = [list(r) for r in cur if isinstance(r, list)] + rows
    else:
        context[META_KEY] = rows


def fact_confirmation_keyboard_rows() -> List[List[Dict[str, str]]]:
    return [
        [
            {"text": "Да", "callback_data": FACT_CFM_YES},
            {"text": "Нет", "callback_data": FACT_CFM_NO},
        ]
    ]


def fact_auto_ask_keyboard_rows(missing: Sequence[str]) -> List[List[Dict[str, str]]]:
    """
    Быстрые ответы на auto_ask_missing (город/валюта/таймзона).
    Пользователь может по-прежнему ответить текстом.
    """
    if not missing:
        return []
    rows: List[List[Dict[str, str]]] = []
    # Берём первый приоритетный тип
    if "location" in missing:
        rows.append(
            [
                {"text": "Позже", "callback_data": "factask:sk:loc"},
                {"text": "Мск", "callback_data": "factask:tx:Москва"},
                {"text": "СПб", "callback_data": "factask:tx:Санкт-Петербург"},
            ]
        )
    elif "currency" in missing:
        rows.append(
            [
                {"text": "EUR", "callback_data": "factask:tx:EUR"},
                {"text": "USD", "callback_data": "factask:tx:USD"},
                {"text": "BYN", "callback_data": "factask:tx:BYN"},
            ]
        )
        rows.append([{"text": "Позже", "callback_data": "factask:sk:cur"}])
    elif "timezone" in missing:
        rows.append(
            [
                {"text": "UTC+3", "callback_data": "factask:tx:Europe/Moscow"},
                {"text": "Позже", "callback_data": "factask:sk:tz"},
            ]
        )
    elif "city" in missing:
        rows.append(
            [
                {"text": "Позже", "callback_data": "factask:sk:city"},
                {"text": "Мск", "callback_data": "factask:tx:Москва"},
            ]
        )
    return rows
