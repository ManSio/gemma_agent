"""Узкий user-промпт (hot path slim) — условия допустимости."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from core.task_depth import tier_prefers_thorough
from core.prompt_routing import (
    text_warrants_textbook_rag,
    user_requests_dialogue_analysis_effective,
)

from core.brain.env import env_flag


def skill_output_heavy(skill_output: Any) -> bool:
    if not skill_output:
        return False
    if isinstance(skill_output, dict):
        try:
            raw = json.dumps(skill_output, ensure_ascii=False, default=str)
        except Exception:
            raw = str(skill_output)
        return len(raw) > 900
    return len(str(skill_output)) > 900


def _brain_slim_shared_rejects(
    *,
    user_text: str,
    context: Dict[str, Any],
    urls_chron: Optional[List[str]],
    missing_facts: List[Any],
    skill_name: Optional[str],
    skill_output: Any,
    image_intent: Optional[str],
    group_transcript_compact: str,
    group_chat_addon_len: int,
    group_in_groups_env: str,
) -> bool:
    """Return True when shared slim guards block hot/chat context slim."""
    ut = (user_text or "").strip()
    if not ut:
        return True
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
    fc = context.get("file_context") if isinstance(context.get("file_context"), dict) else {}
    if fc.get("file_type") == "image" and str(fc.get("local_path") or "").strip():
        return True
    ocr = context.get("ocr_text")
    if ocr and len(str(ocr)) > 600:
        return True
    if str(context.get("telegram_reply_context") or "").strip():
        return True
    doc = context.get("document_intake")
    if isinstance(doc, dict) and doc:
        return True
    op = str(context.get("operator_rules_brain_addon") or "")
    if len(op) > 1400:
        return True
    ep = str(context.get("ephemeral_lessons_brain_addon") or "")
    if len(ep) > 1800:
        return True
    gid = context.get("group_id")
    if gid:
        if not env_flag(group_in_groups_env, default=False):
            return True
        try:
            gt_max = max(0, int(os.getenv("BRAIN_HOT_PATH_SLIM_MAX_GROUP_TRANSCRIPT_CHARS", "320")))
        except ValueError:
            gt_max = 320
        if gt_max and len((group_transcript_compact or "").strip()) > gt_max:
            return True
        try:
            ga_max = max(0, int(os.getenv("BRAIN_HOT_PATH_SLIM_MAX_GROUP_ADDON_CHARS", "900")))
        except ValueError:
            ga_max = 900
        if ga_max and group_chat_addon_len > ga_max:
            return True
    return False


def brain_hot_path_slim_eligible(
    *,
    user_text: str,
    context: Dict[str, Any],
    use_slim_image: bool,
    skill_name: Optional[str],
    skill_output: Any,
    image_intent: Optional[str],
    missing_facts: List[Any],
    group_transcript_compact: str,
    group_chat_addon_len: int,
    task_tier: Optional[str] = None,
    urls_chron: Optional[List[str]] = None,
) -> bool:
    """
    Узкий user-промпт для типичного ЛС без картинки/URL/тяжёлого группового контекста.
    Включается только если не сработал image-slim и не нужен полный каркас.
    """
    if not env_flag("BRAIN_HOT_PATH_SLIM", default=True):
        return False
    if use_slim_image:
        return False
    ds = context.get("dialogue_state") if isinstance(context.get("dialogue_state"), dict) else {}
    tier = (task_tier if task_tier is not None else str(ds.get("task_tier") or "")).strip()
    if tier_prefers_thorough(tier):
        return False
    ut = (user_text or "").strip()
    if not ut:
        return False
    try:
        lim = max(80, int(os.getenv("BRAIN_HOT_PATH_SLIM_MAX_USER_CHARS", "520")))
    except ValueError:
        lim = 520
    ph0 = context.get("predictive_hint") if isinstance(context.get("predictive_hint"), dict) else {}
    if ph0.get("terse_mode") and env_flag("BRAIN_TERSE_EXPAND_HOT_SLIM_USER_CHARS", default=True):
        try:
            bonus = max(0, int(os.getenv("BRAIN_TERSE_HOT_SLIM_CHAR_BONUS", "400")))
        except ValueError:
            bonus = 400
        try:
            cap = max(lim, int(os.getenv("BRAIN_TERSE_HOT_SLIM_MAX_CAP", "2000")))
        except ValueError:
            cap = 2000
        lim = min(lim + bonus, cap)
    if len(ut) > lim:
        return False
    if _brain_slim_shared_rejects(
        user_text=user_text,
        context=context,
        urls_chron=urls_chron,
        missing_facts=missing_facts,
        skill_name=skill_name,
        skill_output=skill_output,
        image_intent=image_intent,
        group_transcript_compact=group_transcript_compact,
        group_chat_addon_len=group_chat_addon_len,
        group_in_groups_env="BRAIN_HOT_PATH_SLIM_IN_GROUPS",
    ):
        return False
    return True


def brain_chat_context_slim_eligible(
    *,
    user_text: str,
    context: Dict[str, Any],
    task_tier: str,
    urls_chron: List[str],
    missing_facts: List[Any],
    skill_name: Optional[str],
    skill_output: Any,
    image_intent: Optional[str],
    group_transcript_compact: str,
    group_chat_addon_len: int,
) -> bool:
    """
    Длинные «чистые» реплики (без URL в тексте/нити): можно убрать полный tools index
    и второстепенные куски external_hint без hot_path_slim (лимит длины user_text).
    """
    if not env_flag("BRAIN_CHAT_CONTEXT_SLIM", default=True):
        return False
    if tier_prefers_thorough((task_tier or "").strip()):
        return False
    if _brain_slim_shared_rejects(
        user_text=user_text,
        context=context,
        urls_chron=urls_chron,
        missing_facts=missing_facts,
        skill_name=skill_name,
        skill_output=skill_output,
        image_intent=image_intent,
        group_transcript_compact=group_transcript_compact,
        group_chat_addon_len=group_chat_addon_len,
        group_in_groups_env="BRAIN_CHAT_CONTEXT_SLIM_IN_GROUPS",
    ):
        return False
    ph = context.get("predictive_hint") if isinstance(context.get("predictive_hint"), dict) else {}
    sp = ph.get("skill_priority")
    if isinstance(sp, list) and sp:
        return False
    return True
