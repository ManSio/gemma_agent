"""
Восстановление пустого ответа до сообщения пользователю (chat + brain).

Порядок: слот погоды → domain fallback → короткий retry LLM (general_empty_recovery).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


async def recover_empty_chat_reply(
    *,
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    ctx = context if isinstance(context, dict) else {}
    user_id = str(ctx.get("user_id") or "").strip()
    group_id = ctx.get("group_id")
    persisted = ctx.get("behavior_record") if isinstance(ctx.get("behavior_record"), dict) else None
    recent = ctx.get("recent_messages")
    if not isinstance(recent, list) and isinstance(persisted, dict):
        recent = persisted.get("recent_messages")

    try:
        from core.dialogue_slots import resolve_slot_for_turn, get_active_slot, SLOT_WEATHER_CITY

        slot_ctx = resolve_slot_for_turn(user_text, recent, persisted)
        if slot_ctx.force_weather or (
            persisted
            and get_active_slot(persisted)
            and str(get_active_slot(persisted).get("kind")) == SLOT_WEATHER_CITY
        ):
            from core.weather_reply import try_weather_reply

            wx = await try_weather_reply(
                user_text,
                persisted=persisted,
                user_id=user_id or None,
                group_id=str(group_id) if group_id is not None else None,
            )
            if wx and str(wx).strip():
                logger.info("[empty_reply_recovery] weather slot/direct ok user=%s", user_id)
                return str(wx).strip()
    except Exception as e:
        logger.debug("empty_reply_recovery weather: %s", e)

    try:
        from core.brain.text_helpers import looks_like_news_headlines_request
        from core.news_reply import (
            _is_news_headlines_request,
            _news_country_iso2,
            _search_pack,
            _user_facts_from_persisted,
            persist_news_digest_from_assistant_reply,
        )
        from core.telegram_output_guard import format_news_from_search

        if looks_like_news_headlines_request(user_text):
            facts = _user_facts_from_persisted(persisted)
            if _is_news_headlines_request(user_text, facts, recent):
                pack = await _search_pack(
                    user_text,
                    country=_news_country_iso2(facts),
                    user_id=str(user_id or ""),
                    timeout=22.0,
                    tag="empty_news_recovery",
                )
                if pack.get("ok"):
                    body = format_news_from_search(
                        str(pack.get("summary") or ""),
                        user_query=user_text,
                    )
                    if body and str(body).strip():
                        out = str(body).strip()[:4500]
                        if isinstance(persisted, dict):
                            persist_news_digest_from_assistant_reply(
                                out,
                                persisted=persisted,
                                context={"user_id": user_id, "group_id": group_id},
                            )
                        logger.info(
                            "[empty_reply_recovery] search news digest ok user=%s",
                            user_id,
                        )
                        MONITOR.inc("brain_empty_reply_news_digest_total")
                        return out
    except Exception as e:
        logger.debug("empty_reply_recovery news: %s", e)

    try:
        from core.brain.general_empty_recovery import dwg_cad_domain_fallback

        fb = dwg_cad_domain_fallback(user_text, recent_dialogue=recent)
        if fb and fb.strip():
            return fb.strip()
    except Exception as e:
        logger.debug("empty_reply_recovery dwg: %s", e)

    return ""


def empty_reply_user_message(*, recovered: bool = False, gate_blocked: bool = False) -> str:
    if recovered:
        return ""
    if gate_blocked:
        return (
            "Не удалось обработать короткую реплику в контексте диалога. "
            "Повтори с городом или темой в одной фразе, например: «погода в Минске»."
        )
    return (
        "Пустой ответ после обработки (фильтр ответа или модель). "
        "Повтори короче или укажи город/тему явно. "
        "Если повторяется — проверь OPENROUTER_MODEL_FREE на сервере (см. openrouter.ai/models)."
    )
