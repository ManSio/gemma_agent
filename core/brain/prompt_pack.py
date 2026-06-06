"""
Сборка user-промпта для call_brain: статичная голова (KV‑кеш) + динамический хвост.

Структура (KV-кеш держит префикс каждого сообщения независимо):

  messages[0] (system) = _sys_first (короткий агентский промпт, ~340 токенов)
  messages[1] (user)   = System: + {system_prompt_for_llm (~4k-18k)}
                          Tools: + {BRAIN_TOOL_FAMILY_SUPPLEMENT}
                          Format: + {BRAIN_STATIC_FORMAT}
                          User message: + {user_text}
  messages[2] (system) = dynamic_tail  (Recent: + Archive: + Context: + Task/Goal:)

System дублирует messages[0] в messages[1] намеренно: KV-кеш держит префикс
каждого сообщения независимо. Без System в messages[1] стабильный префикс
~300 символов (Tools + Format), с System — ~3500+ символов, что даёт
>70% кеша (cached_tok ~4000-5000 для standard/deep).
"""
from __future__ import annotations

import logging

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.prompt_assembly import PromptAssemblyTier
from core.brain.constants import BRAIN_STATIC_FORMAT, BRAIN_TOOL_FAMILY_SUPPLEMENT
from core.brain.profile_registry import (
    PromptTier,
    get_profile,
)
from core.brain.prompt_modules import build_dynamic_tail as _build_modules_tail


logger = logging.getLogger(__name__)

def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _schema_en() -> bool:
    raw = (os.getenv("BRAIN_PROMPT_SCHEMA_EN") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _labels() -> Dict[str, str]:
    if _schema_en():
        return {
            "system": "System:",
            "tools": "Tools:",
            "format": "Format:",
            "context": "Context:",
            "user_msg": "User message:",
            "recent": "Recent messages:",
            "archive": "Archive:",
            "task": "Task/Goal:",
            "instr_vision": "Vision instruction: use vision_precaption; short user-facing answer.",
            "instr_hot": "Reply with text or a single TOOL_CALL per instructions.",
        }
    return {
        "system": "Системная инструкция:",
        "tools": "Инструменты:",
        "format": "Формат:",
        "context": "Контекст:",
        "user_msg": "Сообщение пользователя:",
        "recent": "Последние сообщения:",
        "archive": "Архив диалога:",
        "task": "Задача/Цель:",
        "instr_vision": "Инструкция vision: используй vision_precaption; ответ краткий.",
        "instr_hot": "Ответь текстом или одним TOOL_CALL по инструкции.",
    }


def estimate_tokens_approx(text: str) -> int:
    return max(1, len(text or "") // 4)


def prompt_runtime_breakdown(prompt: str) -> Dict[str, Any]:
    """
    Runtime-разбивка уже собранного промпта по крупным блокам.
    Нужна для /admin_kv_debug_json: фактические chars + rough tokens.
    """
    s = str(prompt or "")
    if not s:
        return {"total_chars": 0, "total_tokens_est": 0, "sections": {}, "context_keys": {}}

    markers = {
        "system": ("System:", "Системная инструкция:"),
        "tools": ("Tools:", "Инструменты:"),
        "format": ("Format:", "Формат:"),
        "user": ("User message:", "Сообщение пользователя:"),
        "recent": ("Recent messages:", "Последние сообщения:"),
        "archive": ("Archive:", "Архив диалога:"),
        "context": ("Context:", "Контекст:"),
        "task": ("Task/Goal:", "Задача/Цель:"),
    }

    def _first_pos(variants: Tuple[str, ...]) -> int:
        pos = -1
        for v in variants:
            p = s.find(v)
            if p >= 0 and (pos < 0 or p < pos):
                pos = p
        return pos

    def _slice(a: int, b: int) -> str:
        if a < 0:
            return ""
        if b < 0:
            return s[a:]
        return s[a:b]

    pos_system = _first_pos(markers["system"])
    pos_tools = _first_pos(markers["tools"])
    pos_format = _first_pos(markers["format"])
    pos_user = _first_pos(markers["user"])
    pos_recent = _first_pos(markers["recent"])
    pos_archive = _first_pos(markers["archive"])
    pos_context = _first_pos(markers["context"])
    pos_task = _first_pos(markers["task"])

    sections_raw = {
        "system": _slice(pos_system, pos_tools if pos_tools >= 0 else pos_format),
        "tools": _slice(pos_tools, pos_format if pos_format >= 0 else pos_user),
        "format": _slice(pos_format, pos_user if pos_user >= 0 else pos_recent),
        "user": _slice(pos_user, pos_recent if pos_recent >= 0 else pos_archive),
        "recent": _slice(pos_recent, pos_archive if pos_archive >= 0 else pos_context),
        "archive": _slice(pos_archive, pos_context if pos_context >= 0 else pos_task),
        "context": _slice(pos_context, pos_task if pos_task >= 0 else -1),
        "task": _slice(pos_task, -1),
    }
    sections = {
        k: {
            "chars": len(v),
            "tokens_est": estimate_tokens_approx(v) if v else 0,
        }
        for k, v in sections_raw.items()
    }

    ctx = sections_raw.get("context") or ""
    key_map: Dict[str, int] = {}
    if ctx:
        lines = ctx.splitlines()
        cur = ""
        for ln in lines:
            m = re.match(r"^\s*-\s*([a-zA-Z0-9_().-]+)\s*:", ln)
            if m:
                cur = m.group(1)
                key_map.setdefault(cur, 0)
            if cur:
                key_map[cur] += len(ln) + 1
    context_keys = {
        k: {"chars": v, "tokens_est": estimate_tokens_approx("x" * v)}
        for k, v in sorted(key_map.items(), key=lambda kv: kv[1], reverse=True)[:18]
    }

    return {
        "total_chars": len(s),
        "total_tokens_est": estimate_tokens_approx(s),
        "sections": sections,
        "context_keys": context_keys,
    }


def _clip_mode() -> str:
    raw = (os.getenv("BRAIN_PROMPT_CLIP_MODE") or "hard").strip().lower()
    return raw if raw in {"soft", "hard", "none"} else "hard"


def _clip_hard(t: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(t) <= n:
        return t
    return t[: max(0, n - 3)] + "..."


def _clip_soft(t: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(t) <= n:
        return t
    suffix = " …"
    suf_len = len(suffix)
    max_body = n - suf_len
    if max_body < 8:
        return _clip_hard(t, n)
    prefix = t[:max_body]
    min_keep = max(8, min(max_body // 3, 400))
    min_keep = min(min_keep, max_body)
    if " " in prefix:
        soft = prefix.rsplit(" ", 1)[0].rstrip()
        if len(soft) >= min_keep:
            return soft + suffix
    return _clip_hard(t, n)


def _clip(s: Any, n: int) -> str:
    t = str(s if s is not None else "")
    mode = _clip_mode()
    if mode == "none":
        return t
    if n <= 0:
        return ""
    if len(t) <= n:
        return t
    if mode == "hard":
        return _clip_hard(t, n)
    return _clip_soft(t, n)


def _format_goal_hints_slim(goal_hints: Any, max_chars: int) -> str:
    if not isinstance(goal_hints, dict) or max_chars <= 0:
        return ""
    ids = goal_hints.get("goal_ids") or []
    ag = goal_hints.get("active_goals") or []
    bits: List[str] = []
    if isinstance(ids, list) and ids:
        bits.append("focus: " + ", ".join(str(x) for x in ids[:5]))
    if isinstance(ag, list):
        for g in ag[:3]:
            if not isinstance(g, dict):
                continue
            tid = str(g.get("id") or "").strip()
            txt = str(g.get("text") or "").strip()
            w = g.get("weight")
            label = tid or (txt[:80] if txt else "")
            if not label:
                continue
            if isinstance(w, (int, float)):
                bits.append(f"{label} (w={w:.2g})")
            else:
                bits.append(label)
    mission = str(goal_hints.get("mission") or "").strip()
    if mission:
        bits.append(f"mission: {mission[:200]}")
    if not bits:
        return ""
    return _clip(" | ".join(bits), max_chars)


# ── budget‑collapse (сохранён для обратной совместимости, но НЕ меняет структуру) ──

def _full_limits(collapse_level: int) -> Dict[str, int]:
    base = {
        "tcmd": 12000, "plugin_prompts": 6000, "dialogue_summary": 900,
        "sess_first": 1200, "pteacher": 800, "op_rules": 8000, "eph": 8000,
        "group_addon": 12000, "external": 4000, "vp_ctx": 8000,
        "ocr": 4000, "document_intake": 14000, "telegram_reply": 5200,
    }
    if collapse_level <= 0:
        return base
    if collapse_level == 1:
        return {**base, "tcmd": 7000, "plugin_prompts": 4000, "dialogue_summary": 500,
                "op_rules": 4500, "eph": 4500, "group_addon": 6000,
                "document_intake": 12000, "telegram_reply": 3600}
    if collapse_level == 2:
        return {**base, "tcmd": 4500, "plugin_prompts": 2400, "dialogue_summary": 320,
                "sess_first": 600, "pteacher": 400, "op_rules": 2400, "eph": 2400,
                "group_addon": 3600, "external": 2400, "document_intake": 8000,
                "telegram_reply": 2400}
    if collapse_level == 3:
        return {**base, "tcmd": 3500, "plugin_prompts": 1800, "dialogue_summary": 520,
                "sess_first": 720, "pteacher": 480, "op_rules": 2200, "eph": 2200,
                "group_addon": 3200, "external": 2800, "vp_ctx": 5600,
                "ocr": 2800, "document_intake": 7000, "telegram_reply": 2800}
    return {**base, "tcmd": 2500, "plugin_prompts": 1200, "dialogue_summary": 400,
            "sess_first": 600, "pteacher": 400, "op_rules": 1600, "eph": 1600,
            "group_addon": 2400, "external": 2000, "vp_ctx": 4800,
            "ocr": 2400, "document_intake": 5600, "telegram_reply": 2200}


def budget_for_tier(tier: PromptAssemblyTier) -> int:
    if tier == PromptAssemblyTier.FULL:
        return _env_int("BRAIN_USER_PROMPT_BUDGET_CHARS", 16000)
    if tier == PromptAssemblyTier.HOT_SLIM:
        return _env_int("BRAIN_USER_PROMPT_BUDGET_CHARS_HOT", 9000)
    return _env_int("BRAIN_USER_PROMPT_BUDGET_CHARS_IMAGE", 12000)


def _adaptive_budget_for_parts(tier: PromptAssemblyTier, parts: Dict[str, Any], base_budget: int) -> int:
    if base_budget <= 0:
        return base_budget
    if tier != PromptAssemblyTier.FULL:
        return base_budget
    ds = parts.get("dialogue_state")
    task_tier = ""
    last_intent = ""
    if isinstance(ds, dict):
        task_tier = str(ds.get("task_tier") or "").strip().lower()
        last_intent = str(ds.get("last_intent") or "").strip().lower()
    if task_tier in {"deep", "nested"} or last_intent in {"reasoning", "math"}:
        return base_budget
    txt = str(parts.get("user_text") or "")
    rd = parts.get("recent_dialogue")
    rd_n = len(rd) if isinstance(rd, list) else 0
    ds_len = len(str(parts.get("dialogue_summary") or ""))
    if len(txt) <= 700 and rd_n <= 6 and ds_len <= 1200:
        compact = _env_int("BRAIN_USER_PROMPT_BUDGET_CHARS_COMPACT", 13000)
        return max(8000, min(base_budget, compact))
    return base_budget


# ── Сборка ──

def _static_head(sys_prompt: str, tools_supplement: str, fmt_block: str, lb: Dict[str, str]) -> str:
    """
    Статичные блоки: System, Tools, Format — идентичны между запросами.
    System дублирует messages[0] намеренно: KV-кеш DeepSeek через OpenRouter
    держит префикс каждого сообщения независимо. Без System в messages[1]
    стабильный префикс ~300 символов (Tools + Format), с System — ~3500+
    символов до User message: → кеш покрывает 4000-5000 токенов.
    """
    return f"{lb['system']}\n{sys_prompt}\n\n{lb['tools']}\n{tools_supplement}\n\n{lb['format']}\n{fmt_block}\n\n"


def _dialogue_followup_hint(user_text: str, p: Dict[str, Any]) -> str:
    """Подсказка для коротких реплик внутри темы (не путать «откуда» с городом из profile)."""
    try:
        from core.behavior_store import _is_short_topic_followup
        from core.telegram_output_guard import _overlap_with_user_query

        if not _is_short_topic_followup(user_text):
            return ""
        tt = p.get("topic_tracking")
        cur = ""
        if isinstance(tt, dict):
            cur = str(tt.get("current") or "").strip()
        if len(cur) < 10:
            return ""
        if _overlap_with_user_query(user_text, cur) > 0.35:
            return ""
        return (
            f"(Реплика «{user_text.strip()[:80]}» продолжает тему «{cur[:140]}». "
            "Отвечай в этой теме; «откуда/почему/как» — про предмет обсуждения, "
            "не про город пользователя из profile.city.)\n"
        )
    except Exception:
        return ""


def _build_user_static_part(p: Dict[str, Any], lb: Dict[str, str], lim: Dict[str, int], tier: PromptAssemblyTier, collapse_level: int) -> str:
    """Только user_text — стабильная часть user-сообщения для KV-кеша."""
    user_text = str(p.get("user_text") or "")
    follow = _dialogue_followup_hint(user_text, p)
    return (
        f"{lb['user_msg']}\n{user_text}\n"
        f"{follow}"
        "(Ответь только на этот вопрос; Recent/Archive ниже — фон, не подменяй тему.)\n"
    )


def _build_dynamic_tail(p: Dict[str, Any], lb: Dict[str, str], lim: Dict[str, int], tier: PromptAssemblyTier, collapse_level: int,
                        profile: str = "standard", intent: str = "") -> str:
    """Вся динамика от хода к ходу: через модули (profile-aware) или старый fallback."""
    if profile and profile != "standard":
        p_mod = {**p, "_collapse_level": collapse_level, "_assembly_tier": tier}
        return _build_dynamic_tail_profile_aware(p_mod, lb, profile, intent)
    # standard: legacy tail с Recent/Archive (тесты и стабильный контракт)
    return _build_dynamic_tail_legacy(p, lb, lim, tier, collapse_level, profile=profile or "standard")


def _has_active_modules() -> bool:
    """Проверка, есть ли зарегистрированные модули."""
    try:
        from core.brain.prompt_modules import _MODULES
        return len(_MODULES) > 0
    except Exception:
        return False


def _build_dynamic_tail_profile_aware(p: Dict[str, Any], lb: Dict[str, str],
                                      profile: str, intent: str) -> str:
    """Profile-aware сборка: только нужные модули."""
    parts: List[str] = []

    # Recent через модули
    tail = _build_modules_tail(
        parts=p,
        profile=profile,
        intent=intent,
        ctx=p,  # p содержит context-like поля
    )
    if tail:
        parts.append(f"{lb['context']}\n{tail}\n")

    return "\n".join(parts)


def _build_dynamic_tail_legacy(
    p: Dict[str, Any],
    lb: Dict[str, str],
    lim: Dict[str, int],
    tier: PromptAssemblyTier,
    collapse_level: int,
    *,
    profile: str = "standard",
) -> str:
    """Старая сборка (для обратной совместимости)."""
    parts: List[str] = []

    # ── Sanitize dialogue before assembly ──
    try:
        from core.brain.cot_strip import sanitize_dialogue
    except Exception:
        sanitize_dialogue = None  # type: ignore

    # ── Recent messages (1–3) ──
    rd = p.get("recent_dialogue") or []
    if not isinstance(rd, list):
        rd = []
    if sanitize_dialogue is not None:
        rd = sanitize_dialogue(rd)
    try:
        from core.context_compression import normalize_dialogue_message_rows

        rd = normalize_dialogue_message_rows(rd)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'prompt_pack', e, exc_info=True)
    try:
        from core.brain.profile_registry import get_profile as _get_prof

        _recent_n = max(1, int(_get_prof(profile).recent_count or 3))
    except Exception:
        _recent_n = 3
    try:
        from core.context_compression import trim_dialogue_messages_paired

        _cap = _recent_n if _recent_n % 2 == 0 else _recent_n + 1
        rd = trim_dialogue_messages_paired(rd, max(2, _cap))
        if len(rd) > _recent_n:
            rd = rd[-_recent_n:]
    except Exception:
        rd = rd[-_recent_n:]
    parts.append(f"{lb['recent']}\n{rd}\n")

    # ── Archive (FIFO, 10–20) ──
    arch = p.get("message_archive") or []
    if not isinstance(arch, list):
        arch = []
    if sanitize_dialogue is not None:
        arch = sanitize_dialogue(arch)
    try:
        from core.context_compression import normalize_dialogue_message_rows

        arch = normalize_dialogue_message_rows(arch)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'prompt_pack', e, exc_info=True)
    try:
        from core.brain.profile_registry import get_profile as _get_prof_arch

        _prof_arch_n = max(0, int(_get_prof_arch(profile).archive_count or 0))
    except Exception:
        _prof_arch_n = 0
    arch_tail = _prof_arch_n if _prof_arch_n > 0 else _env_int("MESSAGE_ARCHIVE_PROMPT_TAIL", 8)
    # Чётность только для env-хвоста (пары user/assistant); profile.archive_count — точное N.
    if _prof_arch_n <= 0 and arch_tail % 2:
        arch_tail -= 1
    arch_tail = max(0, arch_tail)
    arch_slice = arch[-arch_tail:] if arch_tail and arch else []
    parts.append(f"{lb['archive']}\n{arch_slice}\n")

    # ── Context: всё остальное ──
    ctx_lines = _build_context_block(p, lb, lim, tier, collapse_level)
    if ctx_lines:
        parts.append(f"{lb['context']}\n{ctx_lines}\n")

    # ── Task/Goal ──
    task_lines = _build_task_block(p, lb)
    if task_lines:
        parts.append(f"{lb['task']}\n{task_lines}\n")

    return "\n".join(parts)


def _build_dynamic_context(p: Dict[str, Any], lb: Dict[str, str], lim: Dict[str, int], tier: PromptAssemblyTier, collapse_level: int,
                           profile: str = "standard", intent: str = "") -> str:
    """Собирает всю динамику: recent_messages, archive, context, task/goal.
    Сохранена для обратной совместимости (context_collapse и тесты)."""
    user_part = _build_user_static_part(p, lb, lim, tier, collapse_level)
    tail = _build_dynamic_tail(p, lb, lim, tier, collapse_level, profile=profile, intent=intent)
    if tail:
        return f"{user_part}\n{tail}"
    return user_part


def _build_context_block(p: Dict[str, Any], lb: Dict[str, str], lim: Dict[str, int], tier: PromptAssemblyTier, collapse_level: int) -> str:
    """Собирает блок Context из оставшихся полей (без persona/psychology/twin/predictive/style)."""
    lines: List[str] = []

    # Агентная инструкция (может быть большой, но идёт внутри динамики)
    ag = str(p.get("agent_inst") or "")
    if tier == PromptAssemblyTier.FULL and collapse_level >= 2:
        _stub = str(p.get("agent_inst_collapse_stub") or "").strip()
        if _stub:
            ag = _stub
    if ag:
        lines.append(f"- agent_inst: {ag}")

    lines.append(f"- user_id: {p.get('user_id')}")

    mf = p.get("memory_facts")
    if isinstance(mf, list):
        mf = mf[:6] if tier != PromptAssemblyTier.FULL else mf
    lines.append(f"- memory_facts: {mf}")

    lines.append(f"- dialogue_summary: {_clip(p.get('dialogue_summary'), lim['dialogue_summary'])}")
    lines.append(f"- grounding: {p.get('grounding_mini')}")

    _trb = str(p.get("telegram_reply_block") or "").strip()
    if _trb:
        lines.append(f"- telegram_reply_context:\n{_clip(_trb, lim['telegram_reply'])}")

    _di = _clip(p.get("document_intake_block"), lim["document_intake"])
    if str(_di or "").strip():
        lines.append(f"- document_intake:\n{_di}")

    lines.append(f"- user_facts: {p.get('user_facts')}")
    lines.append(f"- user_facts_meta: {p.get('user_facts_meta')}")
    lines.append(f"- routing_prefs_hint: {p.get('routing_prefs_hint') or ''}")
    lines.append(f"- task_facts: {p.get('task_facts')}")

    lines.append(f"- topic_tracking: {p.get('topic_tracking')}")
    lines.append(f"- group_context: {p.get('group_context')}")

    lines.append(f"- telegram_commands_catalog:\n{_clip(p.get('tcmd_cat'), lim['tcmd'])}")

    _pm = str(p.get("plugin_manifest_prompts") or "").strip()
    if _pm:
        lines.append(f"- plugin_manifest_prompts:\n{_clip(_pm, lim['plugin_prompts'])}")

    lines.append(f"- session_first_user_text: {_clip(p.get('sess_first'), lim['sess_first'])}")
    lines.append(f"- persona_teacher_addon: {_clip(p.get('pteacher'), lim['pteacher'])}")
    lines.append(f"- operator_rules: {_clip(p.get('operator_rules'), lim['op_rules'])}")
    lines.append(f"- ephemeral_lessons: {_clip(p.get('ephemeral_lessons'), lim['eph'])}")

    gca = _clip(p.get("group_chat_addon"), lim["group_addon"])
    if gca.strip():
        lines.append(f"- group_chat_addon:\n{gca}")

    lines.append(f"- facts_auto_ask_missing: {p.get('missing_facts')}")
    lines.append(f"- auto_ask_hint: {p.get('auto_ask_hint')}")
    lines.append(f"- behavior_policy: {p.get('behavior_policy')}")

    lines.append(f"- knowledge_hint_summary: {p.get('knowledge_summary')}")
    lines.append(f"- external_hint: {_clip(p.get('external_hint'), lim['external'])}")
    lines.append(f"- tool_routing_hint: {_clip(p.get('tool_routing_hint'), min(520, lim['external']))}")
    # ── Digest FIX: stable session digest (≤ 300 chars) ──
    _digest = str(p.get("session_digest") or "").strip()
    if _digest:
        lines.append(f"- session_digest: {_digest}")

    # Skills
    lines.append(f"- selected_skill: {p.get('skill_name')}")
    lines.append(f"- image_intent: {p.get('image_intent')}")
    lines.append(f"- skill_output: {p.get('skill_output')}")
    lines.append(f"- skill_hint: {p.get('skill_hint')}")

    # Vision
    vp = str(p.get("vp_ctx") or "")
    if vp.strip():
        lines.append(f"- vision_precaption: {vp}")

    lines.append(f"- ocr_text: {_clip(p.get('ocr_text'), lim['ocr'])}")

    # Tools
    lines.append(f"- tool_names: {p.get('tool_names')}")
    lines.append(f"- tools_mode: {p.get('tools_mode')}")
    _tfi = str(p.get("tool_names_full_index") or "").strip()
    if _tfi:
        lines.append(f"- tools_full_index:\n{_clip(_tfi, min(3600, lim['external'] * 2))}")

    lines.append(f"- urls_in_message: {p.get('urls_in_message')}")
    lines.append(f"- blended_style_stable: {p.get('blended_stable')}")

    lines.append(f"- dialogue_state: {p.get('dialogue_state')}")

    # scaffold — только при FULL, как раньше
    if tier == PromptAssemblyTier.FULL:
        sc = str(p.get("scaffold_part") or "\n").strip()
        if sc:
            lines.append(sc)

    return "\n".join(lines)


def _build_task_block(p: Dict[str, Any], lb: Dict[str, str]) -> str:
    """Task/Goal блок: intent_addon, goal_hints, goal_plan."""
    lines: List[str] = []

    intent = str(p.get("intent_addon") or "")
    if intent.strip():
        lines.append(f"- intent: {intent}")

    gh = p.get("goal_hints")
    if isinstance(gh, dict) and gh:
        lines.append(f"- goal_hints: {gh}")

    gp = p.get("goal_plan")
    if isinstance(gp, dict) and gp:
        lines.append(f"- goal_plan: {gp}")

    return "\n".join(lines)


def assemble_brain_user_prompt(
    tier: PromptAssemblyTier,
    parts: Dict[str, Any],
    *,
    collapse_level: int = 0,
) -> str:
    """
    Статичная голова + динамический хвост. collapse_level влияет только на лимиты _clip.
    """
    lb = _labels()
    lim = _full_limits(collapse_level)

    fmt_block = str(parts.get("static_format") or BRAIN_STATIC_FORMAT)
    tools_block = str(parts.get("static_tools") or BRAIN_TOOL_FAMILY_SUPPLEMENT)
    sys_p = str(parts.get("system_prompt_for_llm") or "")

    head = _static_head(sys_p, tools_block, fmt_block, lb)
    body = _build_dynamic_context(parts, lb, lim, tier, collapse_level)

    return f"{head}\n{body}\n"


def _assemble_split_inner(
    tier: PromptAssemblyTier,
    parts: Dict[str, Any],
    *,
    collapse_level: int = 0,
    profile: str = "standard",
    intent: str = "",
) -> Tuple[str, str]:
    """
    Возвращает (user_message, dynamic_tail) разбитые для KV‑cache:
      user_message  = статичная голова + user_text (только стабильные блоки)
      dynamic_tail  = recent + archive + context + task/goal (меняется каждый ход)
    Вставляется отдельным system‑сообщением после user-сообщения в массив messages.
    """
    lb = _labels()
    lim = _full_limits(collapse_level)

    fmt_block = str(parts.get("static_format") or BRAIN_STATIC_FORMAT)
    tools_block = str(parts.get("static_tools") or BRAIN_TOOL_FAMILY_SUPPLEMENT)
    sys_p = str(parts.get("system_prompt_for_llm") or "")

    head = _static_head(sys_p, tools_block, fmt_block, lb)
    user_part = _build_user_static_part(parts, lb, lim, tier, collapse_level)
    user_message = f"{head}\n{user_part}\n"

    dynamic_tail = _build_dynamic_tail(parts, lb, lim, tier, collapse_level, profile=profile, intent=intent)
    if dynamic_tail:
        dynamic_tail = f"\n{dynamic_tail}\n"
    else:
        dynamic_tail = ""

    return user_message, dynamic_tail


def assemble_split_with_budget(
    tier: PromptAssemblyTier,
    parts: Dict[str, Any],
    *,
    profile: str = "standard",
    intent: str = "",
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Раздельная сборка для KV‑cache: возвращает (user_message, dynamic_tail, meta).
    Для FULL: перебор collapse_level 0..4 пока суммарная длина <= budget (если budget > 0).
    meta как у assemble_with_budget.
    """
    budget = _adaptive_budget_for_parts(tier, parts, budget_for_tier(tier))
    meta: Dict[str, Any] = {"budget": budget, "collapse_level": 0, "tier": tier.value}

    if tier != PromptAssemblyTier.FULL or budget <= 0:
        user_msg, tail = _assemble_split_inner(tier, parts, collapse_level=0, profile=profile, intent=intent)
        meta["est_tokens"] = estimate_tokens_approx(user_msg + tail)
        meta["chars"] = len(user_msg) + len(tail)
        return user_msg, tail, meta

    # Batch profile: не коллапсим — модель обязана увидеть все вопросы
    if profile == "batch":
        user_msg, tail = _assemble_split_inner(tier, parts, collapse_level=0, profile=profile, intent=intent)
        meta["collapse_level"] = 0
        meta["chars"] = len(user_msg) + len(tail)
        meta["est_tokens"] = estimate_tokens_approx(user_msg + tail)
        meta["batch_skip_collapse"] = True
        return user_msg, tail, meta

    chosen = 0
    user_msg = ""
    tail = ""
    for level in range(5):
        chosen = level
        user_msg, tail = _assemble_split_inner(tier, parts, collapse_level=level, profile=profile, intent=intent)
        if len(user_msg) + len(tail) <= budget:
            break
    meta["collapse_level"] = chosen
    meta["chars"] = len(user_msg) + len(tail)
    meta["est_tokens"] = estimate_tokens_approx(user_msg + tail)
    if budget > 0 and len(user_msg) + len(tail) > budget:
        meta["budget_exceeded"] = True
    return user_msg, tail, meta


def assemble_with_budget(tier: PromptAssemblyTier, parts: Dict[str, Any], *,
                         profile: str = "standard", intent: str = "") -> Tuple[str, Dict[str, Any]]:
    """
    Для FULL: перебор collapse_level 0..4 пока len(prompt) > budget (если budget > 0).
    Возвращает (prompt, meta) с полями collapse_level, est_tokens, budget.
    """
    budget = _adaptive_budget_for_parts(tier, parts, budget_for_tier(tier))
    meta: Dict[str, Any] = {"budget": budget, "collapse_level": 0, "tier": tier.value}

    if tier != PromptAssemblyTier.FULL or budget <= 0:
        prompt = assemble_brain_user_prompt(tier, parts, collapse_level=0)
        meta["est_tokens"] = estimate_tokens_approx(prompt)
        meta["chars"] = len(prompt)
        return prompt, meta

    # Batch profile: не коллапсим — модель обязана увидеть все вопросы
    if profile == "batch":
        prompt = assemble_brain_user_prompt(tier, parts, collapse_level=0)
        meta["collapse_level"] = 0
        meta["chars"] = len(prompt)
        meta["est_tokens"] = estimate_tokens_approx(prompt)
        meta["batch_skip_collapse"] = True
        return prompt, meta

    chosen = 0
    prompt = ""
    for level in range(5):
        chosen = level
        prompt = assemble_brain_user_prompt(tier, parts, collapse_level=level)
        # assemble_brain_user_prompt не использует profile — это fallback
        if len(prompt) <= budget:
            break
    meta["collapse_level"] = chosen
    meta["chars"] = len(prompt)
    meta["est_tokens"] = estimate_tokens_approx(prompt)
    if budget > 0 and len(prompt) > budget:
        meta["budget_exceeded"] = True
    return prompt, meta
