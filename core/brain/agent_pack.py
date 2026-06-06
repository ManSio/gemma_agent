"""
Adaptive agent instruction: полный монолит (A) vs укороченный chat-core (B) + доменные вставки.

Питфоллы проекта:
- tools_info — уже отфильтрован filter_tools_for_brain; BooksRAG попадает только при RAG-эвристике.
- SelfProgramming + handbook — только в полном пакете (иначе ломается генерация плагинов).
- Law / Adu — в B одна базовая строка; расширение только по сигналам, иначе раздуваем каждый ход.
- Свертка промпта: при collapse_level >= 2 prompt_pack подменяет agent на AGENT_INSTRUCTION_COLLAPSE_STUB.

v1.2.0: profile-based system prompts via profile_registry.compose_system_prompt
for adaptive prompt size and KV-cache stability.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.monitoring import MONITOR
from core.prompt_routing import text_warrants_textbook_rag, user_requests_dialogue_analysis_effective
from core.task_depth import tier_prefers_thorough

from core.brain.agent import agent_instruction_effective
from core.brain.constants import (
    BRAIN_PROFILE_DEEP,
    BRAIN_PROFILE_SHORT,
    BRAIN_PROFILE_STANDARD,
    BRAIN_PROFILE_QUICK_EXPLAIN,
    BRAIN_PROFILE_NEWS_BRIEF,
    BRAIN_PROFILE_DEEP_ANALYSIS,
    BRAIN_PROFILE_CREATIVE,
    BRAIN_PROFILE_TASK_EXECUTOR,
)
from core.brain.directive_blocks import (
    AGENT_AUTONOMY_CONSTITUTION,
    AGENT_INSTRUCTION_CHAT_CORE,
    compose_system_prompt,
)
from core.brain.env import env_flag
from core.brain.hot_path import skill_output_heavy


def _tools_any_prefix(tools_info: Dict[str, str], prefix: str) -> bool:
    return any(str(k).startswith(prefix) for k in tools_info.keys())


def _force_full_agent_pack(
    *,
    user_text: str,
    context: Dict[str, Any],
    task_tier: str,
    tools_mode: str,
    tools_info: Dict[str, str],
    urls_chron: List[str],
    missing_facts: List[Any],
    skill_name: Optional[str],
    skill_output: Any,
    image_intent: Optional[str],
) -> bool:
    if not env_flag("BRAIN_AGENT_PACK_ADAPTIVE", default=True):
        return True
    if context.get("brain_force_full_agent_pack"):
        return True
    if tools_mode == "full":
        return True
    if tier_prefers_thorough((task_tier or "").strip()):
        return True
    ut = (user_text or "").strip()
    low = ut.lower()
    if "http://" in low or "https://" in low:
        return True
    if urls_chron:
        return True
    if user_requests_dialogue_analysis_effective(ut, context):
        return True
    if missing_facts:
        return True
    if text_warrants_textbook_rag(ut):
        return True
    if image_intent:
        return True
    if skill_name and skill_output_heavy(skill_output):
        return True
    ocr = context.get("ocr_text")
    if ocr and len(str(ocr)) > 600:
        return True
    fc = context.get("file_context") if isinstance(context.get("file_context"), dict) else {}
    if fc.get("file_type") == "image" and str(fc.get("local_path") or "").strip():
        return True
    ph = context.get("predictive_hint") if isinstance(context.get("predictive_hint"), dict) else {}
    sp = ph.get("skill_priority")
    if isinstance(sp, list) and sp:
        return True
    if _tools_any_prefix(tools_info, "SelfProgramming."):
        return True
    if _tools_any_prefix(tools_info, "BooksRAG."):
        return True
    return False


def build_agent_instruction_for_turn(
    *,
    tools_mode: str,
    tools_info: Dict[str, str],
    user_text: str,
    context: Dict[str, Any],
    task_tier: str,
    urls_chron: List[str],
    missing_facts: List[Any],
    skill_name: Optional[str],
    skill_output: Any,
    image_intent: Optional[str],
    profile: str = "standard",
) -> Tuple[str, Dict[str, Any]]:
    """
    Возвращает (agent_inst, meta) где meta: pack full|chat, inserts: [...].
    """
    ctx = context if isinstance(context, dict) else {}
    from core.brain.profile_registry import get_profile as _get_profile_cfg
    _pcfg = _get_profile_cfg(profile or "standard")
    if _pcfg.include_plugin_prompts and any(str(k).startswith("SelfProgramming.") for k in tools_info.keys()):
        ctx = {**ctx, "brain_force_full_agent_pack": True}
    if _force_full_agent_pack(
        user_text=user_text,
        context=ctx,
        task_tier=task_tier,
        tools_mode=tools_mode,
        tools_info=tools_info,
        urls_chron=urls_chron,
        missing_facts=missing_facts,
        skill_name=skill_name,
        skill_output=skill_output,
        image_intent=image_intent,
    ):
        MONITOR.inc("brain_agent_pack_full_total")
        return agent_instruction_effective(tools_mode, tools_info), {"pack": "full", "inserts": []}

    MONITOR.inc("brain_agent_pack_chat_total")
    # Доменные вставки (law, uka, adu…) — в prompt_modules, не здесь
    body = "\n\n".join([AGENT_AUTONOMY_CONSTITUTION.strip(), AGENT_INSTRUCTION_CHAT_CORE])
    return body, {"pack": "chat", "inserts": []}


# ── ID дефолтных мягких целей, не требующих deep-профиля ──
# emotional_comfort — ambient-цель (активируется от "устал"/"сложно"), не влияет на сложность
_DEFAULT_SOFT_GOAL_IDS = {"calm_structured_style", "fast_coding_help", "emotional_comfort"}

# Safety: сообщения короче этого лимита НИКОГДА не получают deep (даже от классификатора)
_SHORT_TEXT_HARD_LIMIT = 15


# ── Эвристики памяти: фразы, указывающие на обращение к истории ──
_MEMORY_TRIGGERS = [
    "помнишь", "помните", "ты помни", "вы помни",
    "мы обсуждали", "мы говорили", "мы общались",
    "в прошлый раз", "ранее ты", "раньше ты",
    "ты говорил", "ты сказал", "ты упоминал",
    "мой архив", "мои заметки",
    "поищи в истории", "найди в истории", "посмотри в истории",
    "что я спрашивал", "что я писал",
    "напомни", "напоминаю",
    "просил запомнить", "просила запомнить",
    "запоминал",
    "что я просил", "что я просила",
    "какое слово", "какие слова",
]

# ── Эвристики сложной задачи: фразы, требующие deep-профиля ──
_COMPLEX_TASK_TRIGGERS = [
    "рассчитай", "вычисли", "посчитай",
    "спланируй", "составь план", "разработай план",
    "сравни", "проанализируй", "проанализируйте",
    "разбери", "разложи",
    "оцени", "рассмотри",
    "напиши код", "напиши программу",
    "объясни подробно",
    "пошагово", "шаг за шагом",
]

_NEWS_TRIGGERS = [
    "новост", "сми", "репортаж", "сообщает", "заявил", "заявила",
    "адвокат", "прокурор", "суд", "приговор", "законопроект",
    "выборы", "президент", "правительств", "министр",
    "санкци", "экономик", "банк", "биржа", "курс",
    "политик", "депутат", "парламент",
]

_CREATIVE_TRIGGERS = [
    "напиши стих", "сочини", "придумай", "сгенерируй",
    "расскажи историю", "напиши рассказ",
    "создай сценарий", "придумай сюжет",
    "стихотворени", "поэм", "ода",
    "песн", "текст песн",
]

_INTENT_COMPLEXITY_DEEP = 0.7

# Короткие фактологические вопросы (почему/что такое/как устроено и т.п.)
# — не должны уходить в deep, даже если intent_complexity > порога.
_FACTUAL_QUESTION = re.compile(
    r"(?i)^(почему|откуда|зачем|как\s+устроен|как\s+работает|как\s+образует|"
    r"как\s+получает|как\s+происход|из-за\s+чего|для\s+чего|"
    r"что\s+такое|кто\s+такой|кто\s+такая|"
    r"где\s+находит|когда\s+появил)",
)


def _has_trigger(text: str, triggers: list) -> bool:
    """Проверка наличия любого триггера из списка в тексте (регистронезависимо)."""
    low = text.lower()
    return any(t in low for t in triggers)


def _intent_from_context(context: Dict[str, Any]) -> str:
    """Извлечь последний intent пользователя из dialogue_state."""
    ds = context.get("dialogue_state") if isinstance(context, dict) else {}
    if isinstance(ds, dict):
        return str(ds.get("last_intent") or "general").strip().lower()
    return "general"


def determine_profile(
    *,
    user_text: str = "",
    active_goal_ids: Optional[List[str]] = None,
    intent_complexity: float = 0.0,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Определяет профиль сессии по содержимому запроса и контексту.

    Порядок проверки:
    1. non-default active goals → deep
    2. short (<20 символов, без ?)
    3. quick_explain (explain+короткий, <100 символов)
    4. news_brief (новостные/статейные маркеры, >100 символов)
    5. task_executor (начинается с /)
    6. creative (творческие триггеры)
    7. deep_analysis (длинные запросы с аналитикой, >500 символов)
    8. standard (fallback)
    """
    ctx = context if isinstance(context, dict) else {}
    txt = (user_text or "").strip()
    ut_len = len(txt)

    # Safety: очень короткие сообщения (<_SHORT_TEXT_HARD_LIMIT, без "?" и триггеров)
    # никогда не получают deep — форсируем short даже при активных целях
    if ut_len < _SHORT_TEXT_HARD_LIMIT and "?" not in txt and not _has_trigger(txt, _MEMORY_TRIGGERS):
        return BRAIN_PROFILE_SHORT

    # 1. Не-дефолтные активные цели → deep
    if active_goal_ids:
        non_default = [gid for gid in active_goal_ids if gid not in _DEFAULT_SOFT_GOAL_IDS]
        if non_default:
            return BRAIN_PROFILE_DEEP

    # 2. Короткий (short)
    if ut_len < 20 and "?" not in txt and not _has_trigger(txt, _MEMORY_TRIGGERS):
        return BRAIN_PROFILE_SHORT

    # 3. Быстрое объяснение (explain + короткий)
    _intent = _intent_from_context(ctx)
    if ut_len < 100 and _intent == "explain":
        return BRAIN_PROFILE_QUICK_EXPLAIN

    # 4. Короткий фактологический вопрос (почему/что такое/как устроено)
    # — не должен уходить в deep даже при intent_complexity > 0.7
    if ut_len < 300 and _FACTUAL_QUESTION.match(txt):
        return BRAIN_PROFILE_QUICK_EXPLAIN

    # 5. Новости — только явный дайджест; длинная статья (#myfin_news) → summarization
    try:
        from core.brain.text_helpers import (
            looks_like_news_headlines_request,
            looks_like_pasted_news_article,
        )

        if looks_like_pasted_news_article(txt):
            pass
        elif looks_like_news_headlines_request(txt):
            return BRAIN_PROFILE_NEWS_BRIEF
        elif ut_len > 100 and _has_trigger(txt, _NEWS_TRIGGERS) and looks_like_news_headlines_request(txt):
            return BRAIN_PROFILE_NEWS_BRIEF
    except Exception:
        if ut_len > 100 and _has_trigger(txt, _NEWS_TRIGGERS):
            return BRAIN_PROFILE_NEWS_BRIEF

    # 0. Текстовые / intent сигналы из profile_registry (все имена из реестра)
    from core.brain.profile_registry import profile_from_text_heuristics, profile_for_intent, is_valid_profile
    _by_text = profile_from_text_heuristics(txt)
    if _by_text and is_valid_profile(_by_text):
        return _by_text
    _by_intent = profile_for_intent(_intent)
    if _by_intent not in ("standard",) and is_valid_profile(_by_intent):
        return _by_intent

    # 6. Явная команда или direct_action
    if txt.startswith("/") or _intent == "direct_action":
        return BRAIN_PROFILE_TASK_EXECUTOR

    # 6. Творческие запросы
    if _has_trigger(txt, _CREATIVE_TRIGGERS):
        return BRAIN_PROFILE_CREATIVE

    # 7. Сложный анализ (длинный + аналитические маркеры)
    _has_complex = _has_trigger(txt, _COMPLEX_TASK_TRIGGERS)
    if (ut_len >= 10 and _has_complex) or ut_len > 500:
        return BRAIN_PROFILE_DEEP_ANALYSIS

    # 8. deep: intent_complexity > 0.7
    if intent_complexity > _INTENT_COMPLEXITY_DEEP:
        return BRAIN_PROFILE_DEEP

    # 9. Fallback
    return BRAIN_PROFILE_STANDARD


def estimate_need_memory(*, user_text: str) -> bool:
    """
    Эвристика: нужен ли поиск в памяти на основе текста запроса.
    Возвращает True, если текст содержит триггеры памяти.
    """
    txt = (user_text or "").strip()
    if not txt:
        return False
    return _has_trigger(txt, _MEMORY_TRIGGERS)


def pick_system_prompt_for_profile(
    profile: str,
    standard_prompt: str,
) -> str:
    """
    System prompt по профилю.
    standard — компактная сборка с HONESTY_COMPACT (не merge_system).
    Все остальные — compose_system_prompt.
    """
    from core.brain.directive_blocks import compose_system_prompt_standard

    if profile == BRAIN_PROFILE_STANDARD:
        return compose_system_prompt_standard()
    return compose_system_prompt(profile)
