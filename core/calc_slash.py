"""
Нормализация slash-команды калькулятора: в группах Telegram присылает /calc@username …
"""
from __future__ import annotations

from typing import Optional


def strip_calc_command(payload: str) -> Optional[str]:
    """
    Если первый токен — /calc или /calc@bot, вернуть остаток строки (выражение), может быть пустым.
    Иначе None (в т.ч. для /calculator, /calendar и т.п.).
    """
    s = (payload or "").strip()
    if not s.startswith("/"):
        return None
    parts = s.split(None, 1)
    first = parts[0]
    cmd = first.split("@", 1)[0].lower()
    if cmd != "/calc":
        return None
    return (parts[1] if len(parts) > 1 else "").strip()


def is_calc_slash_payload(payload: str) -> bool:
    """Сообщение начинается с команды /calc (с опциональным @ботом)."""
    return strip_calc_command(payload) is not None
