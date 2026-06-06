"""Разбор и решение линейных уравнений ax+b=c и ax+b=dx+e (x / х)."""
from __future__ import annotations

import re
from typing import Optional, Tuple

# Одна сторона: 2x+43, 21x, x+1, -3x, 15
_SIDE_RE = re.compile(
    r"^([+-]?\d*(?:\.\d+)?)\*?x([+-]\d+(?:\.\d+)?)?$|^([+-]?\d+(?:\.\d+)?)$"
)
# Уравнение внутри произвольного текста (обе стороны целиком)
_EQ_IN_TEXT_RE = re.compile(
    r"([+-]?\d*(?:\.\d+)?\*?x(?:[+-]\d+(?:\.\d+)?)?|[+-]?\d+(?:\.\d+)?)"
    r"="
    r"([+-]?\d*(?:\.\d+)?\*?x(?:[+-]\d+(?:\.\d+)?)?|[+-]?\d+(?:\.\d+)?)"
)


def text_looks_like_equation_solve(text: str) -> bool:
    """Запрос на решение уравнения, а не на калькулятор RHS."""
    raw = (text or "").strip()
    if not raw:
        return False
    try:
        from core.math_investment import text_looks_like_investment_annuity

        if text_looks_like_investment_annuity(raw):
            return False
    except Exception:
        pass
    low = raw.lower()
    if re.search(r"\bуравнен", low):
        return True
    compact = re.sub(r"\s+", "", low).replace(",", ".")
    if "=" not in compact:
        return False
    if re.search(r"[xх]", compact):
        return True
    return False


def _compact_equation_text(text: str) -> str:
    compact = re.sub(r"\s+", "", (text or "").lower().replace(",", "."))
    return compact.replace("х", "x")


def _coeff_from_raw(a_raw: str) -> float:
    if a_raw in {"", "+"}:
        return 1.0
    if a_raw == "-":
        return -1.0
    return float(a_raw)


def _parse_equation_side(side: str) -> Optional[Tuple[float, float]]:
    """Коэффициенты (a, b) для выражения ax + b на одной стороне."""
    side = (side or "").strip()
    if not side:
        return 0.0, 0.0
    m = _SIDE_RE.match(side)
    if not m:
        return None
    if m.group(3) is not None:
        return 0.0, float(m.group(3))
    a_raw, b_raw = m.group(1), m.group(2)
    a = _coeff_from_raw(a_raw)
    b = float(b_raw) if b_raw else 0.0
    return a, b


def _find_equation_in_compact(compact: str) -> Optional[Tuple[float, float, float, float, str]]:
    """(a1, b1, a2, b2, display) для a1*x+b1 = a2*x+b2."""
    for m in _EQ_IN_TEXT_RE.finditer(compact):
        lhs, rhs = m.group(1), m.group(2)
        p1 = _parse_equation_side(lhs)
        p2 = _parse_equation_side(rhs)
        if p1 is None or p2 is None:
            continue
        a1, b1 = p1
        a2, b2 = p2
        display = f"{lhs}={rhs}".replace("*", "")
        return a1, b1, a2, b2, display
    return None


def extract_linear_equation_abc(text: str) -> Optional[Tuple[float, float, float]]:
    """
    Вернуть (a, b, c) для ax + b = c (x только слева) или None.
    Поддерживает: 2x+5=15, -3x=12, x+1=0, 0.5x-2=3.
    """
    found = _find_equation_in_compact(_compact_equation_text(text))
    if found is None:
        return None
    a1, b1, a2, b2, _ = found
    if abs(a2) > 1e-12:
        return None
    return a1, b1, b2


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    s = f"{value:.6f}".rstrip("0").rstrip(".")
    return s


def try_solve_linear_equation(payload: str) -> Optional[str]:
    """Краткий ответ для math-модуля или None."""
    try:
        from core.math_investment import text_looks_like_investment_annuity

        if text_looks_like_investment_annuity(payload):
            return None
    except Exception:
        pass
    found = _find_equation_in_compact(_compact_equation_text(payload))
    if found is None:
        return None
    a1, b1, a2, b2, display = found
    a_net = a1 - a2
    b_net = b1 - b2
    if abs(a_net) < 1e-12:
        if abs(b_net) < 1e-12:
            return f"Уравнение: {display}. Верно при любом x."
        return (
            f"Уравнение: {display}. "
            "Коэффициент при x равен 0, свободные члены не совпадают — решений нет."
        )
    x = -b_net / a_net
    return f"x = {_format_number(x)}  ({display})"
