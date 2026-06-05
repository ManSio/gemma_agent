"""Пакет мозга: тонкие модули + обратно совместимые реэкспорты `core.brain.*`."""

from core.brain.agent import (
    agent_instruction_effective as _agent_instruction_effective,
    brain_tools_mode as _brain_tools_mode,
    filter_tools_for_brain as _filter_tools_for_brain,
)
from core.brain.constants import (
    AGENT_INSTRUCTION,
    AGENT_INSTRUCTION_PRIORITIZE_DIRECT,
    AGENT_INSTRUCTION_SELF_EXTEND,
    BRAIN_CAPABILITY_HONESTY,
    BRAIN_CORE_VERSION,
    BRAIN_INFRASTRUCTURE_HONESTY,
    SILENT_DOCUMENT_USER_PROMPT,
    SILENT_IMAGE_USER_PROMPT,
)
from core.brain.cot_strip import strip_leaked_cot as _strip_leaked_cot, text_has_cyrillic as _text_has_cyrillic
from core.brain.env import env_flag as _env_flag
from core.brain.hot_path import brain_hot_path_slim_eligible as _brain_hot_path_slim_eligible
from core.brain.pipeline import call_brain
from core.brain.runtime import _skills, configure_brain_memory
from core.brain.text_helpers import (
    brain_weather_urlfetch_fallback_enabled as _brain_weather_urlfetch_fallback_enabled,
    build_goal_plan as _build_goal_plan,
    build_micro_emotion_style as _build_micro_emotion_style,
    build_style_hints as _build_style_hints,
    build_thinking_markers as _build_thinking_markers,
    build_typing_hooks as _build_typing_hooks,
    gentle_auto_ask_missing as _gentle_auto_ask_missing,
    is_bot_operational_diag_question as _is_bot_operational_diag_question,
    looks_like_repetition_glitch as _looks_like_repetition_glitch,
    mask_pii_text as _mask_pii_text,
    natural_fallback_response as _natural_fallback_response,
    normalize_user_facts as _normalize_user_facts,
    operational_diag_reply as _operational_diag_reply,
    parse_tool_call as _parse_tool_call,
    safe_json_dumps as _safe_json_dumps,
    safe_text as _safe_text,
    stable_blend_style as _stable_blend_style,
    summarize_knowledge_hint as _summarize_knowledge_hint,
    task_fact_profile as _task_fact_profile,
    weather_city_country_from_message as _weather_city_country_from_message,
    weather_wttr_in_fallback_hint as _weather_wttr_in_fallback_hint,
)
from core.brain.vision_io import vision_image_parts_for_brain, vision_mime_from_path as _vision_mime_from_path
from core.brain.vision_llm import (
    brain_default_vision_system_prompt as _brain_default_vision_system_prompt,
    brain_progress as _brain_progress,
    brain_run_vision_precaption as _brain_run_vision_precaption,
)

__all__ = [
    "BRAIN_CORE_VERSION",
    "BRAIN_CAPABILITY_HONESTY",
    "BRAIN_INFRASTRUCTURE_HONESTY",
    "AGENT_INSTRUCTION",
    "AGENT_INSTRUCTION_PRIORITIZE_DIRECT",
    "AGENT_INSTRUCTION_SELF_EXTEND",
    "SILENT_DOCUMENT_USER_PROMPT",
    "SILENT_IMAGE_USER_PROMPT",
    "call_brain",
    "configure_brain_memory",
    "vision_image_parts_for_brain",
    "_agent_instruction_effective",
    "_brain_default_vision_system_prompt",
    "_brain_hot_path_slim_eligible",
    "_brain_progress",
    "_brain_run_vision_precaption",
    "_brain_tools_mode",
    "_brain_weather_urlfetch_fallback_enabled",
    "_build_goal_plan",
    "_build_micro_emotion_style",
    "_build_style_hints",
    "_build_thinking_markers",
    "_build_typing_hooks",
    "_env_flag",
    "_filter_tools_for_brain",
    "_gentle_auto_ask_missing",
    "_is_bot_operational_diag_question",
    "_looks_like_repetition_glitch",
    "_mask_pii_text",
    "_natural_fallback_response",
    "_normalize_user_facts",
    "_operational_diag_reply",
    "_parse_tool_call",
    "_safe_json_dumps",
    "_safe_text",
    "_skills",
    "_stable_blend_style",
    "_strip_leaked_cot",
    "_summarize_knowledge_hint",
    "_task_fact_profile",
    "_text_has_cyrillic",
    "_vision_mime_from_path",
    "_weather_city_country_from_message",
    "_weather_wttr_in_fallback_hint",
]
