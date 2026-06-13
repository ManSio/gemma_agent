"""
Prompt Modules Registry — реестр модулей динамического контекста.

Каждый модуль — это функция predicate(parts, profile, intent, ctx) → bool
+ функция content(parts, profile) → str.

Сборка: build_dynamic_tail() вызывает все модули с predicate=True
и склеивает результат.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Tuple

from core.brain.directive_blocks import (
    AGENT_DOMAIN_ADU_COMPACT,
    AGENT_DOMAIN_DOCUMENT_CORPUS_COMPACT,
    AGENT_DOMAIN_LAW_COMPACT,
    AGENT_DOMAIN_TASKSCOUT_COMPACT,
    AGENT_DOMAIN_UKA_COMPACT,
)
from core.brain.profile_registry import ProfileConfig, PromptTier, get_profile

logger = logging.getLogger(__name__)

# Registry: (name, predicate, content_fn)
_MODULES: List[Tuple[str, Callable, Callable]] = []


def register_module(
    name: str,
    predicate: Callable,
    content_fn: Callable,
) -> None:
    """Зарегистрировать модуль динамического контекста."""
    _MODULES.append((name, predicate, content_fn))


def build_dynamic_tail(
    parts: Dict[str, Any],
    profile: str,
    intent: str,
    ctx: Dict[str, Any],
) -> str:
    """Собрать динамический хвост из модулей с predicate=True."""
    cfg = get_profile(profile)
    segments: List[str] = []
    for name, pred, fn in _MODULES:
        try:
            if not pred(parts, cfg, intent, ctx):
                continue
            content = fn(parts, cfg)
            if content:
                segments.append(content)
        except Exception as e:
            logger.debug("[prompt_modules] module %s error: %s", name, e)
    return "\n".join(segments)


# =====================================================================
# Вспомогательные функции
# =====================================================================

def _clip(t: Any, n: int) -> str:
    s = str(t if t is not None else "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:max(0, n - 3)] + "..."


def _fmt_block(label: str, body: str) -> str:
    return f"{label}\n{body}\n"


def _turn_index(ctx: Dict[str, Any]) -> int:
    ds = ctx.get("dialogue_state")
    if isinstance(ds, dict):
        return int(ds.get("turn_index", 0))
    return 0


def _last_intent(ctx: Dict[str, Any]) -> str:
    ds = ctx.get("dialogue_state")
    if isinstance(ds, dict):
        return str(ds.get("last_intent") or "")
    return ""


def _has_slash(text: str) -> bool:
    return "/" in str(text or "")


def _topic_change(ctx: Dict[str, Any], parts: Dict[str, Any]) -> bool:
    tt = ctx.get("topic_tracking")
    sft = parts.get("session_first_user_text", "")
    return bool(tt) and bool(sft) and str(tt) != str(sft)


def _text(parts: Dict[str, Any]) -> str:
    return str(parts.get("user_text") or "")


# =====================================================================
# Модуль: recent_dialogue
# =====================================================================

def _recent_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    n = _turn_index(ctx)
    return cfg.recent_count > 0 and n > 1


def _recent_content(parts, cfg: ProfileConfig) -> str:
    rd = parts.get("recent_dialogue") or []
    if not isinstance(rd, list):
        rd = []
    try:
        from core.context_compression import trim_dialogue_messages_paired

        cap = max(4, int(cfg.recent_count))
        if cap % 2:
            cap += 1
        rd = trim_dialogue_messages_paired(rd, cap)
    except Exception:
        rd = rd[-cfg.recent_count:]
    if not rd:
        return ""
    try:
        from core.brain.cot_strip import sanitize_dialogue
        rd = sanitize_dialogue(rd)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'prompt_modules', e, exc_info=True)
    try:
        from core.brain.context_observation_mask import mask_observation_dialogue

        rd = mask_observation_dialogue(rd)
    except Exception as e:
        logger.debug("context_observation_mask dialogue: %s", e)
    return _fmt_block("Recent messages:", str(rd))


register_module("recent", _recent_pred, _recent_content)


# =====================================================================
# Модуль: message_archive
# =====================================================================

def _archive_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    return cfg.archive_count > 0 and _turn_index(ctx) > 3


def _archive_content(parts, cfg: ProfileConfig) -> str:
    arch = parts.get("message_archive") or []
    if not isinstance(arch, list):
        arch = []
    arch_slice = arch[-cfg.archive_count:] if arch else []
    if not arch_slice:
        return ""
    try:
        from core.brain.cot_strip import sanitize_dialogue
        arch_slice = sanitize_dialogue(arch_slice)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'prompt_modules', e, exc_info=True)
    # Нарративный формат вместо Python-дампа
    lines: List[str] = []
    for msg in arch_slice:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "?")).strip()
        text = str(msg.get("text") or msg.get("content") or "").strip()
        if not text:
            continue
        label = "user" if role == "user" else "assistant"
        lines.append(f"{label}: {text}")
    if not lines:
        return ""
    return _fmt_block("Archive:", "\n".join(lines))


register_module("archive", _archive_pred, _archive_content)


# =====================================================================
# Модуль: tools
# =====================================================================

def _tools_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    # Если профиль short или нет инструментов — не надо
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    tool_names = parts.get("tool_names") or []
    return len(tool_names) > 0


def _tools_content(parts, cfg: ProfileConfig) -> str:
    tool_names = parts.get("tool_names") or []
    tools_mode = parts.get("tools_mode") or "auto"
    lines = [f"- tool_names: {tool_names}"]
    lines.append(f"- tools_mode: {tools_mode}")
    return "\n".join(lines)


register_module("tools", _tools_pred, _tools_content)


# =====================================================================
# Модуль: tcmd_catalog
# =====================================================================

def _tcmd_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_tcmd or cfg.tcmd_max_chars <= 0:
        return False
    text = _text(parts)
    if _has_slash(text):
        return True
    return intent in ("help", "admin", "capabilities", "command_help")


def _tcmd_content(parts, cfg: ProfileConfig) -> str:
    tcmd = str(parts.get("tcmd_cat") or "")
    if not tcmd:
        return ""
    clipped = _clip(tcmd, cfg.tcmd_max_chars)
    return f"- telegram_commands_catalog:\n{clipped}"


register_module("tcmd", _tcmd_pred, _tcmd_content)


# =====================================================================
# Модуль: scaffold
# =====================================================================

def _scaffold_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    return cfg.include_scaffold


def _scaffold_content(parts, cfg: ProfileConfig) -> str:
    scaffold = str(parts.get("scaffold_part") or "").strip()
    return scaffold if scaffold else ""


register_module("scaffold", _scaffold_pred, _scaffold_content)


# =====================================================================
# Модуль: operator_rules
# =====================================================================

def _oprules_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_operator_rules:
        return False
    opr = parts.get("operator_rules") or ""
    return bool(str(opr).strip())


def _oprules_content(parts, cfg: ProfileConfig) -> str:
    opr = str(parts.get("operator_rules") or "")
    if not opr.strip():
        return ""
    return f"- operator_rules: {_clip(opr, 3000)}"


register_module("operator_rules", _oprules_pred, _oprules_content)


# =====================================================================
# Модуль: goal_hints
# =====================================================================

def _goal_hints_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_goal_hints:
        return False
    gh = parts.get("goal_hints")
    if not isinstance(gh, dict):
        return False
    ag = gh.get("active_goals") or []
    return len(ag) > 0


def _goal_hints_content(parts, cfg: ProfileConfig) -> str:
    gh = parts.get("goal_hints") or {}
    # Compact display: только active goals
    ag = gh.get("active_goals") or []
    bits = [f"active: {g.get('id','?')}" for g in ag[:3] if isinstance(g, dict)]
    if not bits:
        return ""
    return f"- goal_hints: focus={', '.join(bits)}"


register_module("goal_hints", _goal_hints_pred, _goal_hints_content)


# =====================================================================
# Модуль: goal_plan
# =====================================================================

def _goal_plan_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_goal_plan:
        return False
    gp = parts.get("goal_plan")
    return isinstance(gp, dict) and len(gp) > 0


def _goal_plan_content(parts, cfg: ProfileConfig) -> str:
    gp = parts.get("goal_plan") or {}
    # Compact: только primary_goal + response_shape
    primary = gp.get("primary_goal", "")
    shape = gp.get("response_shape", "")
    tier = gp.get("task_tier", "")
    bits = []
    if primary:
        bits.append(f"goal={primary}")
    if shape:
        bits.append(f"shape={shape}")
    if tier:
        bits.append(f"tier={tier}")
    if not bits:
        return ""
    return f"- goal_plan: {', '.join(bits)}"


register_module("goal_plan", _goal_plan_pred, _goal_plan_content)


# =====================================================================
# Модуль: external_hint
# =====================================================================

def _external_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    mode = cfg.external_hint_mode
    if mode == "none":
        return False
    hint = parts.get("external_hint") or ""
    return bool(str(hint).strip())


def _external_content(parts, cfg: ProfileConfig) -> str:
    hint = str(parts.get("external_hint") or "")
    if not hint.strip():
        return ""
    try:
        from core.brain.context_observation_mask import mask_external_hint

        hint = mask_external_hint(hint)
    except Exception as e:
        logger.debug("context_observation_mask hint: %s", e)
    mode = cfg.external_hint_mode
    if mode == "ultra_short":
        # Только время (первая строка)
        first_line = hint.split("\n")[0] if hint else ""
        return f"- external_hint: {first_line}" if first_line else ""
    elif mode == "slim":
        return f"- external_hint: {_clip(hint, 800)}"
    return f"- external_hint: {hint}"


register_module("external_hint", _external_pred, _external_content)


# =====================================================================
# Модуль: memory_facts
# =====================================================================

def _memory_facts_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_memory_facts:
        return False
    mf = parts.get("memory_facts")
    if isinstance(mf, list) and len(mf) > 0:
        return True
    return False


def _memory_facts_content(parts, cfg: ProfileConfig) -> str:
    mf = parts.get("memory_facts") or []
    if not mf:
        return ""
    clipped = mf[:6] if len(mf) > 6 else mf
    return f"- memory_facts: {clipped}"


register_module("memory_facts", _memory_facts_pred, _memory_facts_content)


# =====================================================================
# Модуль: knowledge_summary
# =====================================================================

def _knowledge_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_knowledge_summary:
        return False
    ks = parts.get("knowledge_summary") or ""
    return bool(str(ks).strip())


def _knowledge_content(parts, cfg: ProfileConfig) -> str:
    ks = str(parts.get("knowledge_summary") or "")
    if not ks.strip():
        return ""
    return f"- knowledge_hint_summary: {ks}"


register_module("knowledge_summary", _knowledge_pred, _knowledge_content)


# =====================================================================
# Модуль: session_digest
# =====================================================================

def _digest_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_session_digest:
        return False
    dig = parts.get("session_digest") or ""
    return bool(str(dig).strip())


def _digest_content(parts, cfg: ProfileConfig) -> str:
    dig = str(parts.get("session_digest") or "")
    if not dig.strip():
        return ""
    return f"- session_digest: {dig}"


register_module("session_digest", _digest_pred, _digest_content)


# =====================================================================
# Модуль: context_fields (user_facts, dialogue_state, topic_tracking, etc.)
# Общий модуль для полей, которые почти всегда нужны, но в slim формате
# =====================================================================

# ULTRA_SHORT (профиль short): в промпт только user_facts, если есть «якорь» личности.
# Раньше требовалось только name → pet_cat/pet_dog без имени не попадали в контекст.
_ULTRA_SHORT_IDENTITY_KEYS = frozenset(
    {"name", "pet_cat", "pet_dog", "city", "country", "timezone", "language"}
)


def _user_facts_has_identity_anchor(uf: Any) -> bool:
    if not isinstance(uf, dict) or not uf:
        return False
    for key in _ULTRA_SHORT_IDENTITY_KEYS:
        val = uf.get(key)
        if val is not None and str(val).strip():
            return True
    return False


def _context_fields_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return _user_facts_has_identity_anchor(parts.get("user_facts"))
    return True


def _context_fields_content(parts, cfg: ProfileConfig) -> str:
    lines: List[str] = []
    uf = parts.get("user_facts")
    if uf:
        lines.append(f"- user_facts: {uf}")
    ds = parts.get("dialogue_state")
    if ds:
        lines.append(f"- dialogue_state: {ds}")
    tt = parts.get("topic_tracking")
    if tt:
        lines.append(f"- topic_tracking: {tt}")
    bp = parts.get("behavior_policy")
    if bp:
        lines.append(f"- behavior_policy: {bp}")
    # agent_inst — collapse или полный
    ag = str(parts.get("agent_inst") or "")
    collapse_level = int(parts.get("_collapse_level") or 0)
    stub = str(parts.get("agent_inst_collapse_stub") or "")
    if collapse_level >= 2 and stub:
        ag = stub
    elif cfg.agent_inst_collapse and stub:
        ag = stub
    if ag:
        lines.append(f"- agent_inst: {ag}")
    bi = parts.get("_budget_info")
    if bi and isinstance(bi, dict):
        cl = bi.get("chars_limit") or "?"
        te = bi.get("tokens_est")
        if te:
            lines.append(f"- context_budget: chars_limit={cl}, tokens_est~{te}")
        else:
            lines.append(f"- context_budget: chars_limit={cl}")
    return "\n".join(lines)


register_module("context_fields", _context_fields_pred, _context_fields_content)


# =====================================================================
# Модуль: grounding (время, календарь)
# =====================================================================

def _grounding_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    return cfg.external_hint_mode != "none"


def _grounding_content(parts, cfg: ProfileConfig) -> str:
    g = parts.get("grounding_mini") or ""
    return f"- grounding: {g}" if str(g).strip() else ""


register_module("grounding", _grounding_pred, _grounding_content)


# =====================================================================
# Модуль: document_intake_block
# =====================================================================

def _doc_intake_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    di = parts.get("document_intake_block")
    return bool(di)


def _doc_intake_content(parts, cfg: ProfileConfig) -> str:
    di = parts.get("document_intake_block")
    return f"- document_intake:\n{di}"


register_module("document_intake", _doc_intake_pred, _doc_intake_content)


# =====================================================================
# Модуль: ephemeral_lessons
# =====================================================================

def _eph_lessons_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_ephemeral_lessons:
        return False
    eph = parts.get("ephemeral_lessons") or ""
    return bool(str(eph).strip())


def _eph_lessons_content(parts, cfg: ProfileConfig) -> str:
    eph = str(parts.get("ephemeral_lessons") or "").strip()
    return f"- ephemeral_lessons: {eph}" if eph else ""


register_module("ephemeral_lessons", _eph_lessons_pred, _eph_lessons_content)


# =====================================================================
# Модуль: plugin_manifest_prompts
# =====================================================================

def _plugin_prompts_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if not cfg.include_plugin_prompts:
        return False
    pm = parts.get("plugin_manifest_prompts") or ""
    return bool(str(pm).strip())


def _plugin_prompts_content(parts, cfg: ProfileConfig) -> str:
    pm = str(parts.get("plugin_manifest_prompts") or "").strip()
    if not pm:
        return ""
    clipped = _clip(pm, 3000)
    return f"- plugin_manifest_prompts:\n{clipped}"


register_module("plugin_manifest_prompts", _plugin_prompts_pred, _plugin_prompts_content)


# =====================================================================
# Доменные вставки (раньше в agent_pack chat-pack)
# =====================================================================

_LAW_HINT = re.compile(
    r"(?i)(право|pravo\.by|etalonline|закон|нпа|статья|кодекс|декрет|постановлен|указ|правов|"
    r"регламент|испытан|налогов|трудов|гк\s*рб|ук\s*рб|коап|общ\w*\s+баз\w*\s+документ|"
    r"локаль\w*\s+баз\w*)",
)
_ADU_HINT = re.compile(
    r"(?i)(учебник|падручник|padruchnik|e-padruchnik|adu|гуо|скачай\s+уч|pdf\s+уч|"
    r"асоблiва|asabliva)",
)
_TASKSCOUT_HINT = re.compile(
    r"(?i)(task\s*scout|плейбук|playbook|стратеги\s+обход|разведк\s+сайт|scout_plan)",
)
_UKA_HINT = re.compile(
    r"(?i)(архив\s+знан|архив\w*\s+замет|заметок\s+и\s+что|что\s+у\s+меня\s+в\s+архив|"
    r"user\s*knowledge|archive_store|archive_read|archive_search|personal_library|личн\w*\s+библиотек|"
    r"личн\w*\s+библиотек\w*\s+файл|перечисл\w*\s+отдельн|сверк\s+факт|моих\s+документ|мои\s+документ|"
    r"мои\s+запис|моих\s+запис|посмотр\w*\s+в\s+мои|посмотр\w*\s+мои|"
    r"запомни\s+слов|запомнить\s+слов|запомни\s+фраз|запомнить\s+фраз|"
    r"запомни\s+числ|запомни\s+код|просил\s+запомнить|просила\s+запомнить|"
    r"что\s+я\s+просил\s+запомнить|что\s+я\s+просила\s+запомнить|"
    r"какое\s+слово|какие\s+слова|запоминал)",
)


def _tool_names(parts: Dict[str, Any]) -> List[str]:
    tn = parts.get("tool_names") or []
    return [str(x) for x in tn] if isinstance(tn, list) else []


def _tools_any_prefix(parts: Dict[str, Any], prefix: str) -> bool:
    return any(n.startswith(prefix) for n in _tool_names(parts))


def _domain_law_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    ut = str(parts.get("user_text") or "")
    has_tools = _tools_any_prefix(parts, "DocumentCorpus.") or _tools_any_prefix(
        parts, "UniversalSearch."
    )
    return has_tools and bool(_LAW_HINT.search(ut))


def _domain_law_content(parts, cfg: ProfileConfig) -> str:
    return f"- domain_law:\n{AGENT_DOMAIN_LAW_COMPACT}"


def _domain_corpus_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    return _tools_any_prefix(parts, "DocumentCorpus.")


def _domain_corpus_content(parts, cfg: ProfileConfig) -> str:
    return f"- domain_document_corpus:\n{AGENT_DOMAIN_DOCUMENT_CORPUS_COMPACT}"


def _domain_adu_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    ut = str(parts.get("user_text") or "")
    return _tools_any_prefix(parts, "BooksRAG.") and bool(_ADU_HINT.search(ut))


def _domain_adu_content(parts, cfg: ProfileConfig) -> str:
    return f"- domain_adu:\n{AGENT_DOMAIN_ADU_COMPACT}"


def _domain_taskscout_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    ut = str(parts.get("user_text") or "")
    return _tools_any_prefix(parts, "TaskScout.") and bool(_TASKSCOUT_HINT.search(ut))


def _domain_taskscout_content(parts, cfg: ProfileConfig) -> str:
    return f"- domain_taskscout:\n{AGENT_DOMAIN_TASKSCOUT_COMPACT}"


def _domain_uka_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    ut = str(parts.get("user_text") or "")
    return _tools_any_prefix(parts, "UserKnowledgeArchive.") and bool(_UKA_HINT.search(ut))


def _domain_uka_content(parts, cfg: ProfileConfig) -> str:
    return f"- domain_uka:\n{AGENT_DOMAIN_UKA_COMPACT}"


register_module("domain_law", _domain_law_pred, _domain_law_content)
register_module("domain_document_corpus", _domain_corpus_pred, _domain_corpus_content)
register_module("domain_adu", _domain_adu_pred, _domain_adu_content)
register_module("domain_taskscout", _domain_taskscout_pred, _domain_taskscout_content)
register_module("domain_uka", _domain_uka_pred, _domain_uka_content)


# =====================================================================
# Модуль: context_anchors (ключевые сущности диалога + последние реплики)
# =====================================================================

def _anchors_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    """Показывать anchors если:
    - Пользователь использует анафору (coreference в любом месте текста)
    - ИЛИ есть сущности в anchor_store или recent_dialogue
    - И профиль не ultra_short
    """
    if cfg.tier == PromptTier.ULTRA_SHORT:
        return False
    ut = str(parts.get("user_text") or "")
    rd = parts.get("recent_dialogue") or []
    if not isinstance(rd, list) or len(rd) < 1:
        return False
    try:
        from core.brain.context_anchors import needs_anchors
        if needs_anchors(ut, rd):
            return True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'prompt_modules', e, exc_info=True)
    # Fallback: есть anchor_entities в dialogue_state
    try:
        ds = parts.get("dialogue_state")
        if isinstance(ds, dict):
            ae = ds.get("anchor_entities")
            if ae and isinstance(ae, list) and len(ae) > 0:
                return True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'prompt_modules', e, exc_info=True)
    return False


def _anchors_content(parts, cfg: ProfileConfig) -> str:
    """Собрать блок context_anchors для промпта.

    Читает entity из dialogue_state.anchor_entities (персистентные, сквозь ходы).
    Сканирует recent_dialogue на новые сущности как fallback.
    """
    rd = parts.get("recent_dialogue") or []
    if not isinstance(rd, list):
        rd = []
    ut = str(parts.get("user_text") or "")

    try:
        from core.brain.context_anchors import (
            build_context_anchors_block,
            get_entities_for_prompt,
        )

        # Читаем персистентные сущности из dialogue_state
        ds = parts.get("dialogue_state")
        anchor_entities: Optional[List[str]] = None
        if isinstance(ds, dict):
            anchor_entities = ds.get("anchor_entities")

        entities = get_entities_for_prompt(anchor_entities, rd)

        # Извлекаем excerpts из dialogue (последняя реплика ассистента и предыдущая пользователя)
        last_assistant = ""
        previous_user = ""
        for turn in reversed(rd):
            if isinstance(turn, dict):
                role = str(turn.get("role") or "").lower()
                text = str(turn.get("text") or turn.get("content") or "")
                if role == "assistant" and not last_assistant and text.strip():
                    last_assistant = text[:500]
                elif role == "user" and not previous_user and text.strip() and text != ut:
                    previous_user = text[:500]

        try:
            from core.telegram_output_guard import _overlap_with_user_query

            if last_assistant and _overlap_with_user_query(ut, last_assistant) < 0.08:
                last_assistant = ""
            if previous_user and _overlap_with_user_query(ut, previous_user) < 0.12:
                previous_user = ""
            if entities:
                _eb = " ".join(str(e) for e in entities)
                if _overlap_with_user_query(ut, _eb) < 0.08:
                    _utl = ut.lower()
                    entities = [
                        e
                        for e in entities
                        if str(e).lower() in _utl or str(e).lower()[:5] in _utl
                    ][:8]
        except Exception as e:
            logger.debug('%s optional failed: %s', 'prompt_modules', e, exc_info=True)
        block = build_context_anchors_block(entities, last_assistant, previous_user, ut)
        return block if block else ""
    except Exception:
        return ""


register_module("context_anchors", _anchors_pred, _anchors_content)


# =====================================================================
# Модуль: active_thread (discourse resolver)
# =====================================================================

def _active_thread_pred(parts, cfg: ProfileConfig, intent, ctx) -> bool:
    block = str((parts.get("active_thread_block") or (ctx or {}).get("active_thread_block") or "")).strip()
    return bool(block)


def _active_thread_content(parts, cfg: ProfileConfig) -> str:
    block = str(parts.get("active_thread_block") or "").strip()
    if not block:
        return ""
    return _fmt_block("Active thread", block)


register_module("active_thread", _active_thread_pred, _active_thread_content)

__all__ = [
    "build_dynamic_tail",
    "register_module",
    "_clip",
    "_fmt_block",
]
