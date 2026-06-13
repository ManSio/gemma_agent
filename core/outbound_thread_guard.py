"""Outbound pre-send guard: followup по нити, не agent-meta."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.brain.env import env_flag
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

_AGENT_META_REPLY_RE = re.compile(
    r"(?i)(ограничени[яе]\s+(?:llm|модел)|я\s+как\s+(?:языковая\s+)?модел|"
    r"не\s+могу\s+проверить\s+баланс|мои\s+возможност|архитектур[аы]\s+бот|"
    r"вычислительн\w*\s+цикл|лимит\w*\s+запрос|пока\s+не\s+пройден\s+набор\s+проверок)",
)


def outbound_thread_guard_enabled() -> bool:
    """Включён ли outbound guard для immediate thread followup."""
    return env_flag("OUTBOUND_THREAD_GUARD_ENABLED", default=True)


def _ctx_from_meta(output_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Собрать discourse/meaning контекст из output meta."""
    meta = output_meta if isinstance(output_meta, dict) else {}
    ctx: Dict[str, Any] = {}
    for key in (
        "discourse_resolution",
        "turn_meaning",
        "turn_meaning_audit",
        "turn_state_audit",
        "recent_dialogue",
        "session_task",
        "conversation_epoch",
    ):
        if key in meta and meta.get(key) is not None:
            ctx[key] = meta.get(key)
    dr = meta.get("discourse_audit")
    if isinstance(dr, dict) and "discourse_resolution" not in ctx:
        ctx["discourse_resolution"] = dr
    if not ctx.get("recent_dialogue"):
        rd = meta.get("recent_messages")
        if isinstance(rd, list):
            ctx["recent_dialogue"] = rd
    return ctx


def _anchor_from_ctx(ctx: Dict[str, Any]) -> str:
    """Активный anchor нити из discourse / turn_meaning."""
    try:
        from core.feedback_contract import _active_anchor_from_context

        return _active_anchor_from_context(ctx)
    except Exception as e:
        logger.debug("outbound anchor: %s", e)
        return ""


def _reply_looks_like_agent_meta(reply: str) -> bool:
    """Ответ уходит в meta про агента/LLM, а не по теме нити."""
    rep = (reply or "").strip()
    if not rep:
        return False
    try:
        from core.brain.text_helpers import is_bot_operational_diag_reply

        if is_bot_operational_diag_reply(rep):
            return True
    except Exception as e:
        logger.debug("outbound operational_diag: %s", e)
    try:
        from core.product_behavior import _BOT_SYSTEM_LEAK_RE

        if _BOT_SYSTEM_LEAK_RE.search(rep):
            return True
    except Exception as e:
        logger.debug("outbound bot_leak: %s", e)
    if _AGENT_META_REPLY_RE.search(rep):
        return True
    low = rep.lower()
    if "openrouter" in low and ("баланс" in low or "api" in low or "ключ" in low):
        return True
    return False


def detect_thread_followup_issues(
    user_text: str,
    reply: str,
    output_meta: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Проблемы доставки: meta-ответ на immediate followup активной нити."""
    if not outbound_thread_guard_enabled():
        return []
    ut = (user_text or "").strip()
    rep = (reply or "").strip()
    if not ut or not rep:
        return []
    ctx = _ctx_from_meta(output_meta)
    try:
        from core.discourse_thread_contract import immediate_thread_followup, thread_content_tokens

        if not immediate_thread_followup(ut, ctx):
            return []
        anchor = _anchor_from_ctx(ctx)
        if not anchor:
            return []
        tm = ctx.get("turn_meaning")
        referent = ""
        if isinstance(tm, dict):
            referent = str(tm.get("referent") or "").strip().lower()
        if referent == "agent":
            return []
        anchor_tok = thread_content_tokens(anchor, min_len=4)
        reply_tok = thread_content_tokens(rep, min_len=4)
        overlap = anchor_tok & reply_tok
        min_overlap = max(1, int((os.getenv("OUTBOUND_THREAD_MIN_TOKEN_OVERLAP") or "1").strip() or "1"))
        if _reply_looks_like_agent_meta(rep):
            MONITOR.inc("outbound_thread_guard_agent_meta_total")
            return ["thread_followup_agent_meta"]
        if anchor_tok and len(overlap) < min_overlap and len(rep) > 72:
            MONITOR.inc("outbound_thread_guard_no_overlap_total")
            return ["thread_followup_no_anchor_overlap"]
    except Exception as e:
        logger.debug("detect_thread_followup_issues: %s", e)
    return []


def recover_thread_followup_reply(
    user_text: str,
    reply: str,
    issues: List[str],
    *,
    output_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Замена meta/drift на продолжение по anchor (без regen LLM)."""
    if not issues:
        return reply
    ctx = _ctx_from_meta(output_meta)
    anchor = _anchor_from_ctx(ctx)
    short = (anchor or (user_text or "")).strip()[:160]
    if "thread_followup_agent_meta" in issues or "thread_followup_no_anchor_overlap" in issues:
        return (
            f"По вашему вопросу «{short}» короткое «почему так» — про ту же тему. "
            "Уточни, что разобрать: причину, механизм или как это доказали — отвечу по сути, "
            "без meta про ограничения бота."
        )[:900]
    return reply
