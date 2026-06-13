"""
Лёгкая маршрутизация без второго LLM: быстрые ответы, гейты учебника, подсказки намерения в промпт (как в GENESIS).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from core.regex_safe import cap_regex_input, safe_re_match, safe_re_search, strip_trailing_sentence_punct

_PURE_CHITCHAT_RE = re.compile(
    r"^[\s\d.,!?:;'\u2019-]*("
    r"привет\w*|здравств\w*|добрый\s+(день|вечер|утро|ночи)\b|"
    r"хай\b|hello\b|hi\b|ку\b|здарова\w*|"
    r"спасибо\w*|благодар\w*|мерси\b|thanks\b|thx\b|"
    r"ок(ей)?\b|okay\b|ладно\b|понятн\w*|понял\w*|ясно\b|ага\b|угу\b|"
    r"как дела\??|как жела\??|что делаешь\??|как ты\??|как настроение\??|ты тут\??|"
    r"что нового\??|что новенького\??|чё нового\??|чё новенького\??|"
    r"доброе\s+утро|добрый\s+вечер"
    r")[\s\d.,!?:;'\u2019-]*$",
    re.IGNORECASE | re.UNICODE,
)

# Только «социальный» читчат (привет / как дела / …), без коротких акков (ок, понял, ага).
# Нужен для ЛС: при assistant_expects_reply не гонять эти фразы в полный мозг.
_SOCIAL_CHITCHAT_TURN_RE = re.compile(
    r"^[\s\d.,!?:;'\u2019-]*("
    r"привет\w*|здравств\w*|добрый\s+(день|вечер|утро|ночи)\b|"
    r"хай\b|hello\b|hi\b|ку\b|здарова\w*|"
    r"как дела\??|как жела\??|что делаешь\??|как ты\??|как настроение\??|ты тут\??|"
    r"что нового\??|что новенького\??|чё нового\??|чё новенького\??|"
    r"доброе\s+утро|добрый\s+вечер"
    r")[\s\d.,!?:;'\u2019-]*$",
    re.IGNORECASE | re.UNICODE,
)


def user_requests_verbatim_group_relay(text: str) -> bool:
    """
    Пользователь просит опубликовать в группе готовую строку с @mention (в т.ч. «как в кавычках»).
    Не путать с поиском контакта: фрагменты вроде буквального «@id» должны уйти в ответ как есть.
    """
    raw = text or ""
    if "@" not in raw:
        return False
    tl = raw.lower()
    markers = (
        "повтори",
        "поговорим через",
        "напиши тут",
        "напиши здесь",
        "просто напиши",
        "скопиру",
        "копируй",
        "дословно",
        "без кавычек",
        "без ковычек",
        "в кавычках",
        "только строку",
        "только что",
        "что я напишу",
        "как я напишу",
        "оставь сообщение",
        "процитируй",
        "пингани",
    )
    return any(m in tl for m in markers)


def is_social_chitchat_turn(text: str) -> bool:
    """Явное переключение на бытовой контакт (привет, как дела), не акк по задаче."""
    raw = (text or "").strip()
    if not raw or len(raw) > 96:
        return False
    if "http://" in raw.lower() or "https://" in raw.lower():
        return False
    low = raw.lower()
    if low.startswith(("спасибо", "благодар")) and len(raw) < 72:
        return True
    return bool(safe_re_match(_SOCIAL_CHITCHAT_TURN_RE, raw.strip(), max_len=96))


def is_pure_chitchat_private(text: str) -> bool:
    """Короткие приветствия/благодарности — без тяжёлого конвейера RAG/инструментов."""
    raw = (text or "").strip()
    if not raw or len(raw) > 96:
        return False
    if "?" in raw and len(raw) > 40:
        return False
    if "http://" in raw.lower() or "https://" in raw.lower():
        return False
    low = raw.lower()
    if low.startswith(("спасибо", "благодар")) and len(raw) < 72:
        return True
    return bool(safe_re_match(_PURE_CHITCHAT_RE, raw.strip(), max_len=96))


def brain_fast_chitchat_eligible(
    text: str,
    group_id: Optional[str],
    file_context: Any,
    doc_context: Any,
    code_context: Any,
) -> bool:
    if not is_pure_chitchat_private(text or ""):
        return False
    if file_context and isinstance(file_context, dict):
        if (file_context.get("local_path") or "").strip():
            return False
        ft = file_context.get("file_type")
        if ft and ft != "text":
            return False
    if doc_context:
        return False
    if code_context:
        return False
    return True


def infer_assistant_expects_reply(
    assistant_text: str,
    *,
    task_tier: str = "",
    last_intent: str = "",
) -> bool:
    """
    Универсальная эвристика (без привязки к языку): последний ответ бота предполагает
    осмысленную реплику пользователя, а не новый «чистый» читчат.
    """
    t = (assistant_text or "").strip()
    if not t:
        return False
    tier = (task_tier or "").strip().lower()
    intent = (last_intent or "").strip().lower()
    tail = t[-400:] if len(t) > 400 else t
    if "?" in tail:
        return True
    heavy_intent = intent in {"reasoning", "logic", "explain", "teacher", "test"}
    deep_tier = tier in {"deep", "nested"}
    if heavy_intent and len(t) >= 160:
        return True
    if deep_tier and len(t) >= 240:
        return True
    if len(t) >= 420:
        return True
    return False


def private_dm_chitchat_continuity_override(
    group_id: Optional[str],
    dialogue_state: Any,
    user_text: str,
) -> bool:
    """
    Личные сообщения: если в прошлом ходу бот ждал продолжение по задаче,
    короткая «читчат»-реплика пользователя не должна уходить в fast-chitchat
    (иначе теряется нить: ок/ага/привет после разбора).

    Благодарности («спасибо») оставляем на лёгком пути.
    """
    raw = (os.getenv("BRAIN_PRIVATE_DM_CHITCHAT_CONTINUITY_GUARD") or "true").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if group_id:
        return False
    if not isinstance(dialogue_state, dict):
        return False
    if not dialogue_state.get("assistant_expects_reply"):
        return False
    if not is_pure_chitchat_private(user_text or ""):
        return False
    low = (user_text or "").strip().lower()
    if low.startswith(("спасибо", "благодар")):
        return False
    # «Как дела» / «привет» после вопроса по задаче — новый социальный ход, не продолжение шага.
    if is_social_chitchat_turn(user_text or ""):
        return False
    return True


_SCHOOLISH = (
    "урок",
    "страниц",
    "стр.",
    "стр ",
    "упражн",
    "упр.",
    "задач",
    "задание",
    "номер ",
    "№",
    "контрольн",
    "гдз",
    "учебник",
    "класс",
    "домаш",
    "математ",
    "геометр",
    "алгебр",
    "физик",
    "хими",
    "биолог",
    "истори",
    "географ",
    "литерат",
    "беларус",
    "русск",
    "англий",
    "экзамен",
    "тест",
    "оценк",
    "реш",
    "докажи",
    "уравнен",
    "формул",
    "тема:",
    "параграф",
    "глава ",
    "сочинен",
    "эссе",
    "курсов",
    "реферат",
)


def text_looks_schoolish(text: str) -> bool:
    tl = (text or "").lower().strip()
    if any(k in tl for k in _SCHOOLISH):
        return True
    if re.search(r"(^|[\s,.;])(дз)([\s\.,!?]|$)", tl):
        return True
    return bool(
        re.search(
            r"\b(упр\.?|упражнен|задани|задач|номер|страниц|стр\.?)\s*\d+",
            tl,
        )
    )


def text_warrants_textbook_rag(text: str) -> bool:
    """
    Явные сигналы ДЗ / номера / учебника — тогда разумно открыть BooksRAG в промпте.
    Обычный чат без этого не получает «учебные» инструменты в режиме BRAIN_TOOLS_MODE=auto.
    """
    tl = (text or "").lower().strip()
    if len(tl) < 8:
        return False
    if re.search(r"\b(дз|домашн|домашка)\b", tl):
        return True
    if re.search(r"\bгдз\b", tl):
        return True
    if "учебник" in tl:
        return True
    if re.search(r"\bупражнен", tl):
        return True
    if re.search(r"\bупр\.?\s*\d", tl):
        return True
    if re.search(r"\b(задани|задач)\w*", tl) and re.search(r"\d", tl):
        return True
    if re.search(r"\b(страниц|стр\.?)\s*\d", tl):
        return True
    if re.search(r"(?:№\s*|ном\.?\s*|номер\s*)\d", tl):
        return True
    if re.search(r"\bконтрольн", tl):
        return True
    if re.search(r"\bсамостоятельн", tl):
        return True
    if re.search(r"\b(параграф|§)\s*\d", tl):
        return True
    if re.search(r"\bглава\s+\d", tl):
        return True
    if re.search(r"\b(сочинен|эссе|реферат|курсов)\w*", tl):
        return True
    if re.search(r"\bэкзамен\b", tl) and re.search(r"\d", tl):
        return True
    if re.search(r"\b(реши|решить|докажи|доказать)\b", tl) and re.search(r"\d", tl):
        return True
    return bool(
        re.search(
            r"\b(упр\.?|упражнен|задани|задач|номер|страниц|стр\.?)\s*\d+",
            tl,
        )
    )


def private_wants_textbook_rag(text: str, *, homework_drill_active: bool = False) -> bool:
    if is_pure_chitchat_private(text):
        return False
    tl = (text or "").lower().strip()
    if homework_drill_active:
        return len(tl) >= 2
    if len(tl) < 12:
        return False
    return text_warrants_textbook_rag(text)


def user_requests_dialogue_analysis(text: str) -> bool:
    """
    Мета-запрос: разобрать саму переписку (не код, не «аудит системы»).
    В этом режиме мозг не должен тащить SelfProgramming.* — иначе типичная утечка CoT.
    """
    tl = (text or "").lower().strip()
    if len(tl) < 8:
        return False
    retro_phrases = (
        "посмотри назад",
        "оглянись назад",
        "загляни назад",
        "загляни в историю",
        "глянь назад",
        "просмотри переписк",
        "пролистай назад",
        "проверь переписк",
        "проверь нашу переписк",
        "проверь чат",
        "проверь историю чата",
        "сверься с переписк",
        "сверься с историей",
        "сверься с чатом",
        "найди в переписк",
        "найди в истории чата",
        "найди в истории",
        "копни переписк",
        "копни историю",
        "вспомни переписк",
        "вспомни что писали",
        "перечитай переписк",
        "что в переписке",
        "что в истории чата",
        "что в чате",
        "look back at",
        "look back in",
        "review the chat",
        "review our conversation",
        "check the chat history",
        "check the conversation",
        "re-read the chat",
        "scroll back",
    )
    if any(p in tl for p in retro_phrases):
        return True
    if "истин" in tl and any(
        x in tl for x in ("переписк", "диалог", "разговор", "бесед", "чат", "сообщен", "реплик")
    ):
        return True
    if len(tl) < 10:
        return False
    if "анализ системы" in tl and "диалог" not in tl and "разговор" not in tl and "переписк" not in tl:
        return False
    if (
        "анализ разговора" in tl
        or "анализ диалога" in tl
        or "анализ переписки" in tl
        or "анализ беседы" in tl
        or "анализ этой беседы" in tl
        or "разбор диалога" in tl
        or "разбор переписки" in tl
        or "разбери диалог" in tl
        or "разбери переписку" in tl
    ):
        return True
    analysis_kw = (
        "анализ",
        "проанализируй",
        "разбор",
        "разбери",
        "полный анализ",
        "ретроспектив",
        "что пошло не так",
        "в чём ошиб",
        "где ошиб",
    )
    conv_kw = (
        "разговор",
        "диалог",
        "переписк",
        "бесед",
        "сообщен",
        "реплик",
        "ты не учишься",
        "не учишься на ошиб",
        "повторяешь ошиб",
    )
    if any(a in tl for a in analysis_kw) and any(c in tl for c in conv_kw):
        return True
    return False


def user_requests_dialogue_analysis_effective(text: str, context: Optional[Dict[str, Any]] = None) -> bool:
    """Ключевые слова или уверенный сигнал meta_intent (dialogue_review) из execute_plan."""
    if user_requests_dialogue_analysis(text or ""):
        return True
    if not isinstance(context, dict):
        return False
    mi = context.get("meta_intent")
    if not isinstance(mi, dict) or str(mi.get("meta") or "") != "dialogue_review":
        return False
    try:
        c = float(mi.get("confidence", 0))
    except (TypeError, ValueError):
        c = 0.0
    try:
        floor = max(0.0, min(1.0, float((os.getenv("META_INTENT_MIN_CONFIDENCE") or "0.5").strip() or "0.5")))
    except ValueError:
        floor = 0.5
    return c >= floor


def skip_automatic_web_search(text: str) -> bool:
    """Тривиальное сообщение — не тянуть «веб-поиск» (в gemma: не подталкивать к лишним инструментам)."""
    if is_pure_chitchat_private(text):
        return True
    raw = (text or "").strip()
    if len(raw) < 10:
        return True
    return False


def _text_from_dialogue_row(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("text") or "")
    return str(row or "")


def recent_dialogue_hints_hygiene_packaging(recent_dialogue: Any, *, lookback: int = 18) -> bool:
    """В недавних репликах уже шла тема прокладок/маркировки — для «Продолжай» и т.п."""
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        return False
    blob = "\n".join(_text_from_dialogue_row(r) for r in recent_dialogue[-lookback:])
    return text_looks_hygiene_packaging_consumer(blob)


def text_looks_minimal_reaction(text: str) -> bool:
    """Очень короткая реакция без явного вопроса: «!», «?», междометия — смысл из recent_dialogue."""
    raw = (text or "").strip()
    if not raw or len(raw) > 22:
        return False
    compact = re.sub(r"\s+", "", raw)
    if len(compact) > 10:
        return False
    if re.fullmatch(r"[!?.…]+", compact):
        return True
    low = raw.lower()
    if low in {"ага", "угу", "хм", "мм", "ну", "а", "э", "эм", "ой", "оу"}:
        return True
    return False


def text_looks_dialog_followup_cue(text: str) -> bool:
    """Продолжение темы без нового длинного текста (в т.ч. «!» после переписки)."""
    if is_pure_chitchat_private(text):
        return False
    return text_looks_continuation_cue(text) or text_looks_minimal_reaction(text)


def recent_dialogue_has_substance(recent_dialogue: Any, *, lookback: int = 14) -> bool:
    """Есть ли в ленте хотя бы одна содержательная реплика пользователя (для коротких реакций)."""
    if not isinstance(recent_dialogue, list):
        return False
    for row in recent_dialogue[-lookback:]:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "").lower() != "user":
            continue
        t = str(row.get("text") or "").strip()
        if len(t) >= 10:
            return True
    return False


def text_looks_continuation_cue(text: str) -> bool:
    """
    Короткие сигналы «продолжи тему» без нового содержания.
    Без привязки к recent_dialogue модель часто начинает другой диалог или тянет случайные факты.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 56:
        return False
    tl = strip_trailing_sentence_punct(raw.lower())
    if tl in {
        "продолжай",
        "продолжи",
        "дальше",
        "далее",
        "ещё",
        "еще",
        "продолжение",
        "go on",
        "continue",
    }:
        return True
    if tl in {"ну продолжай", "ну давай", "давай дальше", "продолжи пожалуйста", "продолжай пожалуйста"}:
        return True
    if tl in {
        "почему",
        "зачем",
        "как",
        "что",
        "когда",
        "где",
        "откуда",
        "куда",
        "смазка",
        "возбуждение",
        "бесполезно",
        "бесполезен",
    }:
        return True
    if re.match(r"^а\s+", tl) and len(tl.split()) <= 8:
        return True
    return False


def text_looks_hygiene_packaging_consumer(text: str) -> bool:
    """
    Женская гигиена / маркировка упаковки — модель часто путает «ежедневки» со спортом
    и выдумывает «символику» цифр в треугольнике вместо кода полимера/переработки.
    """
    tl = (text or "").lower().strip()
    if len(tl) < 8:
        return False
    hygiene = (
        "ежедневк",
        "ежедневн",
        "проклад",
        "тампон",
        "менструац",
        "гигиеническ",
        "вкладыш",
        "лайнер",
        "женщин",
        "для женщин",
    )
    pack = (
        "упаковк",
        "маркировк",
        "треугольник",
        "переработк",
        "пластик",
        "состав",
        "косметик",
    )
    if not any(k in tl for k in hygiene):
        return False
    return any(k in tl for k in pack) or "ежедневк" in tl or "проклад" in tl


def _likely_needs_fresh_facts(text: str) -> bool:
    """Локальная эвристика вместо genesis.web_search.query_needs_web_context."""
    tl = (text or "").lower().strip()
    if len(tl) < 14:
        return False
    keys = (
        "курс доллара",
        "курс евро",
        "курс битко",
        "курс руб",
        "новости про",
        "что известно о",
        "актуальная цена",
        "сегодняшн",
    )
    return any(k in tl for k in keys)


_INTENT_FEW_SHOT_BASE = (
    "\n**Образцы (few-shot):**\n"
    "— «спасибо» → одна тёплая строка, без лекции.\n"
    "— «дз, упр. 3 стр. 22» → шаги по делу; если есть блок учебника/RAG — опирайся на него.\n"
    "— вопрос про курс/новости → опирайся на факты из контекста и ссылки; без выдумки.\n"
)
_INTENT_FEW_SHOT_GROUP = (
    "— В группе: коротко по адресату, **без** саммари всего чата.\n"
    "- В группе — тепло и по делу; неофициально, как в живом чате.\n"
    "- Если ответ по reply — реплай важнее фона чата.\n"
    "- Не приписывай людям цитаты, которых нет в контексте.\n"
    "- «Урок/задача» в шутку — **не** режим ДЗ и не выдуманный учебник.\n"
)


def format_intent_routing_user_addon(
    text: str,
    *,
    for_group: bool,
    recent_dialogue: Any = None,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Короткий блок в user-промпт: как трактовать реплику (как format_intent_routing в GENESIS)."""
    tl = (text or "").lower().strip()
    if not tl:
        return ""
    lines: List[str] = []
    if for_group and user_requests_verbatim_group_relay(text):
        lines.append(
            "• **Намерение:** дословная строка в этот чат — выведи **ровно** текст, который дал пользователь "
            "(все `@username`, текст в кавычках, буквальные фрагменты вроде `через @id`), без поиска пользователя в Telegram, "
            "без ответов «не смог найти пользователя» и без подстановки чужих username. "
            "Если просили без обрамления — без префиксов и пояснений."
        )
    _dfc = text_looks_dialog_followup_cue(text)
    _substance = recent_dialogue_has_substance(recent_dialogue)
    _hygiene_ctx = text_looks_hygiene_packaging_consumer(text) or (
        _dfc and recent_dialogue_hints_hygiene_packaging(recent_dialogue)
    )
    if _dfc and _substance:
        lines.append(
            "• **Намерение:** короткая реплика или **продолжение** («продолжай», «!», «?», междометие) при уже идущем диалоге. "
            "**Не отвечай** шаблоном «не понял запрос» и **не начинай новую тему** (птицы, случайные факты и т.д.), если пользователь явно продолжает претензию/тему. "
            "Восстанови нить из **recent_dialogue** и при необходимости из **telegram_reply_context** (цепочка **ответа** в Telegram важнее догадок). "
            "Если речь о **пересылке**: в апдейте иногда только комментарий, а не весь пересланный текст — опирайся на reply-цепочку и историю; если текста мало, честно скажи ограничение и попроси цитату или пересылку с подписью."
        )
    if _hygiene_ctx:
        lines.append(
            "• **Намерение:** женская гигиена и маркировка упаковки. «Ежедневки» в быту — это **ежедневные прокладки/вкладыши**, "
            "а не «активные занятия» или спорт. **Цифра в треугольнике** на упаковке обычно означает **код типа пластика/переработки** "
            "(международная система полимеров; точная расшифровка — по таблице на упаковке/сайте производителя), "
            "а не абстрактную «стабильность» или «чёткость». Отвечай по делу: слои/состав общими словами, выбор по комфорту и чувствительности, "
            "маркировки качества (ГОСТ/ТР ЕАЭС, CE и т.д. — без выдумки), утилизация — осторожно и по местным правилам. "
            "При необходимости фактов — UniversalSearch, **не** SelfProgramming."
        )
    school = text_looks_schoolish(text)
    if school:
        lines.append(
            "• **Намерение:** школа/ДЗ — приоритет точности; если есть RAG/учебник в инструментах — опирайся, не выдумывай страницы."
        )
    if _likely_needs_fresh_facts(text):
        lines.append(
            "• **Намерение:** актуальные факты — external_hint и knowledge_hint; при сбое первого источника попробуй **другой** доступный инструмент (UrlFetch по публичному URL, RAG и т.д.), не останавливайся на «нет данных», если в списке инструментов есть запасной путь; не выдумывай цифры."
        )
    emo = any(
        x in tl
        for x in (
            "грустн",
            "устал",
            "боюсь",
            "тревож",
            "одинок",
            "не могу",
            "плохо мне",
            "обидел",
            "ссор",
        )
    )
    if emo and not school:
        lines.append(
            "• **Намерение:** поддержка/эмоции — тепло и по делу, без диагнозов; "
            + (
                "не уводи в репетиторский тон."
                if for_group
                else "не уводи в учебник без запроса."
            )
        )
    if is_pure_chitchat_private(text):
        lines.append(
            "• **Намерение:** лёгкий диалог — коротко и по-человечески, без лекции и без «шпаргалки по жизни»."
        )
    elif user_requests_dialogue_analysis_effective(text, context):
        lines.append(
            "• **Намерение:** разбор переписки/диалога — опирайся на recent_dialogue и текущее сообщение; "
            "это **не** аудит кода и **не** повод для SelfProgramming.*; ответь пользователю сразу "
            "структурировано (например: что просили → где промах/повтор → что сделать дальше), "
            "без монолога «какой инструмент выбрать» и без пересказа system-промпта."
        )
    elif not school and len(tl) < 56 and "?" not in text and not (_dfc and _substance):
        lines.append("• **Намерение:** короткая реплика — не раздувай ответ без запроса на подробности.")
    grp = ""
    if for_group:
        grp = " **В группе** плотнее, без саммари всего чата. "
    if not lines:
        return ""
    few = _INTENT_FEW_SHOT_BASE + (_INTENT_FEW_SHOT_GROUP if for_group else "")
    return (
        "\n\n**Маршрут (эвристика, не для цитирования пользователю):**"
        + grp
        + "\n"
        + "\n".join(lines)
        + few
    )
