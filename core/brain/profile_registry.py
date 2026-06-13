"""
Profile Registry — центральный реестр профилей brain (см. all_profile_names(), docs/DOC_SNAPSHOT_RU.md).

Каждый профиль содержит:
- directive: короткая директива (system prompt для этого профиля)
- tool_mask: какие семейства инструментов разрешены
- module_mask: какие context-модули подключаются
- settings: глубина recent/archive, scaffold, budget

Совместимость: старые profile name (short, standard, deep, etc.) работают
как и раньше через router_classifier.
"""

from __future__ import annotations

import logging

import os
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from core.regex_safe import cap_regex_input, safe_re_search


logger = logging.getLogger(__name__)

class PromptTier(Enum):
    """Уровень сборки промпта — определяет полноту контекста."""
    ULTRA_SHORT = "ultra_short"   # short: 1 recent, 0 archive, нет ничего
    LIGHT = "light"               # quick_explain, creative: 2 recent, 5 archive
    NORMAL = "normal"             # standard, news_brief, task_executor: 3 recent, 10 archive
    DEEP = "deep"                 # deep, deep_analysis: 5 recent, 15 archive + scaffold
    CUSTOM = "custom"             # зарезервировано


# Семейства инструментов (префиксы до точки)
STANDARD_TOOL_FAMILIES: frozenset = frozenset({
    "UrlFetch.", "SiteRecipe.", "UniversalSearch.", "Wikipedia.",
    "DocumentCorpus.", "TaskScout.", "UserKnowledgeArchive.",
    "ArithmeticTool.", "DialogRecall.", "News.", "FileIntake.", "Greetings.",
    "GeoMaps.", "Schedule.", "SelfConfig.",
})
PLUGIN_TOOL_FAMILIES: frozenset = frozenset({"SelfProgramming.", "RuntimeDiagnostic."})
WEB_RESEARCH_FAMILIES: frozenset = frozenset({
    "UniversalSearch.", "UrlFetch.", "Wikipedia.", "SiteRecipe.",
})
CODE_TOOL_FAMILIES: frozenset = frozenset({"UniversalSearch.", "DialogRecall."})
LEGAL_TOOL_FAMILIES: frozenset = frozenset({
    "DocumentCorpus.", "UniversalSearch.", "UrlFetch.",
})
EDU_TOOL_FAMILIES: frozenset = frozenset({
    "BooksRAG.", "UniversalSearch.", "Wikipedia.",
})
DOC_TOOL_FAMILIES: frozenset = frozenset({
    "DocumentCorpus.", "UserKnowledgeArchive.", "FileIntake.", "DialogRecall.",
})


@dataclass
class ProfileConfig:
    """Конфигурация одного профиля."""
    name: str
    tier: PromptTier
    directive: str
    tool_families: Set[str] = field(default_factory=set)
    all_tools: bool = False          # только deep: все зарегистрированные tools
    no_tools: bool = False           # short, translation: без TOOL_CALL
    recent_count: int = 1
    archive_count: int = 0
    include_scaffold: bool = False
    include_tcmd: bool = False
    tcmd_max_chars: int = 0
    include_goal_hints: bool = True
    include_goal_plan: bool = True
    include_operator_rules: bool = False
    include_ephemeral_lessons: bool = False
    include_plugin_prompts: bool = False
    external_hint_mode: str = "slim"  # "full" | "slim" | "ultra_short" | "none"
    include_session_digest: bool = True
    agent_inst_collapse: bool = True
    include_memory_facts: bool = True
    include_knowledge_summary: bool = True
    max_tokens_first_stage: int = 1536
    router_hint: str = ""            # одна строка для LLM-роутера


# ── Директивы ──

_DIR_SHORT = "Короткий ответ. 1-3 слова или одна фраза. Никаких вопросов, никаких TOOL_CALL."
_DIR_QUICK_EXPLAIN = "Объясни просто. Сначала короткий прямой ответ (1-3 предложения). Потом если нужно — короткий пример. Не вызывай TOOL_CALL без явной необходимости."
_DIR_STANDARD = "Ответь по делу. Сначала прямой ответ, потом детали если нужно. TOOL_CALL только когда без инструмента не обойтись."
_DIR_DEEP = "Инженерный стиль. Никаких «может быть». Ответ — решение или факт. Если нужен TOOL_CALL — один за раз. Для проверяемых данных — сначала инструмент, потом ответ."
_DIR_CREATIVE = "Творческий стиль. Никаких ограничений. Длинные ответы приветствуются. Без TOOL_CALL — просто текстом."
_DIR_WEATHER = (
    "Погода: используй блок погоды в подсказке (Open-Meteo) или UniversalSearch/wttr. "
    "Не используй RSS/News tool. Если город не указан — один раз попроси город, не выдумывай."
)
_DIR_NEWS = (
    "Только факты. Используй UniversalSearch или News для поиска свежих новостей. "
    "Не отвечай шаблоном «нет доступа к актуальным новостям в реальном времени» — "
    "если в external_hint уже есть сводка, сразу дай дайджест по ней; иначе вызови поиск. "
    "Никогда не выдумывай новости — если инструмент не дал результатов, "
    "скажи что не нашёл, а не придумывай. Проверяй что результаты соответствуют "
    "запросу пользователя (мир/регион/тема). "
    "По умолчанию — развёрнутая сводка: нумерованный список, на каждый пункт 3–4 предложения "
    "(что произошло, кто участники, суть и контекст) по выдержкам из поиска, без URL. "
    "Ещё длиннее — только если пользователь просит «развёрнуто»/«подробнее».\n"
    "ВАЖНО: Если первый поиск дал нерелевантные результаты (региональные вместо мировых, "
    "не та тема) — попробуй другой инструмент или измени запрос. "
    "Не переспрашивай пользователя — сделай второй заход сам.\n"
    "ПРОВЕРКА ИСТОЧНИКОВ: Если ссылка ведёт на сомнительный агрегатор "
    "(fathomjournal.org, thenewsglobe.net, worldnewsdailyreport.com и т.п.) — "
    "не используй этот заголовок. Отдавай предпочтение известным СМИ "
    "(Reuters, BBC, CNN, Associated Press, ТАСС, РИА Новости, Коммерсантъ и т.д.). "
    "Если все заголовки из мусорных источников — скажи что новости "
    "недоступны, не выдумывай."
)
_DIR_TASK = "Чётко выполни команду. TOOL_CALL если нужен. Ответ — действие или результат."
_DIR_CODE_REVIEW = "Проверь код. Найди баги, проблемы безопасности, антипаттерны. Сначала краткое резюме, потом список проблем."
_DIR_CODE_GEN = "Напиши код. Язык из запроса. Сначала код, потом краткое объяснение если нужно."
_DIR_CODE_DEBUG = "Найди ошибку. Проанализируй код/ошибку. TOOL_CALL для поиска решения если нужно."
_DIR_DOC_QA = "Ответь по документу. Используй DocumentCorpus если нужно. Ответ только из документа."
_DIR_SUMMARIZE = "Суммаризируй кратко. 3-5 предложений. Только суть."
_DIR_TRANSLATE = (
    "Перевод. Ответ — только переведённый текст на запрошенный язык. "
    "Без комментариев, без «Примечание», без TOOL_CALL и без обсуждения инструментов."
)
_DIR_MATH = "Реши задачу. Используй ArithmeticTool для вычислений. Покажи шаги решения."
_DIR_PLANNING = "Составь план. Шаги, сроки, ресурсы. Структурированно."
_DIR_RESEARCH = "Разбери тему. Найди источники. TOOL_CALL для поиска. Итог — структурированный обзор."
_DIR_TROUBLESHOOT = "Помоги найти проблему. TOOL_CALL для поиска решения. Шаги диагностики."
_DIR_TUTORIAL = "Объясни как сделать. Пошагово. Примеры в конце."
_DIR_ROLEPLAY = "Ты персонаж. Отвечай от лица персонажа. Без выхода из роли."
_DIR_DEBATE = "Аргументируй. Приведи доводы за и против. Ссылки на источники."
_DIR_DATA_ANALYSIS = "Проанализируй данные. TOOL_CALL для поиска. Итог — выводы."
_DIR_RECOMMENDATION = "Посоветуй. Сравни варианты. Обоснуй выбор."
_DIR_BRAINSTORM = "Предложи идеи. Чем больше, тем лучше. Без критики на этом этапе."
_DIR_LEGAL = ""
_DIR_EDUCATION = "Помоги с учёбой. Объясни тему. TOOL_CALL для учебников если нужно."
_DIR_HELP = "Перечисли доступные команды и возможности. Кратко по категориям."
_DIR_BATCH = (
    "Пользователь прислал НЕСКОЛЬКО пунктов (вопросы, команды, задачи) в одном сообщении. "
    "Определи точное количество пунктов. Ответь на КАЖДЫЙ без пропусков. "
    "Нумеруй ответы 1., 2., 3. и так далее до последнего пункта. "
    "На каждый пункт — отдельный чёткий ответ (1-5 предложений). "
    "НЕ ОСТАНАВЛИВАЙСЯ, пока не ответишь на все пункты. "
    "Проверь: последний номер ответа должен равняться общему числу пунктов. "
    "TOOL_CALL только если без него не обойтись."
)


# ── Реестр профилей ──

_PROFILES: Dict[str, ProfileConfig] = {
    # ── Базовые 8 (существующие) ──
    "short": ProfileConfig(
        name="short",
        tier=PromptTier.ULTRA_SHORT,
        directive=_DIR_SHORT,
        no_tools=True,
        max_tokens_first_stage=512,
        router_hint="greeting, 1-2 words, not a question",
        recent_count=10,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        external_hint_mode="ultra_short",
        include_session_digest=False,
        agent_inst_collapse=True,
        include_memory_facts=False,
        include_knowledge_summary=False,
    ),
    "quick_explain": ProfileConfig(
        name="quick_explain",
        tier=PromptTier.LIGHT,
        directive=_DIR_QUICK_EXPLAIN,
        router_hint="why/what is, short factual explanation",
        max_tokens_first_stage=1536,
        tool_families={"UniversalSearch.", "Wikipedia.", "ArithmeticTool."},
        recent_count=2,
        archive_count=5,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_ephemeral_lessons=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
    ),
    "standard": ProfileConfig(
        name="standard",
        tier=PromptTier.NORMAL,
        directive=_DIR_STANDARD,
        router_hint="regular chat, general question",
        tool_families={"UniversalSearch.", "Wikipedia.", "ArithmeticTool."},
        recent_count=10,
        archive_count=10,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        external_hint_mode="slim",
        include_session_digest=False,
        agent_inst_collapse=True,
    ),
    "deep": ProfileConfig(
        name="deep",
        tier=PromptTier.DEEP,
        directive=_DIR_DEEP,
        all_tools=True,
        max_tokens_first_stage=2000,
        router_hint="code, comparison, analysis, programming, complex task",
        recent_count=5,
        archive_count=15,
        include_scaffold=True,
        include_tcmd=True,
        tcmd_max_chars=4500,
        include_goal_hints=True,
        include_goal_plan=True,
        include_operator_rules=True,
        include_plugin_prompts=True,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "deep_analysis": ProfileConfig(
        name="deep_analysis",
        tier=PromptTier.DEEP,
        directive=_DIR_DEEP,
        router_hint="research, multi-step analysis, long task",
        max_tokens_first_stage=4096,
        tool_families={
            "UrlFetch.", "SiteRecipe.", "UniversalSearch.", "Wikipedia.",
            "DocumentCorpus.", "ArithmeticTool.",
            "DialogRecall.", "GeoMaps.", "Schedule.", "UserKnowledgeArchive.",
        },
        recent_count=5,
        archive_count=15,
        include_scaffold=True,
        include_tcmd=True,
        tcmd_max_chars=4500,
        include_goal_hints=True,
        include_goal_plan=True,
        include_operator_rules=True,
        include_plugin_prompts=True,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "creative": ProfileConfig(
        name="creative",
        tier=PromptTier.LIGHT,
        directive=_DIR_CREATIVE,
        router_hint="write story, poem, creative text",
        max_tokens_first_stage=2048,
        tool_families={"UrlFetch.", "Wikipedia."},
        recent_count=1,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_knowledge_summary=False,
    ),
    "weather_brief": ProfileConfig(
        name="weather_brief",
        tier=PromptTier.NORMAL,
        directive=_DIR_WEATHER,
        router_hint="weather forecast, temperature, city",
        max_tokens_first_stage=1536,
        tool_families={"UniversalSearch.", "UrlFetch.", "GeoMaps."},
        recent_count=2,
        archive_count=2,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
    ),
    "news_brief": ProfileConfig(
        name="news_brief",
        tier=PromptTier.NORMAL,
        directive=_DIR_NEWS,
        router_hint="news, current events, media",
        max_tokens_first_stage=2048,
        tool_families={"UrlFetch.", "SiteRecipe.", "UniversalSearch.", "Wikipedia."},
        recent_count=2,
        archive_count=3,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
    ),
    "task_executor": ProfileConfig(
        name="task_executor",
        tier=PromptTier.NORMAL,
        directive=_DIR_TASK,
        tool_families={
            "UrlFetch.", "SiteRecipe.", "UniversalSearch.", "Wikipedia.",
            "DocumentCorpus.", "ArithmeticTool.",
            "DialogRecall.", "GeoMaps.", "Schedule.", "UserKnowledgeArchive.",
            "SelfProgramming.", "RuntimeDiagnostic.",
        },
        recent_count=2,
        archive_count=3,
        include_scaffold=False,
        include_tcmd=True,
        tcmd_max_chars=1000,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=True,
        include_plugin_prompts=True,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),

    # ── Новые 19 профилей ──
    "code_review": ProfileConfig(
        name="code_review",
        tier=PromptTier.DEEP,
        directive=_DIR_CODE_REVIEW,
        tool_families=set(CODE_TOOL_FAMILIES),
        router_hint="review code, find bugs, security",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=3,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="slim",
        agent_inst_collapse=False,
        include_knowledge_summary=False,
    ),
    "code_generation": ProfileConfig(
        name="code_generation",
        tier=PromptTier.NORMAL,
        directive=_DIR_CODE_GEN,
        tool_families=set(CODE_TOOL_FAMILIES),
        router_hint="write code, implement function",
        max_tokens_first_stage=2048,
        recent_count=1,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_knowledge_summary=False,
    ),
    "code_debug": ProfileConfig(
        name="code_debug",
        tier=PromptTier.DEEP,
        directive=_DIR_CODE_DEBUG,
        tool_families=set(CODE_TOOL_FAMILIES) | set(WEB_RESEARCH_FAMILIES),
        router_hint="debug error, stack trace, fix code",
        max_tokens_first_stage=3072,
        recent_count=2,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_knowledge_summary=False,
    ),
    "document_qa": ProfileConfig(
        name="document_qa",
        tier=PromptTier.NORMAL,
        directive=_DIR_DOC_QA,
        tool_families=set(DOC_TOOL_FAMILIES),
        router_hint="answer from user document or corpus",
        max_tokens_first_stage=2048,
        recent_count=2,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_knowledge_summary=False,
    ),
    "summarization": ProfileConfig(
        name="summarization",
        tier=PromptTier.LIGHT,
        directive=_DIR_SUMMARIZE,
        tool_families={"DialogRecall."},
        router_hint="summarize text briefly",
        max_tokens_first_stage=1024,
        recent_count=1,
        archive_count=5,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_memory_facts=False,
        include_knowledge_summary=False,
    ),
    "translation": ProfileConfig(
        name="translation",
        tier=PromptTier.ULTRA_SHORT,
        directive=_DIR_TRANSLATE,
        no_tools=True,
        router_hint="translate text, keep meaning",
        max_tokens_first_stage=1024,
        recent_count=1,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=False,
        external_hint_mode="none",
        include_session_digest=False,
        agent_inst_collapse=True,
        include_memory_facts=False,
        include_knowledge_summary=False,
    ),
    "math_solve": ProfileConfig(
        name="math_solve",
        tier=PromptTier.NORMAL,
        directive=_DIR_MATH,
        tool_families={"ArithmeticTool.", "UniversalSearch."},
        router_hint="solve math, calculate, show steps",
        max_tokens_first_stage=1536,
        recent_count=2,
        archive_count=2,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        external_hint_mode="slim",
        agent_inst_collapse=False,
    ),
    "planning": ProfileConfig(
        name="planning",
        tier=PromptTier.DEEP,
        directive=_DIR_PLANNING,
        tool_families=set(STANDARD_TOOL_FAMILIES) - {"News."},
        router_hint="plan steps, timeline, resources",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=True,
        include_goal_plan=True,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "research": ProfileConfig(
        name="research",
        tier=PromptTier.DEEP,
        directive=_DIR_RESEARCH,
        router_hint="research topic, sources, structured overview",
        max_tokens_first_stage=3072,
        tool_families={"UniversalSearch.", "UrlFetch.", "Wikipedia."},
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "troubleshooting": ProfileConfig(
        name="troubleshooting",
        tier=PromptTier.DEEP,
        directive=_DIR_TROUBLESHOOT,
        tool_families=set(WEB_RESEARCH_FAMILIES) | set(PLUGIN_TOOL_FAMILIES) | {"DialogRecall.", "RuntimeDiagnostic."},
        include_plugin_prompts=True,
        router_hint="fix problem, diagnose, bot error",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "tutorial": ProfileConfig(
        name="tutorial",
        tier=PromptTier.NORMAL,
        directive=_DIR_TUTORIAL,
        tool_families=set(EDU_TOOL_FAMILIES) | {"UniversalSearch.", "Wikipedia."},
        router_hint="how-to tutorial, step by step",
        max_tokens_first_stage=2048,
        recent_count=2,
        archive_count=0,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="slim",
        agent_inst_collapse=False,
        include_knowledge_summary=False,
    ),
    "roleplay": ProfileConfig(
        name="roleplay",
        tier=PromptTier.LIGHT,
        directive=_DIR_ROLEPLAY,
        no_tools=True,
        router_hint="roleplay character, stay in role",
        max_tokens_first_stage=2048,
        recent_count=3,
        archive_count=3,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        include_session_digest=True,
        agent_inst_collapse=True,
        include_memory_facts=True,
        include_knowledge_summary=False,
    ),
    "debate": ProfileConfig(
        name="debate",
        tier=PromptTier.DEEP,
        directive=_DIR_DEBATE,
        tool_families=set(WEB_RESEARCH_FAMILIES),
        router_hint="argue pro/con, debate with sources",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "data_analysis": ProfileConfig(
        name="data_analysis",
        tier=PromptTier.DEEP,
        directive=_DIR_DATA_ANALYSIS,
        tool_families={"ArithmeticTool.", "UniversalSearch.", "DocumentCorpus."},
        router_hint="analyze data, statistics, conclusions",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "recommendation": ProfileConfig(
        name="recommendation",
        tier=PromptTier.LIGHT,
        directive=_DIR_RECOMMENDATION,
        tool_families=set(WEB_RESEARCH_FAMILIES),
        router_hint="recommend, compare options",
        max_tokens_first_stage=1536,
        recent_count=2,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_knowledge_summary=False,
    ),
    "brainstorm": ProfileConfig(
        name="brainstorm",
        tier=PromptTier.LIGHT,
        directive=_DIR_BRAINSTORM,
        no_tools=True,
        router_hint="brainstorm ideas, many options",
        max_tokens_first_stage=2048,
        recent_count=2,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=True,
        include_knowledge_summary=False,
    ),
    "legal": ProfileConfig(
        name="legal",
        tier=PromptTier.DEEP,
        directive=_DIR_LEGAL,
        tool_families=set(LEGAL_TOOL_FAMILIES),
        router_hint="generic legal research",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        external_hint_mode="slim",
        agent_inst_collapse=False,
        include_knowledge_summary=False,
    ),
    "education": ProfileConfig(
        name="education",
        tier=PromptTier.DEEP,
        directive=_DIR_EDUCATION,
        tool_families=set(EDU_TOOL_FAMILIES),
        router_hint="study, textbook, school topic",
        max_tokens_first_stage=3072,
        recent_count=3,
        archive_count=5,
        include_scaffold=True,
        include_tcmd=False,
        include_goal_hints=True,
        include_goal_plan=True,
        external_hint_mode="full",
        agent_inst_collapse=False,
    ),
    "command_help": ProfileConfig(
        name="command_help",
        tier=PromptTier.NORMAL,
        directive=_DIR_HELP,
        tool_families=set(STANDARD_TOOL_FAMILIES) | set(PLUGIN_TOOL_FAMILIES),
        router_hint="list bot commands, help, capabilities",
        max_tokens_first_stage=1536,
        recent_count=1,
        archive_count=0,
        include_scaffold=False,
        include_tcmd=True,
        tcmd_max_chars=12000,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_plugin_prompts=True,
        external_hint_mode="slim",
        include_session_digest=False,
        agent_inst_collapse=False,
        include_memory_facts=False,
        include_knowledge_summary=False,
    ),
    "batch": ProfileConfig(
        name="batch",
        tier=PromptTier.DEEP,
        directive=_DIR_BATCH,
        router_hint="multiple questions/commands in one message",
        max_tokens_first_stage=16000,
        tool_families={"UniversalSearch.", "ArithmeticTool."},
        recent_count=2,
        archive_count=3,
        include_scaffold=False,
        include_tcmd=False,
        include_goal_hints=False,
        include_goal_plan=False,
        include_operator_rules=False,
        include_ephemeral_lessons=False,
        include_plugin_prompts=False,
        external_hint_mode="slim",
        agent_inst_collapse=False,
        include_session_digest=True,
        include_memory_facts=False,
        include_knowledge_summary=False,
    ),
}


# ── Mapping от старых имён базовых профилей к новым (router_classifier → новый реестр) ──
# Базовые 8 имён = те же. Новые профили маппятся из intent.
_INTENT_TO_PROFILE: Dict[str, str] = {
    "explain": "quick_explain",
    "why": "quick_explain",
    "what": "quick_explain",
    "greeting": "short",
    "thanks": "short",
    "ok": "short",
    "agree": "short",
    "help": "command_help",
    "admin": "command_help",
    "capabilities": "command_help",
    "code_review": "code_review",
    "code_gen": "code_generation",
    "code_debug": "code_debug",
    "code": "deep",
    "debug": "code_debug",
    "analysis": "deep_analysis",
    "research": "research",
    "plan": "planning",
    "planning": "planning",
    "math": "math_solve",
    "calculate": "math_solve",
    "news": "news_brief",
    "weather": "weather_brief",
    "translate": "translation",
    "summarize": "summarization",
    "summary": "summarization",
    "creative": "creative",
    "write": "creative",
    "imagine": "creative",
    "tutorial": "tutorial",
    "learn": "education",
    "study": "education",
    "education": "education",
    "legal": "legal",
    "law": "legal",
    "roleplay": "roleplay",
    "debate": "debate",
    "recommend": "recommendation",
    "recommendation": "recommendation",
    "brainstorm": "brainstorm",
    "idea": "brainstorm",
    "task": "task_executor",
    "command": "task_executor",
    "do": "task_executor",
    "execute": "task_executor",
    "troubleshoot": "troubleshooting",
    "fix": "troubleshooting",
    "data": "data_analysis",
    "data_analysis": "data_analysis",
    "document": "document_qa",
    "doc": "document_qa",
}

# Профили, где recent намеренно минимален (длинный user_text / перевод / ultra-short).
_LOW_RECENT_MEMORY_PROFILES = frozenset({
    "summarization",
    "translation",
    "short",
    "command_help",
    "weather_brief",
    "creative",
})


def _env_recent_count(name: str, fallback: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return fallback
    try:
        return max(3, min(16, int(raw)))
    except ValueError:
        return fallback


def context_load_recent_limit() -> int:
    """Сколько реплик подтягивать из behavior_store в начале call_brain."""
    return _env_recent_count("BRAIN_CONTEXT_LOAD_RECENT_LIMIT", _env_recent_count("BRAIN_STANDARD_RECENT_COUNT", 10))


def router_override_min_confidence() -> float:
    try:
        v = float((os.getenv("BRAIN_ROUTER_OVERRIDE_MIN_CONFIDENCE") or "0.85").strip())
    except ValueError:
        v = 0.85
    return max(0.5, min(0.99, v))


def effective_recent_dialogue_limit(profile_name: str) -> int:
    return max(1, min(16, int(get_profile(profile_name).recent_count or 3)))


def build_route_audit(
    *,
    final_profile: str,
    preflight: Optional[str] = None,
    router_profile: str = "",
    router_source: str = "",
    router_confidence: float = 0.0,
    continuation_profile: str = "",
    situation_lane: str = "",
    classifier_profile: str = "",
    heuristic_gate: Optional[List[Dict[str, Any]]] = None,
    discourse: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "final_profile": (final_profile or "standard").strip(),
        "preflight": (preflight or "").strip() or None,
        "router_profile": (router_profile or "").strip(),
        "router_source": (router_source or "").strip(),
        "router_confidence": round(float(router_confidence or 0.0), 3),
        "continuation": (continuation_profile or "").strip() or None,
        "situation_lane": (situation_lane or "").strip() or None,
        "classifier": (classifier_profile or "").strip() or None,
    }
    if isinstance(heuristic_gate, list) and heuristic_gate:
        out["heuristic_gate"] = heuristic_gate[-6:]
    if isinstance(discourse, dict) and discourse:
        out["discourse"] = discourse
    return out


def get_profile(profile_name: str) -> ProfileConfig:
    """Получить конфиг профиля по имени. Fallback на standard."""
    cfg = _PROFILES.get(profile_name)
    if cfg is None:
        cfg = _PROFILES["standard"]
    if cfg.name == "standard":
        n = _env_recent_count("BRAIN_STANDARD_RECENT_COUNT", 10)
        cfg = replace(cfg, recent_count=n)
    elif cfg.name == "short":
        n = _env_recent_count("BRAIN_SHORT_RECENT_COUNT", 10)
        cfg = replace(cfg, recent_count=n)
    elif cfg.name == "quick_explain":
        n = _env_recent_count(
            "BRAIN_QUICK_EXPLAIN_RECENT_COUNT",
            _env_recent_count("BRAIN_SHORT_RECENT_COUNT", 10),
        )
        cfg = replace(cfg, recent_count=n)
    elif cfg.name in ("code_generation", "code_debug"):
        n = _env_recent_count("BRAIN_CODE_RECENT_COUNT", 6)
        cfg = replace(cfg, recent_count=n)
    elif cfg.name == "research":
        n = _env_recent_count(
            "BRAIN_RESEARCH_RECENT_COUNT",
            _env_recent_count("BRAIN_STANDARD_RECENT_COUNT", 10),
        )
        cfg = replace(cfg, recent_count=n)
    elif cfg.name == "news_brief":
        n = _env_recent_count(
            "BRAIN_NEWS_RECENT_COUNT",
            _env_recent_count("BRAIN_STANDARD_RECENT_COUNT", 10),
        )
        cfg = replace(cfg, recent_count=n)
    elif (
        cfg.name not in _LOW_RECENT_MEMORY_PROFILES
        and cfg.recent_count <= 3
        and cfg.name
        not in (
            "standard",
            "short",
            "quick_explain",
            "code_generation",
            "code_debug",
            "research",
            "news_brief",
        )
    ):
        n = _env_recent_count(
            "BRAIN_LIGHT_RECENT_COUNT",
            _env_recent_count("BRAIN_QUICK_EXPLAIN_RECENT_COUNT", 8),
        )
        cfg = replace(cfg, recent_count=max(cfg.recent_count, n))
    return cfg


def profile_for_intent(intent: str) -> str:
    """Определить профиль по intent (от router/intent heuristic)."""
    if not intent:
        return "standard"
    low = intent.strip().lower()
    return _INTENT_TO_PROFILE.get(low, "standard")


def get_directive(profile_name: str) -> str:
    """Получить директиву для профиля."""
    return get_profile(profile_name).directive


def get_tier(profile_name: str) -> PromptTier:
    return get_profile(profile_name).tier


def get_tool_families(profile_name: str) -> Set[str]:
    """Явные семейства из конфига (не то же самое, что resolve_tool_prefixes)."""
    return get_profile(profile_name).tool_families


def all_profile_names() -> List[str]:
    return list(_PROFILES.keys())


def intent_profile_map() -> Dict[str, str]:
    """Вернуть копию маппинга intent→profile."""
    return dict(_INTENT_TO_PROFILE)


def is_valid_profile(name: str) -> bool:
    return name in _PROFILES


def resolve_tool_prefixes(profile_name: str) -> Optional[Set[str]]:
    """
    Префиксы tools для профиля.
    None = все инструменты (deep).
    Пустой set() = без инструментов.
  """
    cfg = get_profile(profile_name)
    if cfg.no_tools or profile_name == "short":
        return set()
    if cfg.all_tools:
        return None
    if cfg.tool_families:
        return set(cfg.tool_families)
    return set(STANDARD_TOOL_FAMILIES)


def router_profiles_catalog() -> str:
    """Строка Profiles: для LLM-роутера (все 28)."""
    lines: List[str] = []
    for name in sorted(_PROFILES.keys()):
        cfg = _PROFILES[name]
        hint = (cfg.router_hint or cfg.directive).strip().replace("\n", " ")[:120]
        lines.append(f"- {name}: {hint}")
    return "\n".join(lines)


def is_continuation_turn(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Короткий ход продолжает нить диалога (контекст + форма, не словарь слов)."""
    try:
        from core.brain.discourse_resolver import is_continuation_from_context

        if is_continuation_from_context(user_text, context):
            return True
        from core.brain.user_facing_contract import is_continuation_turn_from_context

        return is_continuation_turn_from_context(user_text, context)
    except Exception:
        low = (user_text or "").strip().lower()
        return bool(low) and len(low) <= 28 and low in {
            "продолжи",
            "продолжай",
            "дальше",
            "continue",
        }


def resolve_continuation_profile(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Для «Продолжи» / «дальше» — унаследовать профиль прошлого хода, не standard+71 tools.
  """
    ctx = context if isinstance(context, dict) else {}
    try:
        from core.brain.discourse_resolver import inherited_profile_from_context

        disc_prof = inherited_profile_from_context(ctx)
        if disc_prof and is_valid_profile(disc_prof) and disc_prof not in (
            "short",
            "batch",
            "task_executor",
        ):
            return disc_prof
    except Exception:
        pass
    if not is_continuation_turn(user_text, ctx):
        return None
    ds = ctx.get("dialogue_state")
    if isinstance(ds, dict):
        prev_raw = str(ds.get("last_brain_profile") or ds.get("brain_profile") or "").strip().lower()
        if prev_raw and is_valid_profile(prev_raw) and prev_raw not in ("short", "task_executor", "batch"):
            return prev_raw
    prev_ctx_raw = str(ctx.get("last_brain_profile") or "").strip().lower()
    if prev_ctx_raw and is_valid_profile(prev_ctx_raw) and prev_ctx_raw not in ("short", "task_executor", "batch"):
        return prev_ctx_raw
    return "quick_explain"


def _profile_text_gate(
    rule_id: str,
    profile: str,
    user_text: str,
    planner_context: Optional[Dict[str, Any]],
) -> Optional[str]:
    try:
        from core.heuristic_context_gate import should_run_shortcut

        if should_run_shortcut(
            rule_id,
            user_text,
            planner_context=planner_context,
        ).allowed:
            return profile
    except Exception as e:
        logger.debug("profile_text_gate %s: %s", rule_id, e)
        return profile
    return None


def profile_from_text_heuristics(
    user_text: str,
    *,
    planner_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Текстовые эвристики → профиль (до/после роутера)."""
    txt = (user_text or "").strip()
    if not txt:
        return None
    try:
        from core.brain.profile_route_guard import preflight_profile as _preflight_profile

        _pre = _preflight_profile(txt)
        if _pre and is_valid_profile(_pre):
            return _pre
    except Exception as e:
        logger.debug('%s optional failed: %s', 'profile_registry', e, exc_info=True)
    low = txt.lower()
    if txt.startswith("/"):
        return "task_executor"
    _txt_cap = cap_regex_input(txt, max_len=4096)
    if safe_re_search(r"(?i)(?:^|\n)\s*(?:переведи|translate)\b", _txt_cap) or safe_re_search(
        r"(?i)^перевод\s", low, max_len=4096
    ):
        _tr = _profile_text_gate("profile_translation_prefix", "translation", txt, planner_context)
        if _tr:
            return _tr
    if any(t in low for t in ("суммариз", "кратко перескаж", "резюме", "summarize")):
        _sm = _profile_text_gate("profile_summarization_substring", "summarization", txt, planner_context)
        if _sm:
            return _sm
    if any(t in low for t in ("проверь код", "code review", "ревью кода", "найди баг")):
        return "code_review"
    if any(t in low for t in ("напиши код", "напиши программу", "implement ", "function ")):
        return "code_generation"
    if any(t in low for t in ("ошибка", "traceback", "debug", "не компилируется", "exception")):
        _cd = _profile_text_gate("profile_code_debug_word", "code_debug", txt, planner_context)
        if _cd:
            return _cd
    if any(t in low for t in ("закон", "нпа", "law.example.com", "указ ", "кодекс")):
        _lg = _profile_text_gate("profile_legal_substring", "legal", txt, planner_context)
        if _lg:
            return _lg
    if safe_re_search(r"(?i)(?:^|\n|\.)\s*(?:статья\s+\d|статьёй\s+\d|ст\.?\s*\d)", _txt_cap):
        _lg2 = _profile_text_gate("profile_legal_substring", "legal", txt, planner_context)
        if _lg2:
            return _lg2
    if any(t in low for t in ("реши", "вычисли", "посчитай", "уравнен")) and any(
        c in txt for c in "0123456789=+-*/"
    ):
        _ms = _profile_text_gate("profile_math_substring", "math_solve", txt, planner_context)
        if _ms:
            return _ms
        # Gate blocked math on prose — paste/Habr → summarization ниже; фин. проза → None.
        try:
            from core.intent_heuristics import prose_narrative_disfavors_calculator
            from core.brain.text_helpers import looks_like_pasted_news_article

            if prose_narrative_disfavors_calculator(txt) and not looks_like_pasted_news_article(txt):
                return None
        except Exception as e:
            logger.debug('%s math prose gate: %s', 'profile_registry', e, exc_info=True)
    try:
        from core.batch_continuation import is_unified_problem, looks_like_unified_math_problem

        if looks_like_unified_math_problem(txt) or (
            is_unified_problem(txt)
            and re.search(
                r"(?i)(тессеракт|пентеракт|гиперкуб|четыр[её]хмерн|тр[её]хмерн\s+гран)",
                txt,
            )
        ):
            _ms_geo = _profile_text_gate("profile_math_geometry", "math_solve", txt, planner_context)
            if _ms_geo:
                return _ms_geo
    except Exception as e:
        logger.debug('%s optional failed: %s', 'profile_registry', e, exc_info=True)
    if any(t in low for t in ("спланируй", "составь план", "распиши план", "разработай план", "пошаговый план")):
        return "planning"
    if any(t in low for t in ("исследуй", "разбери тему", "найди информацию", "изучи вопрос", "глубокий анализ")):
        _rs = _profile_text_gate("profile_research_substring", "research", txt, planner_context)
        if _rs:
            return _rs
    try:
        from core.product_behavior import should_force_product_search

        if should_force_product_search(txt):
            return "research"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'profile_registry', e, exc_info=True)
    if any(t in low for t in ("не работает", "сломалось", "ошибка в боте", "помоги разобраться", "траблшутинг", "диагностируй", "почини")):
        _ts = _profile_text_gate("profile_troubleshooting_substring", "troubleshooting", txt, planner_context)
        if _ts:
            return _ts
    if any(t in low for t in ("как сделать", "как настроить", "объясни как", "покажи как", "научи меня")):
        return "tutorial"
    if any(t in low for t in ("ты персонаж", "ты робот", "отыграй", "сыграй роль", "отвечай как")):
        return "roleplay"
    if any(t in low for t in ("аргументируй", "за и против", "плюсы и минусы", "дебаты", "контраргумент")):
        return "debate"
    if any(t in low for t in ("проанализируй данные", "статистика", "тренды", "аналитика", "проанализируй цифры")):
        return "data_analysis"
    if any(t in low for t in ("посоветуй", "порекомендуй", "лучший вариант", "сравни варианты", "какой выбрать")):
        return "recommendation"
    if any(t in low for t in ("учебник", "падручник", "padruchnik", "гуо", "дз ")):
        return "education"
    if any(t in low for t in ("/help", "команды", "справка", "что умеешь", "возможности")):
        return "command_help"
    if any(t in low for t in ("мозговой штурм", "идеи", "brainstorm")):
        return "brainstorm"
    if any(t in low for t in ("по документу", "в файле", "в pdf", "document corpus")):
        return "document_qa"
    if any(t in low for t in ("почему", "отчего", "зачем", "explain", "объясни", "поясни", "расскажи почему")):
        _qe = _profile_text_gate("profile_quick_explain_substring", "quick_explain", txt, planner_context)
        if _qe:
            return _qe
    if any(t in low for t in ("расскажи про", "расскажи о ", "расскажи об ")):
        _qe2 = _profile_text_gate("profile_quick_explain_substring", "quick_explain", txt, planner_context)
        if _qe2:
            return _qe2
    if "простыми словами" in low or "простым языком" in low:
        _qe3 = _profile_text_gate("profile_quick_explain_substring", "quick_explain", txt, planner_context)
        if _qe3:
            return _qe3
    if any(t in low for t in ("сочини", "придумай", "напиши рассказ", "стихи", "поэма")):
        return "creative"
    try:
        from core.brain.text_helpers import (
            _user_text_looks_like_weather_query,
            looks_like_news_headlines_request,
            looks_like_pasted_news_article,
        )

        if _user_text_looks_like_weather_query(low):
            _wb = _profile_text_gate("profile_weather_substring", "weather_brief", txt, planner_context)
            if _wb:
                return _wb
    except Exception:
        pass
    try:
        from core.brain.text_helpers import (
            looks_like_news_headlines_request,
            looks_like_pasted_news_article,
        )

        if looks_like_pasted_news_article(txt):
            return "summarization"
        if looks_like_news_headlines_request(txt):
            _nb = _profile_text_gate("profile_news_headlines", "news_brief", txt, planner_context)
            if _nb:
                return _nb
    except Exception:
        if any(t in low for t in ("новости", "что нового", "новост")):
            _nb = _profile_text_gate("profile_news_headlines", "news_brief", txt, planner_context)
            if _nb:
                return _nb
    return None


_PROFILE_ALIASES: Dict[str, str] = {
    "перевод": "translation",
    "закон": "legal",
    "код": "code_generation",
    "математика": "math_solve",
    "справка": "command_help",
    # legacy: batch unified раньше ставил несуществующий профиль
    "reasoning": "math_solve",
}


def normalize_profile(name: str, *, fallback: str = "standard") -> str:
    """Привести имя профиля к валидному из реестра (27)."""
    raw = str(name or "").strip().lower()
    if is_valid_profile(raw):
        return raw
    mapped = _INTENT_TO_PROFILE.get(raw) or _PROFILE_ALIASES.get(raw)
    if mapped and is_valid_profile(mapped):
        return mapped
    return fallback if is_valid_profile(fallback) else "standard"


def profile_prefers_thorough_tier(profile_name: str) -> bool:
    """Нужен ли nested/deep task_tier (не сбрасывать в shallow)."""
    return get_tier(profile_name) == PromptTier.DEEP


def profile_allows_self_verify(profile_name: str, *, need_memory: bool = False) -> bool:
    """Самопроверка ответа по tier профиля."""
    tier = get_tier(profile_name)
    if tier == PromptTier.DEEP:
        return True
    if need_memory and tier in (PromptTier.NORMAL, PromptTier.LIGHT):
        return profile_name not in ("short", "translation", "brainstorm", "roleplay")
    return False


def merge_classifier_profile(
    profile: str,
    classifier_result: Optional[Dict[str, Any]],
    *,
    router_confidence: float = 1.0,
) -> str:
    """Qdrant/LRU эталон уточняет профиль, если роутер дал generic или низкий conf."""
    if not classifier_result:
        return normalize_profile(profile)
    cp = normalize_profile(str(classifier_result.get("profile") or ""))
    base = normalize_profile(profile)
    if cp == base:
        return base
    if base in ("standard", "quick_explain") or router_confidence < 0.8:
        return cp
    return base


def classifier_need_memory(classifier_result: Optional[Dict[str, Any]]) -> Optional[bool]:
    if not classifier_result:
        return None
    raw = str(classifier_result.get("need_memory") or "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return None


def refine_profile(
    profile: str,
    user_text: str,
    intent: str = "",
    *,
    confidence: float = 1.0,
    planner_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Уточнить профиль: preflight → текст (осторожно) → intent → clamp.

    Специальные профили (batch) не перезатираются — их назначил батч-детектор,
    и текстовая эвристика не должна их отменять.
    """
    profile = normalize_profile(profile)
    _special_profiles = frozenset({"batch"})
    if profile in _special_profiles:
        return profile
    try:
        from core.brain.profile_route_guard import preflight_profile as _preflight_profile

        _pre = _preflight_profile(user_text)
        if _pre and is_valid_profile(_pre):
            return _pre
    except Exception as e:
        logger.debug('%s optional failed: %s', 'profile_registry', e, exc_info=True)
    by_text = profile_from_text_heuristics(user_text, planner_context=planner_context)
    if by_text and is_valid_profile(by_text):
        if confidence < 0.8 or profile in (
            "standard",
            "quick_explain",
            "math_solve",
            "translation",
            "legal",
        ):
            profile = by_text
    by_intent = profile_for_intent(intent)
    if by_intent != "standard" and is_valid_profile(by_intent):
        if profile in ("standard", "quick_explain") or confidence < router_override_min_confidence():
            profile = by_intent
    try:
        from core.brain.profile_route_guard import clamp_profile as _clamp_profile

        return _clamp_profile(
            profile,
            user_text,
            router_confidence=confidence,
        )
    except Exception:
        return profile
