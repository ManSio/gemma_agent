"""Эвристическая подсказка для выбора инструмента (мягкий bias в промпт, без отдельного LLM)."""

from __future__ import annotations

import logging

import os
import re
from dataclasses import dataclass
from typing import Any, List, Set

from core.prompt_routing import (
    recent_dialogue_hints_hygiene_packaging,
    text_looks_dialog_followup_cue,
)
from core.site_recipe_engine import host_matches

logger = logging.getLogger(__name__)


@dataclass
class ToolRoutingHint:
    suggested: List[str]
    prompt_note: str


_LAW_KW = (
    "закон",
    "статья",
    "ук рб",
    "кодекс",
    "право",
    "pravo",
    "etalonline",
    "нпа",
    "постановлен",
    "указ",
    "приказ",
    "декрет",
)
_SEARCH_KW = (
    "найди",
    "поищи",
    "погугли",
    "поиск",
    "кто такой",
    "что такое",
    "информация о",
    "lookup",
    "search for",
)
_WIKI_KW = ("википед", "wikipedia", "wiki ")
# Не включать голое «плагин» — ложные срабатывания на бытовой речи; нужны явные сигналы разработки.
_CODE_PLUGIN_KW = (
    "module.json",
    "selfprogramming",
    "execute(",
    "сгенерируй модуль",
    "сгенерировать модуль",
    "напиши модуль",
    "напиши плагин",
    "создай модуль",
    "создай плагин",
    "hot_install",
    "hot-install",
    "plugin.json",
    "generate_module",
)
_CONSUMER_HYGIENE_KW = (
    "ежедневк",
    "проклад",
    "тампон",
    "менструац",
    "гигиеническ",
    "вкладыш",
    "треугольник",
    "маркировк на упаковке",
)
# Рецепты и пошаговое приготовление — не «энциклопедия»; Википедия редко заменяет кулинарный веб-поиск.
_DIALOG_RECALL_HINT_KW = (
    "что писали",
    "что мы говорили",
    "что обсуждали",
    "напомни переписк",
    "история чата",
    "в переписке",
    "прошлые сообщения",
    "раньше в чате",
    "scroll back",
    "earlier in the chat",
    "what did we write",
    "what did we say",
    "просил запомнить",
    "просила запомнить",
    "что я просил запомнить",
    "что я просила запомнить",
    "какое слово просил",
    "какие слова просил",
    "запоминал",
    "забыл",
    "забудешь",
)
_ARCHIVE_KW = (
    "архив знаний",
    "сохрани текст",
    "сохранить документ",
    "личн баз",
    "личная база",
    "проверь по источник",
    "сверь с интернет",
    "перекрёстн",
    "перекрестн",
    "корпус",
    "сохрани вставк",
    "запомни слов",
    "запомнить слов",
    "запомни фраз",
    "запомнить фраз",
    "запомни числ",
    "запомни код",
)
_PERSONAL_LIB_KW = (
    "личн библиотек",
    "личная библиотек",
    "в личн библиотек",
    "файл в личн",
    "что в вложен",
    "сохранённый pdf",
    "сохраненный pdf",
    "из вложен",
    "user_library",
)
_PROFILE_SNAPSHOT_KW = (
    "цифровой образ",
    "мой образ",
    "что ты обо мне",
    "что думаешь обо мне",
    "интересы из сообщ",
    "интересы из перепис",
    "проанализируй меня",
    "мой профиль",
    "digital twin",
)
_LOCAL_USER_TEXT_KW = (
    "что сохранено",
    "что в общей базе",
    "что у нас в базе",
)
_SCOUT_KW = (
    "план действий",
    "стратегия доступ",
    "как достучаться",
    "не открывается сайт",
    "антибот",
    "капч",
    "waf",
    "rate limit",
    "разведк",
    "task scout",
    "конспект по защит",
    "пошаговый план",
)
# Портал электронных учебников ГУО (вспомогательная школа и др.)
_ADU_HOST_MARKERS = ("edu.example.com", "padruchnik-asabliva.adu.by")
_ADU_TEXTBOOK_KW = (
    "e-padruchnik-asabliva",
    "padruchnik-asabliva",
    "падручнік-асаблів",
    "падручнік асаблів",
    "асаблівыя вучобы",
    "вспомогательная школа",
    "вспомогательн школ",
    "вспомогательное отделен",
    "первое отделен",
    "электронн учебник",
    "электронный учебник",
    "електронн падручнік",
    "акадэмія адукацыі",
    "академия образования",
    "национальн образовательн портал",
)
_ADU_ACTION_KW = (
    "учебник",
    "падручнік",
    "пасобнік",
    "пособие",
    "скачай",
    "скачать",
    "пришли",
    "пришлите",
    "загрузи",
    "файлом",
    "документом",
    "в телеграм",
    "в чат",
    "pdf",
    "найди",
    "найти",
    "поищи",
)
_RECIPE_KW = (
    "рецепт",
    "приготовить",
    "приготовл",
    "как приготовить",
    "пошагов",
    "кулинар",
    "закуск",
    "маринад",
    "запекан",
    "тушён",
    "тушить",
    "блюдо из",
    "салат из",
    "суп из",
    "в духовке",
    "на сковороде",
    "ингредиент",
)
_NEWS_KW = (
    "новости",
    "новость",
    "новост",
    "что нового в мире",
    "мировые новости",
    "последние новости",
    "главные новости",
    "сводка",
    "что произошло",
    "news",
    "headlines",
    "what's new",
    "latest",
)


def _env_enabled() -> bool:
    raw = (os.getenv("BRAIN_TOOL_ROUTING_HINT") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _multistep_universal_enabled() -> bool:
    raw = os.getenv("TOOL_ROUTING_MULTISTEP_UNIVERSAL")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _goal_runner_env_on() -> bool:
    if (os.getenv("GOAL_RUNNER_EXECUTOR_MODE") or os.getenv("GOAL_RUNNER_ULTIMATE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    return os.getenv("GOAL_RUNNER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _url_hosts(urls: List[str]) -> List[str]:
    out: List[str] = []
    for u in urls:
        if not isinstance(u, str):
            continue
        s = u.strip().lower()
        if "://" in s:
            try:
                host = s.split("://", 1)[1].split("/", 1)[0]
                out.append(host)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'tool_routing_hint', e, exc_info=True)
    return out


def _dialogue_scope_low(recent_dialogue: Any, *, limit: int = 14) -> str:
    """Текст последних реплик (все роли) в нижнем регистре — для портала учебников по контексту темы."""
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        return ""
    parts: List[str] = []
    for row in recent_dialogue[-limit:]:
        if isinstance(row, dict):
            t = str(row.get("text") or "").strip()
            if t:
                parts.append(t)
    return " ".join(parts).lower()


def build_tool_routing_hint(
    user_text: str,
    urls_in_message: List[str],
    allowed: Set[str],
    recent_dialogue: Any = None,
) -> ToolRoutingHint:
    if not _env_enabled():
        return ToolRoutingHint([], "")

    text = (user_text or "").strip()
    low = text.lower()
    hygiene_followup = text_looks_dialog_followup_cue(text) and recent_dialogue_hints_hygiene_packaging(
        recent_dialogue
    )
    suggested: List[str] = []
    reasons: List[str] = []

    hosts = _url_hosts(urls_in_message if isinstance(urls_in_message, list) else [])

    dlg_low = _dialogue_scope_low(recent_dialogue)
    scope_low = (low + " " + dlg_low).strip().lower()

    adu_host = any(any(m in h for m in _ADU_HOST_MARKERS) for h in hosts)
    adu_kw = any(k in scope_low for k in _ADU_TEXTBOOK_KW)
    adu_action = any(k in low for k in _ADU_ACTION_KW)
    # Портал учебников: URL портала ИЛИ (маркеры вспомог. школы/портала в контексте + действие в текущем сообщении)
    adu_signal = adu_host or (adu_kw and adu_action)

    if False and adu_signal:  # public: disabled
        adu_chain: List[str] = []
        if urls_in_message and "AduPadruchnik.resolve_url" in allowed:
            adu_chain.append("AduPadruchnik.resolve_url")
        if "AduPadruchnik.search_books" in allowed:
            adu_chain.append("AduPadruchnik.search_books")
        if "AduPadruchnik.resolve_book" in allowed:
            adu_chain.append("AduPadruchnik.resolve_book")
        adu_chain = [t for t in adu_chain if t in allowed]
        if adu_chain:
            suggested = adu_chain + [s for s in suggested if s not in adu_chain]
            reasons.insert(
                0,
                "учебники/пособия портала edu.example.com — сначала AduPadruchnik "
                "(search_books по запросу, resolve_book по book_id из таблицы, resolve_url если дали viewer/PDF); "
                "для файла в Telegram — pdf_url и /filefrom",
            )

    if urls_in_message and "UrlFetch.fetch_page" in allowed:
        suggested.append("UrlFetch.fetch_page")
        reasons.append("в сообщении есть URL — для факта со страницы уместен UrlFetch.fetch_page")

    for h in hosts:
        if host_matches(h, "wikipedia.org") and "Wikipedia.scan" in allowed:
            if "Wikipedia.scan" not in suggested:
                suggested.append("Wikipedia.scan")
                reasons.append("ссылка на Википедию — Wikipedia.scan")
        if False and any(x in h for x in ("law.example.com", "law-archive.example.com")) and "LawSearch.fetch_act" in allowed:
            pass  # public
        if False and False:
            reasons.append("официальный правовой URL — LawSearch.fetch_act")

    if any(k in low for k in _LAW_KW) and "UniversalSearch.search" in allowed:
        if "UniversalSearch.search" not in suggested:
            suggested.insert(0, "UniversalSearch.search")
        reasons.insert(0, "запрос про законодательство — UniversalSearch.search (веб + корпус)")
        if "DocumentCorpus.unified_search" in allowed and any(
            x in low for x in ("локаль", "баз", "корпус", "документ")
        ):
            if "DocumentCorpus.unified_search" not in suggested:
                suggested.append("DocumentCorpus.unified_search")

    if any(k in low for k in _WIKI_KW) and "Wikipedia.scan" in allowed:
        if "Wikipedia.scan" not in suggested:
            suggested.append("Wikipedia.scan")
        reasons.append("энциклопедический вопрос — Wikipedia.scan")

    _local_docs_search_kw = (
        "в моих документах",
        "в моих файлах",
        "по моим документ",
        "в личном архив",
        "в сохранённ",
        "в сохраненн",
        "мои документ",
        "моих документ",
    )
    _local_docs_combo = any(k in low for k in _SEARCH_KW) and any(
        k in low for k in ("мои документ", "моих документ", "мой архив", "сохранён", "сохранен", "личн библиотек")
    )
    if (
        "UserKnowledgeArchive.archive_search" in allowed
        and (any(k in low for k in _local_docs_search_kw) or _local_docs_combo)
    ):
        if "UserKnowledgeArchive.archive_search" not in suggested:
            suggested.insert(0, "UserKnowledgeArchive.archive_search")
        reasons.insert(
            0,
            "поиск по текстам пользователя (заметки архива и личная библиотека .txt) — UserKnowledgeArchive.archive_search с query",
        )

    _news_world = any(k in low for k in ("мировые новости", "новости мира", "world news", "международн", "в мире", "global", "international"))
    if any(k in low for k in _NEWS_KW) and ("News.headlines" in allowed or "UniversalSearch.search" in allowed):
        if _news_world and "UniversalSearch.search" in allowed:
            # Мировые новости — UniversalSearch даёт глобальный контекст, News.headlines — RSS-регионал
            suggested.insert(0, "UniversalSearch.search")
            reasons.insert(0, "запрос про мировые новости/международные события — сначала UniversalSearch.search (веб-поиск по мировым источникам)")
            if "News.headlines" in allowed and "News.headlines" not in suggested:
                suggested.append("News.headlines")
                reasons.append("дополнительно — News.headlines (Google News RSS, без ключа, может дать локальную выдачу)")
        else:
            if "News.headlines" not in suggested:
                suggested.insert(0, "News.headlines")
            reasons.insert(0, "запрос про новости/сводку — News.headlines (Google News RSS, без ключа)")

    if any(k in low for k in _SEARCH_KW) and "UniversalSearch.search" in allowed:
        if "UniversalSearch.search" not in suggested:
            suggested.append("UniversalSearch.search")
        reasons.append("нужен обзор/факты без явного URL — UniversalSearch.search")

    if any(k in low for k in _ARCHIVE_KW) and "UserKnowledgeArchive.archive_store" in allowed:
        if "UserKnowledgeArchive.archive_store" not in suggested:
            suggested.append("UserKnowledgeArchive.archive_store")
        reasons.append("нужно сохранить длинный текст с метаданными — UserKnowledgeArchive.archive_store; сверка — archive_cross_check")

    if any(k in low for k in _PERSONAL_LIB_KW) and "UserKnowledgeArchive.personal_library_list" in allowed:
        if "UserKnowledgeArchive.personal_library_list" not in suggested:
            suggested.insert(0, "UserKnowledgeArchive.personal_library_list")
        reasons.insert(
            0,
            "файлы из вложений (личная библиотека) — UserKnowledgeArchive.personal_library_list; заметки архива — archive_list",
        )

    if any(k in low for k in _PROFILE_SNAPSHOT_KW) and "DigitalTwin.user_snapshot_for_agent" in allowed:
        if "DigitalTwin.user_snapshot_for_agent" not in suggested:
            suggested.insert(0, "DigitalTwin.user_snapshot_for_agent")
        reasons.insert(0, "сводка профиля/сессии — DigitalTwin.user_snapshot_for_agent; глубокая переписка — DialogRecall")

    if any(k in low for k in _LOCAL_USER_TEXT_KW):
        pair_tools: List[str] = []
        if "UserKnowledgeArchive.archive_list" in allowed:
            pair_tools.append("UserKnowledgeArchive.archive_list")
        if "UserKnowledgeArchive.personal_library_list" in allowed:
            pair_tools.append("UserKnowledgeArchive.personal_library_list")
        for t in reversed(pair_tools):
            if t not in suggested:
                suggested.insert(0, t)
        _wants_shared_corpus = any(
            x in low for x in ("общ", "корпус док", "корпус на сервер", "shared_knowledge", "documentcorpus")
        )
        if _wants_shared_corpus and "DocumentCorpus.stats" in allowed:
            if "DocumentCorpus.stats" not in suggested:
                suggested.insert(0, "DocumentCorpus.stats")
            if "DocumentCorpus.unified_search" in allowed and any(
                x in low for x in ("найди", "поиск", "есть ли", "что за")
            ):
                if "DocumentCorpus.unified_search" not in suggested:
                    suggested.insert(1, "DocumentCorpus.unified_search")
        if pair_tools or _wants_shared_corpus:
            reasons.insert(
                0,
                "локальные тексты — archive_list и personal_library_list; **общая база на сервере** (ingest + индекс) — "
                "DocumentCorpus.stats и при необходимости unified_search; книжный RAG — BooksRAG",
            )

    if ("сверь" in low or "проверь" in low) and any(x in low for x in ("источник", "факт", "интернет", "правд", "достовер")):
        if "UserKnowledgeArchive.archive_cross_check" in allowed and "UserKnowledgeArchive.archive_cross_check" not in suggested:
            suggested.append("UserKnowledgeArchive.archive_cross_check")
            reasons.append("сверка сохранённого или утверждения с поиском — UserKnowledgeArchive.archive_cross_check")

    if any(k in low for k in _SCOUT_KW) and "TaskScout.scout_plan" in allowed:
        if "TaskScout.scout_plan" not in suggested:
            suggested.append("TaskScout.scout_plan")
        reasons.append("нужна стратегия/план по сайту или защитам — TaskScout.scout_plan (память + заметки)")

    if re.search(r"\bsite\.|домен|html-страниц", low) and "SiteRecipe.parse_with_recipe" in allowed:
        if "SiteRecipe.parse_with_recipe" not in suggested:
            suggested.append("SiteRecipe.parse_with_recipe")
        reasons.append("разбор конкретного сайта — SiteRecipe")

    if any(k in low for k in _CODE_PLUGIN_KW) and "SelfProgramming.generate_module" in allowed:
        if "SelfProgramming.generate_module" not in suggested:
            suggested.append("SelfProgramming.generate_module")
        reasons.append("обсуждение/генерация плагина — SelfProgramming.generate_module")

    if (any(k in low for k in _CONSUMER_HYGIENE_KW) or hygiene_followup) and "UniversalSearch.search" in allowed:
        if "UniversalSearch.search" not in suggested:
            suggested.append("UniversalSearch.search")
        reasons.append("быт/гигиена/маркировка или продолжение той же темы — UniversalSearch.search для проверяемых формулировок")

    recipe_signal = any(k in low for k in _RECIPE_KW)
    if recipe_signal and "UniversalSearch.search" in allowed:
        if "UniversalSearch.search" not in suggested:
            suggested.insert(0, "UniversalSearch.search")
        else:
            suggested = ["UniversalSearch.search"] + [s for s in suggested if s != "UniversalSearch.search"]
        reasons.insert(
            0,
            "кулинария/рецепт — сначала UniversalSearch.search (обзор рецептурных сайтов); "
            "Википедия обычно не даёт полноценный пошаговый рецепт; если пользователь дал ссылку — UrlFetch.fetch_page",
        )
        if "Wikipedia.scan" in suggested and not any(k in low for k in _WIKI_KW):
            suggested = [s for s in suggested if s != "Wikipedia.scan"]
            reasons = [r for r in reasons if "энциклопед" not in r.lower()]

    hygiene_signal = any(k in low for k in _CONSUMER_HYGIENE_KW) or hygiene_followup
    code_signal = any(k in low for k in _CODE_PLUGIN_KW)
    if hygiene_signal and not code_signal:
        suggested = [s for s in suggested if s != "SelfProgramming.generate_module"]
        reasons = [r for r in reasons if "SelfProgramming" not in r]

    if any(k in scope_low for k in _DIALOG_RECALL_HINT_KW) and "DialogRecall.recall_bundle" in allowed:
        if "DialogRecall.recall_bundle" not in suggested:
            suggested.insert(0, "DialogRecall.recall_bundle")
        reasons.insert(
            0,
            "нужны прошлые реплики этой переписки (архив/digest) — DialogRecall.recall_bundle",
        )

    if False and adu_signal:  # public: disabled
        suggested = [s for s in suggested if s != "UniversalSearch.search"]

    suggested = [s for s in suggested if s in allowed]

    # Обзор «найди / что такое» + маркеры права: UniversalSearch первым.
    _explore_law = any(k in low for k in _SEARCH_KW) and any(k in low for k in _LAW_KW)
    if _explore_law and "UniversalSearch.search" in suggested:
        suggested = ["UniversalSearch.search"] + [s for s in suggested if s != "UniversalSearch.search"]
        reasons.insert(
            0,
            "обзорный запрос про право: сначала UniversalSearch (синонимы и веб), затем UrlFetch/DocumentCorpus",
        )

    try:
        from core.brain.goal_runner_nudge import warrants_multistep_goal_text as _warrants_ms
    except Exception:
        _warrants_ms = lambda _t: False
    if (
        _multistep_universal_enabled()
        and _goal_runner_env_on()
        and _warrants_ms(user_text)
        and "UniversalSearch.search" in allowed
        and not adu_signal
        and not (urls_in_message and isinstance(urls_in_message, list) and len(urls_in_message) > 0)
    ):
        if "UniversalSearch.search" not in suggested:
            suggested.insert(0, "UniversalSearch.search")
            reasons.insert(
                0,
                "многошаговая формулировка без явного URL — обзорный первый шаг часто UniversalSearch.search "
                "(дорожка чата; при автостарте Goal Runner построит план сам)",
            )

    if not suggested:
        return ToolRoutingHint([], "")

    note = (
        "tool_routing_hint (эвристика, не приказ): сначала рассмотри "
        + ", ".join(suggested)
        + ". Причины: "
        + "; ".join(reasons[:4])
        + "."
    )
    return ToolRoutingHint(suggested=suggested, prompt_note=note)
