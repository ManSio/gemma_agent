"""Детерминированный расчёт: вклад + ежемесячные пополнения + сложный процент + налог с прибыли."""
from __future__ import annotations

import re
from typing import Optional, Tuple

from core.regex_safe import cap_regex_input, safe_re_match, safe_re_search


def text_looks_like_investment_annuity(text: str) -> bool:
    """Задача на вклад с пополнениями и годовой доходностью — не линейное уравнение."""
    raw = cap_regex_input((text or "").strip(), max_len=4096)
    if len(raw) < 80:
        return False
    low = raw.lower()
    keys = (
        "инвестир",
        "вклад",
        "пополнен",
        "капитализац",
        "сложн",
        "доходност",
        "годовых",
        "ежемесяч",
    )
    if sum(1 for k in keys if k in low) < 2:
        return False
    if not safe_re_search(r"\d+\s*%\s*годов|\d+\s*%\s*годовых|годов\w*\s*\d+\s*%", low, max_len=4096):
        return False
    if not safe_re_search(r"пополнен|добавля\w+\s+ещ", low, max_len=4096):
        return False
    if not safe_re_search(r"(?:\d+\s*(?:лет|год))|(?:лет|год)\s*\d+", low, max_len=4096):
        return False
    return True


def _num(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _extract_money_byn(text: str, *, around: str) -> Optional[float]:
    low = text.lower()
    idx = low.find(around)
    chunk = text[max(0, idx - 40) : idx + 120] if idx >= 0 else text
    m = re.search(
        r"(\d[\d\s]{0,12}(?:[.,]\d+)?)\s*(?:byn|бел\.?\s*руб|руб(?:\.|лей)?)?",
        chunk,
        re.I,
    )
    if m:
        return _num(m.group(1))
    return None


def _parse_investment_params(text: str) -> Optional[Tuple[float, float, float, int, float]]:
    low = (text or "").lower()
    initial = None
    m0 = re.search(
        r"(?:инвестир\w*|влож\w*|старт|0[-\s]*й\s+месяц)[^\d]{0,40}(\d[\d\s]{2,10}(?:[.,]\d+)?)\s*(?:byn|бел)",
        low,
        re.I,
    )
    if m0:
        initial = _num(m0.group(1))
    if initial is None:
        m0b = re.search(r"(\d[\d\s]{3,10}(?:[.,]\d+)?)\s*byn", text, re.I)
        if m0b:
            initial = _num(m0b.group(1))

    pmt = None
    mp = re.search(
        r"(?:каждый\s+месяц|ежемесяч\w*|пополнен\w*)[^\d]{0,30}(\d[\d\s]{1,8}(?:[.,]\d+)?)\s*(?:byn|бел)",
        text,
        re.I,
    )
    if mp:
        pmt = _num(mp.group(1))
    if pmt is None:
        mp2 = re.search(r"\+?\s*(\d[\d\s]{2,6}(?:[.,]\d+)?)\s*byn", text, re.I)
        if mp2:
            pmt = _num(mp2.group(1))

    annual = None
    ma = re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*годов", low)
    if ma:
        annual = _num(ma.group(1))
    if annual is not None:
        annual /= 100.0

    years = None
    my = re.search(r"(\d+)\s*(?:лет|год)", low)
    if my:
        years = int(my.group(1))
    if years is None:
        my2 = re.search(r"через\s+(\d+)\s*(?:лет|год)", low)
        if my2:
            years = int(my2.group(1))

    tax = 0.13
    mt = re.search(r"налог\w*[^\d]{0,20}(\d+(?:[.,]\d+)?)\s*%", low)
    if mt:
        tv = _num(mt.group(1))
        if tv is not None:
            tax = tv / 100.0

    if initial is None or pmt is None or annual is None or not years or years < 1:
        return None
    return initial, pmt, annual, years, tax


def _fmt_money(x: float) -> str:
    if abs(x - round(x)) < 0.005:
        return f"{int(round(x)):,}".replace(",", " ")
    return f"{x:,.2f}".replace(",", " ")


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def try_solve_investment_annuity(text: str) -> Optional[str]:
    """
    Вклад + пополнения в начале месяца, месячная капитализация, налог с прибыли.
    """
    if not text_looks_like_investment_annuity(text):
        return None
    parsed = _parse_investment_params(text)
    if not parsed:
        return None
    initial, pmt, annual, years, tax = parsed
    n = years * 12
    r = annual / 12.0
    if r <= 0:
        return None
    growth = (1.0 + r) ** n
    fv = initial * growth + pmt * ((growth - 1.0) / r) * (1.0 + r)
    contributed = initial + pmt * n
    profit = fv - contributed
    tax_amt = max(0.0, profit * tax)
    net_profit = profit - tax_amt
    after_tax = fv - tax_amt
    if contributed > 0 and after_tax > 0:
        eff_annual = (after_tax / contributed) ** (1.0 / years) - 1.0
    else:
        eff_annual = 0.0

    lines = [
        "Расчёт (ежемесячная ставка {:.2f}%, пополнение в начале месяца, {} мес.):".format(
            r * 100, n
        ),
        "",
        "1. Сумма на счету через {} лет (до налога): **{} BYN**.".format(
            years, _fmt_money(fv)
        ),
        "2. Чистая прибыль после налога {}%: **{} BYN** "
        "(прибыль {} BYN, налог {} BYN).".format(
            int(round(tax * 100)),
            _fmt_money(net_profit),
            _fmt_money(profit),
            _fmt_money(tax_amt),
        ),
        "3. Эффективная годовая доходность (с пополнениями и налогом): **{}**.".format(
            _fmt_pct(eff_annual)
        ),
        "",
        "Внесено всего: {} BYN (старт {} + {} × {}).".format(
            _fmt_money(contributed),
            _fmt_money(initial),
            n,
            _fmt_money(pmt),
        ),
        "На счету после налога: {} BYN.".format(_fmt_money(after_tax)),
    ]
    return "\n".join(lines)
