"""Текст, факты, погода/валюта/время, стиль ответа, фолбэки, разбор TOOL_CALL."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from core.prompt_routing import is_pure_chitchat_private
from core.task_depth import tier_prefers_thorough
from core.timezone_inference import looks_like_wall_clock_question

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Правило для user-facing ответов в Telegram (plain text, без **…**)
TELEGRAM_PLAIN_REPLY_RULE = (
    "Ответ пользователю — обычный текст как в живом чате: связные абзацы или простой список «1. … 2. …», "
    "без Markdown (**жирный**, ##-заголовков, обрамления терминов звёздочками). "
    "Исключение: одна звёздочка только в конце слова для сноса (например ООН*), это редко."
)


_CAPITAL_TYPO_RE = re.compile(r"(?i)\bстоица\b")


def normalize_capital_query_typos(text: str) -> str:
    """«стоица минска» → «столица минска» (не путать со «стоимость»)."""
    t = (text or "").strip()
    if not t or not _CAPITAL_TYPO_RE.search(t):
        return t
    return _CAPITAL_TYPO_RE.sub("столица", t)


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch >= " " or ch in "\n\t")
    return value.strip()


def recent_dialogue_forbids_service_clarifications(
    recent_dialogue: Any,
    *,
    lookback: int = 10,
) -> bool:
    """
    True, если в хвосте диалога явно просят не навязывать служебные/уточняющие вопросы
    (в т.ч. реплика ассистента «без служебных уточнений» после anti_intrusion_guard).
    """
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        return False
    n = max(1, min(int(lookback), 40))
    rows = recent_dialogue[-n:]
    for m in rows:
        if not isinstance(m, dict):
            continue
        t = str(m.get("text") or m.get("text_or_caption") or "").strip().lower()
        if not t:
            continue
        if "без служебных уточнений" in t:
            return True
        if "без служебных" in t and "уточнен" in t:
            return True
        if "no clarif" in t or "no follow-up question" in t:
            return True
        if "не задавай" in t and ("вопрос" in t or "уточнен" in t):
            return True
    return False


def strip_chat_markdown_for_telegram(text: str) -> str:
    """
    Убирает типичный chat-Markdown (**…**, *…*, __…__) для plain Telegram.
    Сохраняет сноски вида «слово*» (слово из 2+ букв/цифр, одна * сразу после, не **).
    """
    s = safe_text(text)
    if not s:
        return s
    holders: List[str] = []

    def _hold(m: re.Match) -> str:
        holders.append(m.group(0))
        return f"\ue000fn{len(holders) - 1}\ue001"

    s = re.sub(
        r"(?u)(?<!\*)\b([0-9A-Za-zА-Яа-яЁё_]{2,})\*(?!\*)"
        r"(?=\s|$|[.,;:!?)»\n]|[\"«»']|\])",
        _hold,
        s,
    )
    while "**" in s:
        ns = re.sub(r"\*\*([^*]+)\*\*", r"\1", s, count=1)
        if ns == s:
            break
        s = ns
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<!_)__([^_\n]+?)__(?!_)", r"\1", s)
    for i, h in enumerate(holders):
        s = s.replace(f"\ue000fn{i}\ue001", h)
    # Остаточные HTML-теги, если модель скопировала разметку из админских отчётов
    s = re.sub(r"</?(?:b|strong|i|em|u|s|code|pre|span)(?:\s[^>]*)?>", "", s, flags=re.I)
    return s


def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def mask_pii_text(text: Any) -> str:
    s = safe_text(text)
    if not s:
        return ""
    s = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<email>", s)
    s = re.sub(r"\+?\d[\d\-\s()]{7,}\d", "<phone>", s)
    return s


def summarize_knowledge_hint(hint: Any, *, max_items: int = 3, max_chars: int = 400) -> str:
    if not isinstance(hint, dict):
        return ""
    selected = hint.get("selected")
    if not isinstance(selected, list):
        selected = []
    policy = str(hint.get("policy") or "none")
    confidence = hint.get("confidence")
    try:
        conf = round(float(confidence), 3)
    except Exception:
        conf = 0.0
    chunks = []
    for row in selected[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        src = safe_text(row.get("source"))
        tags = row.get("tags")
        if isinstance(tags, list):
            tags_s = ",".join(safe_text(t) for t in tags[:4] if safe_text(t))
        else:
            tags_s = ""
        content = mask_pii_text(row.get("content"))
        if len(content) > 120:
            content = content[:117] + "..."
        part = f"[{src}] tags={tags_s} :: {content}".strip()
        if part:
            chunks.append(part)
    body = " | ".join(chunks)
    summary = f"policy={policy}; confidence={conf}; entries={len(chunks)}; {body}".strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


def normalize_user_facts(facts: Any) -> Dict[str, Any]:
    if not isinstance(facts, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in ("name", "age", "city", "country", "timezone", "language", "currency", "interests", "occupation", "pet_cat", "pet_dog"):
        v = facts.get(k)
        if v is None:
            continue
        out[k] = v
    return out


def gentle_auto_ask_missing(missing: Any) -> str:
    if not isinstance(missing, list) or not missing:
        return ""
    if "location" in missing:
        return "Чтобы ответ был точнее, подскажи, пожалуйста, город или страну."
    if "currency" in missing:
        return "Уточни, пожалуйста, базовую валюту (ISO код, например USD, EUR или BYN)."
    if "timezone" in missing:
        return "Укажи, пожалуйста, твой часовой пояс (например Europe/Moscow или UTC+3)."
    _ASK = {
        "country": "Если нужна точная погода или валюта по умолчанию — напиши страну (например Беларусь).",
        "city": "Можешь уточнить населённый пункт — так удобнее для локальных подсказок.",
        "name": "Как к тебе обращаться по имени?",
        "age": "Уточни возраст — только если хочешь, чтобы ответы были с учётом этого.",
        "language": "Предпочитаемый язык ответов (ru / en)?",
        "interests": "Расскажи в двух словах об интересах — только если хочешь персонализацию.",
    }
    for key in missing:
        if isinstance(key, str) and key in _ASK:
            return _ASK[key]
    return ""


def _ru_token_boundary_match(low: str, token: str) -> bool:
    """
    Токен как целое слово (кириллица/латиница), не подстрока «включай»→«ключ», «напиши»→«апи».
    """
    if not low or not token:
        return False
    return bool(
        re.search(
            rf"(?i)(?<![a-zа-яё0-9_]){re.escape(token)}(?![a-zа-яё0-9_])",
            low,
        )
    )


def _api_or_apis_token(low: str) -> bool:
    if re.search(r"(?i)(?<![a-z0-9])api(?![a-z0-9])", low):
        return True
    return _ru_token_boundary_match(low, "апи")


def _api_key_phrase(low: str) -> bool:
    return bool(
        re.search(
            r"(?i)api[\s.\-_]*ключ|ключ[\s.\-_]*api|апи[\s.\-_]*ключ|ключ[\s.\-_]*апи|apikey|api_key|"
            r"токен[\s.\-_]*(?:api|openrouter|llm)|openrouter[\s,]*ключ|ключ[\s,]*openrouter",
            low,
        )
    )


def _looks_like_admin_connectivity_paste(low: str) -> bool:
    """Вставка результата /admin_connectivity в чат (не вопрос «проверь ключ»)."""
    if "connectivity_check_timeout" in low:
        return True
    if "admin_connectivity_json" in low:
        return True
    if "mem0 (primary)" in low and "telegram" in low:
        return True
    if "searxng_search:" in low and "qdrant_collections:" in low:
        return True
    if "таймут запросов" in low and "сеть и ключи" in low:
        return True
    return False


def _looks_like_admin_system_report_paste(low: str) -> bool:
    """Вставка /admin_system или /admin_health — не путать с вопросом про баланс LLM."""
    if "admin_system_json" in low:
        return True
    if "модулей в отчёте" in low or "модулей в отчете" in low:
        return True
    if "сводка состояния" in low and ("kpi ок" in low or "журнал ошибок" in low):
        return True
    if "мозг (llm)" in low and "журнал ошибок" in low:
        return True
    if "/admin_resilience" in low and ("безоп" in low or "safe mode" in low or "устойчивость" in low):
        return True
    if "admin: full error journal purge" in low:
        return True
    if "текст по-русски" in low and "admin_system_json" in low:
        return True
    return False


def _looks_like_admin_report_paste(low: str) -> bool:
    return _looks_like_admin_connectivity_paste(low) or _looks_like_admin_system_report_paste(low)


def _openrouter_with_operational_intent(low: str) -> bool:
    """
    Упоминание OpenRouter без операционного контекста (документация, «что такое») — не short-circuit.
    """
    if "openrouter" not in low:
        return False
    if any(
        m in low
        for m in (
            "баланс",
            "ключ",
            "ключа",
            "ключей",
            "токен",
            "api",
            "апи",
            "ошиб",
            "не работает",
            "работает ли",
            "сломан",
            "проверь",
            "проверить",
            "проверка",
            "доступ",
            "лимит",
            "квот",
            "пустой",
            "молчит",
            "таймаут",
            "timeout",
            "balance",
            "billing",
            "quota",
            "not working",
            "fail",
            "broken",
        )
    ):
        return True
    if re.search(
        r"(?i)\b(?:rate|limit|401|403|429|500|502|503|key|token|credit)\b",
        low,
    ):
        return True
    return False


def _user_text_looks_like_code(low: str) -> bool:
    """Текст похож на код/конфиг — не пытаться классифицировать как диагностический вопрос."""
    if not low:
        return False
    # Тройные бэктыки — точно код
    if "```" in low:
        return True
    # Импорты, типичные начала файлов
    if re.search(r"(?m)^(?:import |from |def |class |const |let |var |function|export|#include|package )", low):
        return True
    # Строки с =, ==, !=, ->, => без русского текста
    lines = low.strip().split("\n")
    # Однострочные сообщения не могут быть кодом по этому признаку
    if len(lines) < 2:
        return False
    code_lines = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Строка с операторами и без русского текста
        if re.search(r"[=+\-*/%&|<>!]{2,}|->|=>|::", line) and not re.search(r"[а-яё]", line):
            code_lines += 1
        # indent (leading spaces) + нет русских букв
        if line.startswith(("  ", "    ", "\t")) and not re.search(r"[а-яё]", line):
            code_lines += 1
    if code_lines >= 2 and code_lines >= len([l for l in lines if l.strip()]) * 0.3:
        return True
    return False


def _looks_like_architecture_or_article_paste(low: str, user_text: str) -> bool:
    """Длинная вставка про RAG/архитектуру/статью — не short-circuit operational_diag."""
    try:
        from core.brain.profile_route_guard import looks_like_architecture_or_long_form_discussion

        if looks_like_architecture_or_long_form_discussion(user_text):
            return True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'text_helpers', e, exc_info=True)
    if len((user_text or "").strip()) < 280:
        return False
    if "experience_digest" in low and ("openrouter" in low or "api" in low or "ключ" in low):
        return True
    if "gemma_bot" in low and ("rag" in low or "qdrant" in low):
        return True
    if re.search(r"(?i)\bhabr\.com\b", low) and len(user_text) > 400:
        return True
    return False


def _operational_question_in_focus(low: str, user_text: str) -> bool:
    """Операционный вопрос в начале короткой реплики, а не в середине простыни."""
    head = (user_text or "").strip()[:320].lower()
    if len((user_text or "").strip()) <= 280:
        return True
    focus_patterns = (
        r"(?i)^(?:проверь|проверить|есть\s+ли|работает\s+ли|не\s+работает|баланс|ключ|api|апи)",
        r"(?i)(?:^|\n)\s*(?:проверь|проверить)\s+(?:llm|openrouter|api|апи|ключ)",
        r"(?i)openrouter\s+(?:не\s+)?работает",
        r"(?i)баланс\s+openrouter",
    )
    return any(re.search(p, head) for p in focus_patterns)


def is_bot_operational_diag_question(user_text: str) -> bool:
    """Вопросы про работу LLM/API/баланс — не тянуть UrlFetch."""
    low = safe_text(user_text).lower()
    if not low or _user_text_looks_like_weather_query(low):
        return False
    # Если текст похож на код — не перехватывать как диагностику
    if _user_text_looks_like_code(low):
        return False
    if _looks_like_admin_report_paste(low):
        return False
    if _looks_like_architecture_or_article_paste(low, user_text):
        return False
    if not _operational_question_in_focus(low, user_text):
        return False
    if _openrouter_with_operational_intent(low):
        return True
    if re.search(r"\bllm\b", low) and (
        "ошиб" in low
        or re.search(r"(?i)(?<![а-яё])(?:проверь|проверить)(?![а-яё])", low)
        or _ru_token_boundary_match(low, "баланс")
        or _api_key_phrase(low)
        or _api_or_apis_token(low)
        or _ru_token_boundary_match(low, "ключ")
        or _ru_token_boundary_match(low, "ключа")
        or _ru_token_boundary_match(low, "ключей")
    ):
        return True
    if re.search(r"проверь\s+llm|проверить\s+llm|проверка\s+llm", low):
        return True
    if _ru_token_boundary_match(low, "баланс") and any(
        (
            _api_or_apis_token(low),
            "openrouter" in low,
            bool(re.search(r"\bllm\b", low)),
            _ru_token_boundary_match(low, "ключ"),
            _ru_token_boundary_match(low, "ключа"),
            _ru_token_boundary_match(low, "ключей"),
        )
    ):
        return True
    # Явные фразы «api-ключ» / «токен openrouter» — уже задают операционный контекст.
    if _api_key_phrase(low):
        return True
    # «доступ» + только «llm» давало FP в текстах про архитектуру («доступ к инструментам»).
    # Оставляем связку с явным API/OpenRouter (или «апи» как словом).
    if (
        _ru_token_boundary_match(low, "доступ")
        or _ru_token_boundary_match(low, "ключ")
        or _ru_token_boundary_match(low, "ключа")
        or _ru_token_boundary_match(low, "ключей")
    ) and (_api_or_apis_token(low) or "openrouter" in low):
        return True
    return False


def operational_diag_reply() -> str:
    return (
        "Проверить баланс OpenRouter или ваш API‑ключ из обычного чата я не могу — это не видно модели. "
        "Если вы **администратор** этого бота: `/admin_connectivity` (Telegram, OpenRouter, Mem0) и `/admin_health` (сводка, в т.ч. сбои внешних API). "
        "Полный снимок: `/admin_diagnostic` или `/admin_diagnostic net`. "
        "Если бот иногда отвечает пусто или долго — чаще виноваты free‑маршруты или таймаут, а не «нулевой баланс»; "
        "смотрите логи и настройку `OP_TIMEOUT_SEC`."
    )


def is_bot_operational_diag_reply(assistant_text: str) -> bool:
    """
    Текст совпадает с шаблоном operational_diag_reply (в т.ч. после persona polish):
    такие ответы не должны попадать в experience_digest / strategy_paths как «удачный опыт».
    """
    s = safe_text(assistant_text)
    if len(s) < 60:
        return False
    low = s.lower()
    if "openrouter" not in low or "admin_connectivity" not in low:
        return False
    if "не видно модели" not in low:
        return False
    if "admin_health" not in low and "/admin_health" not in s:
        return False
    return True


def _weather_city_token_key(s: str) -> str:
    t = safe_text(s).lower().strip()
    t = t.replace("ё", "е")
    return t


# Разговорные названия → (запрос к геокодеру, ISO-код страны для фильтра, если известен)
def _user_text_looks_like_weather_query(low: str) -> bool:
    """Срабатывает и на «погодой/погоде», где подстроки «погода» нет (часто STT и разговорная речь)."""
    if not low:
        return False
    if any(
        k in low
        for k in (
            "weather",
            "температур",
            "прогноз",
            "осадк",
            "дождь",
            "ливень",
            "снегопад",
            "метель",
        )
    ):
        return True
    return bool(re.search(r"\bпогод", low))


_WEATHER_DEICTIC_HOME_RE = re.compile(
    r"(?i)(?:"
    r"погод\w*.{0,40}?(?:там|тут|здесь|у\s+меня|дома|отсюда|где\s+я)|"
    r"(?:там|тут|здесь|у\s+меня).{0,40}?погод|"
    r"мне\s+нужн\w*\s+погод|"
    r"погод\w*\s+(?:там|тут|здесь)"
    r")"
)

_LOCATION_CONTEXT_RE = re.compile(
    r"(?i)(?:где\s+я|где\s+ты\s+меня|нахожусь|местоположен|"
    r"миханович|аг\.?\s*миханович|мой\s+город|моя\s+страна|живу\s+в)"
)


def user_text_weather_refs_saved_home(user_text: str) -> bool:
    """«погода там», «у меня», «мне нужна погода» — не отдельный город в фразе, а профиль/контекст."""
    low = safe_text(user_text).lower()
    if not low or not _user_text_looks_like_weather_query(low):
        return False
    if _WEATHER_DEICTIC_HOME_RE.search(low):
        return True
    if len(low) <= 48 and not weather_city_extract_from_message_only(user_text)[0]:
        if any(
            k in low
            for k in (
                "у меня",
                "дома",
                "отсюда",
                "мне нужна",
                "мне нужно",
                "нужна погода",
                "нужно погода",
            )
        ):
            return True
    return False


def recent_dialogue_has_location_context(recent_dialogue: Any, *, lookback: int = 8) -> bool:
    """Недавно обсуждали «где я» / населённый пункт из профиля — «погода там» = туда."""
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for row in reversed(rows[-lookback:]):
        if not isinstance(row, dict):
            continue
        t = str(row.get("text") or "")
        if _LOCATION_CONTEXT_RE.search(t):
            return True
    return False


_WEATHER_META_QUESTION_RE = re.compile(
    r"(?i)(?:"
    r"с\s+какого\s+(?:район|област|город|мест|насел)|"
    r"для\s+какого\s+(?:район|област|город|мест)|"
    r"откуда\s+(?:эта\s+|берётся\s+|взята\s+)?погод|"
    r"какой\s+(?:район|област|город).{0,30}?погод|"
    r"погод\w*.{0,25}?для\s+какого"
    r")"
)


def looks_like_weather_meta_question(user_text: str) -> bool:
    """«С какого района погода?» — про последний прогноз, не новый запрос к API."""
    low = safe_text(user_text).lower()
    if not re.search(r"\bпогод", low):
        return False
    return bool(_WEATHER_META_QUESTION_RE.search(low))


def weather_should_use_saved_anchor(
    user_text: str,
    facts: Dict[str, Any],
    anchor: Any,
    *,
    recent_dialogue: Any = None,
) -> bool:
    """
    Координаты из weather_anchor — только если не спорят с профилем и пользователь
    не просит погоду «у меня/там» без явного другого города.
    """
    if not anchor:
        return False
    try:
        from core.weather_location_store import weather_anchor_conflicts_user_facts

        if weather_anchor_conflicts_user_facts(facts, anchor):
            return False
    except Exception:
        pass
    fc = str(facts.get("city") or "").strip()
    if not fc:
        return True
    if weather_city_extract_from_message_only(user_text)[0]:
        return True
    if user_text_weather_refs_saved_home(user_text):
        return False
    if recent_dialogue_has_location_context(recent_dialogue):
        low = safe_text(user_text).lower()
        if _user_text_looks_like_weather_query(low) and not weather_city_extract_from_message_only(
            user_text
        )[0]:
            return False
    low = safe_text(user_text).lower()
    if (
        _user_text_looks_like_weather_query(low)
        and len(low) <= 32
        and not weather_city_extract_from_message_only(user_text)[0]
    ):
        label = str(anchor.get("label") or "").lower().replace("ё", "е")
        city_n = fc.lower().replace("ё", "е")
        if city_n and (
            city_n in label
            or label in city_n
            or city_n.replace(" ", "") in label.replace(" ", "")
        ):
            return True
        return False
    return True


# Фрагмент regex: тема погоды слева от «в <город>» или справа от «в <город>».
_WEATHER_TOPIC_TOKEN = (
    r"(?:weather|прогноз|осадк|дождь|ливень|снегопад|метель|температур\w*|\bпогод\w+)"
)


_WEATHER_CITY_ALIASES: Dict[str, Tuple[str, str]] = {
    "питер": ("Санкт-Петербург", "RU"),
    "питере": ("Санкт-Петербург", "RU"),
    "питеру": ("Санкт-Петербург", "RU"),
    "питера": ("Санкт-Петербург", "RU"),
    "спб": ("Санкт-Петербург", "RU"),
    "санкт-петербург": ("Санкт-Петербург", "RU"),
    "санктпетербург": ("Санкт-Петербург", "RU"),
    "ленинград": ("Санкт-Петербург", "RU"),
    "ленинграде": ("Санкт-Петербург", "RU"),
    "мск": ("Москва", "RU"),
    "москва": ("Москва", "RU"),
    "москве": ("Москва", "RU"),
    "нск": ("Новосибирск", "RU"),
    "екб": ("Екатеринбург", "RU"),
    "екатеринбург": ("Екатеринбург", "RU"),
    "минск": ("Минск", "BY"),
    "минске": ("Минск", "BY"),
    "минска": ("Минск", "BY"),
    "менск": ("Минск", "BY"),
    "менску": ("Минск", "BY"),
    # Дубли в РБ: без области геокодер часто берёт Могилёвскую (больше население).
    "михановичи": ("Springfield", "BY"),
    "михановичах": ("Springfield", "BY"),
}


def canonical_user_city_fact(city: str, context_text: str = "") -> str:
    """
    Канонический населённый пункт для user_facts и геокодера.
    «Springfield» → «Springfield, Example County» (одноимённый НП в Могилёвской).
    """
    c = (city or "").strip()
    if not c:
        return c
    if re.search(r"(минск|могилев).*(област|район)", c, re.IGNORECASE):
        return c
    ctx = safe_text(context_text).lower().replace("ё", "е")
    c_low = c.lower().replace("ё", "е")
    base = re.sub(
        r"^(?:а\.г\.|аг\.|агрогородок)\s+",
        "",
        c,
        count=1,
        flags=re.IGNORECASE,
    ).strip() or c
    base_key = base.lower().replace(" ", "")
    if "миханович" not in base_key and "springfield" not in base_key:
        return c
    has_ag = bool(
        re.search(r"(?:а\.г\.|аг\.|агрогородок)", ctx)
        or re.search(r"(?:а\.г\.|аг\.|агрогородок)", c, re.IGNORECASE)
    )
    has_minsk = bool(re.search(r"минск", ctx)) and bool(re.search(r"(област|район)", ctx))
    has_mogilev = bool(re.search(r"могилев", ctx))
    if has_mogilev and not has_ag and not has_minsk:
        if has_ag:
            return f"аг. {base}, Могилёвская область"
        return f"{base}, Могилёвская область"
    if has_ag or has_minsk:
        if has_ag or re.search(r"(?:а\.г\.|аг\.|агрогородок)", c, re.IGNORECASE):
            return f"аг. {base}, Example County"
        return f"{base}, Example County"
    return c


def weather_region_hint_from_text(text: str) -> str:
    """
    Подсказка области для геокодера (admin1): «минской области» → minsk, «могилёвской» → mogilev.
    Пустая строка, если в тексте нет явного региона.
    """
    low = safe_text(text).lower().replace("ё", "е")
    if not low:
        return ""
    if re.search(r"миханович", low) or re.search(r"springfield", low):
        if re.search(r"могилев", low):
            return "mogilev"
        if re.search(r"(?:а\.г\.|аг\.|агрогородок)", low):
            return "minsk"
        if re.search(r"минск", low) and re.search(r"(област|район|region)", low):
            return "minsk"
    if re.search(r"могилевск", low) or re.search(r"mogilev", low):
        return "mogilev"
    if re.search(r"минск", low) and re.search(r"(област|район|region)", low):
        return "minsk"
    if re.search(r"гродненск", low):
        return "grodno"
    if re.search(r"брестск", low):
        return "brest"
    if re.search(r"витебск", low) and re.search(r"(област|район)", low):
        return "vitebsk"
    if re.search(r"гомельск", low):
        return "gomel"
    return ""


def weather_region_hint_resolve(
    user_text: str,
    facts: Dict[str, Any],
    recent_dialogue: Any = None,
) -> str:
    """Область: текущее сообщение → facts.city (длинный адрес) → недавний диалог пользователя."""
    h = weather_region_hint_from_text(user_text)
    if h:
        return h
    h = weather_region_hint_from_text(str(facts.get("city") or ""))
    if h:
        return h
    if isinstance(recent_dialogue, list):
        for row in reversed(recent_dialogue[-16:]):
            if not isinstance(row, dict) or str(row.get("role") or "") != "user":
                continue
            h = weather_region_hint_from_text(str(row.get("text") or ""))
            if h:
                return h
    return ""


def weather_geo_query_for_api(city: str, country: str, region_hint: str = "") -> Tuple[str, str]:
    """
    Строка для Open-Meteo / wttr и нормализованный admin1_hint для выбора из results[].
    """
    c0 = (city or "").strip()
    co0 = (country or "").strip()
    rh = (region_hint or "").strip()
    if not c0:
        return "", rh
    if rh and re.search(r"(област|район|region)", c0, re.IGNORECASE):
        q = c0
        if co0 and co0.lower() not in c0.lower():
            q = f"{c0}, {co0}"
        return q, rh
    c, co = normalize_weather_city_country(c0, co0)
    if rh == "minsk":
        reg_ru = "Example Region"
    elif rh == "mogilev":
        reg_ru = "Могилёвская область"
    elif rh == "grodno":
        reg_ru = "Гродненская область"
    elif rh == "brest":
        reg_ru = "Брестская область"
    elif rh == "vitebsk":
        reg_ru = "Витебская область"
    elif rh == "gomel":
        reg_ru = "Гомельская область"
    else:
        reg_ru = ""
    parts: List[str] = [c]
    if reg_ru:
        parts.append(reg_ru)
    if co in {"BY", "BLR"} or "беларус" in co.lower() or co == "":
        if not any("беларус" in p.lower() or p.upper() in {"BY", "BLR"} for p in parts):
            parts.append("Беларусь")
    elif co:
        parts.append(co)
    return ", ".join(parts), rh


def _explicit_major_city_from_user_text(user_text: str) -> Tuple[str, str]:
    """
    Полный официальный топоним в том же сообщении важнее жаргона («в Питере»).
    Устраняет геокод «Питер» → деревня Пермского края при явном «г. Санкт-Петербург».
    """
    low = safe_text(user_text).lower()
    if "санкт" in low and "петербург" in low:
        return "Санкт-Петербург", "RU"
    if re.search(r"(?:^|[^а-яё])москва(?:[^а-яё]|$)", low) or "г. москва" in low:
        return "Москва", "RU"
    if re.search(r"(?:^|[^а-яё])минск(?:[^а-яё]|$)", low) or "г. минск" in low:
        return "Минск", "BY"
    return "", ""


def normalize_weather_city_country(city: str, country: str) -> Tuple[str, str]:
    """Жаргон и короткие формы → каноническое имя для Open-Meteo; при необходимости подставить ISO страны."""
    c0 = (city or "").strip()
    co0 = (country or "").strip()
    if not c0:
        return c0, co0
    key = _weather_city_token_key(c0)
    if key in _WEATHER_CITY_ALIASES:
        cn, iso = _WEATHER_CITY_ALIASES[key]
        if not co0 and iso:
            return cn, iso
        return cn, co0
    return c0, co0


def weather_city_extract_from_message_only(user_text: str) -> Tuple[str, str]:
    """Только текущее сообщение: «в Минске погода», «погода в X», «город X» — без facts и истории."""
    ex_c, ex_co = _explicit_major_city_from_user_text(user_text)
    if ex_c:
        return ex_c, ex_co
    low = safe_text(user_text).lower()
    city = ""
    m = re.search(
        _WEATHER_TOPIC_TOKEN + r".{0,55}?\bв\s+([а-яёa-z][а-яёa-z\-]{1,42})\b",
        low,
    )
    if m:
        city = m.group(1).strip()
    if not city:
        m2 = re.search(
            r"\bв\s+([а-яёa-z][а-яёa-z\-]{1,42})\b.{0,30}" + _WEATHER_TOPIC_TOKEN,
            low,
        )
        if m2:
            city = m2.group(1).strip()
    if not city:
        m3 = re.search(
            r"\bгород\s+([а-яёa-z][а-яёa-z\-\s]{1,78}?)(?:\.|$|\?|!|\s*$)",
            low,
            flags=re.IGNORECASE,
        )
        if m3:
            city = m3.group(1).strip()
    if not city:
        m4 = re.search(
            r"\b(?:а\.г\.|аг\.|агрогородок)\s*([а-яёa-z][а-яёa-z\-]{1,42})\b",
            low,
            flags=re.IGNORECASE,
        )
        if m4:
            city = m4.group(1).strip()
    if not city:
        m5 = re.search(
            r"(?:погод\w*|weather).{0,25}?\bв\s+(?:а\.г\.|аг\.)\s*([а-яёa-z][а-яёa-z\-]{1,42})",
            low,
            flags=re.IGNORECASE,
        )
        if m5:
            city = m5.group(1).strip()
    if not city:
        m6 = re.search(
            r"\bв\s+(?:а\.г\.|аг\.)([а-яёa-z][а-яёa-z\-]{1,42})\b",
            low,
            flags=re.IGNORECASE,
        )
        if m6:
            city = m6.group(1).strip()
    country = ""
    return city, country


def weather_city_from_recent_dialogue(recent_dialogue: Any, *, lookback: int = 12) -> Tuple[str, str]:
    """Последний явный город из реплик пользователя (и короткие уточнения вроде «Город Санкт-Петербург»)."""
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        return "", ""
    for row in reversed(recent_dialogue[-lookback:]):
        if not isinstance(row, dict) or str(row.get("role") or "") != "user":
            continue
        t = str(row.get("text") or "")
        c, co = weather_city_extract_from_message_only(t)
        if not c and "санкт" in safe_text(t).lower() and "петербург" in safe_text(t).lower():
            c, co = "Санкт-Петербург", "RU"
        if c:
            return normalize_weather_city_country(c, co)
    return "", ""


def weather_city_country_resolve(
    user_text: str,
    facts: Dict[str, Any],
    recent_dialogue: Any = None,
) -> Tuple[str, str]:
    """
    Город для погоды: явная фраза в сообщении → недавний диалог → facts.city.
    Жаргон («Питер») нормализуется, чтобы не попасть в одноимённую деревню в другом регионе.
    """
    city, country = weather_city_extract_from_message_only(user_text)
    country = str(facts.get("country") or "").strip() if not country else country
    city, country = normalize_weather_city_country(city, country)
    if not city and recent_dialogue is not None:
        dc, dco = weather_city_from_recent_dialogue(recent_dialogue)
        if dc:
            city, country = dc, dco or country
    if not city:
        fc = str(facts.get("city") or "").strip()
        if fc and re.search(r"(област|район|улиц|аг\.|а\.г\.)", fc, re.IGNORECASE):
            city = fc
            country = str(facts.get("country") or "").strip() if not country else country
        else:
            city = fc
            country = str(facts.get("country") or "").strip() if not country else country
            city, country = normalize_weather_city_country(city, country)
    return city, country


def weather_city_country_from_message(user_text: str, facts: Dict[str, Any]) -> Tuple[str, str]:
    """Обратная совместимость: только сообщение + facts (без истории)."""
    return weather_city_country_resolve(user_text, facts, None)


_WEATHER_CITY_CLARIFY_RE = re.compile(
    r"(?i)(какой\s+именно\s+город|город\s+вас\s+интерес|населённ\w+\s+пункт|"
    r"назов\w+\s+населённ|покаж\w+\s+погод|уточн\w+.*город|напишите\s+город)"
)

_ARTICLE_FOLLOWUP_RE = re.compile(
    r"(?i)(дальнейш\w*|перспектив\w*|что\s+дальше|куда\s+движ\w*|"
    r"развит\w+\s+событ|последств\w*|что\s+будет\s+дальше|"
    r"что\s+ещ[её]\s+известн?\w*|что\s+еще\s+известн?\w*|"
    r"ещ[её]\s+подробн\w*|еще\s+подробн\w*|подробн\w*)"
)

_USER_REFERS_ARTICLE_RE = re.compile(
    r"(?i)(про\s+стать\w*|об\s+этой\s+стать|из\s+стать|в\s+статье|"
    r"текст\s+выше|то\s+что\s+я\s+прислал|я\s+про\s+стать)"
)


def _message_looks_like_city_only(user_text: str) -> bool:
    """Короткий ответ-город без слова «погода» (Минск, Питер, …)."""
    t = safe_text(user_text).strip()
    if not t or len(t) > 48:
        return False
    if _user_text_looks_like_weather_query(t.lower()):
        return False
    if _explicit_major_city_from_user_text(t)[0]:
        return True
    key = _weather_city_token_key(t)
    if key in _WEATHER_CITY_ALIASES:
        return True
    parts = t.split()
    return len(parts) <= 3 and bool(weather_city_extract_from_message_only(t)[0])


def weather_pending_city_reply(user_text: str, recent_dialogue: Any = None) -> bool:
    """
    Бот спросил город для погоды, пользователь ответил только топонимом.
    Иначе «Минск» уходит в LLM и может дать пустой ответ.
    """
    try:
        from core.dialogue_slots import resolve_slot_for_turn

        ctx = resolve_slot_for_turn(user_text, recent_dialogue, None)
        if ctx.force_weather:
            return True
    except Exception:
        pass
    if not _message_looks_like_city_only(user_text):
        return False
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    if not rows:
        return False
    for row in reversed(rows[-8:]):
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "")
        text = str(row.get("text") or "")
        low = text.lower()
        if role == "assistant" and _WEATHER_CITY_CLARIFY_RE.search(low):
            return True
        if role == "user" and _user_text_looks_like_weather_query(low):
            return True
    return False


def recent_dialogue_has_pasted_article(recent_dialogue: Any, *, lookback: int = 10) -> bool:
    """В недавнем диалоге была длинная статья или развёрнутый пересказ."""
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    if not rows:
        return False
    for row in reversed(rows[-lookback:]):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        role = str(row.get("role") or "")
        if role == "user" and looks_like_pasted_news_article(text):
            return True
        if role == "assistant" and len(text) >= 120:
            low = text.lower()
            if any(
                m in low
                for m in (
                    "коммерсант",
                    "персидск",
                    "стать",
                    "источник",
                    "сообщает",
                    "отмечает",
                    "аэропорт",
                    "мюнхен",
                    "munich",
                    "беспилотник",
                    "дрон",
                    "закрыт",
                    "перенаправ",
                    "bild",
                    "nato",
                )
            ):
                return True
    return False


def user_refers_to_prior_article(user_text: str, recent_dialogue: Any = None) -> bool:
    """Уточнение по уже обсуждённой статье, не по прикреплённому фото."""
    try:
        from core.dialogue_slots import user_refers_to_article_thread

        if user_refers_to_article_thread(user_text, recent_dialogue):
            return True
    except Exception:
        pass
    low = safe_text(user_text).lower()
    if _USER_REFERS_ARTICLE_RE.search(low):
        return True
    if len(low) > 140:
        return False
    if _ARTICLE_FOLLOWUP_RE.search(low) and recent_dialogue_has_pasted_article(recent_dialogue):
        return True
    return False


def should_suppress_image_for_text_thread(
    user_text: str,
    recent_dialogue: Any,
    file_context: Optional[Dict[str, Any]],
) -> bool:
    """Не описывать картинку из репоста, если пользователь продолжает текст статьи."""
    try:
        from core.dialogue_slots import should_suppress_image_for_slot

        return should_suppress_image_for_slot(user_text, recent_dialogue, file_context)
    except Exception:
        pass
    if not isinstance(file_context, dict) or file_context.get("file_type") != "image":
        return False
    if user_refers_to_prior_article(user_text, recent_dialogue):
        return True
    low = safe_text(user_text).lower()
    if any(k in low for k in ("фото", "картин", "изображен", "снимок", "на фото", "что на")):
        return False
    if recent_dialogue_has_pasted_article(recent_dialogue) and len(low) < 120:
        return True
    return False


def brain_weather_urlfetch_fallback_enabled() -> bool:
    from core.runtime_telegram_settings import effective_bool

    return effective_bool("BRAIN_WEATHER_URLFETCH_FALLBACK", default=True)


# Подмешивается в преамбулу external_hint при готовой сводке погоды (Open-Meteo / wttr / поиск).
WEATHER_REPLY_ANTI_DISCLAIMER_ADDON = (
    "Не утверждай, что прогноз на завтра «отсутствует», что «ответ обрезан» или что «запросишь расширенный прогноз отдельно», "
    "если ниже уже есть суточные строки (в т.ч. «Завтра, …» / макс-мин °C) или достаточно текста с температурами на нужный день. "
    "Не открывай ответ пользователю дословным пересказом строки «Текущий момент на сервере (UTC)» из блока про время — сразу дай прогноз по запросу."
)

# Подмешивается в external_hint при запросе новостей (сводка из поиска/RSS уже в блоке или доступен UniversalSearch).
NEWS_REPLY_ANTI_DISCLAIMER_ADDON = (
    "Не утверждай, что у тебя «нет доступа к актуальным новостям в реальном времени», что ты «не видишь новости» "
    "или что «предыдущий ответ остаётся в силе» без новой сводки. "
    "Если ниже есть блок «сводка из веб-поиска» / заголовки с выдержками — сразу дай дайджест по этим фактам. "
    "Если блока нет — один вызов UniversalSearch.search (или News.headlines), затем ответ; не отказывай шаблоном про лимиты модели."
)

_NEWS_ACCESS_REFUSAL_RE = re.compile(
    r"(?i)(?:"
    r"нет\s+доступа\s+к\s+.{0,40}?(?:новост|актуальн|реальн)|"
    r"не\s+имею\s+доступа\s+к\s+.{0,40}?(?:новост|актуальн|реальн)|"
    r"не\s+могу\s+(?:получить|дать|показать).{0,30}новост|"
    r"не\s+вижу\s+новост|"
    r"новост\w*\s+.{0,24}реальн\w*\s+времен|"
    r"no\s+access\s+to\s+(?:current|live|real[\s-]*time)|"
    r"i\s+don'?t\s+have\s+access\s+to\s+(?:current|live|real[\s-]*time)"
    r")"
)


def looks_like_news_access_refusal(text: str) -> bool:
    """LLM-отказ «нет доступа к новостям» вместо дайджеста по prefetch/поиску."""
    t = safe_text(text).strip()
    if not t:
        return False
    return bool(_NEWS_ACCESS_REFUSAL_RE.search(t))


def brain_weather_short_circuit_requires_anchor() -> bool:
    """Short-circuit «погода» в pipeline только при сохранённых lat/lon (фаза 5)."""
    return _env_flag("BRAIN_WEATHER_SHORT_CIRCUIT_REQUIRES_ANCHOR", default=True)


def brain_weather_pipeline_prefetch_enabled() -> bool:
    """Подмешивать сводку Open-Meteo в external_hint до LLM — по умолчанию выкл."""
    return _env_flag("BRAIN_WEATHER_PIPELINE_PREFETCH", default=False)


def brain_weather_wttr_eager_fetch_enabled() -> bool:
    """Серверный запрос wttr.in при сбое Open-Meteo (без ожидания UrlFetch от LLM). Выкл.: BRAIN_WEATHER_WTTR_EAGER_FETCH=false."""
    return _env_flag("BRAIN_WEATHER_WTTR_EAGER_FETCH", default=True)


def brain_weather_universal_search_fallback_enabled() -> bool:
    """Крайний запас: UniversalSearch по погоде, если Open-Meteo и wttr.in не дали сводку. Выкл.: BRAIN_WEATHER_UNIVERSAL_SEARCH_FALLBACK=false."""
    return _env_flag("BRAIN_WEATHER_UNIVERSAL_SEARCH_FALLBACK", default=True)


def weather_universal_search_fallback_query(user_text: str, city: str, country: str) -> str:
    """Короткий запрос для UniversalSearch при отказе прогнозных API."""
    c = (city or "").strip()
    co = (country or "").strip()
    day_idx = weather_wttr_forecast_day_index(user_text)
    if day_idx >= 2:
        day_ru, day_en = "послезавтра", "day after tomorrow"
    elif day_idx == 1:
        day_ru, day_en = "завтра", "tomorrow"
    else:
        day_ru, day_en = "сегодня", "today"
    loc = ", ".join(x for x in (c, co) if x) or c or co
    ru = bool(re.search(r"[а-яё]", f"{user_text}{c}{co}", re.IGNORECASE))
    if ru:
        return (f"прогноз погоды {day_ru} {loc}").strip()
    return (f"weather forecast {day_en} {loc}").strip()


def wttr_in_j1_url(city: str, country: str) -> str:
    """
    JSON wttr.in для ядра и подсказки UrlFetch (единый URL: lang=ru при кириллице в запросе).
    Пустая строка, если нет ни города, ни страны.
    Для столиц — латинский slug (кириллица «Минск, Беларусь» у wttr часто даёт деревню в области).
    """
    c = (city or "").strip()
    co = (country or "").strip()
    if not c and not co:
        return ""
    c, co = normalize_weather_city_country(c, co)
    loc = _wttr_location_query(c, co)
    if not loc:
        return ""
    path_loc = quote(loc, safe="")
    lang = "ru" if re.search(r"[а-яё]", f"{city}{country}", re.IGNORECASE) else "en"
    return f"https://wttr.in/{path_loc}?format=j1&lang={lang}"


def _wttr_location_query(city: str, country: str) -> str:
    """Строка локации для wttr.in (латиница для известных столиц)."""
    c = (city or "").strip()
    co = (country or "").strip().upper()
    key = _weather_city_token_key(c)
    if key in _WEATHER_CITY_ALIASES:
        c, co = _WEATHER_CITY_ALIASES[key]
    if c == "Минск" and co in ("BY", "BLR", ""):
        return "Minsk,Belarus"
    if c == "Москва" and co in ("RU", ""):
        return "Moscow,Russia"
    if c == "Санкт-Петербург" and co in ("RU", ""):
        return "Saint-Petersburg,Russia"
    parts = [c]
    if co and co not in ("BY", "BLR", "RU"):
        parts.append(co)
    elif co in ("BY", "BLR"):
        parts.append("Belarus")
    elif co == "RU":
        parts.append("Russia")
    return ", ".join(x for x in parts if x)


def weather_wttr_forecast_day_index(user_text: str) -> int:
    """0 — сегодня, 1 — завтра, 2 — послезавтра (RU/EN; для wttr.in и поискового fallback)."""
    low = safe_text(user_text).lower()
    if re.search(r"\bпослезавтра\b", low) or re.search(r"\bday after tomorrow\b", low):
        return 2
    if re.search(r"\bзавтра\b", low) or re.search(r"\btomorrow\b", low):
        return 1
    return 0


def weather_wttr_in_fallback_hint(city: str, country: str) -> str:
    """
    Публичный JSON wttr.in (без ключа). Агент может вызвать UrlFetch, даже если пользователь не прислал ссылку.
    """
    c = (city or "").strip()
    co = (country or "").strip()
    url = wttr_in_j1_url(c, co)
    if not url:
        return ""
    return (
        f'Fallback: вызови UrlFetch.fetch_page с args {{"url": "{url}"}}. '
        "Тело ответа — JSON; из current_condition[0] (temp_C, FeelsLikeC, weatherDesc.value, humidity…) "
        "и при необходимости nearest_area[0] кратко сформулируй прогноз пользователю на его языке."
    )


_NEWS_HEADLINES_REQUEST_RE = re.compile(
    r"(?i)(?:какие\s+)?новост|что\s+нового|последние\s+новости|дай\s+новост|"
    r"новост\w*\s+сводк|сводк\w*\s+новост|новостной\s+сводк|дайджест|"
    r"что\s+в\s+мире|что\s+произошло|что\s+пишут|"
    r"news\s+headlines|latest\s+news|дай\s+сводку\s+новост"
)

# Пользователь явно не хочет Google News RSS / «ленту» — отдать ход brain + UniversalSearch (поток G1).
# «не через rss»: rss не должен попадать в промежуточные \w+ — иначе финальный \brss\b не матчится.
_USER_NEWS_REJECT_RSS_RE = re.compile(
    r"(?i)(?:\bне\s+через\s+rss\b|\bбез\s+rss\b|\bне\s+rss\b|"
    r"\bне\b[^\n]{0,40}\brss\b|\bnot\s+rss\b|\bwithout\s+rss\b)"
)
_USER_NEWS_WANTS_WEB_RE = re.compile(
    r"(?i)(?:\bиз\b|\bво\b)\s+(?:(?:всём|всем)\s+)?(?:интернет\w*|сети)\b|"
    r"\b(?:веб|web)[\s\-]*поиск\w*\b|"
    r"поищ\w*\s+(?:в\s+)?(?:интернет\w*|сети)\b|"
    r"\bsearch\s+the\s+web\b|\bfrom\s+the\s+(?:internet|web)\b"
)


_BARE_SUMMARY_KEYWORDS = frozenset(
    {
        "сводка",
        "сводку",
        "краткая сводка",
        "сводка системы",
        "системная сводка",
    }
)


def looks_like_bare_summary_keyword(user_text: str) -> bool:
    """Одно слово «сводка» — не новости и не operational_diag по вставке admin_system."""
    t = safe_text(user_text).strip().lower()
    return t in _BARE_SUMMARY_KEYWORDS


def admin_or_user_summary_short_reply(*, is_admin: bool) -> str:
    if is_admin:
        return (
            "Сводка сервера: `/admin_system` (текст) или `/admin_system_json`. "
            "Краткий health: `/admin_health`. Сеть и ключи: `/admin_connectivity`."
        )
    return (
        "Краткая сводка о вас: `/me`. Состояние бота для пользователя: `/status` или `/system_state`. "
        "Новостной дайджест: например «последние новости Беларуси»."
    )


_EXPANDED_NEWS_RE = re.compile(
    r"(?i)(?:развёрнут|развернут|подробн|поподробн|детальн|полн(?:ый|ую)\s+(?:дайджест|сводк)|"
    r"не\s+кратко|больше\s+про\s+новост|раскрой|разверни|разверн)"
)
_NEWS_EXPAND_FOLLOWUP_RE = re.compile(
    r"(?i)(?:подробн|развер|развёр|детальн|раскрой|про\s+пункт|пункт\s+\d|"
    r"(?:перв|втор|трет|четвёрт|четверт)\w*\s+пункт|ещё\s+про|еще\s+про)"
)


def _body_looks_like_news_digest(body: str) -> bool:
    """Нумерованный дайджест новостей, а не ответ «1. У пентеракта 10 ячеек…»."""
    b = (body or "").strip()
    if not b:
        return False
    if re.search(r"(?i)(пентеракт|гипергран|четырёхмерн|пятимерн|уравнен)", b):
        return False
    if b.startswith("Новости") and re.search(r"(?m)^\d+\.\s+\S", b):
        return True
    numbered = re.findall(r"(?m)^\d+\.\s+\S", b)
    if numbered and re.search(r"(?m)^\s*[·•]\s*\S", b):
        return True
    if len(numbered) >= 3:
        return True
    if len(numbered) >= 2:
        long_lines = [ln for ln in b.splitlines() if len(ln.strip()) > 32]
        return len(long_lines) >= 2
    paras = [p.strip() for p in re.split(r"\n\s*\n", b) if len(p.strip()) >= 48]
    if len(paras) >= 3 and re.search(
        r"(?i)(новост|пво|израил|иран|украин|трамп|путин|конгресс|ракет|беспилот|санкц|переговор)",
        b,
    ):
        return True
    long_lines = [ln.strip() for ln in b.splitlines() if len(ln.strip()) >= 48]
    if len(long_lines) >= 4 and re.search(
        r"(?i)(новост|пво|израил|иран|украин|трамп|путин|конгресс|ракет|беспилот|санкц|переговор)",
        b,
    ):
        return True
    return False


def _recent_assistant_had_news_digest(recent_dialogue: Any) -> bool:
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for turn in reversed(rows[-8:]):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in ("assistant", "bot", "gemma"):
            continue
        body = str(turn.get("text") or turn.get("content") or turn.get("payload") or "").strip()
        if _body_looks_like_news_digest(body):
            return True
    return False


def looks_like_news_expand_followup(user_text: str, recent_dialogue: Any = None) -> bool:
    """«Подробнее» / «про пункт 2» сразу после краткого дайджеста."""
    t = safe_text(user_text).strip()
    if not t or len(t) > 160:
        return False
    if not _NEWS_EXPAND_FOLLOWUP_RE.search(t.lower()):
        return False
    return _recent_assistant_had_news_digest(recent_dialogue)


_NEWS_STORY_DEEP_FOLLOWUP_RE = re.compile(
    r"(?i)(?:расскаж\w*|подробн\w*|разверн\w*|развёрн\w*|что\s+извест|что\s+случил|"
    r"раскрой|разбери|опиши\s+подробн|есть\s+подробн|углуб\w+|узнай\s+про)\s+(?:про|о|об)\s+"
)
_NEWS_STORY_INTEREST_RE = re.compile(
    r"(?i)(?:меня\s+интересует|хочу\s+узнать|интересует\s+новост|про\s+ту\s+новост)"
)


def looks_like_news_story_deep_followup(user_text: str, recent_dialogue: Any = None) -> bool:
    """После дайджеста: «расскажи про беспилотник в Румынии» — полный разбор темы."""
    t = safe_text(user_text).strip()
    if not t or len(t) < 14 or len(t) > 360:
        return False
    if parse_news_item_pick_index(t, recent_dialogue):
        return False
    if not _recent_assistant_had_news_digest(recent_dialogue):
        return False
    low = t.lower()
    if _NEWS_STORY_DEEP_FOLLOWUP_RE.search(low):
        return True
    if _NEWS_STORY_INTEREST_RE.search(low) and len(t) >= 24:
        return True
    if re.search(r"(?i)^(?:а\s+)?(?:что|как)\s+с\s+", low) and len(t) >= 20:
        return True
    return False


_NEWS_ITEM_PICK_BARE_RE = re.compile(r"^\s*(\d{1,2})\s*[\.\!\?]?\s*$")
_NEWS_ITEM_PICK_LABELED_RE = re.compile(
    r"(?i)^\s*(?:пункт|номер|№|#)\s*(\d{1,2})\s*[\.\!\?]?\s*$"
)


def _has_news_digest_context(
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    if _recent_assistant_had_news_digest(recent_dialogue):
        return True
    if isinstance(persisted, dict):
        ds = persisted.get("dialogue_state")
        if isinstance(ds, dict):
            items = ds.get("last_news_digest_items")
            if isinstance(items, list) and len(items) >= 2:
                return True
    return False


def parse_news_item_pick_index(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Номер пункта из краткого дайджеста: «2», «пункт 2» — без полного brain."""
    t = safe_text(user_text).strip()
    if not t or len(t) > 48:
        return None
    if not _has_news_digest_context(recent_dialogue, persisted):
        return None
    m = _NEWS_ITEM_PICK_BARE_RE.match(t) or _NEWS_ITEM_PICK_LABELED_RE.match(t)
    if not m:
        return None
    try:
        idx = int(m.group(1))
    except (TypeError, ValueError):
        return None
    if idx < 1 or idx > 12:
        return None
    return idx


def looks_like_news_item_pick(user_text: str, recent_dialogue: Any = None) -> bool:
    return resolve_news_item_pick_index(user_text, recent_dialogue) is not None


def _last_assistant_news_item_index(recent_dialogue: Any) -> Optional[int]:
    """Номер из последнего ответа «2. Заголовок» (после выбора пункта)."""
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for turn in reversed(rows[-8:]):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in ("assistant", "bot", "gemma"):
            continue
        body = str(turn.get("text") or turn.get("content") or turn.get("payload") or "").strip()
        if not body or not _body_looks_like_news_digest(body):
            continue
        m = re.match(r"^\s*(\d{1,2})\.\s+\S", body)
        if not m:
            continue
        try:
            idx = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= 12:
            return idx
    return None


_FULL_DIGEST_EXPAND_ONLY_RE = re.compile(
    r"(?i)^\s*(?:развёрнуто|развернуто|полн(?:ый|ая)\s+(?:дайджест|сводк)|"
    r"не\s+кратко|все\s+подробн|больше\s+про\s+новост)\s*[\.\!\?]?\s*$"
)


_AFFIRMATIVE_SHORT_RE = re.compile(
    r"(?i)^\s*(?:да|ага|угу|ок|okay|yes|y|конечно|давай|ищи|найди)\s*[\.\!\?]?\s*$"
)
_ASSISTANT_OFFER_SEARCH_RE = re.compile(
    r"(?i)(?:могу\s+(?:попробовать\s+)?(?:пере)?проверить|перепроверю|"
    r"поиск(?:ом)?\s+именно|найти\s+в\s+интернет|уточню\s+поиск|попробую\s+найти|"
    r"могу\s+поискать|могу\s+найти|поищу\s+в\s+сети|актуальн\w+\s+в\s+интернет)"
)


def looks_like_affirmative_short(user_text: str) -> bool:
    t = safe_text(user_text).strip()
    return bool(t) and bool(_AFFIRMATIVE_SHORT_RE.match(t))


def assistant_offered_search_followup(recent_dialogue: Any) -> bool:
    """Ассистент предложил поиск/перепроверку — «да» не про факты."""
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for turn in reversed(rows[-6:]):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in ("assistant", "bot", "gemma"):
            continue
        body = str(turn.get("text") or turn.get("content") or "").strip()
        if body and _ASSISTANT_OFFER_SEARCH_RE.search(body):
            return True
    return False


def _last_assistant_text(recent_dialogue: Any, *, lookback: int = 6) -> str:
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for turn in reversed(rows[-lookback:]):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role in ("assistant", "bot", "gemma"):
            return str(turn.get("text") or turn.get("content") or "").strip()
    return ""


_FACT_CONFIRM_ASSISTANT_RE = re.compile(
    r"(?i)запомнить\s+(?:страну|город|часовой\s+пояс|валют|имя|язык)"
)


def resolve_affirmative_search_query(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """«да» только после явного предложения поиска — не после «Запомнить страну?»."""
    if not looks_like_affirmative_short(user_text):
        return None
    try:
        from core.user_facts import has_pending_facts_confirmation

        if has_pending_facts_confirmation(persisted):
            return None
    except Exception:
        pass
    last_a = _last_assistant_text(recent_dialogue)
    if last_a and _FACT_CONFIRM_ASSISTANT_RE.search(last_a):
        return None
    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    blob_parts: List[str] = []
    for turn in rows[-12:]:
        if not isinstance(turn, dict):
            continue
        blob_parts.append(str(turn.get("text") or turn.get("content") or ""))
    blob = "\n".join(blob_parts)
    offered = assistant_offered_search_followup(recent_dialogue)
    if not offered:
        return None
    pick = None
    if isinstance(persisted, dict):
        ds = persisted.get("dialogue_state")
        if isinstance(ds, dict):
            try:
                pick = int(ds.get("last_news_picked_index") or 0)
            except (TypeError, ValueError):
                pick = None
    if pick is None:
        pick = resolve_news_item_pick_index(user_text, recent_dialogue, persisted)
    if pick:
        try:
            from core.news_reply import _digest_items_from_dialogue, _focused_entity_query_from_title

            items = _digest_items_from_dialogue(persisted, recent_dialogue)
            if 1 <= pick <= len(items):
                title = str(items[pick - 1].get("title") or "")
                fq = _focused_entity_query_from_title(title)
                if fq:
                    return fq
        except Exception:
            logger.debug("resolve_affirmative_search_query digest pick failed", exc_info=True)
    m = re.search(
        r"(?i)(?:по\s+)?(?:этой\s+)?(?:фамилии|теме|пункту|новости)[^\n]{0,80}?([а-яёa-z\-]{4,})",
        blob,
    )
    if m:
        return f"{m.group(1)} новости"[:180]
    if offered:
        m_topic = re.search(r"(?i)про\s+(.+?)\s+в\s+новост", blob)
        if m_topic:
            topic = m_topic.group(1).strip(" .?!")
            if len(topic) >= 4:
                return f"{topic} новости"[:180]
    return None


def affirmative_overrides_fact_confirmation(
    text: str,
    *,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """«да» — поиск/новости, не подтверждение pending_facts в профиле."""
    try:
        from core.user_facts import has_pending_facts_confirmation

        if has_pending_facts_confirmation(persisted):
            return False
    except Exception:
        pass
    last_a = _last_assistant_text(recent_dialogue)
    if last_a and _FACT_CONFIRM_ASSISTANT_RE.search(last_a):
        return False
    return bool(resolve_affirmative_search_query(text, recent_dialogue, persisted))


def resolve_news_item_pick_index(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Номер пункта: «2», «пункт 2», или «подробнее» после ответа по пункту."""
    pick = parse_news_item_pick_index(user_text, recent_dialogue, persisted)
    if pick is not None:
        return pick
    if not looks_like_news_expand_followup(user_text, recent_dialogue):
        return None
    t = safe_text(user_text).strip()
    if _FULL_DIGEST_EXPAND_ONLY_RE.match(t):
        return None
    if isinstance(persisted, dict):
        ds = persisted.get("dialogue_state")
        if isinstance(ds, dict):
            try:
                last = int(ds.get("last_news_picked_index") or 0)
            except (TypeError, ValueError):
                last = 0
            if 1 <= last <= 12:
                return last
    return _last_assistant_news_item_index(recent_dialogue)


def wants_expanded_news_digest(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """Просьба о развёрнутом дайджесте — не короткий RSS-список без LLM."""
    t = safe_text(user_text).strip()
    if not t:
        return False
    try:
        from core.article_thread_followup import article_followup_blocks_news_digest

        if article_followup_blocks_news_digest(t, recent_dialogue, persisted):
            return False
    except Exception:
        pass
    if looks_like_news_expand_followup(t, recent_dialogue):
        return True
    if not looks_like_news_headlines_request(t):
        low = t.lower()
        if any(k in low for k in ("новост", "что нового", "что с новост")):
            return bool(_EXPANDED_NEWS_RE.search(low))
        return False
    return bool(_EXPANDED_NEWS_RE.search(t.lower()))


def user_prefers_web_search_over_news_rss(user_text: str) -> bool:
    """
    True — пользователь просит не RSS-ленту / «из интернета» (BRAIN_CENTRIC N2–N5, UPGRADE_PLAN G1).

    Используется, чтобы не отвечать через news_direct с Google News RSS и не подмешивать RSS в hint.
    """
    t = safe_text(user_text).strip()
    if not t:
        return False
    if len(t) > 2400:
        t = t[:2400]
    low = t.lower()
    if _USER_NEWS_REJECT_RSS_RE.search(low):
        return True
    if _USER_NEWS_WANTS_WEB_RE.search(low):
        return True
    return False


def looks_like_news_headlines_request(user_text: str) -> bool:
    """Явный запрос дайджеста новостей, а не пересланная статья с #…_news в хэштеге."""
    t = safe_text(user_text).strip()
    if not t:
        return False
    low = t.lower()
    if _NEWS_HEADLINES_REQUEST_RE.search(low):
        return True
    if len(t) < 72 and re.search(r"(?i)(?:^|\s)(?:новост|news|сми)\b", low):
        return True
    return False


def looks_like_pasted_news_article(user_text: str) -> bool:
    """Длинный текст статьи / пост канала — не подменять RSS-дайджестом."""
    t = safe_text(user_text).strip()
    if len(t) < 280 or looks_like_news_headlines_request(t):
        return False
    low = t.lower()
    score = 0
    if re.search(r"#[\w]*news\b", low):
        score += 1
    if t.count("\n") >= 1 or len(re.findall(r"[.!?…]", t)) >= 2:
        score += 1
    if len(t) >= 420:
        score += 1
    if re.search(r"(?i)myfin|onliner|tut\.by|#myfin|читайте\s+также|подробнее\s+на", low):
        score += 1
    if re.search(
        r"(?i)выступил\w*\s+с\s+обращени|заявил\w*\s+о\s+необходимост|обращени\w*\s+к\s+граждан",
        low,
    ):
        score += 1
    return score >= 2


def task_fact_profile(
    user_text: str,
    facts: Dict[str, Any],
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    low = safe_text(user_text).lower()
    is_weather_meta = looks_like_weather_meta_question(user_text)
    is_weather = _user_text_looks_like_weather_query(low) and not is_weather_meta
    profile: Dict[str, Any] = {
        "is_weather": is_weather,
        "is_weather_meta": is_weather_meta,
        "is_currency": any(k in low for k in ("currency", "валют", "курс", "конверт")),
        "is_time": looks_like_wall_clock_question(user_text),
        "is_news": looks_like_news_headlines_request(user_text),
        "is_pasted_article": looks_like_pasted_news_article(user_text),
    }
    if profile["is_weather"]:
        from core.weather_location_store import read_weather_anchor

        anchor = read_weather_anchor(persisted)
        use_anchor = weather_should_use_saved_anchor(
            user_text, facts, anchor, recent_dialogue=recent_dialogue
        )
        if use_anchor and anchor:
            profile["weather_use_coords"] = True
            profile["weather_lat"] = anchor["latitude"]
            profile["weather_lon"] = anchor["longitude"]
            profile["weather_label"] = anchor.get("label") or ""
            profile["weather_region_hint"] = anchor.get("admin1") or ""
            profile["weather_city"] = profile["weather_label"] or str(facts.get("city") or "").strip()
            profile["weather_country"] = str(facts.get("country") or "").strip()
            profile["weather_geo_query"] = profile["weather_label"] or profile["weather_city"]
        else:
            wc, wco = weather_city_country_resolve(user_text, facts, recent_dialogue)
            wrh = weather_region_hint_resolve(user_text, facts, recent_dialogue)
            geo_q, wrh_norm = weather_geo_query_for_api(wc, wco, wrh)
            profile["weather_city"] = wc
            profile["weather_country"] = wco
            profile["weather_region_hint"] = wrh_norm or wrh
            profile["weather_geo_query"] = geo_q or wc
            profile["weather_use_coords"] = False
            profile["weather_from_profile"] = bool(wc) and (
                user_text_weather_refs_saved_home(user_text)
                or not weather_city_extract_from_message_only(user_text)[0]
            )
    try:
        from core.dialogue_slots import apply_slot_to_task_facts

        apply_slot_to_task_facts(profile, user_text, recent_dialogue, persisted)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'task_fact_profile slots', e, exc_info=True)
    if profile["is_currency"]:
        profile["currency_code"] = facts.get("currency")
        profile["currency_country"] = facts.get("country")
    if profile["is_time"]:
        profile["time_timezone"] = facts.get("timezone")
    return profile


# Эвристика сценария/дилеммы: включает блок линз без обязательного nested tier (длинные условия с развилками).
_STRATEGIC_LENS_MARKERS_RU_EN = (
    "сценар",
    "дилемм",
    "симуляц",
    "ролев",
    "ограничен ресурс",
    "ограниченн",
    "жертв",
    "ветк",
    "последств",
    "стратег",
    "этическ",
    "противореч",
    "два пути",
    "два вариант",
    "три вариант",
    "что выбер",
    "твой ход",
    "компромис",
    "trade-off",
    "scenario",
    "dilemma",
    "what if",
    "что если",
)


def strategic_lenses_hint_wanted(user_text: str, task_tier: str) -> bool:
    """
    True — добавить компактный блок многоракурсного рассуждения в external_hint.
    Обычный короткий чат не затрагиваем; nested/deep или явные маркеры сценария.
    """
    from core.runtime_telegram_settings import effective_bool

    if not effective_bool("STRATEGIC_LENSES_HINT_ENABLED", default=True):
        return False
    tier = (task_tier or "shallow").strip().lower()
    if tier_prefers_thorough(tier):
        return True
    t = (user_text or "").strip()
    if len(t) < 100:
        return False
    low = t.lower()
    if is_pure_chitchat_private(t):
        return False
    return any(m in low for m in _STRATEGIC_LENS_MARKERS_RU_EN)


def build_strategic_lenses_hint(user_text: str, task_tier: str) -> str:
    """
    Короткая подсказка для LLM: роли (эмпатия, детектив, учёный, математик, стратег).
    Не дублирует experience/route_risk — напоминает их использовать, если уже в контексте.
    """
    if not strategic_lenses_hint_wanted(user_text, task_tier):
        return ""
    # Жёсткий бюджет символов — не раздувать промпт.
    return (
        "(StrategicLenses) Мысленный каркас — пользователю не зачитывать и не копировать заголовок:\n"
        "• Эмпатия/стейкхолдеры: кто в зоне риска; как ситуация выглядит с их позиции (без морализаторства).\n"
        "• Детектив: 2–3 рабочие версии; какие факты каждую опровергнут; чего не хватает в условии.\n"
        "• Учёный: явные допущения; что должно быть проверяемо; что сделает вывод неверным.\n"
        "• Математик/инженер: единицы, баланс ресурсов, согласованность чисел в постановке.\n"
        "• Стратег: 1–2 хода вперёд, худший исход, запасной план без противоречия фактам.\n"
        "Если выше по контексту уже есть experience_memory_hint, route_risk_hint, strategy_path_hint или "
        "goal_plan.lookahead — согласуй ответ с ними; не противоречь без причины."
    )


def build_engine_presence_hint(context: Dict[str, Any]) -> str:
    """
    Короткая сводка: какие подсистемы оркестратора дали ненулевой сигнал в контекст брейна.
    Помогает модели не игнорировать goal/predictive/память, когда поля разнесены по блокам.
    """
    if not isinstance(context, dict):
        return ""
    lines: List[str] = []
    gh = context.get("goal_hints") if isinstance(context.get("goal_hints"), dict) else {}
    gids = gh.get("goal_ids") if isinstance(gh.get("goal_ids"), list) else []
    if gids:
        lines.append("GoalEngine: " + ", ".join(str(x) for x in gids[:5]))
    ph = context.get("predictive_hint") if isinstance(context.get("predictive_hint"), dict) else {}
    try:
        conf = float(ph.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 0.35 or ph.get("terse_mode") or ph.get("skill_priority"):
        bits = [f"conf~{conf:.2f}"]
        if ph.get("terse_mode"):
            bits.append("terse")
        sp = ph.get("skill_priority")
        if isinstance(sp, list) and sp:
            bits.append("skills=" + ",".join(str(x) for x in sp[:3]))
        lines.append("PredictiveBehavior: " + "; ".join(bits))
    if str(context.get("experience_memory_hint") or "").strip():
        lines.append("ExperienceMemory: есть текст подсказки в external_hint")
    if str(context.get("route_risk_hint") or "").strip():
        lines.append("RouteRisk: есть предупреждение в external_hint")
    if str(context.get("strategy_path_hint") or "").strip():
        lines.append("StrategyPath: есть черновик шагов в external_hint")
    lk = context.get("lookahead_plan") if isinstance(context.get("lookahead_plan"), dict) else {}
    if isinstance(lk.get("steps"), list) and lk.get("steps"):
        lines.append("LookaheadPlanner: есть steps в goal_plan.lookahead")
    if not lines:
        return ""
    return "(Подсистемы с сигналом для этого хода — используй в рассуждении; пользователю не перечисляй этот блок.)\n" + "\n".join(
        f"• {x}" for x in lines
    )


def build_thinking_markers(goal_plan: Dict[str, Any], dialogue_state: Dict[str, Any]) -> Dict[str, Any]:
    lk = goal_plan.get("lookahead") if isinstance(goal_plan.get("lookahead"), dict) else {}
    steps = lk.get("steps") if isinstance(lk.get("steps"), list) else []
    next_focus = ""
    if steps and isinstance(steps[0], dict):
        next_focus = str(steps[0].get("do") or "")[:200]
    phase = "plan_ahead" if steps else "analyze"
    return {
        "phase": phase,
        "goal": goal_plan.get("primary_goal", "help_user"),
        "intent": dialogue_state.get("last_intent", "unknown"),
        "mode": dialogue_state.get("mode", "chat"),
        "next_planned_focus": next_focus,
        "lookahead_horizon": lk.get("horizon"),
    }


def build_typing_hooks(style_hints: Dict[str, Any], dialogue_state: Dict[str, Any]) -> Dict[str, Any]:
    verbosity = style_hints.get("verbosity", "concise")
    simulated_delay_ms = 300 if verbosity == "concise" else 700
    return {
        "enabled": True,
        "phase": "drafting",
        "simulated_delay_ms": simulated_delay_ms,
        "chunks_hint": 1 if verbosity == "concise" else 2,
        "turn_index": dialogue_state.get("turn_index", 0),
    }


def build_micro_emotion_style(
    psychology: Dict[str, Any],
    behavior_engine: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    behavior_engine = behavior_engine or {}
    prev = behavior_engine.get("last_micro_emotion") or {}
    if not isinstance(prev, dict):
        prev = {}
    anxiety = psychology.get("anxiety_level", "medium")
    confidence = psychology.get("confidence_level", "medium")
    if anxiety == "high":
        current = {"emoji_level": "low", "supportiveness": "high", "directiveness": "gentle"}
    elif confidence == "low":
        current = {"emoji_level": "low", "supportiveness": "medium", "directiveness": "step_by_step"}
    else:
        current = {"emoji_level": "low", "supportiveness": "balanced", "directiveness": "clear"}
    if not prev:
        return current
    out = dict(current)
    if prev.get("supportiveness") == "high" and anxiety == "high":
        out["supportiveness"] = "high"
    if prev.get("directiveness") == "gentle" and anxiety == "high":
        out["directiveness"] = "gentle"
    out["continuity"] = True
    return out


def build_style_hints(persona: Dict[str, Any], psychology: Dict[str, Any], twin_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Soft style adaptation hints for prompt steering only.
    """
    persona_name = str(persona.get("name") or "neutral").lower()
    communication_style = psychology.get("communication_style") or "neutral"
    explanation_style = (
        (twin_profile.get("learning_profile") or {}).get("preferred_explanation_style")
        if isinstance(twin_profile, dict)
        else None
    ) or "mixed"

    tone = "balanced"
    if "друг" in persona_name or "friend" in persona_name:
        tone = "friendly"
    elif "учитель" in persona_name or "teacher" in persona_name:
        tone = "didactic"

    verbosity = "concise"
    if communication_style == "formal" or explanation_style in {"detailed", "step_by_step"}:
        verbosity = "structured"

    return {
        "tone": tone,
        "verbosity": verbosity,
        "explanation_style": explanation_style,
    }


def stable_blend_style(
    persona: Dict[str, Any],
    psychology: Dict[str, Any],
    twin_profile: Dict[str, Any],
    anchor: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge persona / psychology / twin with persisted anchor for stable tone."""
    base = build_style_hints(persona, psychology, twin_profile)
    twin_lp = {}
    if isinstance(twin_profile, dict):
        twin_lp = twin_profile.get("learning_profile") or {}
        if not isinstance(twin_lp, dict):
            twin_lp = {}
    merged: Dict[str, Any] = {
        "tone": base.get("tone", "balanced"),
        "verbosity": base.get("verbosity", "concise"),
        "explanation_style": base.get("explanation_style", "mixed"),
        "persona_name": str((persona or {}).get("name") or ""),
        "communication_style": (psychology or {}).get("communication_style"),
        "learning_explanation": twin_lp.get("preferred_explanation_style"),
    }
    if isinstance(anchor, dict) and anchor:
        for k in ("tone", "verbosity", "explanation_style"):
            if anchor.get(k):
                merged[k] = anchor[k]
    return merged


def looks_like_repetition_glitch(text: str) -> bool:
    """Отсекаем «залипшие» completion (100% of the 100% …) вместо показа пользователю.

    Детектирует:
    - 100% of the 100% — типичное залипание
    - of the — повтор 22+ раз
    - Словарный запас < 25 уникальных слов при длине > 1200 символов
    - Format-selector leak: _shape= или response_shape= повторены 3+ раз
    """
    t = (text or "").strip()
    if len(t) < 180:
        return False
    if t.count("100%") >= 8:
        return True
    if t.lower().count(" of the ") >= 22:
        return True
    if len(t) > 1200 and len(set(t.split())) < 25:
        return True
    # Format-selector leak: repeated _shape= / response_shape= artifacts
    if t.count("_shape=") >= 3 or t.count("response_shape=") >= 3:
        return True
    return False


def user_input_heavy_for_llm(text: str) -> bool:
    """
    Длинные или сильно повторяющиеся сообщения пользователя: провайдеры/free-модели
    часто возвращают пустой ответ — лучше не винить «модель» и сразу подсказать сократить ввод.
    Отключается или смягчается через BRAIN_USER_INPUT_HEAVY_* в .env (см. .env.example).
    """
    if not _env_flag("BRAIN_USER_INPUT_HEAVY_GUARD", default=True):
        return False
    t = (text or "").strip()
    try:
        scan_min = max(200, int(os.getenv("BRAIN_USER_INPUT_HEAVY_SCAN_MIN_CHARS", "500")))
    except ValueError:
        scan_min = 500
    if len(t) < scan_min:
        return False
    try:
        char_thr = max(1200, int(os.getenv("BRAIN_USER_INPUT_HEAVY_CHAR_THRESHOLD", "12000")))
    except ValueError:
        char_thr = 12000
    if len(t) >= char_thr:
        return True
    words = t.split()
    n = len(words)
    try:
        min_words = max(20, int(os.getenv("BRAIN_USER_INPUT_HEAVY_REPEAT_MIN_WORDS", "50")))
    except ValueError:
        min_words = 50
    if n < min_words:
        return False
    try:
        min_uniq = max(6, int(os.getenv("BRAIN_USER_INPUT_HEAVY_MIN_UNIQUE_WORDS", "18")))
    except ValueError:
        min_uniq = 18
    try:
        ratio_thr = float(os.getenv("BRAIN_USER_INPUT_HEAVY_REPEAT_RATIO", "0.06"))
    except ValueError:
        ratio_thr = 0.06
    ratio_thr = max(0.01, min(0.45, ratio_thr))
    uniq = len(set(words))
    if uniq < min_uniq:
        return True
    if uniq / max(n, 1) < ratio_thr:
        return True
    return False


def user_requests_prompt_injection_playback(text: str) -> bool:
    """
    Requests to echo system/developer instructions or repeat hidden prompt text.
    Handled before LLM so the model cannot quote jailbreak lines back.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    t = raw.lower()
    if len(t) > 2500:
        t = t[:2500]
    if "repeat the words" in t:
        return True
    if "repeat everything above" in t or "repeat all of the above" in t:
        return True
    if "starting with" in t and "you are" in t:
        return True
    if "начиная с" in t and "you are" in t:
        return True
    if "повтори слова выше" in t or "повтори текст выше" in t:
        return True
    if "повтори" in t and "выше" in t and ("слов" in t or "текст" in t):
        return True
    return False


def user_requests_prompt_exfiltration(text: str) -> bool:
    """
    Obvious jailbreak / exfiltration: dump system prompt, rules, keys, «developer mode» tricks.
    Kept conservative to avoid blocking benign mentions of «developer mode» without demands.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    t = raw.lower()
    if len(t) > 2500:
        t = t[:2500]
    if "игнорируй" in t and "инструкц" in t and ("промпт" in t or "системн" in t):
        return True
    if "выведи" in t and "системн" in t and "промпт" in t:
        return True
    if "ignore" in t and "instruction" in t and ("system prompt" in t or "system message" in t):
        return True
    if "you are now" in t and "developer" in t:
        return True
    if "developer mode" in t and (
        "print your" in t or "api key" in t or "your rules" in t or "rules and" in t
    ):
        return True
    return False


def natural_fallback_response(reason: str, user_id: str = "unknown", user_text: Optional[str] = None) -> str:
    """Stable variant per user (hash) so rephrasing feels consistent, not random."""
    ut = (user_text or "").strip()
    if reason == "empty_llm" and ut and user_input_heavy_for_llm(ut):
        idx = int(hashlib.sha256(f"{user_id}:empty_llm_heavy".encode()).hexdigest(), 16) % 3
        heavy = [
            "Сообщение слишком длинное или слишком однообразное — такой ввод часто даёт пустой ответ у модели. "
            "Сократи до сути (ориентир до ~2000 символов) или задай один конкретный вопрос.",
            "Похоже на очень длинный или повторяющийся текст: модель могла бы и ответить, но на практике провайдеры часто отдают пусто. "
            "Укороти запрос или разбей на части.",
            "Слишком большой или малоинформативный блок (много повторов). Сформулируй короче — так ответ будет стабильнее.",
        ]
        return heavy[idx]

    if reason == "empty_llm" and ut:
        try:
            from core.brain.general_empty_recovery import dwg_cad_domain_fallback, is_dwg_cad_topic

            if is_dwg_cad_topic(ut):
                domain = dwg_cad_domain_fallback(ut)
                if domain:
                    return domain
        except Exception as e:
            logger.debug('%s optional failed: %s', 'text_helpers', e, exc_info=True)
    if ut and is_pure_chitchat_private(ut) and reason in {"llm_error", "empty_llm"}:
        chitchat_fb = [
            "Привет! 👋 На связи — напиши, что нужно.",
            "Привет! Рад видеть. Чем помочь?",
            "Здравствуй! Коротко скажи задачу — отвечу по делу.",
            "Привет! Я здесь; если что-то не ответилось с первого раза, повтори одной фразой.",
        ]
        idx = int(hashlib.sha256(f"{user_id}:chitchat:{reason}:{ut[:32]}".encode()).hexdigest(), 16)
        return chitchat_fb[idx % len(chitchat_fb)]

    idx = int(hashlib.sha256(f"{user_id}:{reason}".encode()).hexdigest(), 16) % 3
    variants = {
        "empty": [
            "Сообщение пришло без текста. Что именно сделать: ответить, посчитать или найти информацию?",
            "Пока не вижу содержания. Можешь коротко сформулировать задачу одним сообщением?",
            "Пустой ввод. Напиши вопрос одной фразой — продолжим от этого.",
        ],
        "llm_error": [
            "Не успел собрать ответ с первого раза. Отправь тот же вопрос ещё раз — одной короткой фразой.",
            "Сейчас ответ не сложился. Напиши снова: нужен факт, пошагово или короткий совет?",
            "Повтори запрос чуть короче — обработаю заново.",
        ],
        "tool_error": [
            "Внешний источник сейчас недоступен. Сформулируй задачу текстом: что нужно на выходе?",
            "Поиск или сервис не ответил. Можно упростить запрос — отвечу из того, что есть.",
            "Инструмент не сработал. Опиши цель одним предложением — продолжу без него.",
        ],
        "empty_llm": [
            "Ответ получился пустым. Повтори короче или укажи тему явно (например: «погода в Минске»).",
            "Нечего показать после обработки. Одна конкретная фраза вместо длинного блока обычно помогает.",
            "Пустой ответ. Сократи запрос или разбей на два коротких сообщения.",
        ],
        "single_glyph": [
            "Похоже на один случайный символ. Напиши целое предложение или вопрос — так будет понятно, чем помочь.",
            "Одна буква без контекста — мало данных. Опиши задачу парой слов или используй /help.",
            "Неясно, что нужно: допиши мысль одним коротким сообщением.",
        ],
        "injection_playback": [
            "Такой запрос не выполняю: не повторяю скрытые инструкции и не цитирую системный текст. Задай обычный вопрос по делу.",
            "Не буду воспроизводить или пересказывать «служебные» фразы из чата. Напиши, что тебе нужно в обычной формулировке.",
            "Это похоже на попытку вытащить внутренние правила. Я на это не отвечаю — сформулируй задачу по-человечески, без «повтори слова выше».",
        ],
        "generic": [
            "Не смог разобрать запрос. Уточни: это вопрос, расчёт, или нужна команда бота?",
            "Запрос неясный. Скажи тему одним предложением и желаемый результат — так будет проще попасть в цель.",
            "Нужно чуть больше контекста. Что сейчас важнее: объяснение, действие или поиск?",
        ],
    }
    bucket = variants.get(reason, variants["generic"])
    return bucket[idx % len(bucket)]


def _parse_tool_call_xmlish(text: str) -> Dict[str, Any]:
    """
    Некоторые free-модели вместо канонического TOOL_CALL: {...} выдают XML-подобный блок.
    Пример: <tool_call>LawSearch.search \\n <arg_key>query</arg_key><arg_value>...</arg_value> ...
    """
    raw = text or ""
    low = raw.lower()
    if "<tool_call" not in low:
        return {}
    idx = low.find("<tool_call")
    segment = raw[idx:]
    m_open = re.match(r"<\s*tool_call\s*>([^\n<]*)", segment, re.IGNORECASE)
    name = ""
    if not m_open:
        return {}
    after = segment[m_open.end() :]
    name = (m_open.group(1) or "").strip()
    if not name:
        head = after.lstrip()
        if head:
            name = head.split("\n", 1)[0].strip()
    if not name or "." not in name:
        return {}
    args: Dict[str, str] = {}
    for m in re.finditer(
        r"<\s*arg_key\s*>([^<]+)</\s*arg_key\s*>\s*<\s*arg_value\s*>([^<]*)</\s*arg_value\s*>",
        segment,
        re.IGNORECASE | re.DOTALL,
    ):
        k = m.group(1).strip()
        v = m.group(2).strip()
        if k:
            args[k] = v
    if not args:
        return {}
    return {"name": name.strip(), "args": args}


def tool_call_marker_body_incomplete(text: str) -> bool:
    """
    True, если после TOOL_CALL: JSON обрезан, с несбалансированными скобками или оборванным url.
    """
    if "TOOL_CALL:" not in (text or ""):
        return False
    _, jp = (text or "").split("TOOL_CALL:", 1)
    s = jp.strip()
    if not s:
        return True
    try:
        o = json.loads(s)
        if not isinstance(o, dict):
            return True
        if "name" not in o or "args" not in o:
            return True
        return False
    except json.JSONDecodeError:
        pass
    if not s.startswith("{"):
        return True
    depth = 0
    for c in s:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
    if depth != 0:
        return True
    if re.search(r'"url"\s*:\s*"https?://[^"]*$', s, flags=re.I):
        return True
    return True


def brain_second_stage_max_tokens(user_text: str = "") -> int:
    """Второй проход мозга (после инструмента): по умолчанию выше первого, чтобы реже было finish_reason=length.

    Для batch-сообщений — расширяется на число вопросов.
    """
    try:
        v = int((os.getenv("BRAIN_SECOND_MAX_TOKENS") or "1200").strip())
    except ValueError:
        v = 1200
    if user_text:
        lines = [l.strip() for l in user_text.split("\n") if l.strip()]
        n = len(lines)
        question_lines = sum(1 for l in lines if "?" in l)
        extra = 0
        if n >= 6:
            q_extra = (question_lines - 1) * 256 if question_lines >= 4 else 0
            c_extra = (n - 5) * 128 if question_lines == 0 else 0
            extra = max(q_extra, c_extra)
        elif n == 1:
            comma_count = user_text.count(",") + user_text.count(";")
            numbered = len(re.findall(r'\d+\.\s', user_text))
            items = max(comma_count, numbered)
            if items >= 8:
                extra = (items - 1) * 64
        if extra:
            v = min(v + extra, 8000)
    try:
        from core.brain.profile_registry import is_continuation_turn

        if is_continuation_turn(user_text or ""):
            v = min(v + 512, 8000)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'text_helpers', e, exc_info=True)
    return max(400, min(v, 8000))


def user_requests_capability_overview(user_text: str) -> bool:
    """
    Пользователь просит показать инструменты, диагностику или «что нового в коде» —
    подмешиваем компактный инвентарь имён в external_hint, чтобы модель не «расплывалась».
    """
    low = safe_text(user_text).lower()
    if not low:
        return False
    if "диагностик" in low or "bundle.json" in low or "admin_diagnostic" in low:
        return True
    if "инструмент" in low or "tools" in low or "tool_call" in low:
        if any(w in low for w in ("покаж", "список", "какие", "перечисл", "что есть", "доступн", "свои ")):
            return True
    if any(p in low for p in ("что ты умеешь", "что умеешь", "твои возможности", "что можешь")):
        return True
    if "нового" in low or "заметил" in low or "замеч" in low:
        if any(
            k in low
            for k in ("код", "когд", "репозитор", "проект", "гит", "разработ", "фич", "изменен", "коде")
        ):
            return True
    return False


def user_provided_ordered_checklist(user_text: str) -> bool:
    """
    Пользователь дал многошаговый чеклист с явными пунктами.
    В таком случае ответ лучше строить по порядку пунктов, а не схлопывать в одно уточнение.
    """
    txt = safe_text(user_text)
    if len(txt) < 40:
        return False
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[\).]|[-*])\s+", txt))
    if numbered < 3:
        numbered += len(re.findall(r"(?iu)\bшаг\s*\d+\b", txt.lower()))
    return numbered >= 3


def user_requests_strict_direct_reasoning(user_text: str) -> bool:
    """
    Пользователь явно просит «строго по условиям» без художественного вступления и
    без выдуманных правил. Для таких кейсов нужен прямой ответ по структуре вопроса.
    """
    txt = safe_text(user_text).lower()
    if len(txt) < 180:
        return False
    constraints = 0
    for marker in (
        "не придумывай",
        "не вводи скрытые правила",
        "не добавляй неданную информацию",
        "работай только с тем",
        "ответь честно",
    ):
        if marker in txt:
            constraints += 1
    has_fork = (
        ("если да" in txt and "если нет" in txt)
        or "можно ли вообще" in txt
    )
    has_strategy_frame = any(
        m in txt
        for m in ("рациональн", "стратег", "неопредел", "тополог")
    )
    return constraints >= 1 and has_fork and has_strategy_frame


def user_requests_compact_mcq_answer(user_text: str) -> bool:
    """
    Несколько задач с вариантами и просьба ответить «номер + буква» (IQ/бланк).
    Снижает бесконечный CoT без ответа в нужном формате.
    """
    low = safe_text(user_text).lower()
    if len(low) < 36:
        return False
    format_markers = (
        "номер + буква",
        "номер и буква",
        "номер, буква",
        "буква варианта",
        "формат: номер",
        "только букв",
        "ответ в формате",
        "формат ответа",
    )
    has_fmt = any(m in low for m in format_markers)
    task_head = len(re.findall(r"(?i)(?:^|\n)\s*задач[аи]\s*\d+", low))
    task_inline = len(re.findall(r"(?i)\bзадач[аи]\s*\d+\s*[:)]", low))
    multi_tasks = task_head >= 2 or task_inline >= 2
    triple_enum = bool(re.search(r"\d+\s*[,;]\s*\d+\s*[,;]\s*\d+", low)) and (
        "задач" in low or "вопрос" in low or "тест" in low
    )
    has_opts = bool(
        re.search(r"(?i)\b[abcdабвг]\)\s", low)
        or re.search(r"(?i)\b(?:вариант|answer)\s*[abcdабвг]", low)
        or re.search(r"(?i)\)\s*[abcdабвг]\b", low)
    )
    iq_ctx = any(
        w in low
        for w in (
            " iq",
            "айку",
            "тест на iq",
            "iq test",
            "iq-тест",
            "hard mode",
            "коэффициент интеллекта",
            "три задачи",
            "несколько задач",
        )
    )
    # Блоки «1) … 2) …» как в IQ TEST v3
    numbered_paren = len(re.findall(r"(?m)^\s*\d+\)\s", low))
    inline_mcq_hint = bool(re.search(r"(?i)\d+\s*[abcd]\s+\d+\s*[abcd]", low))
    if numbered_paren >= 4 and has_opts:
        return True
    if inline_mcq_hint and has_opts:
        return True
    if (re.search(r"\biq\s+test\b", low) or "hard mode" in low) and has_opts:
        return True
    if has_fmt and (multi_tasks or has_opts or triple_enum):
        return True
    if multi_tasks and has_opts:
        return True
    if iq_ctx and has_opts and (multi_tasks or triple_enum or has_fmt):
        return True
    return False


def user_wants_inline_mcq_answer_format(user_text: str) -> bool:
    """Пользователь просит строку вида «1A 2B 3C …»."""
    return bool(re.search(r"(?i)\d+\s*[abcd]\s+\d+\s*[abcd]", safe_text(user_text)))


_MCQ_ANSWER_LINE_RES: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*задач[аи]\s*(\d+)\s*[.:)]\s*([abcdабвг])\b"),
    re.compile(r"(?i)^\s*(\d+)\s*[.)]\s*([abcdабвг])\b"),
    re.compile(r"(?i)^\s*(\d+)\s*[—\-–]\s*([abcdабвг])\b"),
    re.compile(r"(?i)^\s*(\d+)\s*:\s*([abcdабвг])\b"),
    re.compile(r"(?i)^\s*№\s*(\d+)\s*[.:)\-—]?\s*([abcdабвг])\b"),
)


def _normalize_mcq_letter(ch: str) -> str:
    m = {
        "a": "A",
        "b": "B",
        "c": "C",
        "d": "D",
        "а": "А",
        "б": "Б",
        "в": "В",
        "г": "Г",
    }
    k = (ch or "").strip().lower()
    return m.get(k, (ch or "").strip().upper())


def compact_mcq_extract_number_letter_pairs(reply: str) -> List[Tuple[int, str]]:
    """Пары номер–буква: строки «8 — Б», «Задача 9: A», а также склейки «1A 2B 10C»."""
    raw = safe_text(reply)
    if not raw.strip():
        return []
    by_num: Dict[int, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or len(s) > 220:
            continue
        for rx in _MCQ_ANSWER_LINE_RES:
            m = rx.match(s)
            if m:
                try:
                    n = int(m.group(1))
                except (TypeError, ValueError):
                    continue
                letter = _normalize_mcq_letter(m.group(2))
                if letter:
                    by_num[n] = letter
                break

    # Двухбуквенные латинские коды (AD, AF, OL …), иначе «9A» перехватывает только A
    for m in re.finditer(
        r"(?i)(?<![A-Za-zА-Яа-я0-9])(\d{1,2})\s*([a-z]{2})(?![a-zа-я])",
        raw,
    ):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if n < 1 or n > 99:
            continue
        pair = (m.group(2) or "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", pair):
            by_num[n] = pair

    # Склеенный формат и хвосты «… 1A 2B 3C …» (границы не-буквоцифры)
    for m in re.finditer(
        r"(?i)(?<![A-Za-zА-Яа-я0-9])(\d{1,2})\s*([abcdабвг])(?![A-Za-zА-Яа-я0-9])",
        raw,
    ):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if n < 1 or n > 99:
            continue
        if n in by_num:
            continue
        letter = _normalize_mcq_letter(m.group(2))
        if letter:
            by_num[n] = letter
    return sorted(by_num.items(), key=lambda x: x[0])


def maybe_compact_mcq_reply_for_telegram(user_text: str, reply: str) -> str:
    """
    Если это бланк MCQ и ответ раздут — оставить только извлечённые «номер — буква».
    Отключается: BRAIN_MCQ_POST_TRIM_ENABLED=false.
    """
    if not _env_flag("BRAIN_MCQ_POST_TRIM_ENABLED", default=True):
        return reply
    if not user_requests_compact_mcq_answer(user_text):
        return reply
    pairs = compact_mcq_extract_number_letter_pairs(reply)
    if not pairs:
        return reply
    try:
        min_chars = max(200, int(os.getenv("BRAIN_MCQ_POST_TRIM_MIN_ORIGINAL_CHARS", "520")))
    except ValueError:
        min_chars = 520
    long_reply = len((reply or "").strip()) >= min_chars
    if len(pairs) >= 2 or (long_reply and len(pairs) >= 1):
        if user_wants_inline_mcq_answer_format(user_text):
            body = " ".join(f"{n}{letter}" for n, letter in pairs)
            return f"Ответы: {body}"
        body = "\n".join(f"{n} — {letter}" for n, letter in pairs)
        return f"Ответы:\n{body}"
    return reply


def brain_first_stage_max_tokens(user_text: str) -> int:
    """Лимит первого прохода: для юридических запросов шире, для batch ещё шире."""
    try:
        base = int((os.getenv("BRAIN_FIRST_MAX_TOKENS") or "1536").strip())
    except ValueError:
        base = 1536
    ut = (user_text or "").lower()
    try:
        law_cap = int((os.getenv("BRAIN_FIRST_MAX_TOKENS_LAW") or "1600").strip() or "1600")
    except ValueError:
        law_cap = 1600
    law_cap = max(800, min(law_cap, 8000))
    keys = (
        "указ",
        "приказ",
        "закон",
        "статья",
        "кодекс",
        "нпа",
        "pravo",
        "etalonline",
        "lawsearch",
        "fetch_act",
        "документ",
        "республики беларус",
        " рб",
        "беларус",
    )
    if any(k in ut for k in keys):
        base = max(base, law_cap)

    # Batch-scaling: расширяем бюджет для многострочных сообщений
    lines = [l.strip() for l in user_text.split("\n") if l.strip()] if user_text else []
    n = len(lines)
    question_lines = sum(1 for l in lines if "?" in l)

    # Guard: длинные строки без '?' — код/проза, не batch
    has_long = any(len(l) > 150 and "?" not in l for l in lines)
    if not has_long and n >= 6:
        extra = 0
        # Для вопросов: +256 токенов за каждый сверх первого
        q_extra = (question_lines - 1) * 256 if question_lines >= 4 else 0
        # Для команд без ?: +128 токенов за каждую строку сверх 5
        c_extra = (n - 5) * 128 if question_lines == 0 else 0
        extra = max(q_extra, c_extra)
        if extra:
            base = min(base + extra, 8000)
    elif not has_long and n == 1 and user_text:
        # Одна строка — список через запятую или нумерованный
        comma_count = user_text.count(",") + user_text.count(";")
        numbered = len(re.findall(r'\d+\.\s', user_text))
        items = max(comma_count, numbered)
        if items >= 8:
            extra = (items - 1) * 64
            base = min(base + extra, 8000)

    try:
        from core.batch_continuation import is_unified_problem, looks_like_unified_math_problem

        if is_unified_problem(user_text or "") and looks_like_unified_math_problem(user_text or ""):
            try:
                unified_cap = int(
                    (os.getenv("BRAIN_FIRST_MAX_TOKENS_UNIFIED_MATH") or "3072").strip()
                )
            except ValueError:
                unified_cap = 2560
            base = max(base, max(1536, min(unified_cap, 8000)))
    except Exception:
        pass

    out = max(400, min(base, 8000))
    if user_requests_compact_mcq_answer(user_text or ""):
        try:
            mcq_cap = int((os.getenv("BRAIN_FIRST_MAX_TOKENS_MCQ") or "1200").strip())
        except ValueError:
            mcq_cap = 1200
        mcq_cap = max(480, min(mcq_cap, 2500))
        out = min(out, mcq_cap)
    return out


_TOOL_CALL_MARKER_RE = re.compile(r"(?i)TOOL_CALL\s*:")


def strip_leaked_tool_call_markup(text: str) -> str:
    """Убрать из ответа пользователю XML/tool-теги или TOOL_CALL:, если вызов инструмента не был распознан."""
    if not text:
        return text
    t = text
    t = re.sub(r"<\s*tool_call\b[^>]*>.*?</\s*tool_call\s*>", "", t, flags=re.IGNORECASE | re.DOTALL)
    if re.search(r"<\s*tool_call\b", t, re.IGNORECASE):
        t = re.split(r"<\s*tool_call\b", t, maxsplit=1, flags=re.IGNORECASE)[0]
    m = _TOOL_CALL_MARKER_RE.search(t)
    if m:
        t = (t[: m.start()] or "").strip()
    return t.strip()


_TOOL_EXEC_REPORT_RE = re.compile(
    r"(?i)(?:^|\n)\s*внешние\s+вызовы\s*:|"
    r"UniversalSearch\.search\s*:|"
    r"Wikipedia\.(?:search_pages|get_page)\s*:|"
    r"News\.find_\w+\s*:|"
    r"ответил\s+\d+\s+результат"
)


def looks_like_tool_list_leak(text: str) -> bool:
    """Модель перечислила инструменты вместо ответа пользователю."""
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"(?i)\d+\)\s*\w+\.\w+\s*—", s):
        return True
    hits = len(re.findall(r"(?i)\b\w+\.\w+\s*—", s))
    return hits >= 2 and ("поиск" in s.lower() or "search" in s.lower() or "стать" in s.lower())


def looks_like_tool_execution_report_leak(text: str) -> bool:
    """Модель пересказала журнал вызова инструмента вместо ответа пользователю."""
    s = (text or "").strip()
    if not s:
        return False
    if looks_like_tool_list_leak(s):
        return True
    if _TOOL_EXEC_REPORT_RE.search(s):
        return True
    if "UniversalSearch.search" in s and re.search(r"(?i)запрос\s*«", s):
        return True
    if re.search(r"(?i)^\s*[-•]\s*\w+\.\w+\s*:", s) and "ответил" in s.lower():
        return True
    return False


def looks_like_leaked_tool_call_leak(text: str) -> bool:
    """Ответ — сырой TOOL_CALL/JSON инструмента, не текст для пользователя."""
    s = (text or "").strip()
    if not s:
        return False
    if _TOOL_CALL_MARKER_RE.search(s):
        if not strip_leaked_tool_call_markup(s).strip():
            return True
        tc = parse_tool_call(s)
        if tc.get("name") and len(strip_leaked_tool_call_markup(s)) < 48:
            return True
    tc = parse_tool_call(s)
    if tc.get("name") and s.lstrip().startswith(("{", "[")):
        if not re.search(r"(?i)[а-яё]{16,}", s):
            return True
    return False


def parse_tool_call(text: str) -> Dict[str, Any]:
    marker = "TOOL_CALL:"
    if marker in text:
        try:
            _, json_part = text.split(marker, 1)
            raw = (json_part or "").strip()
            if not raw:
                raise ValueError("empty TOOL_CALL payload")
            decoder = json.JSONDecoder()
            data, _end = decoder.raw_decode(raw)
            if isinstance(data, dict):
                nm = data.get("name")
                if not (isinstance(nm, str) and nm.strip()) and isinstance(data.get("tool"), str):
                    data["name"] = str(data["tool"]).strip()
                if not isinstance(data.get("args"), dict) and isinstance(data.get("params"), dict):
                    data["args"] = dict(data["params"])
                if "name" in data and "args" in data and isinstance(data.get("args"), dict):
                    return data
        except Exception as e:
            logger.error(f"[brain] failed to parse TOOL_CALL: {e}")
    xml_tc = _parse_tool_call_xmlish(text)
    if xml_tc:
        return xml_tc
    return {}


def parse_tool_calls_batched(text: str) -> list:
    """
    Parse multiple TOOL_CALL blocks from a single response.
    Returns list of tool calls (each dict with name + args).
    Empty list if no valid tool calls found.
    """
    marker = "TOOL_CALL:"
    if marker not in text:
        single = parse_tool_call(text)
        return [single] if single else []
    parts = text.split(marker)
    results = []
    for part in parts[1:]:  # skip text before first TOOL_CALL
        raw = (part or "").strip()
        if not raw:
            continue
        try:
            # Take only the first JSON object from this TOOL_CALL block
            decoder = json.JSONDecoder()
            data, _end = decoder.raw_decode(raw)
            if isinstance(data, dict):
                nm = data.get("name")
                if not (isinstance(nm, str) and nm.strip()) and isinstance(data.get("tool"), str):
                    data["name"] = str(data["tool"]).strip()
                if not isinstance(data.get("args"), dict) and isinstance(data.get("params"), dict):
                    data["args"] = dict(data["params"])
                if "name" in data and "args" in data and isinstance(data.get("args"), dict):
                    results.append(data)
        except Exception:
            continue
    return results


def tools_batch_enabled() -> bool:
    try:
        from core.token_efficiency import tools_batch_enabled as _be
        return _be()
    except Exception:
        return False


def build_goal_plan(user_text: str, psychology: Dict[str, Any], twin_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight, non-invasive planner that shapes prompting only.
    Does not alter routing or output format.
    """
    text = (user_text or "").strip()
    lower = text.lower()

    primary_goal = "help_user"
    if "?" in text:
        primary_goal = "answer_question"
    if lower.startswith("/") or "команда" in lower:
        primary_goal = "execute_command_or_tool"

    constraints = ["be_concise", "be_safe", "stay_relevant"]
    if psychology.get("anxiety_level") == "high":
        constraints.append("reduce_anxiety")
    if psychology.get("confidence_level") == "low":
        constraints.append("use_step_by_step")
    if twin_profile.get("learning_profile"):
        constraints.append("adapt_to_learning_profile")

    response_shape = "short_answer"
    if "объясни" in lower or "explain" in lower:
        response_shape = "structured_explanation"
    elif "сравни" in lower or "compare" in lower:
        response_shape = "comparison"

    return {
        "primary_goal": primary_goal,
        "constraints": constraints,
        "response_shape": response_shape,
    }
