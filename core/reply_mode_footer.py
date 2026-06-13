"""Подпись режима внизу ответа (admin) + машинная метка для grep/turns.

Метка: [gemma:mf i=...|m=...|s=...|p=...|t=...]
Поиск: turns_search.py, grep turns.jsonl или лог TG по ``gemma:mf``.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Mapping, Optional

from core.dialogue_slots import SLOT_ARTICLE_THREAD, SLOT_SPATIAL_PROJECT, SLOT_WEATHER_CITY, get_active_slot
from core.runtime_telegram_settings import effective_bool

_TAG_RE = re.compile(r"\[gemma:mf[^\]]*\]\s*$", re.MULTILINE)

_SLOT_LABELS = {
    SLOT_SPATIAL_PROJECT: "планировка",
    SLOT_WEATHER_CITY: "погода",
    SLOT_ARTICLE_THREAD: "статья",
}

_INTENT_LABELS = {
    "spatial_design": "планировка",
    "image_generation": "картинка",
    "news": "новости",
    "weather": "погода",
    "math": "счёт",
    "general": "диалог",
    "empty": "пусто",
    "reasoning": "рассуждение",
    "test": "тест",
}

_MODULE_LABELS = {
    "spatial_design": "spatial",
    "image_generator": "image_gen",
    "chat-orchestrator": "brain",
    "__fallback__": "fallback",
    "news_reply": "news",
}


def footer_audience_mode() -> str:
    return (os.getenv("TELEGRAM_REPLY_MODE_FOOTER") or "off").strip().lower()


def footer_enabled() -> bool:
    return footer_audience_mode() not in {"", "0", "off", "false", "no"}


def footer_visible_for_user(*, user_id: str, is_admin: bool) -> bool:
    if not footer_enabled():
        return False
    mode = footer_audience_mode()
    if mode == "all":
        return True
    if mode == "admin":
        return bool(is_admin)
    if mode == "owner":
        owner = (os.getenv("OWNER_TELEGRAM_ID") or "").strip()
        return bool(owner) and str(user_id).strip() == owner
    return False


def _slug(value: str, *, max_len: int = 32) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-./]", "", (value or "").strip().replace(" ", "_"))
    return s[:max_len] if s else ""


def _human_label(
    *,
    intent: str,
    module: str,
    slot_kind: str,
    phase: str,
    pre_llm: str,
) -> str:
    if slot_kind == SLOT_SPATIAL_PROJECT:
        base = "Планировка"
        if phase in ("awaiting_feedback", "confirmed", "done", "cancelled"):
            phase_ru = {
                "awaiting_feedback": "сверка",
                "confirmed": "рисую",
                "done": "готово",
                "cancelled": "сброс",
            }.get(phase, phase)
            return f"{base} · {phase_ru}"
        return base
    if slot_kind:
        return _SLOT_LABELS.get(slot_kind, slot_kind)
    if intent in _INTENT_LABELS:
        return _INTENT_LABELS[intent]
    if module in _MODULE_LABELS:
        return _MODULE_LABELS[module]
    if pre_llm:
        return f"без LLM · {pre_llm}"
    if module:
        return module.replace("_", " ")
    if intent:
        return intent
    return "режим"


def build_mode_footer_fields(
    *,
    output_meta: Optional[Mapping[str, Any]] = None,
    plan_module: str = "",
    route_context: Optional[Mapping[str, Any]] = None,
    persisted: Optional[Mapping[str, Any]] = None,
    trace_id: str = "",
) -> Dict[str, str]:
    """Собрать поля для человеческой строки и машинной метки."""
    om = dict(output_meta or {})
    rc = dict(route_context or {})
    intent = str(rc.get("route_intent") or om.get("route_intent") or "").strip().lower()
    module = str(om.get("module") or plan_module or rc.get("plan_module") or "").strip()
    phase = str(om.get("phase") or om.get("spatial_phase") or "").strip().lower()
    profile = str(om.get("brain_profile") or om.get("router_profile") or rc.get("route_profile") or "").strip()
    pre_llm = str(rc.get("route_pre_llm") or om.get("planner_bypass") or "").strip()
    skill = str(rc.get("route_skill") or om.get("skill") or "").strip()

    slot_kind = ""
    tsa = rc.get("turn_state_audit")
    if isinstance(tsa, dict) and tsa.get("active_slot_kind"):
        slot_kind = str(tsa.get("active_slot_kind") or "").strip()
    if not slot_kind:
        slot_kind = str(rc.get("active_dialogue_slot_kind") or "").strip()
    if not slot_kind:
        slot = get_active_slot(persisted if isinstance(persisted, dict) else None)
        if slot:
            slot_kind = str(slot.get("kind") or "").strip()
    om_module = str(om.get("module") or plan_module or "").strip().lower()
    om_phase = str(om.get("phase") or om.get("spatial_phase") or "").strip().lower()
    if om_module == "spatial_design" and om_phase:
        slot_kind = SLOT_SPATIAL_PROJECT

    human = _human_label(intent=intent, module=module, slot_kind=slot_kind, phase=phase, pre_llm=pre_llm)
    if profile and human == "диалог":
        human = f"диалог · {profile}"

    tag_parts = ["gemma:mf", "v1"]
    if intent:
        tag_parts.append(f"i={_slug(intent)}")
    if module:
        tag_parts.append(f"m={_slug(module)}")
    if slot_kind:
        tag_parts.append(f"s={_slug(slot_kind)}")
    if phase:
        tag_parts.append(f"p={_slug(phase)}")
    if pre_llm:
        tag_parts.append(f"b={_slug(pre_llm)}")
    if skill:
        tag_parts.append(f"k={_slug(skill)}")
    tid = _slug((trace_id or str(om.get("trace_id") or ""))[:12], max_len=12)
    if tid:
        tag_parts.append(f"t={tid}")

    return {
        "human": human,
        "machine_tag": f"[{'|'.join(tag_parts)}]",
    }


def format_mode_footer(fields: Mapping[str, str]) -> str:
    human = str(fields.get("human") or "режим").strip()
    tag = str(fields.get("machine_tag") or "[gemma:mf]").strip()
    return f"───\nРежим: {human}\n{tag}"


def append_mode_footer(
    text: str,
    *,
    fields: Mapping[str, str],
) -> str:
    body = (text or "").rstrip()
    if not body:
        return body
    if _TAG_RE.search(body):
        return body
    foot = format_mode_footer(fields)
    return f"{body}\n\n{foot}"


def should_skip_mode_footer(output_meta: Optional[Mapping[str, Any]]) -> bool:
    om = output_meta or {}
    if om.get("no_mode_footer") or om.get("skip_mode_footer"):
        return True
    if om.get("confirmation") or om.get("slash_exclusive"):
        return True
    mod = str(om.get("module") or "")
    if mod in ("admin_module", "greetings"):
        return True
    return False
