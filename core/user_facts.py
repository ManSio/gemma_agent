"""User Facts Engine: extraction, strict validation, confirmation, expiration.
CIS country extractor (extractor_country): supports all cases, abbreviations,
ISO codes, /set_country, natural language forms for 11 CIS countries."""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.monitoring import MONITOR
from core.timezone_inference import infer_timezone_from_facts, looks_like_wall_clock_question
from core.context_binding import can_persist_user_fact

# Явные коды/символы в тексте — профильная валюта для API не обязательна (сценарии, самодостаточные суммы).
_EXPLICIT_FX_IN_MESSAGE_RE = re.compile(
    r"\b(?:usd|eur|rub|kzt|uah|gbp|cny|jpy|try|pln|chf|aed|byn)\b",
    re.IGNORECASE,
)
_MONEY_AMOUNT_HINT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:"
    r"usd|eur|rub|kzt|uah|gbp|cny|jpy|try|pln|chf|aed|byn|"
    r"рубл|руб\.?|долл|евро|тенге|гривн|фунт|юан|йен|злот|франк"
    r"|£|\$|€|₽|₸|₴)\b",
    re.IGNORECASE,
)
# Сумма от 1000, исключая годы 20xx (часто в датах).
_LARGE_AMOUNT_NOT_YEAR_RE = re.compile(r"\b(?!20[12]\d{2}\b)\d{4,}\b")

# Фразы из школьной математики/логики — не имена населённых пунктов (ложные срабатывания «город …»).
_CITY_MATH_CONTAMINATION_RE = re.compile(
    r"(?i)неравенств|уравнен|производн|интеграл|логарифм|"
    r"теорем|доказат|координат|график\w*|вектор\w*|матриц|"
    r"последовательн|предел\b|ряд\b|ряда\b|\bчастн\w*\s+производн",
)
# Финансовый/инвестиционный контекст — не населённый пункт («портфеля из акций» и т.п.).
_CITY_FINANCE_CONTAMINATION_RE = re.compile(
    r"(?i)акци\w*|облигац|портфел|диверсиф|инвест|etf|брокер|"
    r"дивиденд|капитал|бирж|ценн\w*\s+бумаг",
)


logger = logging.getLogger(__name__)

def _message_has_explicit_fx_context(text: str, low: str) -> bool:
    if _EXPLICIT_FX_IN_MESSAGE_RE.search(low):
        return True
    return any(sym in text for sym in ("₽", "$", "€", "£", "¥"))


def _looks_like_currency_conversion_task(text: str, low: str) -> bool:
    """
    Задача на курс/конвертацию — не мета-разбор («структура», «дискурс», проценты) и не только слово «валюта».
    """
    if _MONEY_AMOUNT_HINT_RE.search(low):
        return True
    if re.search(r"\b(?:конверт|convert)\w*", low, re.IGNORECASE):
        return True
    if re.search(r"\b(?:exchange|currency)\b", low, re.IGNORECASE):
        return True
    if re.search(r"\brate\b", low, re.IGNORECASE):
        return True
    # «курс/курса/…», «обмен/обменник/…» — только как морфема в начале слова (не «дискурс»).
    if re.search(r"\bкурс", low):
        return True
    if re.search(r"\bобмен", low):
        return True
    if "валют" in low:
        if _message_has_explicit_fx_context(text, low):
            return True
        if _MONEY_AMOUNT_HINT_RE.search(low):
            return True
        if _LARGE_AMOUNT_NOT_YEAR_RE.search(low):
            return True
    return False


FACT_FIELDS = {
    "name",
    "age",
    "city",
    "country",
    "timezone",
    "language",
    "currency",
    "interests",
    "occupation",
    "pet_cat",
    "pet_dog",
}

ISO_LANG = {
    "ru", "en", "de", "fr", "es", "it", "pt", "uk", "pl", "tr", "kk", "uz", "zh", "ja", "ko"
}
ISO_CUR = {
    "USD", "EUR", "RUB", "KZT", "UAH", "GBP", "CNY", "JPY", "TRY", "PLN", "CHF", "AED"
}
COUNTRY_TO_CURRENCY = {
    "russia": "RUB",
    "kazakhstan": "KZT",
    "ukraine": "UAH",
    "germany": "EUR",
    "france": "EUR",
    "italy": "EUR",
    "spain": "EUR",
    "poland": "PLN",
    "switzerland": "CHF",
    "united kingdom": "GBP",
    "uk": "GBP",
    "usa": "USD",
    "united states": "USD",
    "turkey": "TRY",
    "japan": "JPY",
    "china": "CNY",
    "uae": "AED",
    "belarus": "BYN",
    "беларусь": "BYN",
}

# ---- СНГ: канонический словарь + lookup-формы (все падежи, разговорные, аббревиатуры) ----
CIS_COUNTRY_CANONICAL: Dict[str, str] = {
    "Беларусь": "Беларусь",
    "Россия": "Россия",
    "Казахстан": "Казахстан",
    "Узбекистан": "Узбекистан",
    "Кыргызстан": "Кыргызстан",
    "Таджикистан": "Таджикистан",
    "Туркменистан": "Туркменистан",
    "Армения": "Армения",
    "Азербайджан": "Азербайджан",
    "Молдова": "Молдова",
    "Грузия": "Грузия",
}

# Короткие коды (ISO 3166-1 alpha-2, валютные, сокращения) — опасны для автоматического поиска
# по токенам сообщения, т.к. "ru", "by", "kz" и т.д. часто встречаются в тексте случайно.
# Используются только при явном совпадении со ВСЕМ текстом (явные команды /set_country и т.п.).
_CIS_SHORT_KEYS: Dict[str, str] = {
    "by": "Беларусь", "rb": "Беларусь", "рб": "Беларусь", "byn": "Беларусь",
    "ru": "Россия",
    "kz": "Казахстан",
    "uz": "Узбекистан",
    "kg": "Кыргызстан",
    "tj": "Таджикистан",
    "tm": "Туркменистан",
    "am": "Армения",
    "az": "Азербайджан",
    "md": "Молдова",
    "ge": "Грузия",
}

_CIS_FUZZY: Dict[str, str] = {
    # Беларусь
    "беларусь": "Беларусь", "беларуси": "Беларусь", "беларусью": "Беларусь",
    "белоруссия": "Беларусь", "белоруссии": "Беларусь", "белорусси": "Беларусь",
    "белоруссией": "Беларусь", "белоруссию": "Беларусь", "белорусь": "Беларусь",
    # Россия
    "россия": "Россия", "россии": "Россия", "россией": "Россия", "россию": "Россия",
    "рф": "Россия", "russia": "Россия",
    "russian federation": "Россия",
    # Казахстан
    "казахстан": "Казахстан", "казахстана": "Казахстан", "казахстаном": "Казахстан",
    "казахстане": "Казахстан", "kazakhstan": "Казахстан",
    # Узбекистан
    "узбекистан": "Узбекистан", "узбекистана": "Узбекистан", "узбекистаном": "Узбекистан",
    "uzbekistan": "Узбекистан",
    # Кыргызстан
    "кыргызстан": "Кыргызстан", "кыргызстана": "Кыргызстан", "кыргызстаном": "Кыргызстан",
    "киргизия": "Кыргызстан", "киргизии": "Кыргызстан", "киргизией": "Кыргызстан",
    "kyrgyzstan": "Кыргызстан",
    # Таджикистан
    "таджикистан": "Таджикистан", "таджикистана": "Таджикистан",
    "таджикистаном": "Таджикистан", "tajikistan": "Таджикистан",
    # Туркменистан
    "туркменистан": "Туркменистан", "туркменистана": "Туркменистан",
    "туркменистаном": "Туркменистан", "туркмения": "Туркменистан",
    "туркмении": "Туркменистан", "turkmenistan": "Туркменистан",
    # Армения
    "армения": "Армения", "армении": "Армения", "арменией": "Армения",
    "армению": "Армения", "armenia": "Армения",
    # Азербайджан
    "азербайджан": "Азербайджан", "азербайджана": "Азербайджан",
    "азербайджаном": "Азербайджан", "azerbaijan": "Азербайджан",
    # Молдова
    "молдова": "Молдова", "молдовы": "Молдова", "молдовой": "Молдова",
    "молдове": "Молдова", "молдавия": "Молдова", "молдавии": "Молдова",
    "молдавией": "Молдова", "moldova": "Молдова",
    # Грузия
    "грузия": "Грузия", "грузии": "Грузия", "грузией": "Грузия",
    "грузию": "Грузия", "georgia": "Грузия",
}

FACT_FIELD_LABELS_RU = {
    "name": "имя",
    "age": "возраст",
    "city": "населённый пункт",
    "country": "страну",
    "timezone": "часовой пояс",
    "language": "язык",
    "currency": "валюту",
    "interests": "интересы",
    "occupation": "работу или профессию",
    "pet_cat": "имя кошки",
    "pet_dog": "имя собаки",
}

# Именительный падеж для карточки /me (не путать с «Запомнить страну?»).
FACT_FIELD_LABELS_PROFILE_RU = {
    "name": "Имя",
    "age": "Возраст",
    "city": "Населённый пункт",
    "country": "Страна",
    "timezone": "Часовой пояс",
    "language": "Язык",
    "currency": "Валюта",
    "interests": "Интересы",
    "occupation": "Работа или профессия",
    "pet_cat": "Кошка",
    "pet_dog": "Собака",
}


def format_fact_fields_nice_ru(keys: Any) -> str:
    """Человекочитаемый перечень полей для подтверждения (не сырые ключи вроде country)."""
    if not keys:
        return ""
    labels: List[str] = []
    for k in sorted({str(x).strip() for x in keys if str(x).strip()}):
        labels.append(FACT_FIELD_LABELS_RU.get(k, k))
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} и {labels[1]}"
    return ", ".join(labels[:-1]) + f" и {labels[-1]}"


def _display_fact_value(value: Any) -> str:
    s = _norm_text(value)
    return s if s else "—"


def build_facts_confirmation_prompt_ru(
    known: Dict[str, Any],
    accepted: Dict[str, Any],
    conflicts: Dict[str, Any],
) -> str:
    """
    Подтверждение перед записью: для перезаписи — было/станет (как «карточка» в Obsidian).
    """
    lines: List[str] = []
    for field in sorted(conflicts.keys()):
        fact = conflicts.get(field) or {}
        label = FACT_FIELD_LABELS_PROFILE_RU.get(field, field)
        old_v = _display_fact_value(known.get(field))
        new_v = _display_fact_value(fact.get("value"))
        lines.append(f"{label}: было «{old_v}» → «{new_v}»")
    if conflicts and accepted:
        nice_new = format_fact_fields_nice_ru(accepted.keys())
        lines.append(f"Также запомнить: {nice_new}.")
    if conflicts:
        return "\n".join(lines) + "\n\nЗаписать новые значения? Ответь «да» или «нет»."
    nice = format_fact_fields_nice_ru(accepted.keys())
    return f"Запомнить {nice}? Ответь «да» или «нет»."


FACT_TTL_DAYS = {
    "age": 460,
    "city": 365,
    "country": 365,
    "timezone": 365,
    "language": 730,
    "currency": 365,
    "interests": 180,
    "name": 3650,
    "occupation": 365,
    "pet_cat": 3650,
    "pet_dog": 3650,
}


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


def _valid_name(name: str) -> bool:
    s = _norm_text(name)
    return 2 <= len(s) <= 60 and bool(re.match(r"^[A-Za-zА-Яа-яЁё\-\s]+$", s))


def _valid_occupation(text: str) -> bool:
    s = _norm_text(text)
    if not (2 <= len(s) <= 80):
        return False
    return bool(re.match(r"^[A-Za-zА-Яа-яЁё0-9\-\s\.,/]+$", s))


def _explicit_remember_facts_intent(text: str) -> bool:
    """Явное «запомни» / «живу в а.г. …» — пишем в профиль без лишнего «да/нет»."""
    low = _norm_text(text).lower()
    if re.search(r"(?:^|\s)(?:запомни|запиши|зафиксируй|remember)\b", low):
        return True
    if re.search(r"(?:живу|проживаю|живём|живем)\s+(?:в\s+)?(?:а\.г\.|аг\.|агрогородок)\b", low):
        return True
    if re.search(
        r"(?:полгода|месяц(?:а|ев)?|год(?:а|ов)?|уже)\s+(?:в\s+)?(?:а\.г\.|аг\.|агрогородок)\b",
        low,
    ):
        return True
    if re.search(r"(?:а\.г\.|аг\.|агрогородок)\s*(?:миханович|springfield)", low):
        return True
    return False


def _same_city_fact(known: str, new: str, context: str) -> bool:
    from core.brain.text_helpers import canonical_user_city_fact

    return canonical_user_city_fact(known, known).casefold() == canonical_user_city_fact(
        new, context
    ).casefold()


def _city_implies_minsk_region(city: str) -> bool:
    s = (city or "").lower().replace("ё", "е")
    if ("миханович" in s or "springfield" in s) and re.search(r"(?:а\.г\.|аг\.|агрогородок)", s):
        return True
    return "минск" in s and ("район" in s or "област" in s)


def _anchor_conflicts_city(city: str, anchor: Any) -> bool:
    try:
        from core.weather_location_store import weather_anchor_conflicts_user_facts

        return weather_anchor_conflicts_user_facts({"city": city}, anchor)
    except Exception:
        if not isinstance(anchor, dict) or not _city_implies_minsk_region(city):
            return False
        a1 = str(anchor.get("admin1") or "").lower().replace("ё", "е")
        return "mogilev" in a1 or "могил" in a1


def _message_looks_unrelated_to_profile_facts(text: str) -> bool:
    """Задача не про профиль — не вешать подтверждение «Запомнить город?»."""
    low = _norm_text(text).lower()
    if not low:
        return False
    return bool(_CITY_FINANCE_CONTAMINATION_RE.search(low))


def _valid_city(city: str) -> bool:
    s = _norm_text(city)
    if not (2 <= len(s) <= 80):
        return False
    if _CITY_MATH_CONTAMINATION_RE.search(s):
        return False
    if _CITY_FINANCE_CONTAMINATION_RE.search(s):
        return False
    if not re.match(r"^[A-Za-zА-Яа-яЁё\-\s\.,]+$", s):
        return False
    sl = s.lower()
    # В город не должны попадать формулировки уровня страны.
    if "республик" in sl:
        return False
    if "вида" in sl and ("неравенств" in sl or "уравнен" in sl or "функц" in sl):
        return False
    # Отсекаем «псевдо-города» из обычной речи: «другой город», «любой город», «какой-то город» и т.п.
    low = s.strip().lower().strip(".")
    generic = {
        "другой",
        "другая",
        "другое",
        "другие",
        "любой",
        "любая",
        "любое",
        "какой-то",
        "какая-то",
        "какое-то",
        "какой нибудь",
        "какой-нибудь",
        "anywhere",
        "somewhere",
        "any city",
        "another",
        "another city",
        "other",
        "other city",
        "город",
    }
    if low in generic:
        return False
    # «другой город», «any city» и подобные конструкции
    if low.startswith(("другой ", "другая ", "любой ", "любая ", "any ", "another ", "other ")):
        return False
    # Отсекаем известные названия стран (чтобы «Беларусь» не стал city)
    if low in _ALL_KNOWN_COUNTRY_NAMES_LO:
        return False
    if extractor_country(s):
        return False
    return True


def _valid_country(country: str) -> bool:
    s = _norm_text(country)
    if not (2 <= len(s) <= 80):
        return False
    if _CITY_MATH_CONTAMINATION_RE.search(s):
        return False
    if not re.match(r"^[A-Za-zА-Яа-яЁё\-\s\.,]+$", s):
        return False
    return True


def normalize_country_fact(value: str) -> str:
    """Нормализация страны для хранения (падежи, «Республика …»)."""
    s = _norm_text(value)
    if not s:
        return s
    cn = extractor_country(s)
    if cn:
        return cn
    low = s.lower().replace("ё", "е")
    if "росси" in low or low in ("рф", "россия"):
        return "Россия"
    if "казах" in low:
        return "Казахстан"
    if "украин" in low:
        return "Украина"
    if "польш" in low:
        return "Польша"
    if "литв" in low:
        return "Литва"
    if "латви" in low:
        return "Латвия"
    return s


def _edit_distance(a: str, b: str) -> int:
    """Расстояние Левенштейна для коротких строк."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    v0 = list(range(lb + 1))
    v1 = [0] * (lb + 1)
    for i in range(1, la + 1):
        v1[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v1[j] = min(v1[j - 1] + 1, v0[j] + 1, v0[j - 1] + cost)
        v0, v1 = v1, v0
    return v0[lb]


# ISO-3166 en + ru для fallback fuzzy (только распространённые; без политики границ).
_ISO_FUZZY: Dict[str, str] = {
    # Канонические русские
    "беларусь": "Беларусь", "россия": "Россия", "казахстан": "Казахстан",
    "узбекистан": "Узбекистан", "кыргызстан": "Кыргызстан", "таджикистан": "Таджикистан",
    "туркменистан": "Туркменистан", "армения": "Армения", "азербайджан": "Азербайджан",
    "молдова": "Молдова", "грузия": "Грузия",
    "украина": "UKR",
    "польша": "POL", "литва": "LTU", "латвия": "LVA", "эстония": "EST",
    "германия": "DEU", "франция": "FRA", "италия": "ITA", "испания": "ESP",
    "великобритания": "GBR", "сша": "USA", "китай": "CHN", "япония": "JPN",
    "турция": "TUR", "швейцария": "CHE", "оаэ": "ARE",
    # Канонические английские
    "belarus": "Беларусь", "russia": "Россия", "kazakhstan": "Казахстан",
    "uzbekistan": "Узбекистан", "kyrgyzstan": "Кыргызстан", "tajikistan": "Таджикистан",
    "turkmenistan": "Туркменистан", "armenia": "Армения", "azerbaijan": "Азербайджан",
    "moldova": "Молдова", "georgia": "Грузия",
    "ukraine": "UKR", "poland": "POL", "lithuania": "LTU", "latvia": "LVA",
    "estonia": "EST", "germany": "DEU", "france": "FRA", "italy": "ITA",
    "spain": "ESP", "united kingdom": "GBR", "uk": "GBR", "usa": "USA",
    "united states": "USA", "china": "CHN", "japan": "JPN", "turkey": "TUR",
    "switzerland": "CHE", "uae": "ARE",
}

# Базовый словарь non-CIS для отсекания ложных city-матчей (страна != город).
_ALL_KNOWN_COUNTRY_NAMES_LO = frozenset(_ISO_FUZZY.keys()) | frozenset(_CIS_FUZZY.keys()) | frozenset(_CIS_SHORT_KEYS.keys())


def _fuzzy_match_cis(token: str) -> Optional[str]:
    """Exact match по _CIS_FUZZY; fallback - edit distance <= 1 к каноническим ключам СНГ."""
    low = token.lower().replace("ё", "е")
    hit = _CIS_FUZZY.get(low)
    if hit:
        return hit
    best: Optional[tuple[int, str]] = None
    for canon, label in CIS_COUNTRY_CANONICAL.items():
        canon_low = canon.lower().replace("ё", "е")
        dist = _edit_distance(low, canon_low)
        if dist <= 1:
            if best is None or dist < best[0]:
                best = (dist, label)
    return best[1] if best else None


def _fuzzy_match_iso(token: str) -> Optional[str]:
    """Exact match по _ISO_FUZZY; fallback - edit distance <= 1 к ключам _ISO_FUZZY."""
    low = token.lower().replace("ё", "е")
    hit = _ISO_FUZZY.get(low)
    if hit:
        return hit if hit in CIS_COUNTRY_CANONICAL else None
    best: Optional[tuple[int, str]] = None
    for key, label in _ISO_FUZZY.items():
        dist = _edit_distance(low, key)
        if dist <= 1:
            if best is None or dist < best[0]:
                best = (dist, label)
    result = best[1] if best else None
    if result and result in CIS_COUNTRY_CANONICAL:
        return result
    return None


def extractor_country(text: str) -> Optional[str]:
    """
    Умный экстрактор страны СНГ.
    Приоритет: exact match по _CIS_FUZZY -> короткие коды -> edit distance по СНГ -> ISO -> multi-token (только полные названия).
    Возвращает каноническое имя (Беларусь, Россия, ...) или None.
    Короткие коды (ru, by, kz и т.д.) проверяются только на полном тексте,
    НЕ по отдельным токенам — чтобы "ru" в середине сообщения не давало ложное срабатывание.
    """
    s = _norm_text(text)
    if not s:
        return None
    low = s.lower().replace("ё", "е")
    # Прямой поиск по CIS_FUZZY (приоритетный)
    hit = _CIS_FUZZY.get(low)
    if hit:
        return hit
    # Короткие коды — только на полном тексте (для /set_country ru и т.п.)
    hit = _CIS_SHORT_KEYS.get(low)
    if hit:
        return hit
    # Убираем кавычки, точку в конце, скобки
    cleaned = re.sub(r'^["\'«»()\[\]{}]+|["\'«»()\[\]{}]+$', '', low).strip(" .,!?:;")
    if cleaned != low:
        hit = _CIS_FUZZY.get(cleaned)
        if hit:
            return hit
        hit = _CIS_SHORT_KEYS.get(cleaned)
        if hit:
            return hit
    # Мульти-токен: "я из Беларуси" -> последнее слово может быть страной
    hit = _fuzzy_match_cis(low)
    if hit:
        return hit
    # Распространённые паттерны с приставками
    for prefix in ("", "республика ", "the republic of "):
        prefixed = prefix + low
        hit = _CIS_FUZZY.get(prefixed)
        if hit:
            return hit
        # edit distance для префиксного варианта
        for canon, label in CIS_COUNTRY_CANONICAL.items():
            if _edit_distance(prefixed, canon.lower().replace("ё", "е")) <= 1:
                return label
    # ISO fallback
    hit = _fuzzy_match_iso(low)
    if hit:
        return hit
    # Multi-token: пробуем каждое слово (только полные названия из _CIS_FUZZY, НЕ короткие ISO-коды)
    tokens = re.split(r"[\s,;/]+", low)
    for tok in tokens:
        tok = tok.strip(" .,!?:;\"'«»")
        if len(tok) < 2:
            continue
        hit = _CIS_FUZZY.get(tok)
        if hit:
            return hit
    # Edit distance по токенам
    for tok in tokens:
        tok = tok.strip(" .,!?:;\"'«»")
        if len(tok) < 2:
            continue
        hit = _fuzzy_match_cis(tok)
        if hit:
            return hit
    return None


def _valid_timezone(tz: str) -> bool:
    s = _norm_text(tz)
    if re.match(r"^[A-Za-z]+/[A-Za-z_\-]+$", s):
        try:
            ZoneInfo(s)
            return True
        except Exception:
            return False
    return False


def _valid_language(lang: str) -> bool:
    s = _norm_text(lang).lower()
    return s in ISO_LANG


def _valid_currency(cur: str) -> bool:
    s = _norm_text(cur).upper()
    return s in ISO_CUR


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _field_confidence(field: str, explicit: bool, valid: bool) -> float:
    if not valid:
        return 0.0
    base = 0.65
    if explicit:
        base += 0.25
    if field in {"age", "currency", "language", "timezone"}:
        base += 0.05
    return min(base, 0.99)


class UserFactsManager:
    def __init__(
        self,
        *,
        behavior_store: Any,
        mem0_memory: Any = None,
        user_system: Any = None,
        digital_twin: Any = None,
    ) -> None:
        self.behavior_store = behavior_store
        self.mem0_memory = mem0_memory
        self.user_system = user_system
        self.digital_twin = digital_twin

    def _utcnow(self) -> datetime:
        return datetime.now(UTC)

    def _iso(self, dt: Optional[datetime] = None) -> str:
        return (dt or self._utcnow()).isoformat()

    def _fact_expiry(self, field: str, dt: Optional[datetime] = None) -> str:
        base = dt or self._utcnow()
        return (base + timedelta(days=FACT_TTL_DAYS.get(field, 365))).isoformat()

    def _should_revoke(self, text: str) -> List[str]:
        low = _norm_text(text).lower()
        revoked: List[str] = []
        revoke_map = {
            "city": ("не живу", "i don't live", "i moved", "переехал", "переехала", "don’t use this", "don't use this"),
            "country": ("не живу", "i don't live", "i moved", "переехал", "переехала"),
            "timezone": ("другой часовой пояс", "new timezone", "timezone changed"),
            "currency": ("другая валюта", "new currency"),
            "language": ("другой язык", "new language"),
            "occupation": (
                "уволился",
                "уволилась",
                "сменил работу",
                "сменила работу",
                "сменил профессию",
                "сменила профессию",
                "quit my job",
                "i quit",
                "i resigned",
                "changed my job",
                "not an accountant anymore",
                "не бухгалтер",
                "не бухгалтером",
            ),
        }
        for field, needles in revoke_map.items():
            if any(n in low for n in needles):
                revoked.append(field)
        if "не используй это" in low:
            revoked.extend(["city", "country", "timezone", "currency"])
        return sorted(set(revoked))

    def _detect_update_intent(self, text: str) -> bool:
        low = _norm_text(text).lower()
        triggers = (
            "now",
            "теперь",
            "сейчас",
            "i moved",
            "я переех",
            "i'm older now",
            "я стал старше",
            "больше не",
            "no longer",
            "сменил работу",
            "сменила работу",
            "теперь работаю",
            "исправ",
            "ошибк",
            "неверно",
            "не верно",
            "неправильн",
            "в профиле не",
            "запомни правильн",
            "на самом деле я",
            "фактически я",
            "поменяй ",
            "обнови город",
            "обнови страну",
            "wrong city",
            "fix my profile",
        )
        return any(t in low for t in triggers)

    def _cleanup_expired(self, rec: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        facts = dict(rec.get("user_facts") or {})
        meta = dict(rec.get("user_facts_meta") or {})
        now = self._utcnow()
        changed = False
        for field in list(facts.keys()):
            info = meta.get(field) or {}
            if info.get("revoked"):
                facts.pop(field, None)
                changed = True
                continue
            expires_at = info.get("expires_at")
            if expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                    if exp_dt <= now:
                        facts.pop(field, None)
                        changed = True
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'user_facts', e, exc_info=True)
        rec["user_facts"] = facts
        rec["user_facts_meta"] = meta
        if changed:
            rec["facts_last_cleanup"] = self._iso(now)
        return rec, changed

    def extract_facts(self, text: str) -> List[Dict[str, Any]]:
        text = _norm_text(text)
        if not text:
            return []
        out: List[Dict[str, Any]] = []
        low = text.lower()

        seen_fields: set = set()

        def _push(field: str, value: Any, explicit: bool, valid: bool = True, source: str = "message_extract") -> None:
            if field in seen_fields:
                return
            if field == "country":
                value = extractor_country(str(value))
                if not value:
                    return
                value = normalize_country_fact(value)
            norm_value = value
            if field == "age":
                try:
                    age = int(value)
                except Exception:
                    age = -1
                valid = 1 <= age <= 120
                norm_value = str(age) if valid else value
            elif field == "name":
                valid = _valid_name(value)
            elif field == "city":
                from core.brain.text_helpers import canonical_user_city_fact

                value = canonical_user_city_fact(str(value), text)
                norm_value = value
                valid = _valid_city(value)
            elif field == "country":
                valid = _valid_country(value)
            elif field == "timezone":
                valid = _valid_timezone(value)
            elif field == "language":
                valid = _valid_language(value)
                norm_value = value.lower()
            elif field == "currency":
                valid = _valid_currency(value)
                norm_value = value.upper()
            elif field == "occupation":
                valid = _valid_occupation(value)
                norm_value = _norm_text(value)
            elif field in ("pet_cat", "pet_dog"):
                valid = _valid_name(value)
                norm_value = _norm_text(value)
            seen_fields.add(field)
            out.append(
                {
                    "field": field,
                    "value": norm_value,
                    "confidence": _field_confidence(field, explicit, valid),
                    "valid": valid,
                    "source": source,
                }
            )

        patterns: List[Tuple[str, str, bool]] = [
            ("name", r"(?:меня зовут|my name is)\s+([A-Za-zА-Яа-яЁё\-\s]{2,60})", True),
            ("age", r"(?:мне|i am)\s+(\d{1,3})\s*(?:лет|years?)?", True),
            # Только явные формулировки: без сырого «город …» (ложные матчи вроде «город неравенства вида»).
            (
                "city",
                r"(?:^|[\s,.!?])мой\s+город\s*[,:]?\s*([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?:\s*[.,!?]|$)",
                True,
            ),
            (
                "city",
                r"(?:^|[\s,.!?]|[!?])город\s*[:—–-]\s*([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?:\s*[.,!?]|$)",
                True,
            ),
            (
                "city",
                r"(?:^|[\s,])аг\.\s*([A-Za-zА-Яа-яЁё\-]{2,48})(?=[\s,.!?]|$)",
                True,
            ),
            (
                "city",
                r"(?:^|[\s,])(?:а\.г\.|агрогородок)\s*([A-Za-zА-Яа-яЁё\-]{2,48})(?=[\s,.!?]|$)",
                True,
            ),
            (
                "city",
                r"(?:живу|проживаю|живём|живем)\s+(?:в\s+)?(?:а\.г\.|аг\.|агрогородок)\s*([A-Za-zА-Яа-яЁё\-]{2,48})",
                True,
            ),
            (
                "city",
                r"(?:живу|проживаю)\s+(?:в\s+)?([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-\s\.]{1,60}?)(?=\s*\(|$|\s*—|\s*–)",
                True,
            ),
            (
                "city",
                r"(?:запомни|запиши|зафиксируй)\s+(?:город\s+)?(?:а\.г\.|аг\.|агрогородок)\s*([A-Za-zА-Яа-яЁё\-]{2,48})",
                True,
            ),
            (
                "city",
                r"(?:запомни|запиши)\s+(?:что\s+)?(?:я\s+)?(?:живу|проживаю)\s+(?:в\s+)?(?:а\.г\.|аг\.)\s*([A-Za-zА-Яа-яЁё\-]{2,48})",
                True,
            ),
            (
                "city",
                r"(?:^|\s)я\s+из\s+([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?=\s*[.,!?]|$)",
                True,
            ),
            ("city", r"(?:^|\s)i(?:'m| am)\s+from\s+([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?=\s*[.,!?]|$)", True),
            (
                "country",
                r"(?:живу в|i live in)\s+([^\n]+?)(?=\s*,|\s+зовут\b|\s+меня\s+зовут\b|\s+я\s+\d{1,4}\s*г|\s+валюта\b|$)",
                True,
            ),
            ("city", r"(?:я переехал(?:а)? в|i moved to)\s+([A-Za-zА-Яа-яЁё\-\s\.]{2,80})", True),
            ("country", r"(?:теперь живу в|i live in)\s+([A-Za-zА-Яа-яЁё\-\s\.]{2,80})\s+now", True),
            # Новые: «страна:…», «моя страна…», «/set_country …»
            ("country", r"(?:^|[\s,.!?])страна\s*[:—–\-]\s*([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?:\s*[.,!?]|$)", True),
            ("country", r"(?:^|[\s,.!?])моя\s+страна\s*[:,\-]?\s*([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?:\s*[.,!?]|$)", True),
            ("country", r"(?:^|[\s,.!?])/set_country\s+([A-Za-zА-Яа-яЁё\-\s\.]{2,80}?)(?:\s*[.,!?]|$)", True),
            ("timezone", r"(?:мой часовой пояс|timezone)\s*[:=]?\s*([A-Za-z]+/[A-Za-z_\-]+|(?:UTC|GMT)\s?[+\-]\d{1,2})", True),
            ("language", r"(?:мой язык|language)\s*[:=]?\s*([a-zA-Z]{2})", True),
            ("currency", r"(?:моя валюта|currency)\s*[:=]?\s*([A-Za-z]{3})", True),
            ("occupation", r"(?:работаю|я работаю)\s+([A-Za-zА-Яа-яЁё\-\s]{3,60}?)(?:\s*$|[.,!?])", True),
            ("occupation", r"(?:по профессии|моя профессия|моя работа)\s*[:\-]?\s*([A-Za-zА-Яа-яЁё\-\s]{3,60})", True),
            ("occupation", r"(?:i work as|i'm an?|i am an?)\s+([A-Za-zА-Яа-яЁё\-\s]{3,60})(?:\s*$|[.,!?])", True),
            (
                "pet_cat",
                r"(?:запомни[,\s:]+)?(?:у меня\s+)?(?:есть\s+)?(?:мо[ейю]?\s+)?кошк[ауеи]\s+"
                r"(?:зовут|зовутся|по имени|по кличке|кличк[ауеи])\s+([A-Za-zА-Яа-яЁё\-]{2,40})",
                True,
            ),
            (
                "pet_cat",
                r"(?:запомни[:\s,]+)?кошк[ауеи]\s+([A-Za-zА-Яа-яЁё\-]{2,40})(?:\s*$|[.,!?])",
                True,
            ),
            (
                "pet_dog",
                r"(?:запомни[,\s:]+)?(?:у меня\s+)?(?:есть\s+)?(?:мо[ейю]?\s+)?(?:собак[ауеи]|пёс|пес)\s+"
                r"(?:зовут|зовутся|по имени|по кличке|кличк[ауеи])\s+([A-Za-zА-Яа-яЁё\-]{2,40})",
                True,
            ),
            (
                "pet_dog",
                r"(?:запомни[:\s,]+)?(?:собак[ауеи]|пёс|пес)\s+([A-Za-zА-Яа-яЁё\-]{2,40})(?:\s*$|[.,!?])",
                True,
            ),
        ]
        for field, pat, explicit in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if not m:
                continue
            value = _norm_text(m.group(1))
            if field == "country":
                value = value[:80].strip()
            _push(field, value, explicit)

        # BONUS PASS: не на вставленной статье — иначе «Беларусь» из paste → «Запомнить страну?»
        try:
            from core.brain.text_helpers import looks_like_pasted_news_article

            _paste_article = looks_like_pasted_news_article(text)
        except Exception:
            _paste_article = False
        if not _paste_article and "country" not in seen_fields:
            cn = extractor_country(text)
            if cn:
                _push("country", cn, True, source="extractor_cis")

        interests_match = re.search(r"(?:мне нравится|я люблю|i like|my interests are)\s+(.+)$", text, flags=re.IGNORECASE)
        if interests_match:
            raw = interests_match.group(1)
            chunks = [c.strip(" .,!?:;").lower() for c in re.split(r",| и | and ", raw) if c.strip()]
            chunks = [c[:48] for c in chunks if 1 < len(c) <= 48]
            if chunks:
                out.append(
                    {
                        "field": "interests",
                        "value": sorted(set(chunks))[:8],
                        "confidence": 0.82,
                        "valid": True,
                        "source": "message_extract",
                    }
                )
        return out

    def parse_confirmation(self, text: str) -> Optional[bool]:
        low = _norm_text(text).lower()
        if not low:
            return None
        yes = {"да", "ок", "конечно", "запомни", "yes", "yep", "sure", "save it"}
        no = {"нет", "не надо", "не запоминай", "no", "nope", "don't"}
        if any(t in low for t in yes):
            return True
        if any(t in low for t in no):
            return False
        return None

    def required_missing_for_task(self, text: str, facts: Dict[str, Any]) -> List[str]:
        low = _norm_text(text).lower()
        missing: List[str] = []
        if any(x in low for x in ("погода", "weather", "температур")):
            if not facts.get("city") and not facts.get("country"):
                missing.append("location")
        # Валюта: спрашиваем недостающие факты только когда это похоже на конвертацию/курс,
        # а не на мета-обсуждение «у меня всплыло слово валюта».
        # Примеры (да): «сколько 100 USD в рублях», «курс доллара», «конвертируй 50 евро в BYN».
        # Примеры (нет): «валюта появилась», «почему ты спрашиваешь валюту», «про валюту в целом».
        currency_task = _looks_like_currency_conversion_task(text, low)
        if currency_task:
            if not facts.get("currency") and not facts.get("country"):
                if not _message_has_explicit_fx_context(text, low):
                    missing.append("currency")
        if looks_like_wall_clock_question(text):
            if not facts.get("timezone") and not infer_timezone_from_facts(facts):
                missing.append("timezone")
        return missing

    def _normalize_fact_payload(self, validated: Dict[str, Any]) -> Dict[str, Any]:
        payload = {}
        for item in validated.values():
            field = item.get("field")
            if field in FACT_FIELDS:
                payload[field] = item.get("value")
        return self._sanitize_committed_facts(payload)

    def _sanitize_committed_facts(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Отсекаем явный мусор и нормализуем страну при записи в архив."""
        out = {k: v for k, v in payload.items() if k in FACT_FIELDS}
        if "country" in out:
            raw = out.get("country")
            if raw is not None:
                cn = normalize_country_fact(str(raw))
                if _valid_country(cn):
                    out["country"] = cn
                else:
                    out.pop("country", None)
        if "city" in out and not _valid_city(str(out["city"])):
            out.pop("city", None)
        return out

    def _safe_overwrite_filter(
        self,
        known: Dict[str, Any],
        candidates: Dict[str, Any],
        *,
        update_intent: bool,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        accepted: Dict[str, Any] = {}
        conflicts: Dict[str, Any] = {}
        auto_overwrite = _env_truthy("USER_FACTS_OVERWRITE_AUTO", False)
        for field, fact in candidates.items():
            old = known.get(field)
            new = fact.get("value")
            if old is None or old == new:
                accepted[field] = fact
                continue
            if auto_overwrite and (
                update_intent or float(fact.get("confidence", 0)) >= 0.9
            ):
                accepted[field] = fact
            else:
                conflicts[field] = fact
        return accepted, conflicts

    def commit_validated(
        self,
        user_id: str,
        group_id: Optional[str],
        validated: Dict[str, Any],
    ) -> None:
        if not validated:
            return
        payload = self._normalize_fact_payload(validated)
        if not payload:
            return
        rec = self.behavior_store.load(user_id, group_id)
        rec, _ = self._cleanup_expired(rec)
        known = dict(rec.get("user_facts") or {})
        meta = dict(rec.get("user_facts_meta") or {})
        known.update(payload)
        now = self._utcnow()
        for field in payload:
            meta[field] = {
                "updated_at": self._iso(now),
                "expires_at": self._fact_expiry(field, now),
                "revoked": False,
                "source": (validated.get(field) or {}).get("source", "validated"),
                "confidence": float((validated.get(field) or {}).get("confidence", 0.0)),
            }
        if payload.get("country") and not payload.get("currency"):
            cc = _norm_text(payload["country"]).lower()
            guessed = COUNTRY_TO_CURRENCY.get(cc)
            if not guessed and "беларус" in cc:
                guessed = "BYN"
            # Memory-safety guard: only auto-persist inferred currency if source is user_input.
            if guessed and not known.get("currency") and can_persist_user_fact(
                (validated.get("country") or {}).get("source", "inferred"),
                True,
            ):
                known["currency"] = guessed
                meta["currency"] = {
                    "updated_at": self._iso(now),
                    "expires_at": self._fact_expiry("currency", now),
                    "revoked": False,
                    "source": "country_inference",
                    "confidence": 0.78,
                }
        rec["user_facts"] = known
        rec["user_facts_meta"] = meta
        rec["facts_last_update"] = datetime.now().isoformat()
        if "city" in payload and _anchor_conflicts_city(str(known.get("city") or ""), rec.get("weather_anchor")):
            rec.pop("weather_anchor", None)
        self.behavior_store.save(user_id, group_id, rec)

        if "city" in payload or "country" in payload:
            try:
                from core.weather_location_store import refresh_weather_anchor_from_facts

                refresh_weather_anchor_from_facts(
                    self.behavior_store, user_id, group_id, known
                )
            except Exception as e:
                logger.debug("weather_anchor from facts commit: %s", e)

        if self.user_system and hasattr(self.user_system, "update_user"):
            user_patch: Dict[str, Any] = {"profile_facts": payload}
            if "name" in payload:
                user_patch["name"] = payload["name"]
            if "language" in payload:
                user_patch["language"] = payload["language"]
            if "timezone" in payload:
                user_patch["timezone"] = payload["timezone"]
            if "currency" in payload:
                user_patch["currency"] = payload["currency"]
            self.user_system.update_user(user_id, user_patch)

        if self.digital_twin:
            twin_patch: Dict[str, Any] = {}
            if "interests" in payload:
                twin_patch["interests"] = payload["interests"]
            if "age" in payload:
                twin_patch["age"] = payload["age"]
            if payload.get("city") or payload.get("country"):
                twin_patch["location"] = {
                    "city": payload.get("city"),
                    "country": payload.get("country"),
                }
            if payload.get("language"):
                twin_patch["language"] = payload.get("language")
            if payload.get("currency"):
                twin_patch["currency"] = payload.get("currency")
            if twin_patch:
                if hasattr(self.digital_twin, "update_from_interaction"):
                    self.digital_twin.update_from_interaction(user_id, twin_patch)
                elif hasattr(self.digital_twin, "update_twin"):
                    self.digital_twin.update_twin(user_id, twin_patch)

        if self.mem0_memory:
            facts_list = []
            for k, v in payload.items():
                facts_list.append({"type": "user_fact", "field": k, "content": v, "timestamp": datetime.now().isoformat()})
            if hasattr(self.mem0_memory, "add_structured_facts"):
                self.mem0_memory.add_structured_facts(user_id, facts_list)
            else:
                existing = getattr(self.mem0_memory, "facts", None)
                if isinstance(existing, dict):
                    existing.setdefault(user_id, []).extend(facts_list)

    def process_turn(
        self,
        user_id: Optional[str],
        group_id: Optional[str],
        text: str,
    ) -> Dict[str, Any]:
        if not user_id:
            return {"facts": {}, "pending_confirmation": None, "auto_ask_missing": []}
        rec = self.behavior_store.load(user_id, group_id)
        rec, expired_dirty = self._cleanup_expired(rec)
        if expired_dirty:
            self.behavior_store.save(user_id, group_id, rec)
        known = dict(rec.get("user_facts") or {})
        facts_meta = dict(rec.get("user_facts_meta") or {})
        pending = rec.get("pending_facts_confirmation") or {}
        if not isinstance(pending, dict):
            pending = {}
        pending_overwrite = rec.get("pending_facts_overwrite") or {}
        if not isinstance(pending_overwrite, dict):
            pending_overwrite = {}

        revoked_fields = self._should_revoke(text)
        if revoked_fields:
            for f in revoked_fields:
                known.pop(f, None)
                info = dict(facts_meta.get(f) or {})
                info["revoked"] = True
                info["revoked_at"] = self._iso()
                facts_meta[f] = info
            rec["user_facts"] = known
            rec["user_facts_meta"] = facts_meta
            rec["pending_facts_confirmation"] = {}
            rec["pending_facts_overwrite"] = {}
            self.behavior_store.save(user_id, group_id, rec)

        extracted = self.extract_facts(text)
        low_turn = _norm_text(text).lower()
        try:
            from core.brain.text_helpers import (
                _user_text_looks_like_weather_query,
                looks_like_pasted_news_article,
            )

            suppress_confirmation = _facts_should_suppress_confirmation(text)
            weather_primary = _user_text_looks_like_weather_query(low_turn)
        except Exception:
            suppress_confirmation = False
            weather_primary = False
        valid_new: Dict[str, Any] = {}
        for fact in extracted:
            if fact.get("valid") and fact.get("confidence", 0) >= 0.7:
                if suppress_confirmation and fact.get("field") in ("country", "city"):
                    if fact.get("source") == "extractor_cis":
                        continue
                if (
                    weather_primary
                    and fact.get("field") == "city"
                    and not _explicit_remember_facts_intent(text)
                ):
                    continue
                valid_new[fact["field"]] = fact
        if "city" in valid_new and known.get("city"):
            if _same_city_fact(str(known.get("city") or ""), str(valid_new["city"].get("value") or ""), text):
                valid_new.pop("city", None)
        update_intent = self._detect_update_intent(text)
        accepted, conflicts = self._safe_overwrite_filter(known, valid_new, update_intent=update_intent)

        confirm_answer = self.parse_confirmation(text)
        confirmation_prompt = None
        committed_facts_this_turn = False
        if (pending or pending_overwrite) and confirm_answer is not None:
            if confirm_answer:
                to_commit = dict(pending)
                to_commit.update(pending_overwrite)
                self.commit_validated(user_id, group_id, to_commit)
                committed_facts_this_turn = True
            rec = self.behavior_store.load(user_id, group_id)
            rec["pending_facts_confirmation"] = {}
            rec["pending_facts_overwrite"] = {}
            self.behavior_store.save(user_id, group_id, rec)
            known = dict(rec.get("user_facts") or {})
            facts_meta = dict(rec.get("user_facts_meta") or {})
            pending = {}
            pending_overwrite = {}
        elif (
            (accepted or conflicts)
            and not suppress_confirmation
            and not _message_looks_unrelated_to_profile_facts(text)
            and _explicit_remember_facts_intent(text)
        ):
            if conflicts:
                rec["pending_facts_confirmation"] = accepted
                rec["pending_facts_overwrite"] = conflicts
                self.behavior_store.save(user_id, group_id, rec)
                confirmation_prompt = build_facts_confirmation_prompt_ru(
                    known, accepted, conflicts
                )
            else:
                loc_only = {
                    k: v for k, v in accepted.items() if k in ("city", "country", "timezone")
                }
                to_commit = loc_only if loc_only else dict(accepted)
                if to_commit:
                    self.commit_validated(user_id, group_id, to_commit)
                    committed_facts_this_turn = True
                rec = self.behavior_store.load(user_id, group_id)
                known = dict(rec.get("user_facts") or {})
                facts_meta = dict(rec.get("user_facts_meta") or {})
                rec["pending_facts_confirmation"] = {}
                rec["pending_facts_overwrite"] = {}
                self.behavior_store.save(user_id, group_id, rec)
                pending = {}
                pending_overwrite = {}
        elif (
            (accepted or conflicts)
            and not suppress_confirmation
            and not _message_looks_unrelated_to_profile_facts(text)
        ):
            rec["pending_facts_confirmation"] = accepted
            rec["pending_facts_overwrite"] = conflicts
            self.behavior_store.save(user_id, group_id, rec)
            pending_fields = set(accepted.keys()) | set(conflicts.keys())
            if pending_fields:
                MONITOR.inc("user_facts_confirmation_prompt_total")
            if "city" in pending_fields:
                MONITOR.inc("user_facts_confirmation_prompt_city_total")
            if "currency" in pending_fields:
                MONITOR.inc("user_facts_confirmation_prompt_currency_total")
            confirmation_prompt = build_facts_confirmation_prompt_ru(
                known, accepted, conflicts
            )

        missing = self.required_missing_for_task(text, known)
        if "currency" in missing and known.get("country") and not known.get("currency"):
            cc = _norm_text(known.get("country")).lower()
            guessed = COUNTRY_TO_CURRENCY.get(cc)
            if guessed:
                known["currency"] = guessed
                missing = [m for m in missing if m != "currency"]
        return {
            "facts": known,
            "facts_meta": facts_meta,
            "new_candidates": valid_new,
            "accepted_candidates": accepted,
            "conflicting_candidates": conflicts,
            "pending_confirmation": pending,
            "pending_overwrite": pending_overwrite,
            "confirmation_prompt": confirmation_prompt,
            "auto_ask_missing": missing,
            "revoked_fields": revoked_fields,
            "committed_facts_this_turn": committed_facts_this_turn,
            "suppress_confirmation": suppress_confirmation,
        }


def _facts_should_suppress_confirmation(text: str) -> bool:
    """Не спрашивать «Запомнить страну?» на paste/Habr и уточнениях поиска (не RSS)."""
    t = (text or "").strip()
    if not t:
        return False
    try:
        from core.brain.text_helpers import looks_like_pasted_news_article

        if looks_like_pasted_news_article(t):
            return True
    except Exception as e:
        logger.debug("facts suppress paste check: %s", e)
    try:
        from core.brain.router_classifier import _is_reference_paste

        if _is_reference_paste(t):
            return True
    except Exception as e:
        logger.debug("facts suppress reference paste: %s", e)
    low = _norm_text(t).lower()
    if re.search(r"(?i)(не rss|без rss|searxng|не в rss|без google|через searx)", low):
        return True
    if "habr.com" in low and len(t) > 200:
        return True
    if len(t) >= 600:
        return True
    return False


def has_pending_facts_confirmation(persisted: Any) -> bool:
    """Ожидается «да/нет» по профилю — в behavior_store, не только в facts_flow."""
    if not isinstance(persisted, dict):
        return False
    pending = persisted.get("pending_facts_confirmation")
    if isinstance(pending, dict) and pending:
        return True
    over = persisted.get("pending_facts_overwrite")
    if isinstance(over, dict) and over:
        return True
    ff = persisted.get("facts_flow")
    if isinstance(ff, dict) and (
        ff.get("pending_confirmation") or ff.get("confirmation_prompt")
    ):
        return True
    return False


def facts_save_confirm_lane_eligible(facts_flow: Any) -> bool:
    """Новые факты извлечены — спросить «да/нет» без полного ответа LLM."""
    if not isinstance(facts_flow, dict):
        return False
    if facts_flow.get("suppress_confirmation"):
        return False
    cp = str(facts_flow.get("confirmation_prompt") or "").strip()
    if not cp:
        return False
    nc = facts_flow.get("new_candidates")
    if not isinstance(nc, dict) or not nc:
        return False
    if not _env_truthy("BRAIN_FACTS_CONFIRM_LANE_ENABLED", True):
        return False
    return True


def try_facts_shortcut_payload(
    text: str,
    facts_flow: Any,
    *,
    recent_dialogue: Any = None,
    persisted: Any = None,
) -> Optional[str]:
    """Короткий ответ на «да/нет» по фактам — без полного LLM."""
    if not isinstance(facts_flow, dict):
        return None
    try:
        from core.brain.text_helpers import affirmative_overrides_fact_confirmation, looks_like_affirmative_short

        if looks_like_affirmative_short(text) and affirmative_overrides_fact_confirmation(
            text, recent_dialogue=recent_dialogue, persisted=persisted
        ):
            return None
    except Exception:
        pass
    low = _norm_text(text).lower()
    if low not in ("да", "ok", "ок", "yes", "y", "нет", "no", "n"):
        return None
    if facts_flow.get("pending_confirmation") or facts_flow.get("confirmation_prompt"):
        return None
    if facts_flow.get("new_candidates"):
        return None
    if facts_flow.get("committed_facts_this_turn"):
        facts = facts_flow.get("facts") if isinstance(facts_flow.get("facts"), dict) else {}
        if facts.get("pet_cat"):
            return f"Запомнил: кошка — {facts.get('pet_cat')}."
        if facts.get("pet_dog"):
            return f"Запомнил: собака — {facts.get('pet_dog')}."
        if facts.get("name"):
            return f"Запомнил, {facts.get('name')}."
        return "Запомнил."
    if low in ("нет", "no", "n"):
        return "Ок, не записываю."
    return None
