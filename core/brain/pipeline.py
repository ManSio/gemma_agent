"""Центральный конвейер: память, twin, persona, RAG, tools, LLM."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.error_analysis import record_error_event
from core.autotune import record_performance as _record_performance
from core.autotune import record_bad_fix as _record_bad_fix
from core.brain.auto_reasoning_plugins import (
    auto_reasoning_plugins_report as _auto_reasoning_plugins_report,
    extract_auto_reasoning_gates as _extract_auto_reasoning_gates,
)
from core.brain.pipeline_routing import log_brain_route as _log_brain_route
from core.brain.pipeline_routing import resolve_brain_route as _resolve_brain_route
from core.brain.pipeline_early_guards import apply_early_input_guards as _apply_early_input_guards
from core.brain.pipeline_first_stage import (
    resolve_tool_calls_from_first_content as _resolve_tool_calls_from_first_content,
    run_first_stage_llm as _run_first_stage_llm,
)
from core.brain.pipeline_session_prep import setup_early_brain_session as _setup_early_brain_session
from core.brain.pipeline_tool_exec import execute_brain_tool as _execute_brain_tool
from core.brain.pipeline_postprocess import (
    emit_brain_tool_finished as _emit_brain_tool_finished,
    get_session_digest_for_prompt as _get_session_digest_for_prompt,
    persona_apply_polished as _persona_apply_polished,
)
from core.brain.self_verify_pass import (
    looks_like_garbage_json as _looks_like_garbage_json,
    retry_with_fix_hint as _retry_with_fix_hint,
    run_self_verify_with_limit as _self_verify,
    self_verify_fix_quality as _self_verify_fix_quality,
    self_verify_model_id as _self_verify_model_id,
    should_self_verify as _should_self_verify,
)
from core.self_learning import reflect_on_error as _reflect_on_error
from core.self_learning import LessonManager as _SelfLearningLessonManager
from core.self_learning import build_lessons_hint as _build_lessons_hint
from core.self_learning import validate_lessons_against_response as _validate_lessons_against_response
from core.document_intake import format_document_intake_for_brain
from core.grounding_pack import build_minimal_grounding
from core.llm_tiered import estimate_tiered_timeouts, llm_generate_tiered
from core.model_profile import clamp_temperature, merge_system, resolve_brain_primary_model, resolve_brain_secondary_model, resolve_model_profile
from core.module_gen_intent import build_generate_module_request
from core.brain.post_module_gen_ui import attach_post_module_gen_keyboard
from core.monitoring import MONITOR
from core.prompt_routing import (
    format_intent_routing_user_addon,
    user_requests_dialogue_analysis_effective,
)
from core.resilience import DEFAULT_RETRIES, DEFAULT_TIMEOUT_SEC, fallback_result, with_retry, with_timeout
from core.task_depth import (
    infer_task_tier as _infer_task_tier,
    infer_task_tier_with_history as _infer_task_tier_with_history,
    max_task_tier as _max_task_tier,
    refine_task_tier_from_outline as _refine_task_tier_from_outline,
    tier_prefers_thorough as _tier_prefers_thorough,
)
from core.conversation_profiles import system_addon_for_conversation_style
from core.calendar_facts import build_calendar_date_hint_for_llm
from core.dialogue_lookups import build_dialogue_lookup_hint_for_llm
from core.memory_recall_facade import build_pipeline_memory_addon
from core.timezone_inference import (
    apply_stated_timezone_to_facts,
    ensure_timezone_in_user_facts,
    format_clock_hint_for_llm,
    format_wall_clock_user_reply,
    infer_timezone_from_facts,
)
from core.url_thread import gather_urls_chronological_for_brain, user_signals_url_content_fetch
from modules.skills.image_skill import ImageSkillRouter
from modules.skills.skill_router import resolve_skill_intent
from modules.skills.router import skill_context_pack

from core.brain.agent import brain_tools_mode as _brain_tools_mode
from core.brain.agent import format_tools_full_index_for_prompt as _format_tools_full_index_for_prompt
from core.brain.agent import tools_for_profile as _tools_for_profile
from core.brain.agent import profile_first_stage_max_tokens as _profile_first_stage_max_tokens
from core.brain.agent_pack import build_agent_instruction_for_turn as _build_agent_instruction_for_turn
from core.brain.agent_pack import pick_system_prompt_for_profile as _pick_system_prompt_for_profile
from core.brain.classifier import save_successful_query as _save_successful_query
from core.brain.constants import AGENT_INSTRUCTION_COLLAPSE_STUB
from core.brain.constants import (
    BRAIN_CAPABILITY_HONESTY,
    BRAIN_INFRASTRUCTURE_HONESTY,
    SILENT_IMAGE_USER_PROMPT,
    brain_instance_attribution_block,
)
from core.brain.cot_strip import strip_leaked_cot as _strip_leaked_cot
from core.brain.env import env_flag as _env_flag
from core.brain.fast_chitchat import brain_fast_chitchat_reply as _brain_fast_chitchat_reply
from core.brain.hot_path import (
    brain_chat_context_slim_eligible as _brain_chat_context_slim_eligible,
    brain_hot_path_slim_eligible as _brain_hot_path_slim_eligible,
)
from core.brain.prompt_pack import _clip_soft, assemble_with_budget, assemble_split_with_budget, budget_for_tier, prompt_runtime_breakdown
from core.prompt_assembly import PromptAssemblyTier, brain_prompt_tier, describe_tier_ru, snapshot_context_policy, tier_label_for_metrics
from core.brain.runtime import _external_apis, _llm, get_memory, _persona, _skills, _twin
from core.brain.text_helpers import (
    brain_first_stage_max_tokens as _brain_first_stage_max_tokens,
    brain_second_stage_max_tokens as _brain_second_stage_max_tokens,
    user_requests_capability_overview as _user_requests_capability_overview,
    brain_weather_urlfetch_fallback_enabled as _brain_weather_urlfetch_fallback_enabled,
    brain_weather_wttr_eager_fetch_enabled as _brain_weather_wttr_eager_fetch_enabled,
    brain_weather_universal_search_fallback_enabled as _brain_weather_universal_search_fallback_enabled,
    build_goal_plan as _build_goal_plan,
    build_micro_emotion_style as _build_micro_emotion_style,
    build_style_hints as _build_style_hints,
    build_thinking_markers as _build_thinking_markers,
    build_typing_hooks as _build_typing_hooks,
    gentle_auto_ask_missing as _gentle_auto_ask_missing,
    is_bot_operational_diag_question as _is_bot_operational_diag_question,
    looks_like_repetition_glitch as _looks_like_repetition_glitch,
    natural_fallback_response as _natural_fallback_response,
    normalize_user_facts as _normalize_user_facts,
    operational_diag_reply as _operational_diag_reply,
    parse_tool_call as _parse_tool_call,
    strip_leaked_tool_call_markup as _strip_leaked_tool_call_markup,
    safe_json_dumps as _safe_json_dumps,
    safe_text as _safe_text,
    stable_blend_style as _stable_blend_style,
    strip_chat_markdown_for_telegram as _strip_chat_markdown_for_telegram,
    summarize_knowledge_hint as _summarize_knowledge_hint,
    user_provided_ordered_checklist as _user_provided_ordered_checklist,
    user_requests_compact_mcq_answer as _user_requests_compact_mcq_answer,
    maybe_compact_mcq_reply_for_telegram as _maybe_compact_mcq_reply_for_telegram,
    user_wants_inline_mcq_answer_format as _user_wants_inline_mcq_answer_format,
    user_requests_strict_direct_reasoning as _user_requests_strict_direct_reasoning,
    build_engine_presence_hint as _build_engine_presence_hint,
    build_strategic_lenses_hint as _build_strategic_lenses_hint,
    task_fact_profile as _task_fact_profile,
    TELEGRAM_PLAIN_REPLY_RULE as _TELEGRAM_PLAIN_REPLY_RULE,
    WEATHER_REPLY_ANTI_DISCLAIMER_ADDON as _WEATHER_REPLY_ANTI_DISCLAIMER_ADDON,
    NEWS_REPLY_ANTI_DISCLAIMER_ADDON as _NEWS_REPLY_ANTI_DISCLAIMER_ADDON,
    weather_wttr_in_fallback_hint as _weather_wttr_in_fallback_hint,
    weather_wttr_forecast_day_index as _weather_wttr_forecast_day_index,
    weather_universal_search_fallback_query as _weather_universal_search_fallback_query,
)
from core.brain.eta_estimate import estimate_llm_eta_sec as _estimate_eta_sec
from core.brain.session_stickiness import (
    resolve_session as _resolve_sticky_session,
    force_session_reset as _force_kv_session_reset,
)
from core.brain.kv_debug_logger import (
    record_kv_trace as _record_kv_trace,
    _prompt_dump_enabled as _kv_prompt_dump_enabled,
    _sanitize_for_log as _kv_sanitize_for_log,
)
from core.brain.vision_io import vision_image_parts_for_brain
from core.brain.vision_llm import brain_progress as _brain_progress, brain_run_vision_precaption as _brain_run_vision_precaption
from core.telegram_progress import telegram_progress_set_timing as _telegram_progress_set_timing
from core.brain.tool_call_support import (
    prioritize_tools_by_hint,
    tool_call_validation_error,
)
from core.brain.tool_dedup import lookup as _tool_dedup_lookup, store as _tool_dedup_store
from core.brain.tool_routing_hint import build_tool_routing_hint
from core.event_bus import bus
from core.tool_args_normalize import normalize_brain_tool_args

logger = logging.getLogger(__name__)


from core.brain.brain_telemetry import stash_brain_turn_telemetry as _stash_brain_turn_telemetry


async def call_brain(user_text: str, context: Dict[str, Any], system_prompt: str) -> str:
    """
    Центральный мозг:
    - память (Mem0MemoryModule)
    - цифровой двойник (DigitalTwinModule)
    - персона (PersonaEngineModule)
    - RAG (BooksRAGModule)
    - авто‑инструменты (core.tools)
    - LLM (OpenRouterProvider)
    """
    # Lazy import avoids import cycle: core.tools auto-discovers core.* including this module.
    from core.tools import list_tools, run_tool

    context = context if isinstance(context, dict) else {}
    context.pop("operational_diag_short_circuit", None)
    user_text = _safe_text(user_text)
    # Свежий recent_dialogue с диска (контекст из plan мог устареть; архив в промпте — отдельно).
    # Используется lazy loading с LRU cache — только recent_messages без полной загрузки JSON.
    try:
        from core.behavior_store import BehaviorStore
        from core.brain.profile_registry import context_load_recent_limit as _ctx_recent_lim

        _uid_fresh = str(context.get("user_id") or "").strip()
        if _uid_fresh:
            _lim = _ctx_recent_lim()
            _rm_f = BehaviorStore().load_recent_messages(_uid_fresh, context.get("group_id"), limit=_lim)
            if _rm_f:
                context["recent_dialogue"] = _rm_f
                context["recent_messages"] = _rm_f
            from core.behavior_store import topic_tracking_for_turn

            context["topic_tracking"] = topic_tracking_for_turn(
                user_text, context.get("topic_tracking")
            )
            from core.product_behavior import apply_pivot_context_hygiene

            context = apply_pivot_context_hygiene(
                context,
                user_text,
                user_id=_uid_fresh,
                group_id=context.get("group_id"),
            )
    except Exception as e:
        logger.debug("brain fresh recent_dialogue: %s", e)
    try:
        from core.telegram_output_guard import _overlap_with_user_query

        _ds = context.get("dialogue_state")
        if isinstance(_ds, dict) and user_text:
            _ae = _ds.get("anchor_entities")
            if isinstance(_ae, list) and _ae:
                _blob = " ".join(str(x) for x in _ae)
                if _overlap_with_user_query(user_text, _blob) < 0.1:
                    _utl = user_text.lower()
                    _filt = [
                        e
                        for e in _ae
                        if str(e).lower() in _utl or str(e).lower()[:5] in _utl
                    ]
                    _ds = dict(_ds)
                    _ds["anchor_entities"] = _filt[:8]
                    context["dialogue_state"] = _ds
    except Exception as e:
        logger.debug("brain anchor filter: %s", e)
    system_prompt = _safe_text(system_prompt) or "Ты универсальный ассистент."
    if context.get("group_id"):
        try:
            from core.group_social import augment_system_prompt_for_group

            system_prompt = augment_system_prompt_for_group(
                system_prompt, group_id=str(context.get("group_id"))
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        _cs_addon = system_addon_for_conversation_style(context.get("conversation_style"))
        if _cs_addon:
            system_prompt = merge_system(system_prompt, _cs_addon)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Autonomy FIX: self_model_trust and autonomy_goal are now added to
    # external_hint (dynamic tail) instead of system_prompt (static head).
    # This preserves KV-cache stability when autonomy flags are toggled.
    _sm_addon = ""
    _goal_addon = ""
    try:
        from core.self_model import self_model_trust_addon_for_prompt

        _sm_addon = self_model_trust_addon_for_prompt(
            context.get("self_model") if isinstance(context.get("self_model"), dict) else {}
        )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.self_model import autonomy_goal_addon_for_prompt

        _goal_addon = autonomy_goal_addon_for_prompt(context)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    user_id = context.get("user_id", "unknown")
    llm_session_id = ""
    _brain_model_primary = resolve_brain_primary_model(_llm)
    _brain_model_secondary = resolve_brain_secondary_model(_llm)
    _prof_primary = resolve_model_profile(_brain_model_primary)
    _prof_secondary = resolve_model_profile(_brain_model_secondary)
    if _env_flag("MODEL_PROFILE_LOG", default=False):
        logger.info(
            "[brain] model_profile primary=%s (%s) secondary=%s (%s)",
            _brain_model_primary,
            _prof_primary.match_label,
            _brain_model_secondary,
            _prof_secondary.match_label,
        )
    dialogue_state = context.get("dialogue_state")
    if not isinstance(dialogue_state, dict):
        dialogue_state = {}
    # Remove first resolve_session since we don't have prompt_chars yet.
    # A lightweight session_id is derived later when prompt is assembled.
    llm_session_id = ""
    _kv_dbg: Dict[str, Any] = {}
    _route = await _resolve_brain_route(user_text, context, llm=_llm)
    _brain_profile = _route.brain_profile
    _heuristic_profile = _route.heuristic_profile
    _need_memory = _route.need_memory
    _router_result = _route.router_result
    _log_brain_route(_route, user_text)

    from core.brain.translation_path import is_translation_turn as _is_translation_turn

    _translation_turn = _is_translation_turn(user_text, brain_profile=_brain_profile)

    if _need_memory:
        MONITOR.inc("brain_need_memory_trigger_total")
    llm_session_id, _kv_dbg = _setup_early_brain_session(
        user_id=user_id,
        user_text=user_text,
        context=context,
        brain_profile=_brain_profile,
        dialogue_state=dialogue_state,
    )
    fc0 = context.get("file_context") if isinstance(context.get("file_context"), dict) else {}
    _input_gate = await _apply_early_input_guards(
        user_id=user_id,
        user_text=user_text,
        context=context,
        need_memory=_need_memory,
        file_context=fc0,
    )
    if _input_gate.early_reply is not None:
        return _input_gate.early_reply
    user_text = _input_gate.user_text
    skip_memory_writes = _input_gate.skip_memory_writes
    skip_mem_fetch = _input_gate.skip_mem_fetch

    try:
        from core.timezone_inference import looks_like_wall_clock_question

        if looks_like_wall_clock_question(user_text):
            uf_clock = _normalize_user_facts(context.get("user_facts"))
            _rd_clock = context.get("recent_dialogue") or context.get("recent_messages")
            for _row in (_rd_clock or [])[-6:]:
                if isinstance(_row, dict) and str(_row.get("role") or "").lower() in (
                    "user",
                    "human",
                    "",
                ):
                    apply_stated_timezone_to_facts(
                        _row.get("text") or _row.get("content") or "", uf_clock
                    )
            ensure_timezone_in_user_facts(uf_clock)
            _tgux_ec = context.get("telegram_message_date_unix")
            try:
                _tg_i_ec = int(_tgux_ec) if _tgux_ec is not None else None
            except (TypeError, ValueError):
                _tg_i_ec = None
            _eff_tz_ec = (
                str(uf_clock.get("timezone") or "").strip()
                or infer_timezone_from_facts(uf_clock)
                or None
            )
            reply = format_wall_clock_user_reply(
                effective_tz=_eff_tz_ec,
                telegram_message_unix=_tg_i_ec,
                city=str(uf_clock.get("city") or "").strip() or None,
            )
            MONITOR.inc("brain_wall_clock_early_short_circuit_total")
            context["wall_clock_early_short_circuit"] = True
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug("wall_clock early short circuit: %s", e)
            if _safe_text(reply):
                return reply
    except Exception as e:
        logger.debug("wall_clock early short circuit: %s", e)

    # 1. Память: входящее сообщение / факты
    memory_facts = context.get("mem0_facts")
    if not isinstance(memory_facts, list):
        memory_facts = []
    if not memory_facts and not skip_mem_fetch:
        try:
            if not skip_memory_writes:
                await get_memory().on_user_message(user_id, user_text)
            memory_facts = await get_memory().on_before_response(user_id, user_text)
        except Exception as e:
            logger.warning(f"[brain] memory error: {e}")
            memory_facts = []

    # 2. Профиль цифрового двойника
    twin_profile = context.get("digital_twin")
    if not isinstance(twin_profile, dict) or not twin_profile:
        try:
            if hasattr(_twin, "get_digital_twin"):
                twin_profile = _twin.get_digital_twin(user_id) or {}
            elif hasattr(_twin, "get_learning_profile"):
                twin_profile = _twin.get_learning_profile(user_id) or {}
            else:
                twin_profile = {}
        except Exception:
            twin_profile = {}

    # 3. Persona
    persona = context.get("persona")
    if not isinstance(persona, dict) or not persona:
        try:
            persona = _persona.get_persona(user_id) or {}
        except Exception:
            persona = {}

    psychology = context.get("psychology")
    if not isinstance(psychology, dict):
        psychology = {}

    behavior_engine = context.get("behavior_engine")
    if not isinstance(behavior_engine, dict):
        behavior_engine = {}

    blended_stable = context.get("blended_style_stable")
    if not isinstance(blended_stable, dict):
        blended_stable = _stable_blend_style(persona, psychology, twin_profile, {})

    recent_dialogue = context.get("recent_dialogue") or context.get("recent_messages")
    if not isinstance(recent_dialogue, list):
        recent_dialogue = []
    # ── Sanitize: вырезать сообщения с format-утечками из истории ──
    try:
        from core.brain.cot_strip import sanitize_dialogue

        recent_dialogue = sanitize_dialogue(recent_dialogue)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.brain.brief_context_filter import filter_recent_after_brief_trap

        recent_dialogue = filter_recent_after_brief_trap(recent_dialogue, user_text)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    topic_tracking = context.get("topic_tracking")
    if not isinstance(topic_tracking, dict):
        topic_tracking = {}
    group_context = context.get("group_context")
    if not isinstance(group_context, dict):
        group_context = {}
    group_transcript_compact = context.get("group_transcript_compact") or ""
    group_commitments_hint = context.get("group_commitments_hint") or ""
    if not isinstance(group_transcript_compact, str):
        group_transcript_compact = ""
    if not isinstance(group_commitments_hint, str):
        group_commitments_hint = ""
    _group_chat_addon = ""
    if context.get("group_id"):
        _g_parts: List[str] = []
        if _env_flag("GROUP_SHOW_PLATFORM_HINTS", default=True):
            _g_parts.append(
                "- telegram_group_limits: статус «онлайн» и полный список людей через Bot API недоступны; "
                "ниже — примерное число участников группы (если Telegram отдал) и имена тех, кто писал, пока бот получал апдейты. "
                "Сообщения других ботов боту обычно не приходят (ограничение Telegram) — если спорят с другим ботом, попроси переслать/процитировать текст или скажи, что реплику не видно."
            )
        if _env_flag("GROUP_RELAY_AS_BOT_HINT", default=True):
            _g_parts.append(
                "- telegram_group_reply_is_bot_message: твой ответ уходит в этот чат **как сообщение этого бота**, не «от имени пользователя». "
                "Просьба «напиши в группу @other_bot …», «повтори как он», «пингани и спроси» означает: выведи **ровно** запрошенный текст (с @username), чтобы он появился в чате от бота — это нормально и разрешено. "
                "Не отвечай отказом в духе «не могу писать от вашего имени»: ты не имитируешь человека, ты публикуешь строку с аккаунта бота. "
                "Если просят одну строку без кавычек и без пояснений — ответ только эта строка. "
                "Фраза «через @id» или другие @ в кавычках могут быть **буквальным текстом** запроса пользователя — не вызывай логику «найти пользователя», не заменяй @id на поиск контакта."
            )
        snap = context.get("group_chat_snapshot")
        if isinstance(snap, dict):
            bits: List[str] = []
            tit = snap.get("title")
            if tit:
                bits.append(f"чат «{tit}»")
            mc = snap.get("member_count")
            if mc is not None:
                bits.append(f"~{mc} участников в группе (всего в чате, не «онлайн»)")
            if bits:
                _g_parts.append("- group_chat_meta: " + "; ".join(bits))
        grh = (context.get("group_roster_hint") or "").strip()
        if grh:
            _g_parts.append("- group_seen_authors (писали, пока бот видел чат; список неполный): " + grh)
        if group_transcript_compact.strip():
            _g_parts.append(
                f"- group_chat_transcript (видел бот; на это могут ссылаться «ты молчал / не ответил»):\n{group_transcript_compact.strip()}"
            )
        if group_commitments_hint.strip():
            _g_parts.append(f"- user_commitments_reminders:\n{group_commitments_hint.strip()}")
        if _g_parts:
            _group_chat_addon = "\n".join(_g_parts) + "\n"
    user_facts = _normalize_user_facts(context.get("user_facts"))
    grounding_mini = build_minimal_grounding(context if isinstance(context, dict) else {}, user_facts)
    _doc_ctx0 = context.get("document_intake") if isinstance(context.get("document_intake"), dict) else {}
    document_intake_block = format_document_intake_for_brain(_doc_ctx0) if _doc_ctx0 else ""
    user_facts_meta = context.get("user_facts_meta")
    if not isinstance(user_facts_meta, dict):
        user_facts_meta = {}
    facts_flow = context.get("facts_flow")
    if not isinstance(facts_flow, dict):
        facts_flow = {}
    missing_facts = facts_flow.get("auto_ask_missing") if isinstance(facts_flow, dict) else []
    if not isinstance(missing_facts, list):
        missing_facts = []
    _persisted_short: Dict[str, Any] = {"user_facts": user_facts}
    if isinstance(dialogue_state, dict) and dialogue_state:
        _persisted_short["dialogue_state"] = dialogue_state
    try:
        _uid_p = str(context.get("user_id") or "").strip()
        if _uid_p:
            from core.behavior_store import BehaviorStore

            _bw = BehaviorStore().load(_uid_p, context.get("group_id"))
            if isinstance(_bw, dict):
                if _bw.get("weather_anchor"):
                    _persisted_short["weather_anchor"] = _bw["weather_anchor"]
                if isinstance(_bw.get("routing_prefs"), dict):
                    _persisted_short["routing_prefs"] = _bw["routing_prefs"]
                _pfc = _bw.get("pending_facts_confirmation")
                if isinstance(_pfc, dict) and _pfc:
                    _persisted_short["pending_facts_confirmation"] = _pfc
                _pfo = _bw.get("pending_facts_overwrite")
                if isinstance(_pfo, dict) and _pfo:
                    _persisted_short["pending_facts_overwrite"] = _pfo
                if isinstance(_bw.get("recent_messages"), list):
                    _persisted_short["recent_messages"] = _bw["recent_messages"]
                _ds_w = _bw.get("dialogue_state")
                if isinstance(_ds_w, dict) and _ds_w.get("last_telegram_location"):
                    _persisted_short.setdefault("dialogue_state", {})
                    if isinstance(_persisted_short["dialogue_state"], dict):
                        _persisted_short["dialogue_state"]["last_telegram_location"] = _ds_w[
                            "last_telegram_location"
                        ]
    except Exception as e:
        logger.debug("pipeline weather_anchor load: %s", e)
    task_facts = _task_fact_profile(
        user_text, user_facts, recent_dialogue, persisted=_persisted_short
    )
    if task_facts.get("is_time") and infer_timezone_from_facts(user_facts):
        missing_facts = [m for m in missing_facts if m != "timezone"]
    if task_facts.get("is_weather") and str(task_facts.get("weather_city") or "").strip():
        missing_facts = [m for m in missing_facts if m != "location"]
    auto_ask_hint = _gentle_auto_ask_missing(missing_facts)
    try:
        from core.brain.text_helpers import (
            admin_or_user_summary_short_reply,
            looks_like_bare_summary_keyword,
        )

        if looks_like_bare_summary_keyword(user_text):
            _is_adm = bool(context.get("telegram_is_admin"))
            reply = admin_or_user_summary_short_reply(is_admin=_is_adm)
            MONITOR.inc("brain_summary_keyword_short_circuit_total")
            context["summary_keyword_short_circuit"] = True
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            return reply if _safe_text(reply) else admin_or_user_summary_short_reply(is_admin=_is_adm)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    if _is_bot_operational_diag_question(user_text):
        MONITOR.inc("brain_operational_diag_short_circuit_total")
        context["operational_diag_short_circuit"] = True
        reply = _operational_diag_reply()
        try:
            reply = _persona_apply_polished(user_id, reply)
            if not skip_memory_writes:
                await get_memory().on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        return reply if _safe_text(reply) else _operational_diag_reply()

    try:
        from core.dialogue_recheck_reply import try_recheck_deterministic_reply

        _tgux_rc = context.get("telegram_message_date_unix")
        try:
            _tg_i_rc = int(_tgux_rc) if _tgux_rc is not None else None
        except (TypeError, ValueError):
            _tg_i_rc = None
        _recheck_reply = try_recheck_deterministic_reply(
            user_text,
            recent_dialogue=recent_dialogue,
            user_facts=user_facts,
            telegram_message_unix=_tg_i_rc,
        )
        if _recheck_reply and str(_recheck_reply).strip():
            MONITOR.inc("brain_recheck_short_circuit_total")
            context["recheck_short_circuit"] = True
            reply = str(_recheck_reply).strip()
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug("recheck short circuit persist: %s", e)
            return reply if _safe_text(reply) else str(_recheck_reply).strip()
    except Exception as e:
        logger.debug("recheck short circuit: %s", e)

    if task_facts.get("is_time"):
        try:
            apply_stated_timezone_to_facts(user_text, user_facts)
            for _row in (recent_dialogue or [])[-6:]:
                if isinstance(_row, dict) and str(_row.get("role") or "").lower() in ("user", "human", ""):
                    apply_stated_timezone_to_facts(_row.get("text") or _row.get("content") or "", user_facts)
            ensure_timezone_in_user_facts(user_facts)
            _tgux_clock = context.get("telegram_message_date_unix")
            try:
                _tg_i_clock = int(_tgux_clock) if _tgux_clock is not None else None
            except (TypeError, ValueError):
                _tg_i_clock = None
            _eff_tz_clock = (
                str(user_facts.get("timezone") or "").strip()
                or infer_timezone_from_facts(user_facts)
                or None
            )
            reply = format_wall_clock_user_reply(
                effective_tz=_eff_tz_clock,
                telegram_message_unix=_tg_i_clock,
                city=str(user_facts.get("city") or "").strip() or None,
            )
            MONITOR.inc("brain_wall_clock_short_circuit_total")
            context["wall_clock_short_circuit"] = True
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            if _safe_text(reply):
                return reply
        except Exception as e:
            logger.debug("wall_clock short circuit: %s", e)

    try:
        from core.brain_own_turn import (
            brain_news_item_reply_enabled,
            brain_pipeline_news_item_short_circuit_enabled,
        )

        if not (
            brain_news_item_reply_enabled()
            or brain_pipeline_news_item_short_circuit_enabled()
        ):
            raise RuntimeError("brain_owns_news_item")
        from core.news_reply import try_news_item_reply

        _news_item_early = await try_news_item_reply(
            user_text,
            persisted=_persisted_short,
            user_id=user_id,
            recent_dialogue=recent_dialogue,
        )
        if _news_item_early and str(_news_item_early).strip():
            MONITOR.inc("brain_news_item_short_circuit_total")
            context["news_item_short_circuit"] = True
            reply = str(_news_item_early).strip()
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            return reply if _safe_text(reply) else str(_news_item_early).strip()
    except RuntimeError:
        pass
    except Exception as e:
        logger.debug("news item short circuit: %s", e)

    try:
        from core.article_thread_followup import try_article_thread_followup_reply

        _art_follow = await try_article_thread_followup_reply(
            user_text or "",
            recent_dialogue=recent_dialogue,
            persisted=_persisted_short,
            user_id=user_id,
        )
        if _art_follow and str(_art_follow).strip():
            MONITOR.inc("brain_article_thread_followup_total")
            context["article_thread_followup_short_circuit"] = True
            reply = str(_art_follow).strip()
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug("article_thread_followup short circuit: %s", e)
            if _safe_text(reply):
                return reply
    except Exception as e:
        logger.debug("article_thread_followup short circuit: %s", e)

    try:
        from core.news_reply import news_story_deep_followup_enabled, try_news_story_deep_reply

        if news_story_deep_followup_enabled():
            _story_deep = await try_news_story_deep_reply(
                user_text,
                persisted=_persisted_short,
                user_id=user_id,
                recent_dialogue=recent_dialogue,
            )
            if _story_deep and str(_story_deep).strip():
                MONITOR.inc("brain_news_story_deep_short_circuit_total")
                context["news_story_deep_short_circuit"] = True
                reply = str(_story_deep).strip()
                try:
                    reply = _persona_apply_polished(user_id, reply)
                    if not skip_memory_writes:
                        await get_memory().on_after_response(user_id, reply)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return reply if _safe_text(reply) else str(_story_deep).strip()
    except Exception as e:
        logger.debug("news story deep short circuit: %s", e)

    try:
        from core.brain.text_helpers import looks_like_affirmative_short
        from core.news_reply import try_affirmative_search_reply
        from core.user_facts import has_pending_facts_confirmation

        if (
            looks_like_affirmative_short(user_text or "")
            and not has_pending_facts_confirmation(_persisted_short)
        ):
            _aff = await try_affirmative_search_reply(
                user_text or "",
                persisted=_persisted_short,
                user_id=user_id,
                recent_dialogue=recent_dialogue,
            )
            if _aff and str(_aff).strip():
                MONITOR.inc("brain_affirmative_search_short_circuit_total")
                context["affirmative_search_short_circuit"] = True
                reply = str(_aff).strip()
                try:
                    reply = _persona_apply_polished(user_id, reply)
                    if not skip_memory_writes:
                        await get_memory().on_after_response(user_id, reply)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return reply if _safe_text(reply) else str(_aff).strip()
    except Exception as e:
        logger.debug("affirmative_search short circuit: %s", e)

    try:
        from core.incident_context_hint import try_incident_followup_search_reply

        _inc_reply = await try_incident_followup_search_reply(
            user_text or "",
            recent_dialogue=recent_dialogue,
            persisted=_persisted_short,
            user_id=user_id,
        )
        if _inc_reply and str(_inc_reply).strip():
            MONITOR.inc("brain_incident_followup_short_circuit_total")
            context["incident_followup_short_circuit"] = True
            reply = str(_inc_reply).strip()
            try:
                reply = _persona_apply_polished(user_id, reply)
                if not skip_memory_writes:
                    await get_memory().on_after_response(user_id, reply)
            except Exception as e:
                logger.debug("incident_followup short circuit: %s", e)
            if _safe_text(reply):
                return reply
    except Exception as e:
        logger.debug("incident_followup short circuit: %s", e)

    if task_facts.get("is_weather"):
        try:
            from core.brain_own_turn import brain_weather_api_enabled
            from core.brain.text_helpers import brain_weather_short_circuit_requires_anchor
            from core.weather_location_store import read_weather_anchor

            if not brain_weather_api_enabled():
                raise RuntimeError("brain_weather_api_off")
            _wx_anchor_ok = (
                not brain_weather_short_circuit_requires_anchor()
                or bool(read_weather_anchor(_persisted_short))
                or bool(str(task_facts.get("weather_city") or "").strip())
                or bool(task_facts.get("weather_use_coords"))
            )
            if not _wx_anchor_ok:
                raise RuntimeError("weather_short_circuit_needs_anchor")
            from core.weather_reply import try_weather_reply

            _wx_early = await try_weather_reply(
                user_text,
                persisted=_persisted_short,
                user_id=user_id,
                group_id=context.get("group_id"),
            )
            if _wx_early and str(_wx_early).strip():
                MONITOR.inc("brain_weather_short_circuit_total")
                context["weather_short_circuit"] = True
                reply = str(_wx_early).strip()
                try:
                    reply = _persona_apply_polished(user_id, reply)
                    if not skip_memory_writes:
                        await get_memory().on_after_response(user_id, reply)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return reply if _safe_text(reply) else str(_wx_early).strip()
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug("weather short circuit: %s", e)

    if task_facts.get("is_news") and not task_facts.get("is_pasted_article"):
        try:
            from core.brain_own_turn import brain_pipeline_news_short_circuit_enabled

            if not brain_pipeline_news_short_circuit_enabled():
                raise RuntimeError("brain_owns_news")
            from core.news_reply import try_news_reply

            _news_early = await try_news_reply(
                user_text,
                persisted=_persisted_short,
                user_id=user_id,
                recent_dialogue=recent_dialogue,
            )
            if _news_early and str(_news_early).strip():
                MONITOR.inc("brain_news_short_circuit_total")
                context["news_short_circuit"] = True
                reply = str(_news_early).strip()
                try:
                    from core.news_reply import (
                        persist_news_digest_from_assistant_reply,
                        sync_news_digest_persisted,
                    )

                    persist_news_digest_from_assistant_reply(
                        reply,
                        persisted=_persisted_short,
                        context=context,
                    )
                    sync_news_digest_persisted(context, _persisted_short)
                except Exception as e:
                    logger.debug("news digest persist early: %s", e)
                try:
                    reply = _persona_apply_polished(user_id, reply)
                    if not skip_memory_writes:
                        await get_memory().on_after_response(user_id, reply)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return reply if _safe_text(reply) else str(_news_early).strip()
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug("news short circuit: %s", e)

    if _translation_turn and _env_flag("BRAIN_TRANSLATION_FAST_PATH", default=True):
        from core.brain.translation_reply import brain_translation_reply as _brain_translation_reply

        if isinstance(context, dict):
            context["_skill_name"] = "translator"
        return await _brain_translation_reply(
            user_text=user_text,
            user_id=user_id,
            skip_memory_writes=skip_memory_writes,
            model_profile=_prof_primary,
            llm_session_id=llm_session_id,
        )

    _doc_ctx_early = context.get("document_intake") if isinstance(context.get("document_intake"), dict) else {}
    _file_ctx_early = context.get("file_context") if isinstance(context.get("file_context"), dict) else {}
    try:
        from core.brain.dialogue_lane import is_direct_dialog_eligible
        from core.brain.direct_dialog_reply import brain_direct_dialog_reply

        if is_direct_dialog_eligible(
            user_text,
            brain_profile=_brain_profile,
            task_facts=task_facts,
            translation_turn=_translation_turn,
            task_tier=str(dialogue_state.get("task_tier") or ""),
            tools_mode=_brain_tools_mode(),
            has_document_intake=bool(_doc_ctx_early),
            has_file_context=bool(_file_ctx_early),
            recent_dialogue=recent_dialogue,
        ):
            _dd_hint = ""
            try:
                from core.user_correction_bus import build_operator_corrections_hint
                from core.dialogue_slots import slot_external_hint

                _dd_hint = build_operator_corrections_hint(
                    context if isinstance(context, dict) else {},
                    user_text=user_text,
                    user_id=user_id,
                )
                _persisted_dd = None
                if isinstance(context, dict) and context.get("behavior_record"):
                    _persisted_dd = context.get("behavior_record")
                _slot_dd = slot_external_hint(
                    user_text,
                    recent_dialogue,
                    persisted=_persisted_dd if isinstance(_persisted_dd, dict) else None,
                    user_id=str(user_id or ""),
                    chat_id=str(
                        (context.get("chat_id") or context.get("group_id") or "")
                        if isinstance(context, dict)
                        else ""
                    ),
                )
                if _slot_dd.strip():
                    _dd_hint = (
                        f"{_dd_hint.strip()}\n\n{_slot_dd.strip()}" if _dd_hint.strip() else _slot_dd.strip()
                    )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            if isinstance(dialogue_state, dict):
                dialogue_state["dialogue_lane"] = "direct_llm"
            return await brain_direct_dialog_reply(
                user_text=user_text,
                user_id=user_id,
                system_prompt=system_prompt,
                persona=persona if isinstance(persona, dict) else {},
                memory_facts=memory_facts,
                recent_dialogue=recent_dialogue,
                skip_memory_writes=skip_memory_writes,
                model_profile=_prof_primary,
                llm_session_id=llm_session_id,
                external_hint=_dd_hint,
                context=context,
                brain_profile=_brain_profile,
            )
    except Exception as e:
        logger.debug("direct_dialog: %s", e)

    if context.get("brain_fast_chitchat") and _env_flag("BRAIN_FAST_CHITCHAT", default=True):
        return await _brain_fast_chitchat_reply(
            user_text=user_text,
            user_id=user_id,
            system_prompt=system_prompt,
            persona=persona if isinstance(persona, dict) else {},
            memory_facts=memory_facts,
            recent_dialogue=recent_dialogue,
            skip_memory_writes=skip_memory_writes,
            model_profile=_prof_primary,
            llm_session_id=llm_session_id,
            context=context,
            brain_profile=_brain_profile,
        )

    await _brain_progress("💭 Собираю контекст…")

    behavior_policy = context.get("behavior_policy")
    if not isinstance(behavior_policy, dict):
        behavior_policy = {}
    _no_service_clar = bool(behavior_policy.get("no_service_clarifications"))
    knowledge_hint = context.get("knowledge_hint")
    if not isinstance(knowledge_hint, dict):
        knowledge_hint = {}
    knowledge_summary = _summarize_knowledge_hint(knowledge_hint, max_items=3, max_chars=420)
    predictive_hint = context.get("predictive_hint")
    if not isinstance(predictive_hint, dict):
        predictive_hint = {}
    goal_hints = context.get("goal_hints")
    if not isinstance(goal_hints, dict):
        goal_hints = {}
    task_tier = str(dialogue_state.get("task_tier") or "").strip()
    if not task_tier:
        task_tier = _infer_task_tier(user_text)
    task_tier = _max_task_tier(
        task_tier,
        _infer_task_tier_with_history(user_text, recent_dialogue, max_user_turns=4),
    )
    dialogue_state["task_tier"] = task_tier
    # ── Profile-based task tier demotion ──
    # Если профиль не deep — сбросить task_tier до shallow,
    # чтобы slim-режим не блокировался tier_prefers_thorough().
    from core.brain.profile_registry import profile_prefers_thorough_tier as _profile_prefers_thorough
    if not _profile_prefers_thorough(_brain_profile):
        if task_tier in ("deep", "nested"):
            task_tier = "shallow"
            dialogue_state["task_tier"] = "shallow"
    if not context.get("brain_fast_chitchat"):
        try:
            from core.llm_task_outline import fetch_llm_task_outline, should_run_outline

            _fc_outline = context.get("file_context") if isinstance(context.get("file_context"), dict) else None
            _skip_outline = False
            try:
                from core.image_gen_nl import attachment_wants_image_generation

                if attachment_wants_image_generation(_fc_outline, user_text):
                    _skip_outline = True
            except Exception:
                pass
            if (
                not _skip_outline
                and should_run_outline(user_text, task_tier)
                and not context.get("llm_task_outline")
            ):
                oe = await fetch_llm_task_outline(
                    user_text=user_text,
                    task_tier=task_tier,
                    dialogue_state=dialogue_state,
                )
                if oe:
                    context["llm_task_outline"] = oe
                    task_tier = _refine_task_tier_from_outline(task_tier, oe)
                    dialogue_state["task_tier"] = task_tier
                    MONITOR.inc("strategy_llm_outline_ok_total")
        except Exception as e:
            logger.debug("task_outline: %s", e)
    file_context = context.get("file_context") if isinstance(context.get("file_context"), dict) else {}
    try:
        from core.dialogue_slots import should_suppress_image_for_slot

        if should_suppress_image_for_slot(
            user_text, recent_dialogue, file_context, persisted=_persisted_short
        ):
            file_context = {}
            if isinstance(context, dict):
                context["file_context"] = {}
                context["brain_suppress_image_for_article_thread"] = True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    _skip_image_skill_for_nl_gen = False
    try:
        from core.image_gen_nl import attachment_wants_image_generation, prose_wants_image_gen_or_edit

        if isinstance(file_context, dict) and file_context.get("file_type") == "image":
            if attachment_wants_image_generation(file_context, user_text) or prose_wants_image_gen_or_edit(
                user_text
            ):
                _skip_image_skill_for_nl_gen = True
    except Exception as e:
        logger.debug("image_gen nl skip image_skill: %s", e)
    image_intent = (
        None
        if _skip_image_skill_for_nl_gen
        else (ImageSkillRouter.classify(user_text, file_context) if isinstance(file_context, dict) else None)
    )
    if _translation_turn:
        skill_name = "translator"
    elif image_intent:
        skill_name = "image_skill"
    else:
        skill_name = await resolve_skill_intent(user_text)
    if skill_name == "news_helper":
        try:
            from core.brain.text_helpers import wants_expanded_news_digest

            if (
                _env_flag("BRAIN_NEWS_DIRECT_FROM_SEARCH", default=False)
                and not wants_expanded_news_digest(user_text or "", recent_dialogue)
            ):
                skill_name = ""
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    if isinstance(context, dict):
        context["_skill_name"] = skill_name
    skill_output = {}
    skill_hint = ""
    if skill_name:
        skill = _skills.get(skill_name)
        if skill:
            try:
                async def _run_skill():
                    return await skill.run(
                        intent=skill_name,
                        user_text=user_text,
                        context={**skill_context_pack(context), "file_context": file_context},
                        user_facts=user_facts,
                        digital_twin=twin_profile if isinstance(twin_profile, dict) else {},
                    )

                skill_res = await with_retry(_run_skill, retries=DEFAULT_RETRIES, timeout_sec=DEFAULT_TIMEOUT_SEC, tag="skill_run")
                skill_output = skill_res.result
                skill_hint = skill_res.hint
                # Side-channel attachments for adapter layer (no output schema change).
                if isinstance(skill_output, dict):
                    if skill_output.get("path"):
                        context["image_output_path"] = skill_output.get("path")
                        context["image_operation"] = skill_output.get("operation")
                    if skill_output.get("operation") == "ocr" and skill_output.get("text"):
                        context["ocr_text"] = skill_output.get("text")
            except Exception as e:
                record_error_event("skills", "skill run failed", exc=e, extra={"skill": skill_name, "user_id": user_id})

    wr_weather: Dict[str, Any] = {}
    external_hint = ""
    try:
        from core.product_behavior import eager_product_search_hint

        _pb_search = await eager_product_search_hint(user_text, user_facts)
        if _pb_search:
            external_hint = _pb_search
            if isinstance(context, dict):
                context["product_behavior_search"] = True
    except Exception as e:
        logger.debug("product_behavior search: %s", e)
    try:
        from core.product_behavior import build_cosmology_scope_hint

        _cosmo_hint = build_cosmology_scope_hint(user_text, recent_dialogue)
        if _cosmo_hint:
            external_hint = (
                f"{external_hint.strip()}\n\n{_cosmo_hint}"
                if (external_hint or "").strip()
                else _cosmo_hint
            )
    except Exception as e:
        logger.debug("product_behavior cosmology: %s", e)
    try:
        from core.brain.user_facing_contract import (
            build_continuation_dialogue_hint,
            build_local_models_scope_hint,
        )

        for _hint_block in (
            build_continuation_dialogue_hint(user_text, recent_dialogue),
            build_local_models_scope_hint(user_text, recent_dialogue),
        ):
            if not (_hint_block or "").strip():
                continue
            external_hint = (
                f"{external_hint.strip()}\n\n{_hint_block}"
                if (external_hint or "").strip()
                else _hint_block
            )
    except Exception as e:
        logger.debug("user_facing hints: %s", e)
    try:
        from core.heuristic_context_gate import build_topic_gate_hint

        _topic_hint = build_topic_gate_hint(context.get("topic_tracking"))
        if (_topic_hint or "").strip():
            external_hint = (
                f"{external_hint.strip()}\n\n{_topic_hint}"
                if (external_hint or "").strip()
                else _topic_hint
            )
    except Exception as e:
        logger.debug("topic_gate_hint: %s", e)
    # ── KV-CACHE NOTE: clock_hint and calendar_hint MUST stay in external_hint
    # (dynamic tail) — NEVER move to system_prompt (static head). ──
    _tgux0 = context.get("telegram_message_date_unix")
    try:
        _tg_i_shared = int(_tgux0) if _tgux0 is not None else None
    except (TypeError, ValueError):
        _tg_i_shared = None
    _eff_tz_shared = str(user_facts.get("timezone") or "").strip() or infer_timezone_from_facts(user_facts) or None
    _clock_block = format_clock_hint_for_llm(effective_tz=_eff_tz_shared, telegram_message_unix=_tg_i_shared)
    try:
        from core.brain.text_helpers import brain_weather_pipeline_prefetch_enabled

        if task_facts.get("is_weather") and brain_weather_pipeline_prefetch_enabled():
            wc = str(task_facts.get("weather_city") or "").strip()
            wco = str(task_facts.get("weather_country") or "").strip()
            geo_q = str(task_facts.get("weather_geo_query") or wc).strip()
            _wx_hint = str(task_facts.get("weather_region_hint") or "").strip()
            _wx_lat = task_facts.get("weather_lat")
            _wx_lon = task_facts.get("weather_lon")
            _wx_label = str(task_facts.get("weather_label") or wc).strip()

            async def _prefetch_weather() -> Dict[str, Any]:
                if task_facts.get("weather_use_coords") and _wx_lat is not None and _wx_lon is not None:
                    return await _external_apis.weather_or_fallback(
                        city=geo_q,
                        country=wco,
                        admin1_hint=_wx_hint,
                        latitude=float(_wx_lat),
                        longitude=float(_wx_lon),
                        label=_wx_label,
                    )
                return await _external_apis.weather_or_fallback(
                    city=geo_q, country=wco, admin1_hint=_wx_hint
                )

            wr_weather = await with_retry(
                _prefetch_weather,
                retries=1,
                timeout_sec=10.0,
                tag="weather_api",
            )
            if (
                not wr_weather.get("configured")
                and wc
                and len(wc) >= 4
                and wc[-1].lower() in "её"
                and "no location" in str(wr_weather.get("error", "")).lower()
            ):
                wr_weather = await with_retry(
                    lambda: _external_apis.weather_or_fallback(
                        city=geo_q[:-1] if geo_q.endswith(wc[-1]) else wc[:-1],
                        country=wco,
                        admin1_hint=_wx_hint,
                    ),
                    retries=1,
                    timeout_sec=10.0,
                    tag="weather_api_stem",
                )
            if wr_weather.get("configured") and wr_weather.get("summary"):
                external_hint = (
                    "Погода уже сверстана ниже (Open-Meteo). Передай пользователю как есть или слегка сократи; "
                    "блок «По дням» содержит сегодня и завтра — для вопроса про завтра опирайся на строку «Завтра, …», не говори, что данных нет. "
                    + _WEATHER_REPLY_ANTI_DISCLAIMER_ADDON
                    + " UrlFetch для погоды не нужен.\n\n"
                    + str(wr_weather.get("summary"))
                )
            elif wr_weather.get("error"):
                err = str(wr_weather.get("error"))
                external_hint = f"Встроенный Weather API не смог: {err}. "
                if _brain_weather_urlfetch_fallback_enabled():
                    fb = _weather_wttr_in_fallback_hint(geo_q, wco)
                    if fb:
                        wttr_pre: Optional[str] = None
                        if _brain_weather_wttr_eager_fetch_enabled():
                            try:
                                wttr_pre = await with_retry(
                                    lambda: _external_apis.wttr_in_eager_summary(
                                        geo_q,
                                        wco,
                                        forecast_day_index=_weather_wttr_forecast_day_index(user_text),
                                    ),
                                    retries=1,
                                    timeout_sec=12.0,
                                    tag="weather_wttr_eager",
                                )
                            except Exception:
                                wttr_pre = None
                        if isinstance(wttr_pre, str) and wttr_pre.strip():
                            external_hint = (
                                "Погода уже сверстана ниже (запасной источник wttr.in; основной Open-Meteo недоступен). "
                                "Передай пользователю как есть или слегка сократи; не говори, что «нет доступа к данным о погоде». "
                                + _WEATHER_REPLY_ANTI_DISCLAIMER_ADDON
                                + " UrlFetch для погоды не нужен.\n\n"
                                + wttr_pre.strip()
                            )
                        else:
                            search_pre: Optional[str] = None
                            if _brain_weather_universal_search_fallback_enabled():
                                try:
                                    from core.universal_search_module import UniversalSearchModule

                                    _us_q = _weather_universal_search_fallback_query(user_text, wc, wco)
                                    _us_mod = UniversalSearchModule()
                                    _us_pack = await with_retry(
                                        lambda: _us_mod.search(
                                            _us_q,
                                            country=(wco or "").strip(),
                                            user_id=str(user_id or ""),
                                        ),
                                        retries=0,
                                        timeout_sec=22.0,
                                        tag="weather_universal_search_eager",
                                    )
                                    if (
                                        isinstance(_us_pack, dict)
                                        and _us_pack.get("ok")
                                        and str(_us_pack.get("summary") or "").strip()
                                    ):
                                        search_pre = str(_us_pack.get("summary")).strip()
                                except Exception:
                                    search_pre = None
                            if isinstance(search_pre, str) and search_pre.strip():
                                _cap = 4000
                                _body = search_pre.strip()
                                if len(_body) > _cap:
                                    _body = _body[: _cap - 1] + "…"
                                external_hint = (
                                    "Ниже — выжимка из веб-поиска по погоде (Open-Meteo и wttr.in в этой попытке не дали готовую сводку). "
                                    "Передай пользователю смысл кратко; опирайся на факты из блока, не выдумывай цифры. "
                                    + _WEATHER_REPLY_ANTI_DISCLAIMER_ADDON
                                    + " Если нужна точность по одному сайту и в сводке есть подходящий https — один вызов UrlFetch.fetch_page.\n\n"
                                    + _body
                                )
                            else:
                                external_hint += fb
                    else:
                        external_hint += (
                            "Город неизвестен для геокода — уточни у пользователя населённый пункт (и страну), "
                            "затем снова попробуй ответить по погоде."
                        )
                else:
                    external_hint += "UrlFetch для погоды отключён (BRAIN_WEATHER_URLFETCH_FALLBACK=false)."
        elif task_facts.get("is_currency"):
            base = str(user_facts.get("currency") or "USD")
            cr = await with_retry(
                lambda: _external_apis.currency_or_fallback(base=base, quote="EUR"),
                retries=1,
                timeout_sec=10.0,
                tag="currency_api",
            )
            if cr.get("configured"):
                external_hint = f"Currency API data available: {cr}"
        elif task_facts.get("is_time"):
            external_hint = _clock_block
        elif (task_facts.get("is_news") or _brain_profile == "news_brief") and not task_facts.get(
            "is_pasted_article"
        ):
            _news_q = (user_text or "").strip() or "последние новости"
            _news_co = str(user_facts.get("country") or "").strip()
            try:
                from core.news_reply import refine_news_digest_search_query
                from modules.external_apis.clients import NewsAPIClient

                _news_world = bool(NewsAPIClient().wants_world_news(_news_q))
                _news_q = refine_news_digest_search_query(
                    _news_q, country=_news_co, world_feed=_news_world
                )
            except Exception as e:
                logger.debug("news query refine: %s", e)
            _news_body: Optional[str] = None
            _news_search_results: List[Dict[str, Any]] = []
            _fetch_rss_hint = False
            try:
                from core.brain_own_turn import pipeline_news_rss_fetch_enabled

                _fetch_rss_hint = pipeline_news_rss_fetch_enabled(user_text or "")
            except Exception as e:
                logger.debug("pipeline news rss fetch gate: %s", e)
            if _fetch_rss_hint:
                try:
                    from modules.external_apis.clients import NewsAPIClient

                    # country задаёт gl/язык RSS (BY → ru), даже для мировой ленты — не обнулять.
                    _rss = await with_retry(
                        lambda: NewsAPIClient().headlines(topic=_news_q, country=_news_co),
                        retries=0,
                        timeout_sec=14.0,
                        tag="news_google_rss",
                    )
                    if _rss.get("configured") and (_rss.get("items") or []):
                        _rss_items = _rss.get("items")
                        if isinstance(context, dict):
                            context["_news_rss_items"] = _rss_items
                            context["_news_rss_meta"] = {
                                "locale": _rss.get("locale"),
                                "world_feed": _rss.get("world_feed"),
                                "topic": _rss.get("topic"),
                            }
                        _news_body = str(_rss.get("summary") or "").strip()
                except Exception as e:
                    record_error_event("external_apis", "news rss failed", exc=e, extra={"user_id": user_id})
            _fetch_news_search = _env_flag("NEWS_ENRICH_SEARCH_SNIPPETS", default=True)
            try:
                from core.brain_own_turn import news_digest_search_only_enabled

                if news_digest_search_only_enabled():
                    _fetch_news_search = True
            except Exception as e:
                logger.debug("news search-only fetch gate: %s", e)
            if _fetch_news_search:
                try:
                    from core.universal_search_module import UniversalSearchModule

                    _us_news = UniversalSearchModule()
                    _news_pack = await with_retry(
                        lambda: _us_news.search(_news_q, country=_news_co, user_id=str(user_id or "")),
                        retries=0,
                        timeout_sec=22.0,
                        tag="news_universal_search_enrich",
                    )
                    if isinstance(_news_pack, dict) and _news_pack.get("ok"):
                        raw_results = _news_pack.get("results")
                        if isinstance(raw_results, list):
                            _news_search_results = [r for r in raw_results if isinstance(r, dict)]
                        if not _news_body:
                            _news_body = str(_news_pack.get("summary") or "").strip()
                except Exception as e:
                    record_error_event(
                        "external_apis", "news search enrich failed", exc=e, extra={"user_id": user_id}
                    )
            elif not _news_body:
                try:
                    from core.universal_search_module import UniversalSearchModule

                    _us_news = UniversalSearchModule()
                    _news_pack = await with_retry(
                        lambda: _us_news.search(_news_q, country=_news_co, user_id=str(user_id or "")),
                        retries=0,
                        timeout_sec=22.0,
                        tag="news_universal_search",
                    )
                    if (
                        isinstance(_news_pack, dict)
                        and _news_pack.get("ok")
                        and str(_news_pack.get("summary") or "").strip()
                    ):
                        _news_body = str(_news_pack.get("summary")).strip()
                except Exception as e:
                    record_error_event("external_apis", "news search failed", exc=e, extra={"user_id": user_id})
            if not _news_body:
                try:
                    from core.brain_own_turn import pipeline_news_emergency_rss_on_search_fail_enabled

                    if pipeline_news_emergency_rss_on_search_fail_enabled():
                        from modules.external_apis.clients import NewsAPIClient

                        _rss_em = await with_retry(
                            lambda: NewsAPIClient().headlines(topic=_news_q, country=_news_co),
                            retries=0,
                            timeout_sec=14.0,
                            tag="news_google_rss_emergency",
                        )
                        if _rss_em.get("configured") and (_rss_em.get("items") or []):
                            _rss_items_em = _rss_em.get("items")
                            if isinstance(context, dict):
                                context["_news_rss_items"] = _rss_items_em
                                context["_news_rss_meta"] = {
                                    "locale": _rss_em.get("locale"),
                                    "world_feed": _rss_em.get("world_feed"),
                                    "topic": _rss_em.get("topic"),
                                    "emergency": True,
                                }
                            _news_body = str(_rss_em.get("summary") or "").strip()
                            MONITOR.inc("brain_news_emergency_rss_total")
                except Exception as e:
                    record_error_event(
                        "external_apis",
                        "news emergency rss failed",
                        exc=e,
                        extra={"user_id": user_id},
                    )
            if isinstance(context, dict) and _news_search_results and context.get("_news_rss_items"):
                try:
                    from core.telegram_output_guard import enrich_news_items_with_snippets

                    context["_news_rss_items"] = enrich_news_items_with_snippets(
                        context.get("_news_rss_items") or [],
                        _news_search_results,
                    )
                except Exception as e:
                    logger.debug("news snippet enrich: %s", e)
            if isinstance(context, dict):
                _rss_for_hint = context.get("_news_rss_items")
                if isinstance(_rss_for_hint, list) and _rss_for_hint:
                    try:
                        from core.telegram_output_guard import build_news_llm_source_block

                        _rich_news = build_news_llm_source_block(
                            _rss_for_hint,
                            search_results=_news_search_results,
                        )
                        if _rich_news.strip():
                            _news_body = _rich_news
                    except Exception as e:
                        logger.debug("news llm source block: %s", e)
            if isinstance(context, dict) and _news_search_results:
                context["_news_search_results"] = _news_search_results
            if isinstance(context, dict) and _news_body:
                context["_news_search_body"] = _news_body
            try:
                from core.brain.text_helpers import wants_expanded_news_digest

                _want_long = wants_expanded_news_digest(user_text or "", recent_dialogue)
            except Exception:
                _want_long = False
            if _news_body:
                try:
                    from core.telegram_output_guard import _news_brief_hint_max_chars

                    _hint_cap = 7500 if _want_long else _news_brief_hint_max_chars()
                except Exception:
                    _hint_cap = 7500 if _want_long else 5500
                if len(_news_body) > _hint_cap:
                    _news_body = _news_body[: _hint_cap - 1] + "…"
                _news_instr = (
                    "Пользователь просит подробнее: по каждому пункту — 4–6 предложений с деталями "
                    "из выдержек (кто, что, когда/где если есть, последствия). Без выдумок."
                    if _want_long
                    else (
                        "Сформируй дайджест для Telegram: 5–7 пунктов. На каждый пункт:\n"
                        "— строка «N. Заголовок» (коротко, по сути);\n"
                        "— абзац 3–4 предложения: что внутри новости — событие, участники, "
                        "контекст и почему это важно; только факты из «Выдержка» и заголовка;\n"
                        "— строка «· Издание» (если указано).\n"
                        "Если выдержки нет — честно сожми заголовок в 2 предложения, не выдумывай детали. "
                        "Без URL, без доменов, без вступления «вот новости» и без общего заключения."
                    )
                )
                external_hint = (
                    "Ниже — сводка из веб-поиска по запросу о новостях. "
                    f"{_NEWS_REPLY_ANTI_DISCLAIMER_ADDON} "
                    f"{_news_instr}; не выдумывай заголовки, даты и факты вне блока. "
                    "Если в сводке нет ответа на уточнение — скажи честно, что в выдаче этого нет.\n\n"
                    + _news_body
                )
            else:
                external_hint = (
                    f"{_NEWS_REPLY_ANTI_DISCLAIMER_ADDON} "
                    "Живой модуль новостей недоступен; веб-поиск не дал сводку. "
                    "Сначала один вызов UniversalSearch.search (или News.headlines), затем дайджест по результатам. "
                    "Не придумывай заголовки и события. Если поиск снова пуст — предложи уточнить регион/тему; "
                    "при необходимости один вызов UrlFetch по конкретной ссылке из прошлого диалога."
                )
    except Exception as e:
        record_error_event("external_apis", "api call failed", exc=e, extra={"user_id": user_id})
        external_hint = str(fallback_result("external api unavailable"))

    if not task_facts.get("is_time"):
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_clock_block}"
        else:
            external_hint = _clock_block

    _cal_hint = build_calendar_date_hint_for_llm(user_text)
    if _cal_hint:
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_cal_hint}"
        else:
            external_hint = _cal_hint

    try:
        from core.heuristic_fixes import build_heuristic_hint_block

        _hf = build_heuristic_hint_block(
            user_text,
            intent=str(context.get("intent") or context.get("last_intent") or ""),
        )
        if _hf.strip():
            if (external_hint or "").strip():
                external_hint = f"{external_hint.strip()}\n\n{_hf}"
            else:
                external_hint = _hf
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.dialogue_recheck_anchor import build_recheck_anchor_hint

        _recheck = build_recheck_anchor_hint(user_text, recent_dialogue)
        if _recheck.strip():
            external_hint = (
                f"{_recheck.strip()}\n\n{external_hint.strip()}" if (external_hint or "").strip() else _recheck
            )
    except Exception as e:
        logger.debug("recheck_anchor: %s", e)
    try:
        from core.incident_context_hint import build_incident_context_hint

        _inc = build_incident_context_hint(user_text, recent_dialogue, _persisted_short)
        if _inc.strip():
            external_hint = (
                f"{_inc.strip()}\n\n{external_hint.strip()}" if (external_hint or "").strip() else _inc
            )
    except Exception as e:
        logger.debug("incident_context: %s", e)
    try:
        from core.law_query_builder import prefetch_law_for_brain

        _law_pref = await prefetch_law_for_brain(
            user_text,
            recent_dialogue,
            user_id=str(user_id or ""),
        )
        if _law_pref:
            if (external_hint or "").strip():
                external_hint = f"{_law_pref}\n\n{external_hint.strip()}"
            else:
                external_hint = _law_pref
            MONITOR.inc("brain_law_prefetch_total")
    except Exception as e:
        record_error_event("law_prefetch", "prefetch failed", exc=e, extra={"user_id": user_id})

    _scenario_addon = str(context.get("scenario_brain_addon") or "").strip()
    if _scenario_addon:
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_scenario_addon}"
        else:
            external_hint = _scenario_addon

    if _translation_turn:
        try:
            from core.brain.translation_path import translation_external_hint as _tr_hint

            _th = _tr_hint(user_text)
            if (external_hint or "").strip():
                external_hint = f"{external_hint.strip()}\n\n{_th}"
            else:
                external_hint = _th
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.brain.router_classifier import _is_reference_paste

        if _is_reference_paste(user_text):
            _rp_hint = (
                "REFERENCE_PASTE: пользователь прислал длинную готовую статью/инструкцию. "
                "Кратко перескажи суть; не проси «пришлите снова»; не спорь с текстом без запроса. "
                "Если просят проверить факты — укажи сомнительное мягко и предложи официальный источник."
            )
            external_hint = (
                f"{external_hint.strip()}\n\n{_rp_hint}" if (external_hint or "").strip() else _rp_hint
            )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    _gid = context.get("group_id")
    _dlg_lookup = build_dialogue_lookup_hint_for_llm(
        user_text,
        recent_messages=recent_dialogue,
        dialogue_summary=str(context.get("dialogue_summary") or ""),
        user_facts=user_facts,
        user_id=str(user_id) if user_id else None,
        group_id=str(_gid) if _gid is not None and str(_gid).strip() else None,
    )
    if _dlg_lookup.strip():
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_dlg_lookup}"
        else:
            external_hint = _dlg_lookup

    _mem_recall = build_pipeline_memory_addon(
        user_text=user_text,
        user_id=str(user_id) if user_id else None,
        group_id=str(_gid) if _gid is not None and str(_gid).strip() else None,
        context=context if isinstance(context, dict) else {},
        recent_dialogue=recent_dialogue,
        user_facts=user_facts,
        telegram_message_unix=_tg_i_shared,
        need_memory=bool(_need_memory),
    )
    if _mem_recall.strip():
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_mem_recall}"
        else:
            external_hint = _mem_recall

    _news_direct_body = ""
    _news_rss_items: list = []
    _news_rss_short_circuit_ok = False
    try:
        from core.brain_own_turn import news_rss_fallback_enabled

        _news_rss_short_circuit_ok = news_rss_fallback_enabled()
    except Exception as e:
        logger.debug("pipeline news rss short-circuit gate: %s", e)
    if isinstance(context, dict):
        _news_direct_body = str(context.pop("_news_search_body", "") or "").strip()
        _raw_items = context.pop("_news_rss_items", None)
        if isinstance(_raw_items, list) and _news_rss_short_circuit_ok:
            _news_rss_items = _raw_items
    _news_expanded = False
    try:
        from core.brain.text_helpers import wants_expanded_news_digest

        _news_expanded = wants_expanded_news_digest(user_text or "", recent_dialogue)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)

    def _news_prefetch_fallback(reply: str) -> str:
        try:
            from core.news_reply import apply_news_prefetch_fallback_if_needed

            return apply_news_prefetch_fallback_if_needed(
                reply or "",
                search_body=_news_direct_body,
                user_query=user_text or "",
                task_facts=task_facts,
                brain_profile=_brain_profile,
                prefer_news_direct=bool(context.get("brain_prefer_news_direct")),
            )
        except Exception as ex:
            logger.debug("news prefetch fallback wrapper: %s", ex)
            return reply or ""

    _news_search_only_ok = False
    _news_search_results_sc: list = []
    try:
        from core.brain_own_turn import news_digest_search_only_enabled

        _news_search_only_ok = news_digest_search_only_enabled()
    except Exception as e:
        logger.debug("news search-only gate: %s", e)
    if isinstance(context, dict):
        _raw_sr = context.get("_news_search_results")
        if isinstance(_raw_sr, list):
            _news_search_results_sc = _raw_sr

    if (
        _news_search_only_ok
        and not _news_expanded
        and not task_facts.get("is_pasted_article")
        and (
            task_facts.get("is_news")
            or _brain_profile == "news_brief"
            or bool(context.get("brain_prefer_news_direct"))
        )
        and (_news_direct_body or _news_search_results_sc)
    ):
        try:
            from core.news_reply import (
                compose_news_digest_from_search,
                sync_news_digest_persisted,
                _news_country_iso2,
            )

            _nc_so = _news_country_iso2(user_facts if isinstance(user_facts, dict) else {})
            _wc_so = False
            try:
                from modules.external_apis.clients import NewsAPIClient

                _wc_so = NewsAPIClient().wants_world_news(user_text or "")
            except Exception:
                pass
            reply = await compose_news_digest_from_search(
                user_text or "",
                search_results=_news_search_results_sc,
                search_summary=_news_direct_body,
                persisted=_persisted_short,
                user_id=str(user_id or ""),
                expanded=False,
            )
            if reply and str(reply).strip():
                sync_news_digest_persisted(context, _persisted_short)
                try:
                    reply = _persona_apply_polished(user_id, reply)
                    if not skip_memory_writes:
                        await get_memory().on_after_response(user_id, reply)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                MONITOR.inc("brain_news_search_only_digest_total")
                return reply if _safe_text(reply) else str(reply).strip()
        except Exception as e:
            logger.debug("news search-only digest pipeline: %s", e)

    if (
        _env_flag("BRAIN_NEWS_DIRECT_FROM_SEARCH", default=False)
        and not _news_expanded
        and not task_facts.get("is_pasted_article")
        and (
            task_facts.get("is_news")
            or _brain_profile == "news_brief"
            or bool(context.get("brain_prefer_news_direct"))
        )
        and (_news_direct_body or _news_rss_items)
    ):
        from core.telegram_output_guard import format_news_from_items, format_news_from_search

        if _news_rss_items and _news_rss_short_circuit_ok:
            _shown_news: List[Any] = []
            try:
                from core.news_reply import (
                    _compose_digest_reply,
                    stash_news_digest_context_async,
                    sync_news_digest_persisted,
                )

                _wc = False
                try:
                    _wc = NewsAPIClient().wants_world_news(user_text or "")
                except Exception:
                    pass
                _nc = ""
                try:
                    from core.news_reply import _news_country_iso2

                    _nc = _news_country_iso2(user_facts if isinstance(user_facts, dict) else {})
                except Exception:
                    pass
                _shown_news = await stash_news_digest_context_async(
                    _persisted_short,
                    _news_rss_items,
                    query=user_text or "",
                    country=_nc,
                    world_feed=_wc,
                    user_id=str(user_id or ""),
                )
                sync_news_digest_persisted(context, _persisted_short)
            except Exception as e:
                logger.debug("brain news digest stash: %s", e)
                _shown_news = []
            if _shown_news:
                reply = await _compose_digest_reply(
                    _shown_news,
                    user_query=user_text or "",
                    expanded=_news_expanded,
                    user_id=str(user_id or ""),
                    country=_nc,
                    world_feed=_wc,
                )
            else:
                reply = format_news_from_items(_news_rss_items, user_query=user_text)
        else:
            reply = format_news_from_search(_news_direct_body, user_query=user_text)
        try:
            reply = _persona_apply_polished(user_id, reply)
            if not skip_memory_writes:
                await get_memory().on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        MONITOR.inc("brain_news_direct_from_search_total")
        return reply if _safe_text(reply) else format_news_from_search(_news_direct_body, user_query=user_text)

    urls_chron = gather_urls_chronological_for_brain(
        user_text,
        recent_dialogue,
        group_transcript_compact,
    )
    tools_mode = _brain_tools_mode()
    _chat_ctx_slim = _brain_chat_context_slim_eligible(
        user_text=user_text,
        context=context if isinstance(context, dict) else {},
        task_tier=task_tier,
        urls_chron=urls_chron,
        missing_facts=missing_facts,
        skill_name=skill_name,
        skill_output=skill_output,
        image_intent=image_intent,
        group_transcript_compact=group_transcript_compact,
        group_chat_addon_len=len(_group_chat_addon),
    )
    if _chat_ctx_slim:
        MONITOR.inc("brain_chat_context_slim_total")

    _slim_ext = _chat_ctx_slim and _env_flag("BRAIN_CHAT_CONTEXT_SLIM_EXTERNAL", default=True)

    if not _slim_ext and _env_flag("EXPERIENCE_HINT_IN_PROMPT", default=True):
        _exp_hint = str(context.get("experience_memory_hint") or "").strip()
        if _exp_hint:
            if (external_hint or "").strip():
                external_hint = f"{_exp_hint}\n\n{external_hint.strip()}"
            else:
                external_hint = _exp_hint

    if not _slim_ext:
        _rr_hint = str(context.get("route_risk_hint") or "").strip()
        if _rr_hint:
            if (external_hint or "").strip():
                external_hint = f"{external_hint.strip()}\n\n{_rr_hint}"
            else:
                external_hint = _rr_hint

    _ur_hint = str(context.get("user_remark_hint") or "").strip()
    if _ur_hint:
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_ur_hint}"
        else:
            external_hint = _ur_hint

    try:
        from core.brain.content_hints import intimate_health_education_hint

        _ih_hint = intimate_health_education_hint(user_text)
        if _ih_hint:
            if (external_hint or "").strip():
                external_hint = f"{_ih_hint}\n\n{external_hint.strip()}"
            else:
                external_hint = _ih_hint
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.user_correction_bus import build_operator_corrections_hint

        _op_corr = build_operator_corrections_hint(
            context if isinstance(context, dict) else {},
            user_text=user_text,
            user_id=user_id,
        )
        if _op_corr:
            if (external_hint or "").strip():
                external_hint = f"{_op_corr}\n\n{external_hint.strip()}"
            else:
                external_hint = _op_corr
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Strategy path hint: теперь в промпте (раньше был вырезан) ──
    _spath = str(context.get("strategy_path_hint") or "").strip()
    if _spath and not _chat_ctx_slim:
        if (external_hint or "").strip():
            external_hint = f"{_spath}\n\n{external_hint.strip()}"
        else:
            external_hint = _spath

    _truth = str(context.get("operator_truth_signals_hint") or "").strip()
    if _truth:
        if (external_hint or "").strip():
            external_hint = f"{_truth}\n\n{external_hint.strip()}"
        else:
            external_hint = _truth
    if _user_provided_ordered_checklist(user_text):
        _checklist_hint = (
            "STRUCTURED_CHECKLIST_MODE: пользователь дал многошаговый список. "
            "Выполни пункты по порядку, верни ответ с той же нумерацией. "
            "Не схлопывай всё в один уточняющий вопрос, если пользователь явно не просил только вопрос."
        )
        external_hint = f"{external_hint.strip()}\n\n{_checklist_hint}" if (external_hint or "").strip() else _checklist_hint
    try:
        from core.dialogue_slots import slot_external_hint

        _slot_hint = slot_external_hint(
            user_text,
            recent_dialogue,
            persisted=_persisted_short,
            user_id=str(context.get("user_id") or ""),
            chat_id=str(context.get("chat_id") or context.get("group_id") or ""),
        )
        if _slot_hint:
            external_hint = (
                f"{external_hint.strip()}\n\n{_slot_hint}"
                if (external_hint or "").strip()
                else _slot_hint
            )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.policy_memory_runtime import compute_memory_telemetry

        if isinstance(context, dict):
            context["memory_telemetry"] = compute_memory_telemetry(
                persisted=_persisted_short,
                user_text=user_text,
                recent_dialogue=recent_dialogue,
                external_hint=external_hint or "",
                user_id=str(context.get("user_id") or user_id or ""),
                chat_id=str(context.get("chat_id") or context.get("group_id") or ""),
            )
    except Exception as e:
        logger.debug("memory_telemetry stash: %s", e)
    try:
        from core.batch_continuation import looks_like_unified_math_problem

        if looks_like_unified_math_problem(user_text):
            _unified_math_hint = (
                "UNIFIED_MATH_COMPACT: одна геометрическая задача с подпунктами. "
                "Ответь нумерованным списком 1. 2. 3. — только итог (число или короткая фраза на пункт), "
                "без рассуждений вслух, без «мы находимся…», без пересказа условия."
            )
            external_hint = (
                f"{external_hint.strip()}\n\n{_unified_math_hint}"
                if (external_hint or "").strip()
                else _unified_math_hint
            )
            behavior_policy["verbosity"] = "concise"
            behavior_policy["no_service_clarifications"] = True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    if _user_requests_strict_direct_reasoning(user_text):
        _strict_reasoning_hint = (
            "STRICT_DIRECT_REASONING: ответь строго по формулировке пользователя. "
            "Без ролевого вступления, без художественного нарратива, без встречных вопросов. "
            "Формат: (1) можно/нельзя говорить о рациональной стратегии; "
            "(2) если можно — краткая концептуальная стратегия; "
            "(3) если нельзя — почему постановка рушит само понятие стратегии."
        )
        external_hint = (
            f"{external_hint.strip()}\n\n{_strict_reasoning_hint}"
            if (external_hint or "").strip()
            else _strict_reasoning_hint
        )
        behavior_policy["verbosity"] = "concise"
        behavior_policy["no_service_clarifications"] = True
    _mcq_compact = _user_requests_compact_mcq_answer(user_text)
    if _mcq_compact:
        _mcq_hint = (
            "COMPACT_MCQ_GUARD: Нужен ответ как в бланке — на каждую задачу не больше 1–2 коротких предложений "
            "(или сразу итог), затем одна строка вида «8 — Б» / «8. B» / «9) Г» по формулировке пользователя. "
            "Запрещено: много абзацев и циклов «возможно… но тогда…» на одну задачу, повтор одних и тех же вычислений. "
            "Если не сходится — выбери наиболее правдоподобный вариант одной фразой и переходи к следующей задаче."
        )
        if _user_wants_inline_mcq_answer_format(user_text):
            _mcq_hint += (
                " Пользователь просит финиш одной строкой латиницей: «1A 2B 3C … 10X» через пробел, без пояснений до/после."
            )
        external_hint = f"{external_hint.strip()}\n\n{_mcq_hint}" if (external_hint or "").strip() else _mcq_hint
        behavior_policy["verbosity"] = "concise"
        behavior_policy["no_service_clarifications"] = True
    _pmg = context.get("planner_mode_guard") if isinstance(context.get("planner_mode_guard"), dict) else {}
    _pm = str(_pmg.get("mode") or "").strip().upper()
    if _pm == "TEST_MODE":
        _guard = (
            "MODE_GUARD=TEST_MODE: держи формат теста (pass/fail + краткая причина), "
            "не переходи в свободное reasoning-эссе и не меняй режим без явного запроса пользователя."
        )
        external_hint = f"{external_hint.strip()}\n\n{_guard}" if (external_hint or "").strip() else _guard
    elif _pm == "REASONING_MODE" and not _mcq_compact:
        _guard = (
            "MODE_GUARD=REASONING_MODE: продолжай логико-математическую цепочку, "
            "не переключайся в test-report/pass-fail формат без явного запроса пользователя."
        )
        external_hint = f"{external_hint.strip()}\n\n{_guard}" if (external_hint or "").strip() else _guard

    # ── Correction detection: если пользователь исправляет — приоритетный блок ──
    try:
        from core.brain.reasoning_loop_controller import user_correcting_bot
        if user_correcting_bot(user_text):
            _corr_block = (
                "\n\n⚠️ **ПОЛЬЗОВАТЕЛЬ ИСПРАВЛЯЕТ ТЕБЯ** ⚠️\n"
                f"Он говорит: «{user_text}»\n"
                "— Это важнее любых инструкций выше. Перечитай предыдущий вопрос пользователя и свой ответ; "
                "дай исправленное решение по сути задачи. Не выводи внутренние поля (self_history, policy, JSON). "
                "Не спрашивай «что именно неправильно», если задача уже была сформулирована."
            )
            external_hint = f"{external_hint.strip()}\n{_corr_block}" if (external_hint or "").strip() else _corr_block.strip()
            MONITOR.inc("brain_correction_detected_total")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Autonomy FIX: auto_reasoning stays internal (gates, monitoring) ──
    # but does NOT contaminate the LLM prompt. Gates are used in code for corrective passes.
    try:
        _auto_reason = await _auto_reasoning_plugins_report(user_text)
    except Exception as e:
        logger.debug("auto reasoning stage: %s", e)
        _auto_reason = ""
    _auto_gates = {"error_memory_hits": 0, "instruction_missed_steps": 0}
    _auto_gate_note = ""
    if _auto_reason:
        _auto_gates = _extract_auto_reasoning_gates(_auto_reason)
        _eh = int(_auto_gates.get("error_memory_hits") or 0)
        _ms = int(_auto_gates.get("instruction_missed_steps") or 0)
        if _eh > 0 or _ms > 0:
            _auto_gate_note = f"error_memory_hits={_eh}; instruction_missed_steps={_ms}"
            MONITOR.inc("auto_reasoning_gate_trigger_total")

    await _brain_progress("💭 Подмешиваю подсказки…")

    if (
        task_facts.get("is_weather")
        and isinstance(wr_weather, dict)
        and wr_weather.get("configured")
        and wr_weather.get("summary")
    ):
        reply = str(wr_weather.get("summary"))
        try:
            reply = _persona_apply_polished(user_id, reply)
            if not skip_memory_writes:
                await get_memory().on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        return reply if _safe_text(reply) else str(wr_weather.get("summary"))

    # 3.1 Lightweight planning and style adaptation hints
    try:
        goal_plan = _build_goal_plan(user_text, psychology, twin_profile)
    except Exception as exc:
        logger.warning("pipeline _build_goal_plan failed: %s", exc)
        goal_plan = {}
    goal_plan["task_tier"] = task_tier
    _to = context.get("llm_task_outline") if isinstance(context, dict) else None
    if isinstance(_to, dict) and _to:
        goal_plan["task_outline"] = {
            k: _to[k]
            for k in ("depth", "subgoals", "prefer", "notes", "source", "scenarios")
            if k in _to
        }
    _lk = context.get("lookahead_plan") if isinstance(context, dict) else None
    if isinstance(_lk, dict) and (_lk.get("steps") or _lk.get("likely_followups")):
        goal_plan["lookahead"] = _lk
    if user_facts.get("city") or user_facts.get("country"):
        goal_plan.setdefault("constraints", []).append("use_known_location_facts")
    if user_facts.get("timezone"):
        goal_plan.setdefault("constraints", []).append("use_known_timezone")
    if user_facts.get("currency"):
        goal_plan.setdefault("constraints", []).append("use_known_currency")
    style_hints = _build_style_hints(persona, psychology, twin_profile)
    if behavior_policy.get("tone"):
        style_hints["tone"] = behavior_policy.get("tone")
    if behavior_policy.get("verbosity"):
        style_hints["verbosity"] = behavior_policy.get("verbosity")
    if user_facts.get("language") and "tone" in style_hints:
        style_hints["language_hint"] = user_facts.get("language")
    try:
        age_n = int(user_facts.get("age")) if user_facts.get("age") is not None else None
    except Exception:
        age_n = None
    if age_n is not None:
        if age_n < 16:
            style_hints["audience_age"] = "teen"
            style_hints["verbosity"] = "structured"
            style_hints["tone"] = "supportive"
        elif age_n >= 60:
            style_hints["audience_age"] = "senior"
            style_hints["verbosity"] = "structured"
    if user_facts.get("interests"):
        style_hints["interest_bias"] = user_facts.get("interests")
    for k in ("tone", "verbosity", "explanation_style"):
        if blended_stable.get(k):
            style_hints[k] = blended_stable[k]
    micro_emotion_style = _build_micro_emotion_style(psychology, behavior_engine)
    if user_facts.get("language"):
        micro_emotion_style["language_alignment"] = user_facts.get("language")
    thinking_markers = _build_thinking_markers(goal_plan, dialogue_state)
    typing_hooks = _build_typing_hooks(style_hints, dialogue_state)

    _da_sys_extra = ""
    if user_requests_dialogue_analysis_effective(user_text, context):
        _da_sys_extra = (
            " Запрос на разбор переписки: только готовый ответ пользователю, "
            "без обсуждения выбора инструментов и без имён SelfProgramming."
        )

    from core.brain.profile_registry import get_profile as _get_brain_profile_cfg

    _brain_prof_cfg = _get_brain_profile_cfg(_brain_profile)
    _scaffold_mode = (_prof_primary.reasoning_scaffold or "full").strip().lower()
    if not _brain_prof_cfg.include_scaffold or _translation_turn:
        _scaffold_mode = "omit"
    if _scaffold_mode == "omit":
        reasoning_scaffold_block = ""
    elif _scaffold_mode == "short":
        reasoning_scaffold_block = (
            "Кратко определи цель и ограничения; реши, нужен ли TOOL_CALL или прямой ответ; "
            "пользователю выдай только итог, без пересказа полей контекста и без внутреннего чек-листа."
        )
        if _no_service_clar:
            reasoning_scaffold_block += (
                " Не заканчивай ответ уточняющими вопросами (no_service_clarifications)."
            )
    else:
        _scaffold_line7 = (
            "7) Используй user_facts/task_facts для задач времени/погоды/валюты/локации, а если данных не хватает — задай один короткий auto-ask вопрос мягким тоном."
            if not _no_service_clar
            else (
                "7) Используй user_facts/task_facts для задач времени/погоды/валюты/локации. "
                "Не добавляй в конец ответа уточняющие вопросы (no_service_clarifications): "
                "условие задачи и переписка считаются достаточными; не спрашивай про валюту или лимиты, если это уже задано сценарием."
            )
        )
        reasoning_scaffold_block = f"""Инженерный стиль: никаких «может быть», «возможно», «кажется». Ответ — это решение или факт. Trade-off — это осознанный выбор, а не ошибка.

Каркас рассуждения (внутренний, не показывай пользователю):
1) Определи цель запроса и важные ограничения.
2) Реши, нужен ли TOOL_CALL, или можно ответить напрямую.
3) Если вызываешь инструмент — передай минимально необходимые args.
4) Финальный ответ сделай кратким и полезным.
5) Подстрой тон и стиль под style_hints и blended_style_stable; учитывай непрерывность micro_emotion_style.
6) Если тема продолжает topic_tracking — сохраняй связность ответа.
{_scaffold_line7}
8) Если selected_skill задан, примени skill_hint и skill_output в итоговом ответе.
9) Учитывай behavior_policy и knowledge_hint_summary при формировании ответа.
10) Если predictive_hint содержит уверенный прогноз намерения — согласуй ответ с ним; если пользователь **явно** сменил ситуацию (разрыв, новый факт, отмена прежнего сценария) — прогноз и старая линия вторичны, опирайся на последнюю реплику.
11) Если goal_hints не пусты — учти направление целей пользователя (кратко, без лишней лекции).
12) Если urls_in_message не пуст и нужен фактический текст с сайта — вызови UrlFetch.fetch_page с точным url.
13) Если основной ответ или первичный источник не дали результата, а нужны факты/данные — рассмотри **другой** инструмент из списка (запасной URL в external_hint, SiteRecipe, RAG и т.д.); лучше один дополнительный шаг, чем «оборвался на полпути».
14) **Lookahead (goal_plan.lookahead)** — это не декоративная подсказка, а внутренний план рассуждения: пройди шаги `do/why` мысленно, закрой проверки `verify`, учти риски смены сюжета; в ответ пользователю не читай их списком. Если реплика **ломает** прежнюю линию — отбрось шаги старого канона.
15) **task_tier / task_outline**: при nested/deep или `depth=multi` / `prefer=thorough` / непустые `scenarios` строй ответ как сценарный разбор (ветки, условия, последствия) структурно; подцели `subgoals` закрывай по смыслу, не зачитывай.
16) **Ликвидность / банк / поездка / несколько сценариев**: если в контексте есть **operator_rules** (системная директива из файла) — следуй ей по структуре ответа; иначе кратко: не смешивай несовместимые ветки в одном таймлайне без «если»; не выдумывай банки и курсы; валюту лимитов спрашивай у пользователя, не у «ИИ».
17) **Гибрид «дерево + хронология»** (неопределённость, task_outline, п.16): известное → неизвестное → 2–3 ветки с условием; внутри ветки — риск + мини-хронология. Полный шаблон и примеры — в директиве (addon), не дублируй его целиком в ответ пользователю."""
    _scaffold_part = f"\n{reasoning_scaffold_block}\n" if reasoning_scaffold_block else "\n"

    system_prompt_for_llm = merge_system(
        system_prompt,
        _prof_primary.system_addon_first,
        BRAIN_CAPABILITY_HONESTY,
        brain_instance_attribution_block(),
        BRAIN_INFRASTRUCTURE_HONESTY,
    )
    # ── Profile-based system prompt adaptation ──
    system_prompt_for_llm = _pick_system_prompt_for_profile(_brain_profile, system_prompt_for_llm)
    _need_capability_catalog = bool(
        _user_requests_capability_overview(user_text)
        or "/help" in user_text
        or "команд" in user_text.lower()
        or "плагин" in user_text.lower()
        or "инструмент" in user_text.lower()
    )
    _tcmd_full = str(context.get("telegram_commands_catalog_full") or "").strip()
    _tcmd_min = str(
        context.get("telegram_commands_catalog_min") or context.get("telegram_commands_catalog") or ""
    ).strip()
    if not _tcmd_full:
        _tcmd_full = _tcmd_min
    if not _tcmd_min:
        _tcmd_min = _tcmd_full
    _low_ut = (user_text or "").lower()
    _need_full_slash_catalog = bool(
        _env_flag("BRAIN_COMMAND_CATALOG_ALWAYS_FULL", default=False)
        or bool(context.get("brain_force_full_command_catalog"))
        or bool(context.get("telegram_is_admin"))
        or _need_capability_catalog
        or bool(re.search(r"/admin(?:_\w+|\b)", user_text or "", re.IGNORECASE))
        or (
            "команда" in _low_ut
            and any(w in _low_ut for w in ("список", "все", "полн", "перечисл", "какие", "полный"))
        )
    )
    _tcmd_cat = _tcmd_full if _need_full_slash_catalog else _tcmd_min
    _tcmd_cap = 14000 if _need_full_slash_catalog else 4500
    if len(_tcmd_cat) > _tcmd_cap:
        _tcmd_cat = _tcmd_cat[: _tcmd_cap - 3] + "..."
    # OPTIMIZE: каталог команд — только если спросили про возможности
    if not _need_capability_catalog and not context.get("telegram_is_admin"):
        _tcmd_cat = ""
    _plugin_mf_prompts = ""
    if _need_capability_catalog:
        _plugin_mf_prompts = str(context.get("plugin_manifest_prompts") or "").strip()
        if len(_plugin_mf_prompts) > 8000:
            _plugin_mf_prompts = _plugin_mf_prompts[:7997] + "..."
    _sess_first = str(context.get("session_first_user_text") or "").strip()
    if len(_sess_first) > 1200:
        _sess_first = _sess_first[:1197] + "..."
    _pteacher = str(context.get("persona_teacher_addon") or "").strip()

    # 4. Доступные инструменты (режим auto/lite — узкий набор, как у «старого» бота)
    try:
        tools_full = list_tools()
    except Exception as e:
        logger.warning("[brain] tools discovery failed: %s", e)
        tools_full = {}
    # ── Profile-based tool selection ──
    tools_info = _tools_for_profile(_brain_profile, tools_full, user_text)
    if _translation_turn:
        tools_info = {}
    if context.get("brain_disable_tools"):
        tools_info = {}
    try:
        from core.math_linear import text_looks_like_equation_solve

        if text_looks_like_equation_solve(user_text) and tools_info:
            tools_info = {
                k: v
                for k, v in tools_info.items()
                if not str(k).startswith("ArithmeticTool.")
            }
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # Для batch: только ArithmeticTool — остальные инструменты заставляют модель 
    # прерывать список вызовами, вместо того чтобы ответить на всё текстом
    if _brain_profile == "batch":
        try:
            from core.batch_continuation import (
                is_unified_problem as _batch_is_unified,
                resolve_unified_problem_profile as _resolve_unified_profile,
            )

            if _batch_is_unified(user_text):
                _brain_profile = _resolve_unified_profile(user_text)
                tools_info = _tools_for_profile(_brain_profile, tools_full, user_text)
                logger.info(
                    "[brain] unified problem — skip parallel batch, profile=%s",
                    _brain_profile,
                )
            else:
                tools_info = {k: v for k, v in tools_info.items()
                              if str(k).startswith("ArithmeticTool.")}
        except Exception:
            tools_info = {k: v for k, v in tools_info.items()
                          if str(k).startswith("ArithmeticTool.")}
        # ── Parallel batch processor interception ──
        if _brain_profile == "batch":
            try:
                from core.batch_processor import run_parallel_batch, is_parallel_eligible
                from core.batch_continuation import extract_items

                _batch_items = extract_items(user_text)
                if _batch_items and len(_batch_items) >= 3 and is_parallel_eligible(_batch_items):
                    _bp_result = await run_parallel_batch(_batch_items, user_id, user_facts)
                    if _bp_result.get("ok") and _bp_result.get("reply"):
                        _reply = str(_bp_result["reply"])
                        _mode = str(_bp_result.get("mode", "parallel"))
                        _answered = int(_bp_result.get("answered", 0))
                        _total = int(_bp_result.get("total", 0))
                        MONITOR.inc("batch_parallel_used_total")
                        if not skip_memory_writes:
                            try:
                                await get_memory().on_after_response(user_id, _reply)
                            except Exception as e:
                                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                        logger.info(
                            "[brain] parallel batch done: %d/%d items, mode=%s",
                            _answered, _total, _mode,
                        )
                        return _reply
                    logger.info(
                        "[brain] parallel batch declined (ok=%s), fallthrough to sequential",
                        _bp_result.get("ok"),
                    )
            except Exception as _bp_e:
                logger.debug("[brain] parallel batch init error: %s", _bp_e)
    if user_requests_dialogue_analysis_effective(user_text, context):
        tools_info = {k: v for k, v in tools_info.items() if not str(k).startswith("SelfProgramming.")}
    _rh = build_tool_routing_hint(
        user_text, urls_chron[:5], set(tools_info.keys()), recent_dialogue=recent_dialogue
    )
    tool_routing_hint_str = _rh.prompt_note
    try:
        from core.goal_domain_policy import format_domain_routing_addon

        _dom_addon = format_domain_routing_addon(user_text)
        if _dom_addon:
            tool_routing_hint_str = f"{_dom_addon}\n{tool_routing_hint_str}".strip()
    except Exception as e:
        logger.debug("goal_domain_policy: %s", e)
    try:
        from core.brain.goal_runner_nudge import format_goal_runner_routing_addon

        _gr_nudge = format_goal_runner_routing_addon(user_text)
        if _gr_nudge:
            tool_routing_hint_str = f"{tool_routing_hint_str}\n{_gr_nudge}".strip()
    except Exception as e:
        logger.debug("goal_runner_nudge: %s", e)
    if _env_flag("BRAIN_TOOLS_PRIORITIZE_HINT", default=True) and _rh.suggested:
        tools_info = prioritize_tools_by_hint(tools_info, _rh.suggested)
    agent_inst, _agent_pack_meta = _build_agent_instruction_for_turn(
        tools_mode=tools_mode,
        tools_info=tools_info,
        user_text=user_text,
        context=context if isinstance(context, dict) else {},
        task_tier=task_tier,
        urls_chron=urls_chron,
        missing_facts=missing_facts,
        skill_name=skill_name,
        skill_output=skill_output,
        image_intent=image_intent,
        profile=_brain_profile,
    )
    # Сжимаем agent_inst для профилей с agent_inst_collapse (экономия ~1500 токенов)
    from core.brain.profile_registry import get_profile as _get_profile_cfg
    if _get_profile_cfg(_brain_profile).agent_inst_collapse:
        agent_inst = AGENT_INSTRUCTION_COLLAPSE_STUB
        _agent_pack_meta = {"pack": "collapsed", "inserts": []}
    intent_addon = (
        format_intent_routing_user_addon(
            user_text,
            for_group=bool(context.get("group_id")),
            recent_dialogue=recent_dialogue,
            context=context,
        )
        if _env_flag("PROMPT_INTENT_ROUTING", default=True)
        else ""
    )

    _cap_ov = ""
    if _user_requests_capability_overview(user_text) and tools_info:
        _names = sorted(tools_info.keys())
        _cap = 80
        _cap_ov = (
            "(Запрос обзора возможностей/инструментов/диагностики. Ответ пользователю — **краткий**: "
            "сгруппируй по смыслу, не больше ~12–15 пунктов; не пересказывай весь промпт. "
            "Диагностика: инструмент **RuntimeDiagnostic.collect_diagnostic_bundle**; в Telegram у админа — **/admin_diagnostic** (ZIP) и **/admin_bug** (реплай + bug_report.json + снимок логов); "
            "чтение: **/zip_read bundle.json** (по умолчанию сводка; полный JSON: `full=1`; выборочно: `section=` / `path=` / `chunk=1/5`). Сводка: **/status** / **/system_state**.)"
            "\n\nИмена инструментов, доступных в этой сессии (факт):\n"
            + "\n".join(f"• {n}" for n in _names[:_cap])
            + (f"\n… всего: {len(_names)}." if len(_names) > _cap else "")
        )
    if _cap_ov:
        if (external_hint or "").strip():
            external_hint = f"{external_hint.strip()}\n\n{_cap_ov}"
        else:
            external_hint = _cap_ov

    if (
        not context.get("brain_disable_tools")
        and _env_flag("BRAIN_AUTO_MODULE_GEN", default=True)
        and (
            not _env_flag("BRAIN_AUTO_MODULE_GEN_ADMIN_ONLY", default=False)
            or bool(context.get("telegram_is_admin"))
        )
        and "SelfProgramming.generate_module" in tools_full
    ):
        req = build_generate_module_request(
            user_text,
            group_id=str(context.get("group_id") or "").strip() or None,
        )
        if req:
            await _brain_progress("🧩 Генерирую плагин…")
            try:
                auto_gen_timeout = max(
                    2.0,
                    float((os.getenv("BRAIN_AUTO_MODULE_GEN_TIMEOUT_SEC") or "8").strip()),
                )
            except ValueError:
                auto_gen_timeout = 8.0
            try:
                tool_out = await asyncio.wait_for(
                    run_tool(
                        "SelfProgramming.generate_module",
                        module_name=req["module_name"],
                        description=req["description"],
                        commands=req.get("commands"),
                        buttons=req.get("buttons"),
                        game_crocodile=bool(req.get("is_crocodile")),
                        command_prefix=str(req.get("command_prefix") or ""),
                        user_id=user_id,
                    ),
                    timeout=auto_gen_timeout,
                )
            except asyncio.TimeoutError:
                MONITOR.inc("brain_auto_module_gen_timeout_total")
                logger.warning(
                    "[brain] auto module gen timeout after %.1fs; fallback to normal dialogue",
                    auto_gen_timeout,
                )
                tool_out = {"success": False, "error": "timeout", "continue_dialogue": True}
            except Exception as e:
                logger.warning("[brain] auto module gen: %s", e)
                tool_out = {"success": False, "error": str(e)}
            if isinstance(tool_out, dict) and tool_out.get("success"):
                pfx = str(req.get("command_prefix") or "").strip()
                hot = tool_out.get("hot_install") if isinstance(tool_out.get("hot_install"), dict) else {}
                strict = tool_out.get("strict_report") if isinstance(tool_out.get("strict_report"), dict) else {}
                lines = [
                    f"Готово: каталог modules/{req['module_name']}/ создан.",
                ]
                if strict:
                    if strict.get("ok"):
                        lines.append("Strict-проверки: пройдены (validate + smoke).")
                    else:
                        lines.append(f"Strict-проверки: ошибка — {strict.get('error')}")
                if hot.get("success"):
                    lines.append("Плагин подхвачен живым реестром (hot_install).")
                elif hot.get("skipped"):
                    lines.append(f"Hot-install не выполнен: {hot.get('reason', '')}. Проверь PLUGIN_HOT_INSTALL_AFTER_GENERATE.")
                if pfx:
                    lines.append(
                        f"Команды: /{pfx}_… (список в /help → Модули). В супергруппе при необходимости укажите @username_бота после команды."
                    )
                if req.get("is_crocodile"):
                    lines.append("Крокодил: ведущий жмёт «Новый раунд» или /…_new; слово для него в спойлере.")
                lines.append("Кнопки под ответом: 🧪 тест первой команды, ♻️ повторная загрузка в реестр.")
                reply_ag = "\n".join(lines)
                try:
                    reply_ag = _persona_apply_polished(user_id, reply_ag)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                if _env_flag("BRAIN_POST_MODULE_GEN_BUTTONS", default=True):
                    try:
                        attach_post_module_gen_keyboard(context, req)
                    except Exception as e:
                        logger.debug("post_module_gen keyboard: %s", e)
                if not skip_memory_writes:
                    try:
                        await get_memory().on_after_response(user_id, reply_ag)
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return reply_ag
            if isinstance(tool_out, dict) and tool_out.get("continue_dialogue"):
                # Не обрываем ответ пользователю, если авто-генерация зависла/упёрлась в таймаут.
                pass
            else:
                err = ""
                if isinstance(tool_out, dict):
                    err = str(tool_out.get("error") or "").strip()
                reply_err = "Не удалось сгенерировать модуль" + (f": {err}" if err else ".")
                try:
                    reply_err = _persona_apply_polished(user_id, reply_err)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                if not skip_memory_writes:
                    try:
                        await get_memory().on_after_response(user_id, reply_err)
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return reply_err

    urls_in_message = urls_chron[:5]

    if (
        _env_flag("BRAIN_AUTO_URLFETCH", default=True)
        and urls_chron
        and user_signals_url_content_fetch(user_text, urls_chron)
        and _env_flag("URL_FETCH_ENABLED", True)
    ):
        fetch_url = urls_chron[-1]
        await _brain_progress("📥 Загружаю страницу…")
        try:
            _args_auto = {"url": fetch_url, "user_id": user_id}
            _cached_a = _tool_dedup_lookup(user_id, "UrlFetch.fetch_page", _args_auto)
            if _cached_a is not None:
                MONITOR.inc("brain_tool_dedup_hit_total")
                tool_result_auto = _cached_a
            else:
                tool_result_auto = await run_tool("UrlFetch.fetch_page", url=fetch_url, user_id=user_id)
                _tool_dedup_store(user_id, "UrlFetch.fetch_page", _args_auto, tool_result_auto)
        except Exception as e:
            logger.warning("[brain] auto urlfetch: %s", e)
            tool_result_auto = {"error": str(e)}
        tr_text = ""
        if isinstance(tool_result_auto, dict):
            tr_text = str(tool_result_auto.get("text") or "").strip()
        if isinstance(tool_result_auto, dict) and tool_result_auto.get("ok") and tr_text:
            payload_auto = {
                "ok": tool_result_auto.get("ok"),
                "url": tool_result_auto.get("url"),
                "title": tool_result_auto.get("title"),
                "http_status": tool_result_auto.get("http_status"),
                "truncated": tool_result_auto.get("truncated"),
                "text": _clip_soft(str(tool_result_auto.get("text") or ""), 14000),
            }
            second_prompt_auto = f"""
Системная инструкция:
{system_prompt_for_llm}

Страница уже загружена (авто UrlFetch) — пользователь просил содержимое/документацию, ссылка из текущего сообщения или недавней нити.
URL: {fetch_url}
Результат загрузки (JSON):
{_safe_json_dumps(payload_auto)}

Ответь по-русски: что это за страница, краткий конспект или структура (маркированный список), что полезного для задачи пользователя.
Если это документация библиотеки — укажи версию в заголовке (если видна) и 2–4 ключевых раздела. Не проси прислать URL снова.

Сообщение пользователя:
{user_text}
"""
            await _brain_progress("✍️ Кратко по странице…")
            try:
                _sys_urlfetch = merge_system(
                    "Ты ассистент: кратко резюмируешь загруженную веб-страницу. "
                    "Только итог пользователю, без английских рассуждений вслух.",
                    _prof_primary.system_addon_first,
                )
                if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
                    second_a = await llm_generate_tiered(
                        _llm,
                        tag="llm_auto_urlfetch",
                        prompt=second_prompt_auto,
                        system_prompt=_sys_urlfetch,
                        max_tokens=900,
                        temperature=clamp_temperature(0.45, _prof_primary.temperature_first_delta),
                        base_timeout=None,
                        telemetry_tag="urlfetch",
                        session_id=llm_session_id,
                        conversation_id=llm_session_id,
                    )
                else:
                    second_a = await with_timeout(
                        _llm.generate(
                            prompt=second_prompt_auto,
                            system_prompt=_sys_urlfetch,
                            max_tokens=900,
                            temperature=clamp_temperature(0.45, _prof_primary.temperature_first_delta),
                            telemetry_tag="urlfetch",
                            session_id=llm_session_id,
                            conversation_id=llm_session_id,
                        ),
                        timeout_sec=DEFAULT_TIMEOUT_SEC,
                        tag="llm_auto_urlfetch",
                    )
            except Exception as e:
                logger.error("[brain] auto urlfetch llm: %s", e)
                second_a = {"error": str(e), "content": ""}
            if not second_a.get("error"):
                reply_auto = _strip_leaked_cot(
                    _safe_text(second_a.get("content", "")),
                    extra_markers_en=_prof_primary.cot_extra_markers_en,
                    extra_markers_ru=_prof_primary.cot_extra_markers_ru,
                )
                if (reply_auto or "").strip():
                    try:
                        reply_auto = _persona_apply_polished(user_id, reply_auto)
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                    if not skip_memory_writes:
                        try:
                            await get_memory().on_after_response(user_id, reply_auto)
                        except Exception as e:
                            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                    return reply_auto
            title_fb = str(tool_result_auto.get("title") or "").strip()
            head_fb = f"{title_fb}\n\n" if title_fb else ""
            clipped = _clip_soft(tr_text, 4500)
            reply_fb = head_fb + clipped
            if len(tr_text) > len(clipped):
                reply_fb += "\n\n… (фрагмент; краткая сводка через модель недоступна.)"
            try:
                reply_fb = _persona_apply_polished(user_id, reply_fb)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            if not skip_memory_writes:
                try:
                    await get_memory().on_after_response(user_id, reply_fb)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            return reply_fb

    vision_parts: Optional[List[Tuple[str, str]]] = None
    vision_precaption = ""
    if (
        image_intent == "describe"
        and _env_flag("BRAIN_VISION_OPENROUTER", default=True)
        and isinstance(file_context, dict)
    ):
        vision_parts = vision_image_parts_for_brain(file_context)

    if vision_parts and _env_flag("BRAIN_VISION_TWO_STEP", default=True):
        try:
            vision_precaption = await _brain_run_vision_precaption(
                user_text=user_text,
                vision_parts=vision_parts,
            )
        except Exception as e:
            logger.warning("[brain] vision_precaption failed: %s", e)
            vision_precaption = ""
        if vision_precaption:
            so = skill_output if isinstance(skill_output, dict) else {}
            skill_output = {**so, "vision_precaption": vision_precaption}
            await _brain_progress("👁 Встраиваю описание снимка…")

    use_slim = (
        bool(vision_precaption)
        and image_intent == "describe"
        and _env_flag("BRAIN_IMAGE_SLIM_PROMPT", default=True)
    )

    hot_path_slim = _brain_hot_path_slim_eligible(
        user_text=user_text,
        context=context if isinstance(context, dict) else {},
        use_slim_image=use_slim,
        skill_name=skill_name,
        skill_output=skill_output,
        image_intent=image_intent,
        missing_facts=missing_facts,
        group_transcript_compact=group_transcript_compact,
        group_chat_addon_len=len(_group_chat_addon),
        task_tier=task_tier,
    )
    if isinstance(context, dict) and context.get("fatigue_force_slim"):
        if not _tier_prefers_thorough(task_tier):
            hot_path_slim = True
    if str(context.get("prompt_assembly_override") or "").strip().lower() == "full":
        hot_path_slim = False
    if hot_path_slim and not use_slim:
        MONITOR.inc("brain_hot_path_slim_total")
        logger.debug("[brain] hot_path_slim prompt (user_chars=%s tools=%s)", len(user_text), len(tools_info))

    _assembly_tier = brain_prompt_tier(use_slim_image=use_slim, hot_path_slim=hot_path_slim)

    vp_ctx = ""
    if vision_precaption:
        vp_ctx = (
            "- vision_precaption (vision step facts; do not invent objects): "
            f"{vision_precaption}\n"
        )

    _kh_hot = _summarize_knowledge_hint(knowledge_hint, max_items=2, max_chars=280)
    _tr_raw = str(context.get("telegram_reply_context") or "").strip()
    _telegram_reply_block = ""
    if _tr_raw:
        _telegram_reply_block = (
            "Пользователь пишет в ветке Telegram (ответ на сообщение / возможно пересылка). "
            "Ниже — цитаты предков в цепочке от ближайшего к более ранним; опирайся на них, иначе реплика кажется «с нуля»:\n"
            + _tr_raw
        )
    _tool_names_full_index = _format_tools_full_index_for_prompt(tools_full, tools_info, tools_mode)
    if (
        _chat_ctx_slim
        and _env_flag("BRAIN_CHAT_CONTEXT_SLIM_TOOLS_INDEX", default=True)
        and tools_mode != "full"
    ):
        _tool_names_full_index = ""
        MONITOR.inc("brain_chat_context_slim_drop_tools_index_total")
    # Архив в промпт — по умолчанию ВЫКЛ (испорченный FIFO давал ответ «на прошлый вопрос»).
    _message_archive: List[Dict[str, Any]] = []
    if _env_flag("BRAIN_INCLUDE_ARCHIVE_IN_PROMPT", default=False):
        try:
            from core.message_archive import items_for_prompt

            _message_archive = items_for_prompt(str(user_id), context.get("group_id"))
            try:
                from core.brain.cot_strip import sanitize_dialogue as _sanitize_dialogue

                _message_archive = _sanitize_dialogue(_message_archive)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Autonomy FIX: append self_model_trust and autonomy_goal to dynamic tail (external_hint)
    # instead of system_prompt (static head). This keeps KV-cache stable regardless of flags.
    try:
        _autonomy_dynamic_hints = " ".join(p for p in (_sm_addon, _goal_addon) if (p or "").strip()).strip()
        if _autonomy_dynamic_hints:
            external_hint = (
                f"{external_hint.strip()}\n\n{_autonomy_dynamic_hints}"
                if (external_hint or "").strip()
                else _autonomy_dynamic_hints
            )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Self-Learning: вставляем релевантные уроки из прошлых ошибок ──
    _injected_lessons = []
    _lessons_hint = ""
    if not _translation_turn:
        try:
            _lessons_hint, _injected_lessons = await _build_lessons_hint(user_text, max_lessons=3)
            if _lessons_hint:
                external_hint = (
                    f"{external_hint.strip()}\n\n{_lessons_hint}"
                    if (external_hint or "").strip()
                    else _lessons_hint
                )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Sanitize external_hint: remove leaked format instructions ──
    try:
        from core.brain.cot_strip import sanitize_external_hint

        external_hint = sanitize_external_hint(external_hint)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    _prompt_parts: Dict[str, Any] = {
        "system_prompt_for_llm": system_prompt_for_llm,
        "agent_inst": agent_inst,
        "agent_inst_collapse_stub": AGENT_INSTRUCTION_COLLAPSE_STUB,
        "intent_addon": intent_addon,
        "user_text": user_text,
        "telegram_reply_block": _telegram_reply_block,
        "user_id": user_id,
        "memory_facts": memory_facts,
        "recent_dialogue": recent_dialogue,
        "message_archive": _message_archive,
        "dialogue_summary": context.get("dialogue_summary") or "",
        "grounding_mini": grounding_mini,
        "document_intake_block": document_intake_block,
        "user_facts": user_facts,
        "routing_prefs_hint": context.get("routing_prefs_hint") or "",
        "tcmd_cat": _tcmd_cat,
        "plugin_manifest_prompts": _plugin_mf_prompts,
        "sess_first": _sess_first,
        "pteacher": _pteacher,
        "operator_rules": context.get("operator_rules_brain_addon") or "",
        "ephemeral_lessons": context.get("ephemeral_lessons_brain_addon") or "",
        "task_facts": task_facts,
        "knowledge_summary": knowledge_summary,
        "knowledge_hot": _kh_hot,
        "external_hint": external_hint,
        "vp_ctx": vp_ctx,
        "skill_name": skill_name,
        "image_intent": image_intent,
        "skill_output": skill_output,
        "skill_hint": skill_hint,
        "ocr_text": context.get("ocr_text", ""),
        "tools_mode": tools_mode,
        "tool_names": list(tools_info.keys()),
        "tool_names_full_index": _tool_names_full_index,
        "urls_in_message": urls_in_message,
        "group_chat_addon": _group_chat_addon,
        "topic_tracking": topic_tracking,
        "group_context": group_context,
        "user_facts_meta": user_facts_meta,
        "missing_facts": missing_facts,
        "auto_ask_hint": auto_ask_hint,
        "behavior_policy": behavior_policy,
        "goal_hints": goal_hints,
        "blended_stable": blended_stable,
        "goal_plan": goal_plan,
        "dialogue_state": dialogue_state,
        "scaffold_part": _scaffold_part,
        "tool_routing_hint": tool_routing_hint_str,
        # ── Digest FIX: stable session digest (≤ 300 chars) ──
        "session_digest": _get_session_digest_for_prompt(
            context.get("user_id", ""),
            context.get("group_id"),
            recent_dialogue=recent_dialogue,
        ),
        # ── Context budget: LLM видит лимиты ──
        "_budget_info": {
            "tier": _assembly_tier.value,
            "chars_limit": budget_for_tier(_assembly_tier),
        },
    }
    _tier_ru = describe_tier_ru(_assembly_tier)
    await _brain_progress(f"💭 Собираю промпт ({_tier_ru})…")
    # ── Tool-calls batching: when enabled, pass static_format_batched to prompt assembly ──
    try:
        from core.token_efficiency import tools_batch_enabled as _tb
        if _tb():
            from core.brain.constants import BRAIN_STATIC_FORMAT_BATCHED
            _prompt_parts["static_format"] = BRAIN_STATIC_FORMAT_BATCHED
            MONITOR.inc("tools_batch_enabled_total")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── KV‑cache split: стабильный user_message + динамический tail ──
    _prompt_intent = str(dialogue_state.get("last_intent") or "general") if isinstance(dialogue_state, dict) else "general"
    prompt, _kv_cache_tail, _pack_meta = assemble_split_with_budget(
        _assembly_tier, _prompt_parts,
        profile=_brain_profile, intent=_prompt_intent,
    )
    # ── Prewarm FIX: cold-start — first request uses static_head_only ──
    if context.get("session_is_new"):
        _prompt_parts_prewarm = {
            "system_prompt_for_llm": _prompt_parts.get("system_prompt_for_llm", ""),
            "agent_inst": _prompt_parts.get("agent_inst", ""),
            "user_text": _prompt_parts.get("user_text", ""),
            "static_format": _prompt_parts.get("static_format", ""),
            "static_tools": _prompt_parts.get("static_tools", ""),
        }
        prompt, _, _pack_meta = assemble_split_with_budget(
            _assembly_tier, _prompt_parts_prewarm,
            profile=_brain_profile, intent=_prompt_intent,
        )
        _kv_cache_tail = ""
        MONITOR.inc("prewarm_static_head_only_total")
    _full_combined = prompt + (_kv_cache_tail or "")
    _prompt_breakdown = prompt_runtime_breakdown(_full_combined)
    # ── Update _budget_info with actual estimate (для второго прохода) ──
    try:
        _est_tok = int(_prompt_breakdown.get("total_tokens_est") or max(1, len(_full_combined) // 4))
        _prompt_parts["_budget_info"]["tokens_est"] = _est_tok
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── LLM Compactor: сжатие диалога/документов через дешёвую модель ──
    _compactor_meta: Dict[str, Any] = {"compacted": False}
    _compaction_log: Dict[str, Any] = {}
    if _brain_profile != "batch":
        _compactor_collapse_level = int(_pack_meta.get("collapse_level", 0))
        if recent_dialogue:
            try:
                from core.compactor import (
                    build_compaction_log,
                    compact_dialogue_llm,
                    compact_document_llm,
                    compactor_min_dialogue_messages,
                    evaluate_compaction_triggers,
                    inject_dialogue_compact,
                    inject_document_compact,
                )

                _turn_idx = 0
                try:
                    _ds_ci = context.get("dialogue_state") if isinstance(context, dict) else None
                    if isinstance(_ds_ci, dict):
                        _turn_idx = int(_ds_ci.get("turn_index") or 0)
                except (TypeError, ValueError):
                    _turn_idx = 0
                _need_compact, _compact_eval = evaluate_compaction_triggers(
                    collapse_level=_compactor_collapse_level,
                    est_tokens=_est_tok,
                    dialogue_messages=recent_dialogue if isinstance(recent_dialogue, list) else None,
                    turn_index=_turn_idx,
                )
                _compactor_meta.update(_compact_eval)
                _min_msgs = compactor_min_dialogue_messages()
                if _need_compact:
                    _compacted_anything = False
                    # Compact recent dialogue (with protect_last_n)
                    if isinstance(recent_dialogue, list) and len(recent_dialogue) >= _min_msgs:
                        _compacted, _protected = await compact_dialogue_llm(_llm, recent_dialogue)
                        # Только если LLM-сжатие реально отработало:
                        # подменяем recent_dialogue на [сводка + protected].
                        # Если LLM вернул пустоту — recent_dialogue остаётся как был (все сообщения целы).
                        if _compacted:
                            inject_dialogue_compact(_prompt_parts, _compacted, _protected, _compactor_meta)
                            _compacted_anything = True

                    # Compact large document
                    _dib = document_intake_block
                    if _dib and isinstance(_dib, str) and len(_dib) > 2000:
                        _compacted_doc = await compact_document_llm(_llm, _dib)
                        if _compacted_doc:
                            inject_document_compact(_prompt_parts, _compacted_doc, _compactor_meta)
                            _compacted_anything = True

                    if _compacted_anything:
                        # Rebuild prompt from compacted parts
                        prompt, _kv_cache_tail, _collapsed_pack_meta = assemble_split_with_budget(
                            _assembly_tier, _prompt_parts,
                            profile=_brain_profile, intent=_prompt_intent,
                        )
                        _full_combined = prompt + (_kv_cache_tail or "")
                        _prompt_breakdown = prompt_runtime_breakdown(_full_combined)
                        try:
                            _compactor_meta["est_tokens_after"] = int(
                                _prompt_breakdown.get("total_tokens_est")
                                or max(1, len(_full_combined) // 4)
                            )
                        except (TypeError, ValueError):
                            pass
                        MONITOR.inc("compactor_triggered_total")
                _compaction_log = build_compaction_log(_compactor_meta)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Context Collapse Engine (token_efficiency.collapse) ──
    _collapse_meta: Dict[str, Any] = {"collapsed": False}
    if _brain_profile != "batch":
        try:
            from core.context_collapse import collapse_context
            _est_tokens = int(_prompt_breakdown.get("total_tokens_est") or max(1, len(_full_combined) // 4))
            _collapsed_prompt, _collapse_meta = collapse_context(
                prompt=_full_combined,
                est_tokens=_est_tokens,
                parts=_prompt_parts,
                recent_dialogue=recent_dialogue,
                message_archive=_message_archive,
                document_intake_block=document_intake_block,
                dialogue_summary=str(context.get("dialogue_summary") or ""),
            )
            if _collapse_meta.get("collapsed"):
                # Recompute split from collapsed parts
                prompt, _kv_cache_tail, _collapsed_pack_meta = assemble_split_with_budget(
                    _assembly_tier, _prompt_parts,
                    profile=_brain_profile, intent=_prompt_intent,
                )
                _pack_meta["collapse_level"] = max(int(_pack_meta.get("collapse_level") or 0), int(_collapsed_pack_meta.get("collapse_level") or 0))
                _full_combined = prompt + (_kv_cache_tail or "")
                _prompt_breakdown = prompt_runtime_breakdown(_full_combined)
                MONITOR.inc("context_collapse_total")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Prompt Budgeting (token_efficiency.budget) ──
    _brain_recent_limit = 0
    try:
        _brain_recent_limit = int(getattr(_get_profile_cfg(_brain_profile), "recent_count", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        _brain_recent_limit = 0
    _telemetry_extra: Dict[str, Any] = {
        "prompt_chars": int(_prompt_breakdown.get("total_chars") or len(_full_combined)),
        "prompt_tokens_est": int(_prompt_breakdown.get("total_tokens_est") or 0),
        "prompt_breakdown": _prompt_breakdown,
        "agent_pack": dict(_agent_pack_meta),
        "prompt_collapse_level": int(_pack_meta.get("collapse_level") or 0),
        "context_collapsed": _collapse_meta.get("collapsed", False),
        "context_collapse_meta": _collapse_meta,
        "tokens_cached": 0,
        "brain_recent_limit": _brain_recent_limit,
        "brain_profile": _brain_profile,
    }
    if _compaction_log:
        _telemetry_extra["compaction"] = _compaction_log
    _stash_brain_turn_telemetry(
        context,
        telemetry_extra=_telemetry_extra,
        brain_profile=_brain_profile,
        brain_recent_limit=_brain_recent_limit,
    )
    try:
        from core.token_efficiency import budget_enabled, budget_hard_limit_tokens
        if budget_enabled() and _brain_profile != "batch":
            # Skip hard-limit when image_slim — images inflate est_tokens
            # but the two-step vision pipeline already compacted the prompt
            if _assembly_tier == PromptAssemblyTier.IMAGE_SLIM:
                _telemetry_extra["budget_skipped_image"] = True
            else:
                _est_tok = int(_prompt_breakdown.get("total_tokens_est") or max(1, len(_full_combined) // 4))
                _hard_limit = budget_hard_limit_tokens()
                _telemetry_extra["budget_hard_limit"] = _hard_limit
                _telemetry_extra["budget_enabled"] = True
                if _est_tok > _hard_limit:
                    MONITOR.inc("budget_exceeded_total")
                    logger.warning(
                        "[brain] budget exceeded: est_tokens=%d > hard_limit=%d — triggering collapse",
                        _est_tok, _hard_limit,
                    )
                    try:
                        from core.context_collapse import collapse_context
                        _collapsed_prompt, _bc_meta = collapse_context(
                            prompt=_full_combined,
                            est_tokens=_est_tok,
                            parts=_prompt_parts,
                            recent_dialogue=recent_dialogue,
                            message_archive=_message_archive,
                            document_intake_block=document_intake_block,
                            dialogue_summary=str(context.get("dialogue_summary") or ""),
                        )
                        if _bc_meta.get("collapsed"):
                            prompt, _kv_cache_tail, _bc_pack_meta = assemble_split_with_budget(
                                _assembly_tier, _prompt_parts,
                                profile=_brain_profile, intent=_prompt_intent,
                            )
                            _pack_meta["collapse_level"] = max(int(_pack_meta.get("collapse_level") or 0), int(_bc_pack_meta.get("collapse_level") or 0))
                            _full_combined = prompt + (_kv_cache_tail or "")
                            _prompt_breakdown = prompt_runtime_breakdown(_full_combined)
                            _telemetry_extra["prompt_chars"] = int(_prompt_breakdown.get("total_chars") or len(_full_combined))
                            _telemetry_extra["prompt_tokens_est"] = int(_prompt_breakdown.get("total_tokens_est") or 0)
                            _telemetry_extra["budget_collapsed"] = True
                            MONITOR.inc("budget_collapse_triggered_total")
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                    # Reduce reasoning depth when budget exceeded
                    task_tier = _max_task_tier("shallow", task_tier)
                    _telemetry_extra["budget_reduced_reasoning_depth"] = True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Update _budget_info with actual estimates after assembly ──
    try:
        if _prompt_breakdown and not _telemetry_extra.get("budget_skipped_image"):
            _est_tok_now = int(_prompt_breakdown.get("total_tokens_est") or 0)
            _prompt_parts.setdefault("_budget_info", {})["used_est_tokens"] = _est_tok_now
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    _intent_conf: Optional[float] = None
    try:
        _intent_conf = float(predictive_hint.get("confidence")) if isinstance(predictive_hint, dict) else None
    except (TypeError, ValueError):
        _intent_conf = None
    llm_session_id, _kv_dbg = _resolve_sticky_session(
        user_id=user_id,
        group_id=context.get("group_id"),
        intent=str(dialogue_state.get("last_intent") or "general"),
        prompt_chars=len(prompt),
        intent_confidence=_intent_conf,
        user_text=user_text,
        profile=_brain_profile,
    )
    if isinstance(context, dict) and isinstance(_kv_dbg, dict) and _kv_dbg:
        context["kv_session_debug"] = _kv_dbg

    if isinstance(context, dict):
        try:
            from core.brain.context_budget import stash_context_budget_user_note

            stash_context_budget_user_note(
                context,
                prompt=prompt,
                system_prompt=system_prompt_for_llm,
                external_hint=external_hint or "",
            )
        except Exception as e:
            logger.debug("context_budget user note: %s", e)

    # ── KV Debug Trace: log prompt assembly + session state ──
    _system_hash = hashlib.sha256((str(_prof_primary.system_addon_first) + str(context.get("group_id", ""))).encode()).hexdigest()[:12]
    _prompt_dump = ""
    if _kv_prompt_dump_enabled():
        _prompt_dump = _kv_sanitize_for_log(
            f"=== SYSTEM PROMPT ===\n{system_prompt_for_llm}\n\n=== USER PROMPT ===\n{prompt}",
            max_chars=16000,
        )
    try:
        _trace = {
            "event": "prompt_assembled",
            "user_id": user_id,
            "group_id": context.get("group_id"),
            "session_id": llm_session_id,
            "kv_dbg": _kv_dbg,
            "prompt_chars": len(prompt),
            "prompt_tokens_est": _telemetry_extra.get("prompt_tokens_est", 0),
            "system_prompt_hash": _system_hash,
            "assembly_tier": _assembly_tier.value,
            "collapse_level": _pack_meta.get("collapse_level", 0),
            "hot_path_slim": hot_path_slim,
            "use_slim": use_slim,
            "budget": _pack_meta.get("budget", 0),
            "profile": _brain_profile,
        }
        if _prompt_dump:
            _trace["prompt_dump"] = _prompt_dump
        _record_kv_trace(_trace)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── KV-Session Reset (safety.yml: kv_session_reset_enabled) — second stage ──
    try:
        from core.safety_config import kv_session_reset_enabled
        if kv_session_reset_enabled():
            from core.dialog_state import get_kv_session_epoch
            _dialog_epoch2 = get_kv_session_epoch(
                user_id=str(user_id),
                group_id=context.get("group_id"),
            )
            if _dialog_epoch2 > 0:
                llm_session_id = f"{llm_session_id}.ds{_dialog_epoch2}"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    first_stage_vision: Optional[List[Tuple[str, str]]] = None
    if vision_parts and _env_flag("BRAIN_VISION_OPENROUTER", default=True):
        if not _env_flag("BRAIN_VISION_TWO_STEP", default=True):
            first_stage_vision = vision_parts
        elif not vision_precaption and _env_flag("BRAIN_VISION_FALLBACK_SINGLE_STAGE", default=True):
            first_stage_vision = vision_parts

    if first_stage_vision:
        await _brain_progress("👁 Разбираю изображение…", force=True)

    _tier_first_timeout: Optional[float] = None
    if hot_path_slim and not use_slim:
        try:
            ts = float((os.getenv("BRAIN_LLM_FREE_TIMEOUT_SHORT_SEC") or "0").strip() or "0")
        except ValueError:
            ts = 0.0
        if 5.0 <= ts <= 175.0:
            _tier_first_timeout = ts

    _first_max_tok = _brain_first_stage_max_tokens(user_text)
    # Profile-based override (if profile has a specific budget)
    _profile_tokens = _profile_first_stage_max_tokens(_brain_profile)
    if _profile_tokens > 0:
        _first_max_tok = max(_first_max_tok, _profile_tokens)
    if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
        _t1 = estimate_tiered_timeouts(
            tag="llm_first_stage",
            prompt=prompt,
            max_tokens=_first_max_tok,
            base_timeout=_tier_first_timeout,
            task_tier=task_tier,
        )
        _telegram_progress_set_timing(
            eta_sec=_estimate_eta_sec(
                max_tokens=_first_max_tok,
                task_tier=task_tier,
                prompt_len=len(prompt),
                stage="first",
                user_text_len=len(user_text),
            ),
            timeout_sec=float(_t1.get("timeout_upper_bound_sec") or 0.0),
            from_now=True,
            record_assembly=True,
        )
    else:
        _telegram_progress_set_timing(
            eta_sec=_estimate_eta_sec(
                max_tokens=_first_max_tok,
                task_tier=task_tier,
                prompt_len=len(prompt),
                stage="first",
                user_text_len=len(user_text),
            ),
            timeout_sec=float(_tier_first_timeout) if _tier_first_timeout else float(DEFAULT_TIMEOUT_SEC),
            from_now=True,
            record_assembly=True,
        )

    await _brain_progress("✍️ Первый запрос к модели…", force=True)

    if _env_flag("BRAIN_AGENT_SYSTEM_EN", default=False):
        _sys_first_body = (
            "You are an agent: call tools or answer directly. "
            "User sees only the final answer — no long meta, no 'the user writes…', no English chain-of-thought. "
            "Without TOOL_CALL: short, on-point reply in the user's language (Russian unless they use another). "
            "If image/vision_precaption and user asks to recolor/retouch the file — you cannot change photo bytes; "
            "say so briefly and suggest a service or IMAGE_COLORIZATION_BACKEND; describe the image if you see it."
        )
    else:
        _sys_first_body = (
            "Ты агент: вызываешь инструменты или отвечаешь сам. "
            "Пользователю только итог: без длинного разбора, без «пользователь пишет…», "
            "без английских рассуждений вроде «we need to…». "
            "Если без TOOL_CALL — сразу короткий ответ по делу на языке пользователя. "
            "Если есть изображение или vision_precaption и просят раскрасить/отретушировать файл — "
            "ты не меняешь бинарник фото; кратко скажи это и предложи сервис или локальный IMAGE_COLORIZATION_BACKEND, "
            "опиши снимок если видишь."
        )
    _sys_first = merge_system(
        _sys_first_body,
        _TELEGRAM_PLAIN_REPLY_RULE,
        _da_sys_extra,
        _prof_primary.system_addon_first,
    )
    _temp_first = clamp_temperature(0.4, _prof_primary.temperature_first_delta)

    async def _run_gate_corrective_pass(draft_reply: str) -> str:
        reply0 = str(draft_reply or "").strip()
        if not reply0 or not _auto_gate_note:
            return draft_reply
        if not _env_flag("BRAIN_AUTO_REASONING_GATE_CORRECTION", default=True):
            return draft_reply
        try:
            max_iters = int((os.getenv("BRAIN_AUTO_REASONING_GATE_MAX_ITERS") or "1").strip())
        except ValueError:
            max_iters = 1
        max_iters = max(0, min(max_iters, 1))
        if max_iters == 0:
            return draft_reply
        fixed_reply = reply0
        for _ in range(max_iters):
            MONITOR.inc("auto_reasoning_gate_correction_total")
            corr_prompt = f"""
Исправь ответ по авто-gate проверкам.

Gate-сигналы: {_auto_gate_note}
Исходное сообщение пользователя:
{user_text}

Черновой ответ:
{fixed_reply}

Требования:
1) Убери логические дыры и пропуски шагов.
2) Не нарушай ограничения пользователя.
3) Без внутренних рассуждений и без TOOL_CALL.
4) Коротко и по делу.
"""
            try:
                if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
                    corr = await llm_generate_tiered(
                        _llm,
                        tag="llm_gate_correction",
                        prompt=corr_prompt,
                        system_prompt=_sys_first,
                        max_tokens=700,
                        temperature=0.2,
                        base_timeout=None,
                        task_tier=task_tier,
                        telemetry_tag="brain_gate_correction",
                        telemetry_extra=_telemetry_extra,
                        session_id=llm_session_id,
                        conversation_id=llm_session_id,
                    )
                else:
                    corr = await with_timeout(
                        _llm.generate(
                            prompt=corr_prompt,
                            system_prompt=_sys_first,
                            max_tokens=700,
                            temperature=0.2,
                            telemetry_tag="brain_gate_correction",
                            telemetry_extra=_telemetry_extra,
                            session_id=llm_session_id,
                            conversation_id=llm_session_id,
                        ),
                        timeout_sec=DEFAULT_TIMEOUT_SEC,
                        tag="llm_gate_correction",
                    )
                if corr.get("error"):
                    return fixed_reply
                fixed = _strip_leaked_cot(
                    _safe_text(corr.get("content", "")),
                    extra_markers_en=_prof_secondary.cot_extra_markers_en,
                    extra_markers_ru=_prof_secondary.cot_extra_markers_ru,
                )
                if (fixed or "").strip() and not _looks_like_repetition_glitch(fixed):
                    MONITOR.inc("auto_reasoning_gate_correction_ok_total")
                    fixed_reply = fixed
                else:
                    return fixed_reply
            except Exception:
                return fixed_reply
        return fixed_reply

    if _env_flag("BRAIN_LOG_PROMPT_METRICS", default=True):
        _tot = len(prompt) + len(_sys_first)
        logger.info(
            "[brain] prompt_metrics stage=first tier=%s pack=%s policy=%s model_profile=%s chars_prompt=%s chars_system=%s est_tokens~=%s tools=%s budget_limit=%s budget_enabled=%s",
            tier_label_for_metrics(_assembly_tier),
            _pack_meta,
            snapshot_context_policy(context if isinstance(context, dict) else {}),
            _prof_primary.match_label,
            len(prompt),
            len(_sys_first),
            max(1, _tot // 4),
            len(tools_info),
            _telemetry_extra.get("budget_hard_limit", "-"),
            _telemetry_extra.get("budget_enabled", False),
        )

    allowed_tool_names = set(tools_info.keys())
    _first_stage = await _run_first_stage_llm(
        user_id=user_id,
        prompt=prompt,
        sys_first=_sys_first,
        first_max_tok=_first_max_tok,
        temp_first=_temp_first,
        first_stage_vision=first_stage_vision,
        tier_first_timeout=_tier_first_timeout,
        task_tier=task_tier,
        telemetry_extra=_telemetry_extra,
        llm_session_id=llm_session_id,
        kv_cache_tail=_kv_cache_tail,
        brain_profile=_brain_profile,
        prof_primary=_prof_primary,
        allowed_tool_names=allowed_tool_names,
        context=context,
    )
    first = _first_stage.first
    first_content = _first_stage.first_content
    if _first_stage.has_llm_error:
        logger.warning("[brain] first stage fallback: %s", first.get("error"))
        reply = _natural_fallback_response("llm_error", user_id, user_text)
        try:
            if not skip_memory_writes:
                await get_memory().on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        return reply

    tool_call, _batched_tool_calls = _resolve_tool_calls_from_first_content(first_content)
    # 6. Если инструмент не нужен — обычный ответ
    if not tool_call:
        reply = _strip_leaked_tool_call_markup(first_content)
        try:
            from core.brain.code_empty_recovery import (
                apply_code_delivery_if_needed,
                user_requests_code,
            )

            if user_requests_code(user_text):
                reply = apply_code_delivery_if_needed(user_text, reply or "")
        except Exception as e:
            logger.debug("code_delivery_any_profile: %s", e)
        if _brain_profile in ("code_generation", "code_debug"):
            try:
                from core.brain.code_empty_recovery import (
                    looks_like_code_payload,
                    looks_like_internal_code_monologue,
                    resolve_code_delivery_fallback,
                    user_requests_code,
                )
                from core.brain.response_finalize import looks_like_prompt_instruction_leak

                if (reply or "").strip() and not looks_like_code_payload(reply):
                    _schema_leak = False
                    try:
                        from core.brain.schema_leak_strip import looks_like_tool_schema_leak

                        _schema_leak = looks_like_tool_schema_leak(reply)
                    except Exception as e:
                        logger.debug("schema_leak check: %s", e)
                    if (
                        looks_like_prompt_instruction_leak(reply)
                        or looks_like_internal_code_monologue(reply)
                        or _schema_leak
                    ):
                        logger.warning(
                            "[brain] code profile leak/monologue/schema discarded (profile=%s)",
                            _brain_profile,
                        )
                        reply = ""
                if (reply or "").strip():
                    from core.brain.code_empty_recovery import code_reply_incomplete

                    if code_reply_incomplete(user_text, reply):
                        _code_fb = resolve_code_delivery_fallback(user_text)
                        if _code_fb:
                            reply = _code_fb
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        if not (reply or "").strip():
            logger.warning(
                "[brain] first stage empty content (model=%s profile=%s)",
                first.get("model"),
                _brain_profile,
            )
            try:
                from core.brain.code_empty_recovery import try_recover_empty_code_reply
                from core.brain.general_empty_recovery import try_recover_empty_general_reply

                recovered = await try_recover_empty_code_reply(
                    llm=_llm,
                    user_text=user_text,
                    brain_profile=_brain_profile,
                    first_result=first,
                    task_tier=task_tier,
                    telemetry_extra=_telemetry_extra,
                    llm_session_id=llm_session_id,
                )
                if (recovered or "").strip():
                    reply = recovered
                if not (recovered or "").strip():
                    recovered = await try_recover_empty_general_reply(
                        llm=_llm,
                        user_text=user_text,
                        brain_profile=_brain_profile,
                        first_result=first,
                        task_tier=task_tier,
                        telemetry_extra=_telemetry_extra,
                        llm_session_id=llm_session_id,
                        recent_dialogue=recent_dialogue,
                    )
                    if (recovered or "").strip():
                        reply = recovered
            except Exception as e:
                logger.debug("empty_reply_recovery: %s", e)
            if not (reply or "").strip():
                try:
                    from core.empty_reply_recovery import recover_empty_chat_reply

                    _er_ctx: Dict[str, Any] = {
                        "user_id": user_id,
                        "group_id": context.get("group_id"),
                        "behavior_record": _persisted_short,
                        "recent_messages": recent_dialogue,
                    }
                    _er = await recover_empty_chat_reply(
                        user_text=user_text,
                        context=_er_ctx,
                    )
                    if (_er or "").strip():
                        reply = _er.strip()
                        MONITOR.inc("brain_empty_reply_slot_recovery_total")
                except Exception as e:
                    logger.debug("pipeline empty_reply_recovery: %s", e)
            if not (reply or "").strip():
                try:
                    from core.brain.code_empty_recovery import (
                        resolve_code_delivery_fallback,
                        user_requests_code,
                    )

                    if user_requests_code(user_text):
                        _code_fb = resolve_code_delivery_fallback(user_text)
                        if _code_fb:
                            reply = _code_fb
                except Exception as e:
                    logger.debug("code_delivery_fallback: %s", e)
            if not (reply or "").strip():
                reply = _natural_fallback_response("empty_llm", user_id, user_text)
        if _looks_like_repetition_glitch(reply) or _looks_like_garbage_json(reply):
            reply = _natural_fallback_response("llm_error", user_id, user_text)

        # (multi-chunk removed — completeness guard at end of call_brain handles all profiles)
        try:
            from core.brain.reasoning_loop_controller import (
                run_reasoning_loop_text_only,
                wants_reasoning_loop,
            )

            if wants_reasoning_loop(user_text, context, task_tier) and (reply or "").strip():
                await _brain_progress("🧠 Проверка и доработка ответа…", force=True)
                reply = await run_reasoning_loop_text_only(
                    user_text=user_text,
                    draft_reply=reply,
                    task_tier=task_tier,
                    telemetry_extra=_telemetry_extra,
                    llm_session_id=llm_session_id,
                    system_for_passes=_sys_first,
                    prof_secondary=_prof_secondary,
                )
        except Exception as e:
            logger.debug("reasoning_loop: %s", e)
        # ── Self-verify active (только deep) — перед gate, чтобы не дублировать LLM вызов ──
        ver = "ok"
        if _should_self_verify(_brain_profile, need_memory=_need_memory) and (reply or "").strip():
            _sv_clock = _clock_block or format_clock_hint_for_llm(effective_tz=_eff_tz_shared, telegram_message_unix=_tg_i_shared)
            _sv_user_name = str(user_facts.get("name") or "").strip()
            ver = await _self_verify(
                reply, user_text, _llm,
                clock_info=_sv_clock,
                user_name=_sv_user_name,
            )
            if ver.startswith("fix:"):
                fix_text = ver[4:].strip()
                if fix_text and _self_verify_fix_quality(fix_text):
                    reply = fix_text
                    try:
                        if not skip_memory_writes:
                            await get_memory().on_after_response(user_id, reply)
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                    _record_performance({"profile": _brain_profile, "self_verify_fix_applied": True})
                    # Сохраняем успешный запрос как эталон
                    try:
                        if len((user_text or "").strip()) > 20:
                            await _save_successful_query(
                                user_text=user_text,
                                profile=_brain_profile,
                                tools_count=len(tools_info) if isinstance(tools_info, dict) else 0,
                                need_memory=_need_memory,
                                need_verify=True,
                            )
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                    return reply
                elif fix_text:
                    logger.info("[self_verify] fix rejected by quality check, keeping original")
                    record_error_event(
                        component="self_verify",
                        message="bad_fix_rejected",
                        extra={"model": _self_verify_model_id(), "user_text": user_text[:100]},
                    )
                    _record_bad_fix(profile=_brain_profile, model=_self_verify_model_id())
                    # Self-Learning: генерируем и сохраняем урок из ошибки
                    try:
                        _new_lesson = await _reflect_on_error(
                            user_text=user_text,
                            original_reply=reply,
                            bad_fix=fix_text,
                            profile=_brain_profile,
                            model=_brain_model_primary,
                            self_verify_model=_self_verify_model_id(),
                            rejection_reason="quality_check",
                            llm=_llm,
                        )
                        if _new_lesson:
                            await _SelfLearningLessonManager.get_instance().store_lesson(_new_lesson)
                            # Сохраняем в task_profiles с need_memory=True (reflexion-урок)
                            try:
                                if len((user_text or "").strip()) > 20:
                                    await _save_successful_query(
                                        user_text=user_text,
                                        profile=_brain_profile,
                                        tools_count=len(tools_info) if isinstance(tools_info, dict) else 0,
                                        need_memory=True,
                                        need_verify=True,
                                    )
                            except Exception as e:
                                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                else:
                    reply = await _retry_with_fix_hint(
                        user_text, reply, ver, _llm,
                        system_prompt_for_llm=system_prompt_for_llm,
                        kv_cache_tail=_kv_cache_tail,
                    )
        # ── Сохранить успешный запрос как эталон ──
        try:
            if ver == "ok" and len((user_text or "").strip()) > 20:
                _tools_count = len(tools_info) if isinstance(tools_info, dict) else 0
                await _save_successful_query(
                    user_text=user_text,
                    profile=_brain_profile,
                    tools_count=_tools_count,
                    need_memory=_need_memory,
                    need_verify=True,
                )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        # ── Пассивное обучение: LLM в фоне (fire-and-forget) ──
        try:
            from core.brain.router_classifier import passive_learn as _passive_learn
            if ver == "ok":
                asyncio.ensure_future(_passive_learn(
                    user_text=user_text,
                    heuristic_profile=_heuristic_profile,
                    llm=_llm,
                    context=context,
                ))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        # Gate corrective pass (self-verify с quality fix уже вернул ответ выше)
        reply = await _run_gate_corrective_pass(reply)
        reply = _news_prefetch_fallback(reply)
        if auto_ask_hint and "?" not in reply:
            reply = f"{reply}\n\n{auto_ask_hint}"
        if missing_facts and not _no_service_clar:
            try:
                from core.clarification_inline_keyboard import (
                    fact_auto_ask_keyboard_rows,
                    merge_telegram_inline_rows,
                )

                _ask_rows = fact_auto_ask_keyboard_rows(missing_facts)
                if _ask_rows:
                    merge_telegram_inline_rows(context, _ask_rows)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        try:
            reply = _persona_apply_polished(user_id, reply, user_text=user_text)
            if not (reply or "").strip():
                reply = _natural_fallback_response("empty_llm", user_id, user_text)
            if not skip_memory_writes:
                await get_memory().on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        if not (reply or "").strip():
            reply = _natural_fallback_response("empty_llm", user_id, user_text)
        reply = _maybe_compact_mcq_reply_for_telegram(user_text, reply)
        # ── Self-Learning: validate injected lessons against response ──
        if _injected_lessons:
            try:
                await _validate_lessons_against_response(_injected_lessons, reply, ver)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        return reply

    # 7. Выполняем инструмент
    _tool_exec = await _execute_brain_tool(
        tool_call,
        user_id=user_id,
        context=context,
        user_facts=user_facts,
        task_facts=task_facts,
        run_tool=run_tool,
    )
    tool_name = _tool_exec.tool_name
    tool_args = _tool_exec.tool_args
    tool_result = _tool_exec.tool_result

    # 8. Второй вызов LLM — финальный ответ с учётом результата инструмента
    system_prompt_for_second = merge_system(
        system_prompt,
        _prof_secondary.system_addon_first,
        _prof_secondary.system_addon_second,
    )
    _sys_second = merge_system(
        "Ты ассистент: объясняешь результат инструмента пользователю. "
        "Только итоговый ответ, без внутреннего разбора и без английских «рассуждений вслух».",
        _TELEGRAM_PLAIN_REPLY_RULE,
        _prof_secondary.system_addon_second,
    )
    _temp_second = clamp_temperature(0.5, _prof_secondary.temperature_second_delta)

    second_prompt = f"""
Системная инструкция:
{system_prompt_for_second}

Ты уже вызвал инструмент {tool_name} с аргументами {tool_args}.
Результат инструмента:
{_safe_json_dumps(tool_result)}

Теперь дай пользователю финальный ответ, учитывая этот результат.
Если результат инструмента — ошибка или пусто, не имитируй успех: коротко объясни и один конкретный следующий шаг (что уточнить, какую ссылку прислать, команда админа), без общих «попробуйте ещё раз» без содержания.
Внутренний порядок мыслей не показывай; только итог пользователю (цель → что дал инструмент → вывод).
Учитывай goal_plan={goal_plan} и style_hints={style_hints}.
Учитывай predictive_hint={predictive_hint} и goal_hints={goal_hints}.
Сохраняй micro_emotion_style={micro_emotion_style}.
Используй user_facts={user_facts}, если это помогает (время/погода/валюта/стиль ответа).
Если выбран skill={skill_name}, учти skill_output={skill_output} и skill_hint={skill_hint}.
Если есть external_hint={external_hint}, используй его как приоритетный источник, иначе сделай fallback-рассуждение.
Технические хинты (не выводи их пользователю): thinking_markers={thinking_markers}, typing_hooks={typing_hooks}.

Сообщение пользователя:
{user_text}
"""

    await _brain_progress("🔧 Второй проход…", force=True)
    _second_max_tok = _brain_second_stage_max_tokens(user_text)
    if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
        _t2 = estimate_tiered_timeouts(
            tag="llm_second_stage",
            prompt=second_prompt,
            max_tokens=_second_max_tok,
            base_timeout=None,
            task_tier=task_tier,
        )
        _telegram_progress_set_timing(
            eta_sec=_estimate_eta_sec(
                max_tokens=_second_max_tok,
                task_tier=task_tier,
                prompt_len=len(second_prompt),
                stage="second",
                user_text_len=len(user_text),
            ),
            timeout_sec=float(_t2.get("timeout_upper_bound_sec") or 0.0),
            from_now=True,
        )
    else:
        _telegram_progress_set_timing(
            eta_sec=_estimate_eta_sec(
                max_tokens=_second_max_tok,
                task_tier=task_tier,
                prompt_len=len(second_prompt),
                stage="second",
                user_text_len=len(user_text),
            ),
            timeout_sec=float(DEFAULT_TIMEOUT_SEC),
            from_now=True,
        )

    try:
        if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
            second = await llm_generate_tiered(
                _llm,
                tag="llm_second_stage",
                prompt=second_prompt,
                system_prompt=_sys_second,
                max_tokens=_second_max_tok,
                temperature=_temp_second,
                base_timeout=None,
                task_tier=task_tier,
                telemetry_tag="brain_second",
                telemetry_extra=_telemetry_extra,
                session_id=llm_session_id,
                conversation_id=llm_session_id,
            )
        else:
            second = await with_timeout(
                _llm.generate(
                    prompt=second_prompt,
                    system_prompt=_sys_second,
                        max_tokens=_second_max_tok,
                    temperature=_temp_second,
                    telemetry_tag="brain_second",
                    telemetry_extra=_telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                ),
                timeout_sec=DEFAULT_TIMEOUT_SEC,
                tag="llm_second_stage",
            )
    except Exception as e:
        logger.error("[brain] second llm call failed: %s", e)
        record_error_event("brain", "second_llm_generate", exc=e, extra={"user_id": user_id})
        second = {"error": str(e), "content": ""}

    if second.get("error"):
        logger.warning("[brain] second stage fallback: %s", second.get("error"))
        # Track cached tokens from LLM step-cache (second stage)
        if second.get("cached"):
            _telemetry_extra["tokens_cached"] = int(_telemetry_extra.get("tokens_cached") or 0) + len(second_prompt) // 4
            MONITOR.inc("brain_prompt_cache_hit_total")
        reply = ""
        if (
            tool_name == "UrlFetch.fetch_page"
            and isinstance(tool_result, dict)
            and tool_result.get("ok")
            and (str(tool_result.get("text") or "").strip())
        ):
            tt = str(tool_result.get("text") or "").strip()
            title = str(tool_result.get("title") or "").strip()
            head = f"{title}\n\n" if title else ""
            clipped_tt = _clip_soft(tt, 4500)
            reply = head + clipped_tt
            if len(tt) > len(clipped_tt):
                reply += "\n\n… (показан фрагмент; вторичная модель не ответила — см. полный текст в ответе UrlFetch.)"
        if not reply.strip():
            reply = _natural_fallback_response("llm_error", user_id, user_text)
        try:
            if not skip_memory_writes:
                await get_memory().on_after_response(user_id, reply)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        return reply

    raw_second = _safe_text(second.get("content", ""))
    reply = ""
    try:
        chain_max = int((os.getenv("BRAIN_TOOL_CHAIN_MAX") or "0").strip() or "0")
    except ValueError:
        chain_max = 0
    tc2 = _parse_tool_call(raw_second)
    chain_ok = (
        chain_max >= 1
        and bool(tc2)
        and isinstance(tool_result, dict)
        and not tool_result.get("error")
        and bool(tool_name)
        and not second.get("error")
    )
    t2n = ""
    t2a: Dict[str, Any] = {}
    if chain_ok:
        t2n_raw = tc2.get("name")
        t2a_raw = tc2.get("args", {}) or {}
        if not isinstance(t2n_raw, str) or not t2n_raw.strip():
            chain_ok = False
        elif not isinstance(t2a_raw, dict):
            chain_ok = False
        else:
            t2n = t2n_raw.strip()
            t2a = dict(t2a_raw)
            if t2n == tool_name:
                chain_ok = False
            elif tool_call_validation_error(tc2, allowed_tool_names):
                chain_ok = False
            else:
                _tid2 = t2a.get("user_id")
                if _tid2 is None or (isinstance(_tid2, str) and not _tid2.strip()):
                    t2a["user_id"] = user_id
                t2a = normalize_brain_tool_args(t2n, t2a)
    if chain_ok:
        try:
            await _brain_progress("🔧 Следующий инструмент…")
            _c2 = _tool_dedup_lookup(user_id, t2n, t2a)
            if _c2 is not None:
                MONITOR.inc("brain_tool_dedup_hit_total")
                tr2 = _c2
            else:
                tr2 = await run_tool(t2n, **t2a)
                _tool_dedup_store(user_id, t2n, t2a, tr2)
        except Exception as e:
            logger.warning("[brain] tool chain: %s", e)
            tr2 = {"error": str(e)}
        _emit_brain_tool_finished(user_id, context, t2n, tr2)
        agg_tools = _safe_json_dumps(
            {
                "first_tool": {"name": tool_name, "args": tool_args, "result": tool_result},
                "second_tool": {"name": t2n, "args": t2a, "result": tr2},
            }
        )
        third_user = (
            f"Сообщение пользователя:\n{user_text}\n\nУже выполнены инструменты:\n{agg_tools}\n\n"
            "Сформируй финальный ответ пользователю одним текстом (без TOOL_CALL), "
            "свяжи оба результата кратко и по делу."
        )
        _third_max_tok = 900
        if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
            _t3 = estimate_tiered_timeouts(
                tag="llm_tool_chain",
                prompt=third_user,
                max_tokens=_third_max_tok,
                base_timeout=None,
                task_tier=task_tier,
            )
            _telegram_progress_set_timing(
                eta_sec=_estimate_eta_sec(
                    max_tokens=_third_max_tok,
                    task_tier=task_tier,
                    prompt_len=len(third_user),
                    stage="tool_chain",
                    user_text_len=len(user_text),
                ),
                timeout_sec=float(_t3.get("timeout_upper_bound_sec") or 0.0),
                from_now=True,
            )
        else:
            _telegram_progress_set_timing(
                eta_sec=_estimate_eta_sec(
                    max_tokens=_third_max_tok,
                    task_tier=task_tier,
                    prompt_len=len(third_user),
                    stage="tool_chain",
                    user_text_len=len(user_text),
                ),
                timeout_sec=float(DEFAULT_TIMEOUT_SEC),
                from_now=True,
            )
        try:
            if _env_flag("BRAIN_LLM_TIERED_RETRY", default=True):
                third = await llm_generate_tiered(
                    _llm,
                    tag="llm_tool_chain",
                    prompt=third_user,
                    system_prompt=_sys_second,
                    max_tokens=_third_max_tok,
                    temperature=_temp_second,
                    task_tier=task_tier,
                    telemetry_tag="brain_tool_chain",
                    telemetry_extra=_telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                )
            else:
                third = await with_timeout(
                    _llm.generate(
                        prompt=third_user,
                        system_prompt=_sys_second,
                        max_tokens=900,
                        temperature=_temp_second,
                        telemetry_tag="brain_tool_chain",
                        telemetry_extra=_telemetry_extra,
                        session_id=llm_session_id,
                        conversation_id=llm_session_id,
                    ),
                    timeout_sec=DEFAULT_TIMEOUT_SEC,
                    tag="llm_tool_chain",
                )
            if not third.get("error"):
                reply = _strip_leaked_cot(
                    _safe_text(third.get("content", "")),
                    extra_markers_en=_prof_secondary.cot_extra_markers_en,
                    extra_markers_ru=_prof_secondary.cot_extra_markers_ru,
                )
                if (reply or "").strip():
                    MONITOR.inc("brain_tool_chain_ok_total")
        except Exception as e:
            logger.debug("tool chain third llm: %s", e)
    if not (reply or "").strip():
        reply = _strip_leaked_cot(
            raw_second,
            extra_markers_en=_prof_secondary.cot_extra_markers_en,
            extra_markers_ru=_prof_secondary.cot_extra_markers_ru,
        )
        reply = _strip_leaked_tool_call_markup(reply)
    if _looks_like_repetition_glitch(reply) or (reply and _looks_like_garbage_json(reply)):
        reply = ""
    if not reply.strip() and isinstance(tool_result, dict) and tool_result.get("error"):
        reply = _natural_fallback_response("tool_error", user_id, user_text)
    elif not reply.strip():
        logger.warning("[brain] second stage empty content (tool=%s)", tool_name)
    _news_fb_before = (reply or "").strip()
    reply = _news_prefetch_fallback(reply)
    if not _news_fb_before and (reply or "").strip():
        logger.info("[brain] news search-body fallback applied after empty/refusal LLM")
    if not (reply or "").strip():
        try:
            from core.brain.general_empty_recovery import try_recover_empty_general_reply

            recovered = await try_recover_empty_general_reply(
                llm=_llm,
                user_text=user_text,
                brain_profile=_brain_profile,
                first_result=second if isinstance(second, dict) else {},
                task_tier=task_tier,
                telemetry_extra=_telemetry_extra,
                llm_session_id=llm_session_id,
                recent_dialogue=recent_dialogue,
            )
            reply = recovered if (recovered or "").strip() else _natural_fallback_response(
                "empty_llm", user_id, user_text
            )
        except Exception as e:
            logger.debug("general_empty_recovery (2nd): %s", e)
            reply = _natural_fallback_response("empty_llm", user_id, user_text)
    if _looks_like_repetition_glitch(reply):
        reply = _natural_fallback_response("llm_error", user_id, user_text)
    # ── Self-verify active (только deep) — перед gate, чтобы не дублировать LLM вызов ──
    ver = "ok"
    if _should_self_verify(_brain_profile, need_memory=_need_memory) and (reply or "").strip():
        ver = await _self_verify(
            reply, user_text, _llm,
            clock_info=_clock_block,
            user_name=str(user_facts.get("name") or "").strip(),
        )
        if ver.startswith("fix:"):
            fix_text = ver[4:].strip()
            if fix_text and _self_verify_fix_quality(fix_text):
                try:
                    if not skip_memory_writes:
                        await get_memory().on_after_response(user_id, fix_text)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                _record_performance({"profile": _brain_profile, "self_verify_fix_applied": True})
                # Сохраняем успешный запрос как эталон
                try:
                    if len((user_text or "").strip()) > 20:
                        await _save_successful_query(
                            user_text=user_text,
                            profile=_brain_profile,
                            tools_count=len(tools_info) if isinstance(tools_info, dict) else 0,
                            need_memory=_need_memory,
                            need_verify=True,
                        )
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                return fix_text
            elif fix_text:
                logger.info("[self_verify] fix rejected by quality check (2nd stage), keeping original")
                record_error_event(
                    component="self_verify",
                    message="bad_fix_rejected",
                    extra={"model": _self_verify_model_id(), "user_text": user_text[:100]},
                )
                _record_bad_fix(profile=_brain_profile, model=_self_verify_model_id())
                # Self-Learning: генерируем и сохраняем урок из ошибки
                try:
                    _new_lesson = await _reflect_on_error(
                        user_text=user_text,
                        original_reply=reply,
                        bad_fix=fix_text,
                        profile=_brain_profile,
                        model=_brain_model_primary,
                        self_verify_model=_self_verify_model_id(),
                        rejection_reason="quality_check",
                        llm=_llm,
                    )
                    if _new_lesson:
                        await _SelfLearningLessonManager.get_instance().store_lesson(_new_lesson)
                        # Сохраняем в task_profiles с need_memory=True (reflexion-урок)
                        try:
                            if len((user_text or "").strip()) > 20:
                                await _save_successful_query(
                                    user_text=user_text,
                                    profile=_brain_profile,
                                    tools_count=len(tools_info) if isinstance(tools_info, dict) else 0,
                                    need_memory=True,
                                    need_verify=True,
                                )
                        except Exception as e:
                            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
            else:
                reply = await _retry_with_fix_hint(
                    user_text, reply, ver, _llm,
                    system_prompt_for_llm=system_prompt_for_llm,
                    kv_cache_tail=_kv_cache_tail,
                )
    # ── Сохранить успешный запрос как эталон (tool-call path) ──
    try:
        if ver == "ok" and len((user_text or "").strip()) > 20:
            _tools_count = len(tools_info) if isinstance(tools_info, dict) else 0
            await _save_successful_query(
                user_text=user_text,
                profile=_brain_profile,
                tools_count=_tools_count,
                need_memory=_need_memory,
                need_verify=True,
            )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # Gate corrective pass (self-verify с quality fix уже вернул ответ выше)
    reply = await _run_gate_corrective_pass(reply)
    reply = _news_prefetch_fallback(reply)
    try:
        reply = _persona_apply_polished(user_id, reply, user_text=user_text)
        if not (reply or "").strip():
            reply = _natural_fallback_response("empty_llm", user_id, user_text)
        if not skip_memory_writes:
            await get_memory().on_after_response(user_id, reply)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    if not (reply or "").strip():
        reply = _natural_fallback_response("empty_llm", user_id, user_text)
    reply = _maybe_compact_mcq_reply_for_telegram(user_text, reply)

    # ── Self-Learning: validate injected lessons against response ──
    if _injected_lessons:
        try:
            await _validate_lessons_against_response(_injected_lessons, reply, ver)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── KV Debug Trace: final response ──
    try:
        _record_kv_trace({
            "event": "brain_complete",
            "user_id": user_id,
            "group_id": context.get("group_id"),
            "session_id": llm_session_id,
            "reply_chars": len(reply),
            "reply_truncated": len(reply) > 2000,
        })
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        _record_performance({"profile": _brain_profile, "latency_ms": 0})
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    if isinstance(context, dict):
        try:
            ds_out = context.setdefault("dialogue_state", {})
            if isinstance(ds_out, dict):
                ds_out["last_brain_profile"] = _brain_profile
                ds_out["brain_profile"] = _brain_profile
                try:
                    ds_out["prompt_tokens_est"] = int(_telemetry_extra.get("prompt_tokens_est") or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    ds_out["brain_recent_limit"] = int(_telemetry_extra.get("brain_recent_limit") or 0)
                except (TypeError, ValueError):
                    pass
                _stash_brain_turn_telemetry(
                    context,
                    telemetry_extra=_telemetry_extra,
                    brain_profile=_brain_profile,
                    brain_recent_limit=int(_telemetry_extra.get("brain_recent_limit") or 0),
                )
                if not str(ds_out.get("dialogue_lane") or "").strip():
                    try:
                        from core.brain.dialogue_lane import resolve_lane_label

                        ds_out["dialogue_lane"] = resolve_lane_label(
                            brain_profile=_brain_profile,
                            translation_turn=bool(_translation_turn),
                            tools_used=bool(ds_out.get("last_tool_steps") or ds_out.get("tool_steps")),
                        )
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    # ── Completeness guard: проверить, что ответил на ВСЕ вопросы ──
    if (reply or "").strip() and len(reply) > 30:
        try:
            # Собираем все пункты из user_text (строки, а не только ?/команды)
            user_lines = [l.strip() for l in user_text.split("\n") if l.strip()]
            # Отсекаем заголовок "Ответь по списку на все вопросы:" или подобный
            input_items = []
            header_skipped = False
            for line in user_lines:
                stripped = line.strip().rstrip(":")
                if not header_skipped and (stripped.lower().startswith("ответь") or stripped.lower().startswith("список")):
                    header_skipped = True
                    continue
                # Учитываем any non-tiny line as a request
                if len(line) > 1:
                    input_items.append(line)

            # Считаем ответы: строки с нумерацией 1. 2. 3.
            reply_nums = re.findall(r'^\d+[\.\)]\s', reply, re.MULTILINE)
            answer_count = len(reply_nums)

            if answer_count < len(input_items) and len(input_items) >= 3:
                remaining = len(input_items) - answer_count
                logger.info("[completeness] missing %d/%d answers, chunking...", remaining, len(input_items))
                # Чанкуем по 8 для генерации
                MAX_CHUNK = 8
                for chunk_start in range(answer_count, len(input_items), MAX_CHUNK):
                    chunk_end = min(chunk_start + MAX_CHUNK, len(input_items))
                    chunk_items = input_items[chunk_start:chunk_end]
                    prompt_chunk = (
                        f"Продолжи отвечать на вопросы списка. "
                        f"Не повторяй уже отвеченное. Пиши ТОЛЬКО новые ответы, нумеруя их.\n\n"
                    )
                    for i, item in enumerate(chunk_items, start=chunk_start + 1):
                        prompt_chunk += f"{i}. {item}\n"
                    prompt_chunk += "\nОтвечай нумерованным списком. Не останавливайся, пока не ответишь на все."
                    try:
                        cont_call = await llm_generate_tiered(
                            _llm,
                            tag=f"llm_completeness_chunk_{chunk_start // MAX_CHUNK}",
                            prompt=prompt_chunk,
                            system_prompt=_sys_first,
                            max_tokens=8000,
                            temperature=0.3,
                            base_timeout=None,
                            task_tier=task_tier,
                            telemetry_tag="brain_completeness_fix",
                            telemetry_extra=_telemetry_extra,
                            session_id=llm_session_id,
                            conversation_id=llm_session_id,
                        )
                        if cont_call.get("content"):
                            chunk_reply = cont_call["content"].strip()
                            if chunk_reply:
                                reply += "\n" + chunk_reply
                    except Exception:
                        logger.debug("[completeness] chunk error at %d", chunk_start)
                        break
                    MONITOR.inc("brain_completeness_fix_total")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    try:
        from core.brain.incomplete_reply_recovery import maybe_continue_incomplete_reply

        reply = await maybe_continue_incomplete_reply(
            llm=_llm,
            user_text=user_text,
            reply=reply or "",
            task_tier=task_tier,
            system_prompt=_sys_first,
            llm_session_id=llm_session_id,
            telemetry_extra=_telemetry_extra,
        )
    except Exception as e:
        logger.debug("incomplete_continue: %s", e)
    reply = _news_prefetch_fallback(reply)
    try:
        from core.brain.response_finalize import finalize_user_reply

        reply = finalize_user_reply(
            reply or "",
            user_text=user_text,
            extra_markers_en=_prof_secondary.cot_extra_markers_en,
            extra_markers_ru=_prof_secondary.cot_extra_markers_ru,
        )
        try:
            from core.brain.code_empty_recovery import apply_code_delivery_if_needed

            reply = apply_code_delivery_if_needed(user_text, reply or "")
        except Exception as e:
            logger.debug("apply_code_delivery_if_needed: %s", e)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'pipeline', e, exc_info=True)
    if isinstance(context, dict):
        try:
            from core.brain.context_budget import prepend_context_budget_user_note

            reply = prepend_context_budget_user_note(context, reply or "")
        except Exception as e:
            logger.debug("prepend context_budget note: %s", e)
    try:
        if (reply or "").strip() and isinstance(_persisted_short, dict):
            from core.news_reply import persist_news_digest_from_assistant_reply

            persist_news_digest_from_assistant_reply(
                reply or "",
                persisted=_persisted_short,
                context=context,
            )
    except Exception as e:
        logger.debug("persist news digest after brain: %s", e)
    return reply
