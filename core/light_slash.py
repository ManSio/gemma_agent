"""Разбор slash-команды для лёгких плагинов: /cmd@bot args."""

from __future__ import annotations


def parse_slash_args(payload: str) -> tuple[str, str]:
    """
    Возвращает (команда_без_слэша_и_бота, остаток_текста).
    Пример: '/radd@MyBot 30 купить' -> ('radd', '30 купить')
    """
    p = (payload or "").strip()
    if not p.startswith("/"):
        return "", p
    sp = p.split(maxsplit=1)
    head = sp[0].lstrip("/").split("@")[0].lower()
    tail = sp[1].strip() if len(sp) > 1 else ""
    return head, tail
