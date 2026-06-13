"""
Эвристики intent без LLM: отделить «математика» от ссылок (t.me/+invite, query с =, и т.д.).

UTC/GMT±N: вырезаются перед math-probe; см. также is_utc_gmt_offset_only_message — второй рубеж в modules/math
(если роутер снова отдаст math без деплоя старого кода или из другого entrypoint).
"""
from __future__ import annotations

import logging

import os
import re

from core.regex_safe import collapse_whitespace, safe_re_search

# URL и похожие фрагменты вырезаем перед проверкой «есть цифры и +-*/=»
_URL_LIKE = re.compile(
    r"(?i)\b(?:https?://|www\.|t\.me/|telegram\.me/|tg://)[^\s]+",
)
# «картинку/документ», «и/или» — слэш не оператор деления (иначе + цифры «1.» из списка → math)
_JOIN_WORD_SLASH = re.compile(r"(?u)(?<=\w)/(?=\w)")
# Смещение пояса (UTC+3, GMT-5) — не математическое выражение для роутера math.
_TZ_UTC_GMT_OFFSET = re.compile(
    r"(?i)\(?\s*(?:UTC|GMT)\s*[+\-−]\s*\d{1,2}(?::\d{2})?\s*\)?",
)


logger = logging.getLogger(__name__)

def _strip_tz_offsets_for_math_probe(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = _TZ_UTC_GMT_OFFSET.sub(" ", s)
    return collapse_whitespace(s)


def _math_strict_mode_enabled() -> bool:
    raw = os.getenv("BRAIN_MATH_STRICT_MODE", "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _scrub_prose_joiners_for_math_probe(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = _JOIN_WORD_SLASH.sub(" ", s)
    return collapse_whitespace(s)


def _scrub_hyphen_between_letters_for_math_probe(s: str) -> str:
    """
    Дефис внутри слова («скин-тоны», «какой-то») не должен вместе с цифрами
    из другого места текста давать naive math (ложный /calc).
    """
    t = s or ""
    if "-" not in t or len(t) < 3:
        return t
    out: list[str] = []
    for i, ch in enumerate(t):
        if ch == "-" and i > 0 and i + 1 < len(t):
            if t[i - 1].isalpha() and t[i + 1].isalpha():
                out.append(" ")
                continue
        out.append(ch)
    return "".join(out)


def _scrub_hyphen_letter_digit_boundary_for_math_probe(s: str) -> str:
    """
    Дефис между буквой и цифрой (КРОКОДИЛ-774, SKU-12) — не математический минус;
    иначе «774» + этот «-» дают ложный naive math и ответ модуля /calc.
    Выражения вида 2-2 или 12-34 не трогаем (цифра–цифра).
    """
    t = s or ""
    if "-" not in t or len(t) < 3:
        return t
    out: list[str] = []
    for i, ch in enumerate(t):
        if ch == "-" and i > 0 and i + 1 < len(t):
            L, R = t[i - 1], t[i + 1]
            if (L.isalpha() and R.isdigit()) or (L.isdigit() and R.isalpha()):
                out.append(" ")
                continue
        out.append(ch)
    return "".join(out)


def _scrub_unary_plus_before_digits_for_math_probe(s: str) -> str:
    r"""
    «+375», «latin +123» — плюс перед числом, не оператор «2+2».
    Плюс не удаляется, если слева уже цифра (например 2+2).
    """
    t = (s or "").strip()
    if "+" not in t:
        return s or ""
    return re.sub(r"(?<!\d)\+(?=\d)", " ", t)


def strip_urls_and_mentions_for_math_probe(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _URL_LIKE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Глаголы — с границами слов для EN (иначе «calculate» внутри «recalculate», «compute» внутри «computer» → ложный math).
# «пример\s*:» без \b даёт ложное срабатывание на «например:»; «уравнен» — подстрока в «уравнение».
_MATH_VERB_RE = re.compile(
    r"(?i)(?:"
    r"посчитай|посчитать|посчита|вычисли|вычислить|считай|посчитаем|"
    # NB: не используем голое «задачу» как триггер — в обычной речи это даёт ложный math.
    # «реши» только как целое слово — иначе ложное срабатывание на «разрешило/разрешение».
    r"сколько\s+будет|\bреши(?:\s+уравн)?|\bреши\s+задач\w*|/calc\b|"
    r"\bуравнен(ие|ия|ию|ием|ии|ий|ым|ых|ом|ами)\b|\bквадратн[а-яё]*\s+уравнен(ие|ия)\b|\bпример\s*:|"
    r"\bcalculate\b|\bcomputes?\b|\bsolve\s+for\b|\bmath\s*problem\b"
    r")",
)

# «Посчитай количество символов # …» — задача на разбор строки/логику, не арифметический /calc.
_COUNT_SYMBOLS_IN_TEXT_RE = re.compile(r"(?i)количеств\w*\s+символ")
_SENSOR_TEXT_ANALYSIS_RE = re.compile(
    r"(?is)(?:\btest\s*[a-z0-9]\b|\bтест\s*[a-zа-я0-9]\b|позици\w+|посимв\w+|букв\w+|символ\w+)"
)

_ARITHMETIC_DIGIT_OP_DIGIT_RE = re.compile(r"\d\s*[\+\-\*/]\s*\d")


def _long_prose_allows_explicit_math_verb(text: str, s: str) -> bool:
    """
    В длинном сообщении глагол «посчитай/вычисли/пример:…» без /calc, без компактного выражения
    и без фрагмента «цифра оператор цифра» не должен уводить в math (шаблон «отправьте /calc»).
    """
    raw = (text or "").strip()
    try:
        thresh = int((os.getenv("MATH_VERB_LONG_PROSE_CHARS") or "320").strip())
    except ValueError:
        thresh = 320
    thresh = max(160, min(thresh, 8000))
    if len(raw) <= thresh:
        return True
    if re.search(r"(?i)/calc\b", s):
        return True
    if _implicit_compact_arithmetic_ok(raw, s):
        return True
    if _ARITHMETIC_DIGIT_OP_DIGIT_RE.search(s):
        return True
    return False


def _looks_like_symbolic_text_analysis_task(text: str, s: str) -> bool:
    """
    Длинные задания на подсчёт букв/символов/позиций (TEST A/B/C, «посимвольно», «позиции»)
    не должны уходить в /calc по одному слову «посчитай».
    """
    raw = (text or "").strip()
    if len(raw) < 180:
        return False
    if re.search(r"(?i)/calc\b", s):
        return False
    if _ARITHMETIC_DIGIT_OP_DIGIT_RE.search(s):
        return False
    return bool(_SENSOR_TEXT_ANALYSIS_RE.search(raw))


def _strip_gt_chat_quote_prefixes(text: str) -> str:
    """Убрать префиксы вида «> …» из вставки из чата (цитаты Telegram и т.п.)."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(">"):
            s = s[1:].strip()
        lines.append(s)
    return "\n".join(lines).strip()


def prose_narrative_disfavors_calculator(text: str) -> bool:
    """
    Длинный сюжет/задача со словами про проценты, баланс, сценарии — не явный калькулятор.
    Снижает ложные math и лишнее уточнение math_ambiguous (B1-бенчмарк, фин. план и т.п.).
    """
    raw = (text or "").strip()
    if len(raw) < 130:
        return False
    low = raw.lower()
    markers = (
        "налог",
        "процент",
        "баланс",
        "депозит",
        "вклад",
        "бенчмарк",
        "таблиц",
        "итерац",
        "сценари",
        "риск",
        "ликвидн",
        "usd",
        "eur",
        "руб",
        "byn",
        "конец дня",
        "каждый день",
        "чётн",
        "нечётн",
        "дилемм",
        "если ",
        "word problem",
        "compound interest",
        "формул",
        "расчет",
        "расчёт",
        "итоговая оценка",
        "критерий",
        "статус",
    )
    hits = sum(1 for m in markers if m in low)
    if hits >= 2:
        return True
    if re.search(r"день\s*[1-9]\d?", low) and ("баланс" in low or "налог" in low or "процент" in low):
        return True
    return False


def is_system_operator_directive(text: str) -> bool:
    """
    Управляющая инструкция для ассистента (самокоррекция, правила), не запрос в калькулятор.
    Срабатывает по заголовку «СИСТЕМНАЯ ДИРЕКТИВА» или по сочетанию маркеров длинного ТЗ.
    """
    raw = _strip_gt_chat_quote_prefixes(text)
    if not raw:
        return False
    probe = re.sub(r"[*_`]+", "", raw)
    low = probe.lower()
    head = low[:900]
    if head.startswith("системная директива"):
        return True
    if head.startswith("system directive"):
        return True
    if re.search(r"(?im)^\s*системная\s+директива\b", probe[:1600]):
        return True
    if re.search(r"(?im)^\s*system\s+directive\b", probe[:1600]):
        return True
    markers = (
        "работа над ошибками",
        "твой новый алгоритм проверки себя",
        "обязательные изменения",
        "финансово-математический блок",
        "блок логических итераций",
        "сначала найди аномали",
        "автономность и инструменты",
        "дифференциальной диагностики",
    )
    hits = sum(1 for m in markers if m in low)
    if hits >= 3 and len(low) >= 220:
        return True
    if "системн" in low and "директив" in low and len(low) >= 80:
        return True
    return False

# Дата/слэш — не «деление» для имплицитного math
_DATE_SLASH_RE = re.compile(r"\b\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b")
# ISO-даты и метки времени — иначе «2026-05-01» даёт ложное «6-0» для math probe
_ISO_DATE_RE = re.compile(
    r"\b(19|20)\d{2}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\u2212\-]\d{2}:\d{2})?)?\b"
)


def looks_like_structured_multistep_instruction(text: str) -> bool:
    """
    Длинный структурированный чеклист (1) ... 2) ... / «шаг 1» / маркеры «запомни, ответь»):
    это не «неявная математика» даже при наличии цифр и операторов внутри одного из пунктов.
    """
    raw = (text or "").strip()
    if len(raw) < 120:
        return False
    low = raw.lower()
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[\).]|[-*])\s+", raw))
    if numbered < 3:
        numbered += len(re.findall(r"(?iu)\bшаг\s*\d+\b", low))
    if numbered < 3:
        return False
    action_markers = (
        "запомни",
        "проверка памяти",
        "кратко",
        "анти-мета",
        "ответь",
        "сделай",
        "прибавь",
        "умнож",
        "explain",
        "remember",
        "step",
    )
    hits = sum(1 for m in action_markers if m in low)
    return hits >= 2


def _scrub_iso_dates_for_math_probe(s: str) -> str:
    if not s:
        return s
    return _ISO_DATE_RE.sub(" ", s)


def _looks_like_memory_or_facts_dump(text: str) -> bool:
    """Дамп /facts, mem0, JSON-мета — там «=», цифры и «-» в датах; не калькулятор."""
    t = text or ""
    if len(t) < 60:
        return False
    low = t.lower()
    markers = (
        "expires_at",
        "updated_at",
        "confidence",
        "message_extract",
        "revoked",
        "user_facts",
        "значения",
        "мета",
        "mem0",
        "routing_prefs",
    )
    return sum(1 for m in markers if m in low) >= 2


def _letter_count_alpha(s: str) -> int:
    return sum(1 for c in (s or "") if c.isalpha())


def _implicit_compact_arithmetic_ok(raw: str, s: str) -> bool:
    """
    Разрешить math без слов «посчитай» только для короткого, почти формульного фрагмента.
    Длинный текст с вкраплением «2+2» сюда не проходит.
    """
    r = (raw or "").strip()
    s2 = (s or "").strip()
    if not s2:
        return False
    try:
        raw_max = int((os.getenv("MATH_IMPLICIT_RAW_MAX_CHARS") or "220").strip())
        scrub_max = int((os.getenv("MATH_IMPLICIT_SCRUB_MAX_CHARS") or "132").strip())
        letters_max = int((os.getenv("MATH_IMPLICIT_MAX_LETTERS") or "28").strip())
    except ValueError:
        raw_max, scrub_max, letters_max = 220, 132, 28
    if len(r) > raw_max or len(s2) > scrub_max:
        return False
    if _letter_count_alpha(s2) > letters_max:
        return False
    if _DATE_SLASH_RE.search(s2):
        return False
    if not re.search(r"\d\s*[\+\-\*/]\s*\d", s2):
        return False
    return True


def normalized_math_probe_scrub(text: str) -> str:
    """Текст после тех же очисток, что и для explicit_math (без lower)."""
    s0 = strip_urls_and_mentions_for_math_probe(text)
    s = _scrub_iso_dates_for_math_probe(s0)
    s = _scrub_prose_joiners_for_math_probe(s)
    s = _scrub_hyphen_between_letters_for_math_probe(s)
    s = _scrub_hyphen_letter_digit_boundary_for_math_probe(s)
    s = _scrub_unary_plus_before_digits_for_math_probe(s)
    return s


def _long_prose_without_math_cue(text: str, *, max_chars: int) -> bool:
    """Длинный текст без явной математической формулировки — не вмешиваемся с «уточнением про калькулятор»."""
    raw = (text or "").strip()
    if len(raw) <= max_chars:
        return False
    low = raw.lower()
    if _MATH_VERB_RE.search(low):
        return False
    return True


def math_route_is_ambiguous(text: str) -> bool:
    """
    Похоже на арифметический фрагмент (цифра оператор цифра), но нет явного запроса в math
    и сообщение не компактное — лучше спросить у пользователя, чем молча увести в диалог или калькулятор.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if looks_like_structured_multistep_instruction(raw):
        return False
    if prose_narrative_disfavors_calculator(raw):
        return False
    try:
        prose_cutoff = int((os.getenv("MATH_AMBIGUOUS_SKIP_LONG_PROSE_CHARS") or "320").strip())
    except ValueError:
        prose_cutoff = 320
    prose_cutoff = max(120, min(prose_cutoff, 8000))
    if _long_prose_without_math_cue(raw, max_chars=prose_cutoff):
        return False
    if is_system_operator_directive(raw):
        return False
    if _looks_like_memory_or_facts_dump(raw):
        return False
    try:
        from core.module_gen_intent import plugin_programming_prefers_general

        if plugin_programming_prefers_general(raw):
            return False
    except Exception as e:
        logger.debug('%s optional failed: %s', 'intent_heuristics', e, exc_info=True)
    s0 = strip_urls_and_mentions_for_math_probe(raw)
    if explicit_math_request(raw, s0):
        return False
    if not naive_math_intent_from_text(raw):
        return False
    s = normalized_math_probe_scrub(raw)
    if not re.search(r"\d\s*[\+\-\*/]\s*\d", s):
        return False
    if _implicit_compact_arithmetic_ok(raw, s):
        return False
    return True


def explicit_math_request(text: str, scrubbed: Optional[str] = None) -> bool:
    """
    Math intent только если:
    - явная просьба (посчитать, уравнение, calc, …) / команда /calc; или
    - короткое почти чисто арифметическое сообщение (без простыни текста вокруг).
    Вкрапление «3+7» в большой текст без ключевых слов — не math.
    """
    if is_system_operator_directive(text or ""):
        return False
    if user_asked_disable_calculator_router(text or ""):
        return False
    if scrubbed is not None:
        s0 = scrubbed
        s = _scrub_prose_joiners_for_math_probe(s0)
        s = _scrub_hyphen_between_letters_for_math_probe(s)
        s = _scrub_hyphen_letter_digit_boundary_for_math_probe(s)
        s = _scrub_unary_plus_before_digits_for_math_probe(s)
    else:
        s = normalized_math_probe_scrub(text)
    blob = f"{text or ''} {s}".lower()
    if _math_strict_mode_enabled():
        return bool(re.search(r"(?i)/calc\b", s))
    if _looks_like_symbolic_text_analysis_task(text or "", s):
        return _implicit_compact_arithmetic_ok((text or "").strip(), s)
    # Для длинной аналитики/отчётов не включаем math по одному слову-триггеру:
    # либо явный /calc, либо компактное формульное сообщение.
    if prose_narrative_disfavors_calculator(text or "") and not re.search(r"(?i)/calc\b", s):
        return _implicit_compact_arithmetic_ok((text or "").strip(), s)
    if _MATH_VERB_RE.search(blob):
        if _COUNT_SYMBOLS_IN_TEXT_RE.search(blob):
            return _implicit_compact_arithmetic_ok((text or "").strip(), s)
        if _long_prose_allows_explicit_math_verb(text or "", s):
            return True
    if re.search(r"(?i)/calc\b", s):
        return True
    if _implicit_compact_arithmetic_ok((text or "").strip(), s):
        return True
    return False


def explicit_summarization_request(text: str) -> bool:
    """«резюме/суммариз» в начале или короткий запрос — не середина статьи."""
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if not any(t in low for t in ("суммариз", "кратко перескаж", "резюме", "summarize")):
        return False
    if len(raw) < 200:
        return True
    if re.search(r"(?i)^(?:суммариз|кратко\s+перескаж|резюме|summarize)\b", raw):
        return True
    return False


def explicit_research_request(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    triggers = (
        "исследуй",
        "разбери тему",
        "найди информацию",
        "изучи вопрос",
        "глубокий анализ",
    )
    if not any(t in low for t in triggers):
        return False
    if len(raw) < 220:
        return True
    if re.search(
        r"(?i)^(?:исследуй|разбери\s+тему|найди\s+информацию|изучи\s+вопрос|глубокий\s+анализ)\b",
        raw,
    ):
        return True
    return False


def explicit_troubleshooting_request(text: str) -> bool:
    """Проблема с ботом/сервисом — не медицинская «ошибка в лечении»."""
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if any(
        t in low
        for t in (
            "не работает",
            "сломалось",
            "ошибка в боте",
            "помоги разобраться",
            "траблшутинг",
            "диагностируй",
            "почини",
        )
    ):
        return True
    if "ошибка" in low and any(t in low for t in ("бот", "telegram", "gemma", "сервер", "api")):
        return True
    return False


def explicit_quick_explain_request(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if not any(
        t in low
        for t in (
            "почему",
            "отчего",
            "зачем",
            "explain",
            "объясни",
            "поясни",
            "расскажи почему",
            "расскажи про",
            "расскажи о ",
            "расскажи об ",
            "простыми словами",
            "простым языком",
        )
    ):
        return False
    if len(raw) < 160:
        return True
    if re.search(
        r"(?i)^(?:почему|отчего|зачем|объясни|поясни|расскажи|explain)\b",
        raw,
    ):
        return True
    return False


def naive_math_intent_from_text(text: str) -> bool:
    """Старая эвристика «цифры и оператор» — только на тексте без URL."""
    if is_system_operator_directive(text or ""):
        return False
    if _looks_like_memory_or_facts_dump(text or ""):
        return False
    s = strip_urls_and_mentions_for_math_probe(text)
    s = _scrub_iso_dates_for_math_probe(s)
    s = _scrub_prose_joiners_for_math_probe(s)
    s = _scrub_hyphen_between_letters_for_math_probe(s)
    s = _scrub_hyphen_letter_digit_boundary_for_math_probe(s)
    s = _scrub_unary_plus_before_digits_for_math_probe(s)
    s = _strip_tz_offsets_for_math_probe(s)
    if not s:
        return False
    if any(ch.isdigit() for ch in s) and any(op in s for op in "+-*/="):
        return True
    return False


def is_utc_gmt_offset_only_message(text: str) -> bool:
    """Реплика состоит только из UTC/GMT±… (и пробелов) — не выражение для калькулятора."""
    raw = (text or "").strip()
    if not raw:
        return False
    s = strip_urls_and_mentions_for_math_probe(text)
    s = _scrub_prose_joiners_for_math_probe(s)
    s = _strip_tz_offsets_for_math_probe(s)
    return len(s) == 0


def user_asked_disable_calculator_router(text: str) -> bool:
    t = (text or "").lower()
    return any(
        p in t
        for p in (
            "калькулятор не нужен",
            "убери калькулятор",
            "без калькулятора",
            "не нужен калькулятор",
            "не суй калькулятор",
            "не суешь калькулятор",
            "не калькулятор",
            "не вызывай калькулятор",
            "не калькулятор вызывай",
            "без вызова калькулятор",
            "ложн срабатыван",
            "ложные срабатыван",
            "не предлагай /calc",
            "не предлагай calc",
            "отключи калькулятор",
            "выключи калькулятор",
        )
    )


def user_asked_enable_calculator_router(text: str) -> bool:
    t = (text or "").lower()
    return any(
        p in t
        for p in (
            "можно калькулятор",
            "включи калькулятор",
            "верни калькулятор",
            "нужен калькулятор",
        )
    )


def merge_routing_prefs_from_turn(record: Dict[str, Any], user_text: str) -> None:
    """Обновляет record['routing_prefs'] по реплике пользователя (вызывать из behavior_store)."""
    if not isinstance(record, dict):
        return
    rp = dict(record.get("routing_prefs") or {})
    changed = False
    try:
        from core.dialogue_feedback_signals import merge_recent_remarks_into_routing_prefs

        before = str(rp.get("recent_user_remarks") or "")
        rp = merge_recent_remarks_into_routing_prefs(rp, user_text or "")
        if str(rp.get("recent_user_remarks") or "") != before:
            changed = True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'intent_heuristics', e, exc_info=True)
    if user_asked_disable_calculator_router(user_text):
        if not rp.get("prefer_general_over_math"):
            rp["prefer_general_over_math"] = True
            changed = True
    if user_asked_enable_calculator_router(user_text):
        if rp.get("prefer_general_over_math"):
            rp["prefer_general_over_math"] = False
            changed = True
    try:
        from core.policy_memory_runtime import detect_web_not_rss_preference

        if detect_web_not_rss_preference(user_text or ""):
            ps = dict(rp.get("policy_slots") or {})
            pref = dict(ps.get("user_pref") or {})
            if not pref.get("web_over_rss"):
                pref["web_over_rss"] = True
                ps["user_pref"] = pref
                rp["policy_slots"] = ps
                changed = True
    except Exception as e:
        logger.debug("policy_slots web_not_rss: %s", e)
    if changed or rp:
        record["routing_prefs"] = rp


_INTENT_TEXT_PATTERNS: Dict[str, List[str]] = {
    "explain": ["объясни", "поясни", "разъясни", "почему", "отчего", "зачем", "explain"],
    "creative": [
        "сочини", "напиши рассказ", "придумай историю", "стихотворени",
        "story", "poem", "creative", "напиши сказку", "фантастическ",
        "narrative", "imaginative", "придумай", "сочинение", "эссе",
    ],
    "news": [
        "новост", "последние новости", "что нового", "что произошло",
        "что в мире", "news", "current.event", "сводк", "последние событи",
        "сводка новост", "дайджест",
    ],
    "code": ["code", "coding", "bug", "fix", "refactor", "пофикси", "код", "баг", "рефактор"],
    "reasoning": ["рассужд", "докажи", "выведи", "reasoning", "reason", "логик", "logic"],
}


def detect_text_intent(text: str) -> str:
    """
    Детекция intent по тексту (быстрый regex).
    Возвращает имя intent или пустую строку.
    Используется в orchestrator._intent_override_from_text.
    """
    low = (text or "").strip().lower()
    if not low:
        return ""
    for intent, patterns in _INTENT_TEXT_PATTERNS.items():
        for pat in patterns:
            if pat in low:
                return intent
    return ""


def detect_pre_llm_shortcut(
    text: str,
    *,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Дорожка до полного brain/LLM (P3): погода-followup, статья, время, отмена напоминания.

    Возвращает lane-id или "" — orchestrator/pipeline могут усилить short-circuit.
    """
    t = (text or "").strip()
    if not t:
        return ""
    try:
        from core.user_facts import plain_text_requests_user_facts_identity

        if plain_text_requests_user_facts_identity(t):
            return "user_facts_identity"
    except Exception as e:
        logger.debug("detect_pre_llm_shortcut user_facts: %s", e)
    try:
        from core.dialogue_slots import resolve_slot_for_turn

        slot_ctx = resolve_slot_for_turn(t, recent_dialogue, persisted)
        if slot_ctx.force_weather:
            return "weather_followup"
        if slot_ctx.kind == "article_thread" or slot_ctx.suppress_image:
            return "article_thread"
    except Exception as e:
        logger.debug("detect_pre_llm_shortcut slots: %s", e)
    try:
        from core.article_thread_followup import article_followup_blocks_news_digest

        if article_followup_blocks_news_digest(t, recent_dialogue, persisted):
            return "article_thread"
    except Exception as e:
        logger.debug("detect_pre_llm_shortcut article: %s", e)
    low = t.lower()
    if re.search(
        r"(?i)\b(?:который\s+(?:сейчас\s+)?час|сколько\s+(?:сейчас\s+)?(?:времени|время|часов)|какое\s+(?:локальн\w*\s+)?время|what\s+time|current\s+time)\b",
        low,
    ):
        return "wall_clock"
    if re.search(
        r"(?i)(?:отмен\w*\s+напомин|сними\s+напомин|удали\s+напомин|rdel\b|/rdel\b)",
        low,
    ):
        return "reminder_cancel"
    try:
        from core.memory_recall_facade import (
            plain_text_requests_dialog_recall,
            plain_text_requests_session_meta_recall,
        )

        if plain_text_requests_session_meta_recall(t):
            return "session_meta_recall"
        if plain_text_requests_dialog_recall(t):
            return "dialog_recall"
    except Exception as e:
        logger.debug("detect_pre_llm_shortcut recall: %s", e)
    return ""
