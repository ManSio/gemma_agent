"""
Извлечение арифметического выражения из естественного запроса («посчитай 2+2»).
Используется modules/math после маршрутизации intent=math.
"""
from __future__ import annotations

import re
from typing import Optional

from core.intent_heuristics import normalized_math_probe_scrub
from core.math_linear import text_looks_like_equation_solve

_LEADING_MATH_VERB_RE = re.compile(
    r"(?is)^(?:"
    r"посчитай|посчитать|посчита\w*|вычисли|вычислить|считай|посчитаем|"
    r"сколько\s+будет|реши(?:\s+уравн)?|реши\s+задач\w*|"
    r"calculate|computes?|solve\s+for|math\s*problem|"
    r"пример\s*:"
    r")[\s,:—\-]*"
)

_ARITH_CHARS_RE = re.compile(r"^[0-9+\-*/().%\^]+$")

_PERCENT_OF_RE = re.compile(
    r"(?i)(\d+(?:[.,]\d+)?)\s*(?:%|процент\w*|percent)\s*(?:от|of)\s*(\d+(?:[.,]\d+)?)"
)


def _normalize_expr_fragment(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "").strip().replace(",", "."))


def _looks_like_arith_expr(expr: str) -> bool:
    if not expr or len(expr) > 512:
        return False
    if not re.search(r"\d", expr):
        return False
    if not _ARITH_CHARS_RE.fullmatch(expr):
        return False
    if re.search(r"[\+\-\*/\^%]|//", expr):
        return True
    return bool(re.fullmatch(r"[0-9.]+", expr))


def extract_percent_of_expression(text: str) -> Optional[str]:
    """«15% от 2500» → (2500)*(15/100) для safe_eval_arithmetic."""
    raw = (text or "").strip()
    if not raw:
        return None
    m = _PERCENT_OF_RE.search(raw)
    if not m:
        return None
    pct = float(m.group(1).replace(",", "."))
    base = float(m.group(2).replace(",", "."))
    return f"({base})*({pct}/100)"


def extract_arithmetic_expression(text: str) -> Optional[str]:
    """
    Вернуть подстроку для safe_eval_arithmetic или None, если явной формулы нет.
    Не обрабатывает slash-команды — вызывающий код должен отделить /calc.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("/"):
        return None
    if text_looks_like_equation_solve(raw):
        return None
    pct_expr = extract_percent_of_expression(raw)
    if pct_expr:
        return pct_expr
    s = normalized_math_probe_scrub(raw)
    if not s:
        return None
    s = _LEADING_MATH_VERB_RE.sub("", s, count=1).strip()
    if not s:
        return None
    compact = _normalize_expr_fragment(s)
    if _looks_like_arith_expr(compact):
        return compact
    candidates = re.findall(r"[0-9\.\,\+\-\*/\(\)\^%]+", s)
    for cand in reversed(candidates):
        expr = _normalize_expr_fragment(cand)
        if _looks_like_arith_expr(expr):
            return expr
    return None
