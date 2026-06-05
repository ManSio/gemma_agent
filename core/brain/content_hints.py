"""Точечные подсказки в external_hint по типу запроса (без хардкода intent)."""
from __future__ import annotations

from typing import Tuple

_INTIMATE_MARKERS: Tuple[str, ...] = (
    "возбуд",
    "либид",
    "сексуал",
    "оргазм",
    "интим",
    "близост",
    "постель",
)

_POSTPARTUM_MARKERS: Tuple[str, ...] = (
    "рожал",
    "родил",
    "роды",
    "после род",
    "послерод",
    "после родов",
    "груднич",
    "кормлен",
    "лактац",
)


def intimate_health_education_hint(user_text: str) -> str:
    """Послеродовая близость / либидо — справочно, без отказа «не могу помочь»."""
    tl = (user_text or "").strip().lower()
    if not tl:
        return ""
    if not any(m in tl for m in _INTIMATE_MARKERS):
        return ""
    if not any(m in tl for m in _POSTPARTUM_MARKERS):
        return ""
    return (
        "Запрос про послеродовое восстановление и близость с партнёршей после родов. "
        "Отвечай справочно: физиология (гормоны, заживление, усталость), эмоции, "
        "нежность без давления, сроки возвращения интимности, когда к врачу/акушеру. "
        "Тон уважительный и по делу; не отказывайся фразой «не могу помочь» на безопасный образовательный вопрос."
    )
