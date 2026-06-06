"""Убрать meta-черновик reasoning loop из ответа пользователю."""
from __future__ import annotations

import re

_REASONING_META_LINE = re.compile(
    r"(?im)^\s*("
    r"мы\s+должны\s+дать|итоговый\s+ответ\s+пользователю|"
    r"первый\s+черновик|внутреннее\s+ревью|пользователь\s+дал\s+условие|"
    r"нужно\s+дать\s+четк|задача\s+[\"«].*[\"»]\s*:|"
    r"мы\s+находимся\s+в|нужно\s+ответить\s+на|вспомним:|"
    r"это\s+первое|второй\s+вопрос:|"
    r"draft\s+answer|final\s+answer\s+for\s+the\s+user"
    r").*$"
)


def strip_reasoning_meta_leak(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        if _REASONING_META_LINE.search(line):
            continue
        lines.append(line)
    out = "\n".join(lines).strip()
    if not out:
        return ""
    # Если после очистки осталась одна строка-meta
    if _REASONING_META_LINE.search(out):
        return ""
    return out
