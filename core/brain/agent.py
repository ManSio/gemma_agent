import os
import re
from typing import Dict, List

from core.brain.prompt_pack import _clip_soft
from core.monitoring import MONITOR
from core.prompt_routing import text_warrants_textbook_rag
from core.tools import brain_lite_opt_in_prefixes

from core.brain.constants import (
    AGENT_AUTONOMY_CONSTITUTION,
    AGENT_INSTRUCTION,
    AGENT_INSTRUCTION_PRIORITIZE_DIRECT,
    AGENT_INSTRUCTION_SELF_EXTEND,
)
from core.brain.env import env_flag
from core.brain.plugin_author_context import plugin_author_handbook_for_prompt


def _brain_extension_tool_prefixes() -> List[str]:
    if env_flag("BRAIN_EXTENSION_TOOLS", default=True):
        return ["SelfProgramming.", "RuntimeDiagnostic.", "AgentSelfTools.", "SkillStore.", "KnowledgeGraph."]
    return []


def brain_tools_mode() -> str:
    raw = (os.getenv("BRAIN_TOOLS_MODE") or "auto").strip().lower()
    if raw in {"full", "lite", "auto"}:
        return raw
    return "auto"


_SCHEDULE_EXTRA_TOOL = "Schedule.suburban_rail_schedule_links"


def _extra_brain_tool_names(user_text: str, profile: str) -> frozenset:
    """Schedule.suburban только при запросе про электрички — не в code/math."""
    if profile in ("code_generation", "code_debug", "math_solve", "translation"):
        return frozenset()
    low = (user_text or "").lower()
    if re.search(
        r"(?i)(электрич|пригород|расписан\w*\s+поезд|поезд\w*\s+между|suburban|"
        r"могил[её]в|баранович|orsha)",
        low,
    ):
        return frozenset({_SCHEDULE_EXTRA_TOOL})
    return frozenset()


def _brain_extra_prefixes_from_env() -> List[str]:
    """Доп. префиксы без правок кода: BRAIN_TOOLS_EXTRA_PREFIXES=AduPadruchnik.,MyParser."""
    raw = (os.getenv("BRAIN_TOOLS_EXTRA_PREFIXES") or "").strip()
    if not raw:
        return []
    out: List[str] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        out.append(p if p.endswith(".") else f"{p}.")
    return out


def format_tools_full_index_for_prompt(
    tools_full: Dict[str, str],
    tools_info: Dict[str, str],
    mode: str,
) -> str:
    """
    В lite/auto в контекст попадает только подмножество tools_info — модель «не видит» остальные имена.
    Добавляем компактный алфавитный перечень всех зарегистрированных имён (без полных схем), с явным ограничением TOOL_CALL.
    """
    if mode == "full" or not tools_full:
        return ""
    if not tools_info:
        return ""
    if not env_flag("BRAIN_TOOLS_FULL_INDEX_IN_PROMPT", default=True):
        return ""
    # Когда все инструменты уже в tools_info — полный индекс не нужен (deep profile)
    if len(tools_info) >= len(tools_full):
        return ""
    try:
        max_c = int((os.getenv("BRAIN_TOOLS_FULL_INDEX_MAX_CHARS") or "3200").strip())
    except ValueError:
        max_c = 3200
    max_c = max(400, min(max_c, 12000))
    names = sorted(str(k) for k in tools_full.keys())
    joined = ", ".join(names)
    body = _clip_soft(joined, max_c) if len(joined) > max_c else joined
    return (
        f"В процессе зарегистрировано инструментов: {len(names)}. "
        f"В этом режиме ({mode}) ядро принимает TOOL_CALL только для имён из строки tools ({len(tools_info)} шт.). "
        "Остальные возможности — slash-команды из telegram_commands_catalog или режим BRAIN_TOOLS_MODE=full (админ).\n"
        f"Полный перечень имён (справочник): {body}"
    )


def filter_tools_for_brain(tools_info: Dict[str, str], user_text: str) -> Dict[str, str]:
    mode = brain_tools_mode()
    if mode == "full" or not tools_info:
        return dict(tools_info)
    prefixes = [
        "UrlFetch.",
        "SiteRecipe.",
        "UniversalSearch.",
        "Wikipedia.",
        "DocumentCorpus.",
        "TaskScout.",
        "UserKnowledgeArchive.",
        "ArithmeticTool.",
        "DialogRecall.",
        "News.",
        "FileIntake.",
        "Greetings.",
    ]
    prefixes.extend(_brain_extension_tool_prefixes())
    prefixes.extend(brain_lite_opt_in_prefixes())
    prefixes.extend(_brain_extra_prefixes_from_env())
    if mode == "auto" and text_warrants_textbook_rag(user_text):
        prefixes.append("BooksRAG.")
    filtered = {
        k: v
        for k, v in tools_info.items()
        if any(k.startswith(p) for p in prefixes) or k in _extra_brain_tool_names(user_text, "")
    }
    if not filtered:
        return dict(tools_info)
    if len(filtered) < len(tools_info):
        MONITOR.inc("brain_tools_filtered_total")
    return filtered


# ── Profile-based tool selection ──


def tools_for_profile(profile: str, tools_full: Dict[str, str], user_text: str) -> Dict[str, str]:
    """Отфильтрованные tools по profile_registry (все профили реестра)."""
    from core.brain.profile_registry import resolve_tool_prefixes, get_profile

    prefixes = resolve_tool_prefixes(profile)
    if prefixes is not None and len(prefixes) == 0:
        return {}
    if prefixes is None:
        out = dict(tools_full)
        if "SearchMemory.search_memory" not in out:
            out["SearchMemory.search_memory"] = "archive-memory-search"
        return out

    cfg = get_profile(profile)
    prefix_list = list(prefixes)
    _narrow = {"quick_explain", "news_brief", "creative", "translation", "brainstorm", "roleplay", "summarization"}
    if profile not in _narrow and not cfg.tool_families:
        prefix_list.extend(_brain_extension_tool_prefixes())
        prefix_list.extend(brain_lite_opt_in_prefixes())
        prefix_list.extend(_brain_extra_prefixes_from_env())
    elif cfg.tool_families and any(p in cfg.tool_families for p in ("SelfProgramming.", "RuntimeDiagnostic.")):
        prefix_list.extend(_brain_extension_tool_prefixes())
    if text_warrants_textbook_rag(user_text) and profile in ("education", "tutorial", "standard", "deep"):
        prefix_list.append("BooksRAG.")

    out = {
        k: v for k, v in tools_full.items()
        if any(k.startswith(p) for p in prefix_list) or k in _extra_brain_tool_names(user_text, profile)
    }
    if not out:
        return {}
    return out


def profile_first_stage_max_tokens(profile: str) -> int:
    """Бюджет первого прохода (max_tokens) для профиля."""
    from core.brain.profile_registry import get_profile
    return int(get_profile(profile).max_tokens_first_stage or 1536)


def agent_instruction_effective(tools_mode: str, tools_info: Dict[str, str]) -> str:
    if tools_mode == "full":
        base = AGENT_INSTRUCTION
    else:
        base = AGENT_INSTRUCTION + AGENT_INSTRUCTION_PRIORITIZE_DIRECT
    if any(str(k).startswith("SelfProgramming.") for k in tools_info.keys()):
        base += AGENT_INSTRUCTION_SELF_EXTEND
        base += plugin_author_handbook_for_prompt()
    return f"{AGENT_AUTONOMY_CONSTITUTION.strip()}\n\n{base}"
