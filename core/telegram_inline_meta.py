"""Сборка InlineKeyboardMarkup из meta ответа (Output.meta)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

META_KEY = "telegram_inline_keyboard"


def inline_markup_from_meta(meta: Any) -> Optional[InlineKeyboardMarkup]:
    """
    meta['telegram_inline_keyboard']: [[{"text": "...", "callback_data": "..."}, ...], ...]
    """
    if not isinstance(meta, dict):
        return None
    raw = meta.get(META_KEY)
    if not isinstance(raw, list) or not raw:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    for row in raw:
        if not isinstance(row, list):
            continue
        btns: List[InlineKeyboardButton] = []
        for cell in row:
            if not isinstance(cell, dict):
                continue
            text = str(cell.get("text") or "").strip()[:64]
            cb = str(cell.get("callback_data") or "").strip()
            if not text or not cb or len(cb.encode("utf-8")) > 64:
                continue
            btns.append(InlineKeyboardButton(text=text, callback_data=cb))
        if btns:
            rows.append(btns)
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)
