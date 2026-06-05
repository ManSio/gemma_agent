"""
Эвристики смены «канона» в диалоге: резкий поворот сюжета, разрыв отношений и т.п.
Используется, чтобы не цепляться за устаревший lookahead / память стратегии.
"""
from __future__ import annotations

import re
from typing import FrozenSet

# Глаголы/ситуации, после которых старые подсказки и прошлые «успешные стратегии» вредят.
_TWIST_SUBSTR: FrozenSet[str] = frozenset(
    {
        "развел",
        "развёл",
        "развод",
        "расстал",
        "разошл",
        "разошлась",
        "бросил меня",
        "бросила меня",
        "меня бросил",
        "меня бросила",
        "бросил её",
        "бросила его",
        "не вместе",
        "не пара",
        "конец отнош",
        "всё кончено",
        "все кончено",
        "расставан",
        "изменил мне",
        "изменила мне",
        "изменяет мне",
        "предал меня",
        "предала меня",
        "ушла от меня",
        "ушёл от меня",
        "ушел от меня",
        "ушла к другому",
        "ушёл к другой",
        "ушел к другой",
        "divorced",
        "broke up",
        "break up",
        "left me",
        "cheated on",
        "we're over",
        "we are over",
        "not together anymore",
    }
)

# «Сброс» ролевой линии: новый факт, который отменяет предыдущую договорённость.
_RESET_SUBSTR: FrozenSet[str] = frozenset(
    {
        "забудь про",
        "не было этого",
        "с нуля",
        "другая история",
        "новый сценарий",
        "переписываем",
        "откатываем",
        "retcon",
        "forget the previous",
        "start over",
        "new scenario",
    }
)


def plot_twist_likely(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if any(s in low for s in _RESET_SUBSTR):
        return True
    if any(s in low for s in _TWIST_SUBSTR):
        return True
    # «… быстро …» + (развод|расстал) уже покрыто подстроками; короткие удары вроде «всё, конец»
    if re.search(r"\bвсё\s*,\s*конец\b", low) or re.search(r"\bвсе\s*,\s*конец\b", low):
        return True
    return False
