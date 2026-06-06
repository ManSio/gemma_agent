"""
Вывод IANA timezone из user_facts (страна/город) без внешних API.
Используется для запросов «который час» когда timezone в профиле пустой.
"""
from __future__ import annotations

import logging

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore


logger = logging.getLogger(__name__)

def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


# Ключи — фрагменты нормализованной страны (ru/en/локальные названия)
_COUNTRY_TO_TZ: Dict[str, str] = {
    # Беларусь
    "by": "Europe/Minsk",
    "belarus": "Europe/Minsk",
    "беларус": "Europe/Minsk",
    "рб": "Europe/Minsk",
    "republic of belarus": "Europe/Minsk",
    # Россия
    "ru": "Europe/Moscow",
    "russia": "Europe/Moscow",
    "росси": "Europe/Moscow",
    "rf": "Europe/Moscow",
    "russian federation": "Europe/Moscow",
    # Украина
    "ua": "Europe/Kyiv",
    "ukraine": "Europe/Kyiv",
    "украин": "Europe/Kyiv",
    # Польша
    "pl": "Europe/Warsaw",
    "poland": "Europe/Warsaw",
    # Германия
    "de": "Europe/Berlin",
    "germany": "Europe/Berlin",
    # Франция
    "fr": "Europe/Paris",
    "france": "Europe/Paris",
    # Великобритания
    "gb": "Europe/London",
    "uk": "Europe/London",
    "united kingdom": "Europe/London",
    # США
    "us": "America/New_York",
    "usa": "America/New_York",
    "united states": "America/New_York",
    # Казахстан
    "kz": "Asia/Almaty",
    "kazakh": "Asia/Almaty",
    "казах": "Asia/Almaty",
    # Литва
    "lt": "Europe/Vilnius",
    "lithuania": "Europe/Vilnius",
    "литв": "Europe/Vilnius",
    # Латвия
    "lv": "Europe/Riga",
    "latvia": "Europe/Riga",
    "латви": "Europe/Riga",
    # Эстония
    "ee": "Europe/Tallinn",
    "estonia": "Europe/Tallinn",
    "эстони": "Europe/Tallinn",
    # Чехия
    "cz": "Europe/Prague",
    "czech": "Europe/Prague",
    "чехи": "Europe/Prague",
    # Италия
    "it": "Europe/Rome",
    "italy": "Europe/Rome",
    "итали": "Europe/Rome",
    # Испания
    "es": "Europe/Madrid",
    "spain": "Europe/Madrid",
    "испан": "Europe/Madrid",
    # Нидерланды
    "nl": "Europe/Amsterdam",
    "netherlands": "Europe/Amsterdam",
    "нидерланд": "Europe/Amsterdam",
    # Турция
    "tr": "Europe/Istanbul",
    "turkey": "Europe/Istanbul",
    "турци": "Europe/Istanbul",
    # Китай
    "cn": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "кита": "Asia/Shanghai",
    # Япония
    "jp": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "япони": "Asia/Tokyo",
    # Индия
    "in": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "инди": "Asia/Kolkata",
    # ОАЭ
    "ae": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "оаэ": "Asia/Dubai",
    # Израиль
    "il": "Asia/Jerusalem",
    "israel": "Asia/Jerusalem",
    "израил": "Asia/Jerusalem",
    # Австралия
    "au": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "австрали": "Australia/Sydney",
    # Канада (восточная часть как наиболее населённая)
    "ca": "America/Toronto",
    "canada": "America/Toronto",
    "канад": "America/Toronto",
    # Грузия
    "ge": "Asia/Tbilisi",
    "georgia": "Asia/Tbilisi",
    "грузи": "Asia/Tbilisi",
    # Армения
    "am": "Asia/Yerevan",
    "armenia": "Asia/Yerevan",
    "армени": "Asia/Yerevan",
    # Азербайджан
    "az": "Asia/Baku",
    "azerbaijan": "Asia/Baku",
    "азербайдж": "Asia/Baku",
    # Узбекистан
    "uz": "Asia/Tashkent",
    "uzbekistan": "Asia/Tashkent",
    "узбек": "Asia/Tashkent",
    # Молдова
    "md": "Europe/Chisinau",
    "moldova": "Europe/Chisinau",
    "молдов": "Europe/Chisinau",
    # Финляндия
    "fi": "Europe/Helsinki",
    "finland": "Europe/Helsinki",
    "финлянди": "Europe/Helsinki",
    # Швеция
    "se": "Europe/Stockholm",
    "sweden": "Europe/Stockholm",
    "швеци": "Europe/Stockholm",
    # Норвегия
    "no": "Europe/Oslo",
    "norway": "Europe/Oslo",
    "норвеги": "Europe/Oslo",
    # Швейцария
    "ch": "Europe/Zurich",
    "switzerland": "Europe/Zurich",
    "швейцари": "Europe/Zurich",
    # Бельгия
    "be": "Europe/Brussels",
    "belgium": "Europe/Brussels",
    "бельги": "Europe/Brussels",
    # Австрия
    "at": "Europe/Vienna",
    "austria": "Europe/Vienna",
    "австри": "Europe/Vienna",
    # Румыния
    "ro": "Europe/Bucharest",
    "romania": "Europe/Bucharest",
    "румыни": "Europe/Bucharest",
    # Болгария
    "bg": "Europe/Sofia",
    "bulgaria": "Europe/Sofia",
    "болгари": "Europe/Sofia",
    # Сербия
    "rs": "Europe/Belgrade",
    "serbia": "Europe/Belgrade",
    "серби": "Europe/Belgrade",
    # Греция
    "gr": "Europe/Athens",
    "greece": "Europe/Athens",
    "греци": "Europe/Athens",
    # Египет
    "eg": "Africa/Cairo",
    "egypt": "Africa/Cairo",
    "египет": "Africa/Cairo",
    # ЮАР
    "za": "Africa/Johannesburg",
    "south africa": "Africa/Johannesburg",
    "юар": "Africa/Johannesburg",
    # Бразилия
    "br": "America/Sao_Paulo",
    "brazil": "America/Sao_Paulo",
    "бразили": "America/Sao_Paulo",
    # Аргентина
    "ar": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    "аргентин": "America/Argentina/Buenos_Aires",
    # Саудовская Аравия
    "sa": "Asia/Riyadh",
    "saudi": "Asia/Riyadh",
    "саудов": "Asia/Riyadh",
    # Таиланд
    "th": "Asia/Bangkok",
    "thailand": "Asia/Bangkok",
    "таиланд": "Asia/Bangkok",
    # Корея (Южная)
    "kr": "Asia/Seoul",
    "south korea": "Asia/Seoul",
    "коре": "Asia/Seoul",
}

# Города → пояс (если страна не сматчилась)
_CITY_TO_TZ: Dict[str, str] = {
    "минск": "Europe/Minsk",
    "михановичи": "Europe/Minsk",
    "гомель": "Europe/Minsk",
    "брест": "Europe/Minsk",
    "москв": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "киев": "Europe/Kyiv",
    "kyiv": "Europe/Kyiv",
    "warsaw": "Europe/Warsaw",
    "варшава": "Europe/Warsaw",
    "london": "Europe/London",
    "лондон": "Europe/London",
    "berlin": "Europe/Berlin",
    "берлин": "Europe/Berlin",
    "paris": "Europe/Paris",
    "париж": "Europe/Paris",
    "new york": "America/New_York",
    "tallinn": "Europe/Tallinn",
    "таллин": "Europe/Tallinn",
    "vilnius": "Europe/Vilnius",
    "вильнюс": "Europe/Vilnius",
    "riga": "Europe/Riga",
    "рига": "Europe/Riga",
    "prague": "Europe/Prague",
    "прага": "Europe/Prague",
    "rome": "Europe/Rome",
    "рим": "Europe/Rome",
    "madrid": "Europe/Madrid",
    "мадрид": "Europe/Madrid",
    "tokyo": "Asia/Tokyo",
    "токио": "Asia/Tokyo",
    "dubai": "Asia/Dubai",
    "дубай": "Asia/Dubai",
    "istanbul": "Europe/Istanbul",
    "стамбул": "Europe/Istanbul",
    "shanghai": "Asia/Shanghai",
    "шанхай": "Asia/Shanghai",
}


# Запрос «который час» / current time — только явные формулировки (не подстроки вроде «часовой», «sometimes»).
# «в текущее время …» — устойчивое «сейчас», не вопрос о часах; «сегодня дата X» / «дата и время прилёта» — тоже не wall-clock.
_WALL_CLOCK_INTENT_RE = re.compile(
    r"(?ui)"
    r"\b(?:который|какой)\s+(?:сейчас\s+)?час\b|"
    r"\bсколько(?:\s+\S+){0,5}?\s*(?:сейчас\s+)?(?:времени|часов|время)\b|"
    r"\b(?:какое|какой)\s+(?:у\s+(?:меня|тебя|нас|него|неё|нее)\s+)?(?:локальн\w*\s+)?(?:сейчас\s+)?время\b|"
    r"(?<![в]\s)текущее\s+время\b|"
    r"\b(?:какая|какой)\s+сегодня\s+дата\b|"
    r"\b(?:какая|какой)\s+дата\s+и\s+время\b|"
    r"\bдата\s+и\s+время\s*\?|"
    r"\bwhat(?:'s| is|\s+is)?\s+(?:the\s+)?time\b"
)

# «питерское время», «я в Санкт-Петербурге» — без отдельного LLM-шага
_TZ_STATEMENT_RE = re.compile(
    r"(?ui)"
    r"(?:"
    r"(?:питерск\w*|петербургск\w*|с\s*пб|спб)\w*\s+(?:врем\w*|час\w*)"
    r"|(?:московск\w*|минск\w*|киевск\w*)\w*\s+(?:врем\w*|час\w*)"
    r"|(?:utc|gmt)\s*[+-]?\s*\d{1,2}"
    r"|(?:я\s+)?(?:живу|нахожусь|в)\s+(?:в\s+)?(?:санкт[-\s]?петербург\w*|питер\w*|москв\w*|минск\w*|киев\w*)"
    r"|(?:у\s+меня|моё|мое)\s+(?:локальн\w*\s+)?(?:питерск\w*|петербургск\w*|московск\w*)\s+(?:врем\w*|час\w*)"
    r")"
)


def looks_like_wall_clock_question(text: Any) -> bool:
    """True, если пользователь спрашивает текущее время/дату, а не просто упоминает «время» или «часовой пояс» в другом смысле."""
    s = _norm(text)
    if not s:
        return False
    if re.search(r"(?ui)сколько\s+(?:частей|элементов|букв|символов|строк|абзац)", s):
        return False
    return bool(_WALL_CLOCK_INTENT_RE.search(s))


def parse_location_timezone_from_statement(text: Any) -> Dict[str, str]:
    """Из явной реплики пользователя — city и/или IANA timezone (мутация не делает)."""
    s = _norm(text)
    out: Dict[str, str] = {}
    if not s or not _TZ_STATEMENT_RE.search(s):
        return out
    if re.search(r"(?ui)(?:питер|петербург|с\s*пб|спб)", s):
        out["city"] = "Санкт-Петербург"
        out["country"] = "RU"
        out["timezone"] = "Europe/Moscow"
    elif re.search(r"(?ui)москв", s):
        out["city"] = "Москва"
        out["country"] = "RU"
        out["timezone"] = "Europe/Moscow"
    elif re.search(r"(?ui)минск", s):
        out["city"] = "Минск"
        out["country"] = "BY"
        out["timezone"] = "Europe/Minsk"
    elif re.search(r"(?ui)киев|kyiv", s):
        out["city"] = "Киев"
        out["country"] = "UA"
        out["timezone"] = "Europe/Kyiv"
    m = re.search(r"(?ui)(?:utc|gmt)\s*([+-])\s*(\d{1,2})", s)
    if m and "timezone" not in out:
        sign, hrs = m.group(1), int(m.group(2))
        if sign == "+" and hrs == 3:
            out["timezone"] = "Europe/Moscow"
        elif sign == "+" and hrs == 2:
            out["timezone"] = "Europe/Kyiv"
    return out


def apply_stated_timezone_to_facts(text: Any, facts: Dict[str, Any]) -> bool:
    """Записать city/country/timezone из явной реплики; не перезаписывает timezone если уже есть."""
    if not isinstance(facts, dict):
        return False
    parsed = parse_location_timezone_from_statement(text)
    if not parsed:
        return False
    changed = False
    for key in ("city", "country"):
        val = str(parsed.get(key) or "").strip()
        if val and not str(facts.get(key) or "").strip():
            facts[key] = val
            changed = True
    tz_new = str(parsed.get("timezone") or "").strip()
    if tz_new and not str(facts.get("timezone") or "").strip():
        facts["timezone"] = tz_new
        changed = True
    return changed


def infer_timezone_from_facts(facts: Dict[str, Any]) -> Optional[str]:
    if not isinstance(facts, dict):
        return None
    tz = str(facts.get("timezone") or "").strip()
    if tz:
        return tz
    country = _norm(facts.get("country"))
    city = _norm(facts.get("city"))
    for key, iana in _COUNTRY_TO_TZ.items():
        if key in country:
            return iana
    for key, iana in _CITY_TO_TZ.items():
        if city and key in city:
            return iana
    return None


def ensure_timezone_in_user_facts(facts: Dict[str, Any]) -> Optional[str]:
    """Вывести timezone из страны/города и сразу записать в facts (мутация).
    Возвращает IANA-строку или None если вывести не из чего.
    Вызывать один раз после загрузки persisted, перед сохранением.
    Не перезаписывает уже установленный timezone."""
    if not isinstance(facts, dict):
        return None
    if str(facts.get("timezone") or "").strip():
        return str(facts.get("timezone")).strip()
    inferred = infer_timezone_from_facts(facts)
    if inferred:
        facts["timezone"] = inferred
    return inferred


def format_clock_hint_for_llm(
    *,
    effective_tz: Optional[str],
    telegram_message_unix: Optional[int] = None,
) -> str:
    """Текст в external_hint: жёсткие факты о времени, чтобы модель не отвечала «нет доступа»."""
    now_utc = datetime.now(timezone.utc)
    lines = [
        f"Текущий момент на сервере (реально): {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC.",
    ]
    if telegram_message_unix is not None:
        try:
            mt = datetime.fromtimestamp(int(telegram_message_unix), tz=timezone.utc)
            lines.append(f"Время отправки этого сообщения в Telegram (UTC): {mt.strftime('%Y-%m-%d %H:%M:%S')} UTC.")
        except (OSError, ValueError, TypeError):
            pass
    if effective_tz and ZoneInfo:
        try:
            loc = now_utc.astimezone(ZoneInfo(effective_tz))
            lines.append(
                f"Локальное время в поясе {effective_tz}: {loc.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(пояс из профиля пользователя или выведен из страны/города)."
            )
            d0 = loc.date()
            d_m1 = d0 - timedelta(days=1)
            d_p1 = d0 + timedelta(days=1)
            lines.append(
                f"Календарь в этом поясе: сегодня={d0.isoformat()}, вчера={d_m1.isoformat()}, завтра={d_p1.isoformat()}."
            )
        except Exception:
            lines.append(f"Пояс {effective_tz!r} не удалось применить; отвечай по UTC.")
            d_utc = now_utc.date()
            lines.append(
                f"Календарь (UTC): сегодня={d_utc.isoformat()}, вчера={(d_utc - timedelta(days=1)).isoformat()}, "
                f"завтра={(d_utc + timedelta(days=1)).isoformat()}."
            )
    else:
        lines.append(
            "Часовой пояс пользователя неизвестен — дай время в UTC и одной строкой предложи назвать пояс, если нужен локальный."
        )
        d_utc = now_utc.date()
        lines.append(
            f"Календарь (UTC): сегодня={d_utc.isoformat()}, вчера={(d_utc - timedelta(days=1)).isoformat()}, "
            f"завтра={(d_utc + timedelta(days=1)).isoformat()}."
        )
    lines.append(
        "Фразы «вчера/сегодня утром/завтра»: опирайся на календарь выше; в recent_dialogue поле telegram_ts — Unix UTC, "
        "можно сравнить с полуночью нужного локального дня (или с границами UTC, если пояс неизвестен)."
    )
    lines.append(
        "Используй эти строки как факты; не утверждай, что нет доступа к текущему времени и не проси пояс, если он уже выведен выше."
    )
    lines.append(
        "Это справка по дате/времени для формулировок «сегодня/завтра»; не путай её с полнотой погодного прогноза. "
        "Если в том же external_hint выше уже есть блок с температурой/осадками — опирайся на него для погоды, а не на этот календарь."
    )
    return "\n".join(lines)


_RU_WEEKDAY = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
_RU_MONTH_GEN = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_TZ_USER_LABEL: Dict[str, str] = {
    "Europe/Minsk": "по минскому времени",
    "Europe/Moscow": "по московскому времени",
    "Europe/Kiev": "по киевскому времени",
    "Europe/Kyiv": "по киевскому времени",
    "Europe/Warsaw": "по варшавскому времени",
}


def _clock_label(*, effective_tz: Optional[str], city: Optional[str] = None) -> str:
    city_n = _norm(city)
    if effective_tz == "Europe/Moscow":
        if any(k in city_n for k in ("питер", "петербург", "спб")):
            return "по петербургскому времени"
        if "москв" in city_n:
            return "по московскому времени"
    if effective_tz == "Europe/Minsk" and "минск" in city_n:
        return "по минскому времени"
    if effective_tz in ("Europe/Kiev", "Europe/Kyiv") and "киев" in city_n:
        return "по киевскому времени"
    return _TZ_USER_LABEL.get(str(effective_tz or ""), f"по поясу {effective_tz}")


def format_wall_clock_user_reply(
    *,
    effective_tz: Optional[str],
    telegram_message_unix: Optional[int] = None,
    city: Optional[str] = None,
) -> str:
    """Готовый ответ пользователю на «который час» — без сухого «12:31»."""
    anchor = datetime.now(timezone.utc)
    if telegram_message_unix is not None:
        try:
            anchor = datetime.fromtimestamp(int(telegram_message_unix), tz=timezone.utc)
        except (OSError, ValueError, TypeError):
            pass
    if effective_tz and ZoneInfo:
        try:
            loc = anchor.astimezone(ZoneInfo(effective_tz))
            wd = _RU_WEEKDAY[loc.weekday()] if 0 <= loc.weekday() <= 6 else ""
            mon = _RU_MONTH_GEN[loc.month] if 1 <= loc.month <= 12 else str(loc.month)
            tz_label = _clock_label(effective_tz=effective_tz, city=city)
            date_bit = f"{wd}, {loc.day} {mon} {loc.year}" if wd else loc.strftime("%d.%m.%Y")
            return f"Сейчас {loc.strftime('%H:%M')} {tz_label} ({date_bit})."
        except Exception as e:
            logger.debug('%s optional failed: %s', 'timezone_inference', e, exc_info=True)
    utc_s = anchor.strftime("%H:%M")
    return (
        f"Сейчас {utc_s} UTC. "
        "Если нужен локальный час — напишите город или часовой пояс."
    )


def try_wall_clock_direct_reply(
    user_text: str,
    *,
    user_facts: Any = None,
    recent_dialogue: Any = None,
    telegram_message_unix: Any = None,
) -> str:
    """Детерминированный ответ «который час» — для plan/pipeline без LLM."""
    if not looks_like_wall_clock_question(user_text):
        return ""
    try:
        from core.brain.text_helpers import normalize_user_facts
    except Exception:
        normalize_user_facts = lambda x: dict(x) if isinstance(x, dict) else {}  # type: ignore

    uf = normalize_user_facts(user_facts)
    for _row in (recent_dialogue or [])[-6:]:
        if isinstance(_row, dict) and str(_row.get("role") or "").lower() in ("user", "human", ""):
            apply_stated_timezone_to_facts(_row.get("text") or _row.get("content") or "", uf)
    apply_stated_timezone_to_facts(user_text, uf)
    ensure_timezone_in_user_facts(uf)
    try:
        tg_i = int(telegram_message_unix) if telegram_message_unix is not None else None
    except (TypeError, ValueError):
        tg_i = None
    eff_tz = str(uf.get("timezone") or "").strip() or infer_timezone_from_facts(uf) or None
    return format_wall_clock_user_reply(
        effective_tz=eff_tz,
        telegram_message_unix=tg_i,
        city=str(uf.get("city") or "").strip() or None,
    )
