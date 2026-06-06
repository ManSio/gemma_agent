"""Подсказка для «инцидент» / «найди информацию» — опираться на недавний контекст диалога."""
from __future__ import annotations

import re
import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_INCIDENT_QUERY_RE = re.compile(
    r"(?ui)"
    r"(?:"
    r"инцидент\w*"
    r"|(?:найди|найти|собери|дай)\s+(?:вс[её]|полн\w*)?\s*(?:информаци\w*|данн\w*|сведени\w*)"
    r"|(?:про\s+)?(?:друг\w*\s+)?(?:стран\w*|событ\w*)"
    r")"
)

_CONTEXT_ENTITY_MARKERS = (
    "галац",
    "galati",
    "galatz",
    "беспилотник",
    "дрон",
    "герань",
    "румын",
    "жилой дом",
    "nato",
    "panel_nohup",
    "упал api",
    "api после",
    "деплой",
    "gemma_panel",
    "nohup_bot",
)


def _row_text(row: dict) -> str:
    return str(row.get("text") or row.get("content") or "").strip()


def _recent_user_snippets(recent_dialogue: Any, *, limit: int = 8) -> List[str]:
    if not isinstance(recent_dialogue, list):
        return []
    out: List[str] = []
    for row in reversed(recent_dialogue):
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "").lower() not in ("user", "human", ""):
            continue
        t = _row_text(row)
        if len(t) >= 24:
            out.append(t[:280])
        if len(out) >= limit:
            break
    return list(reversed(out))


def extract_incident_subject_from_dialogue(recent_dialogue: Any) -> Optional[str]:
    snippets = _recent_user_snippets(recent_dialogue)
    for sn in reversed(snippets):
        low = sn.lower()
        if any(m in low for m in _CONTEXT_ENTITY_MARKERS):
            return sn[:320]
    if snippets:
        return snippets[-1][:320]
    return None


def _incident_search_query(subject: str) -> str:
    sub = (subject or "").strip()[:220]
    if not sub:
        return ""
    low = sub.lower()
    if any(m in low for m in ("panel_nohup", "деплой", "упал", "api", "gemma_panel")):
        return f"{sub} диагностика логи восстановление"
    return sub


async def try_incident_followup_search_reply(
    user_text: str,
    *,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Follow-up «найди по инциденту» — поиск по теме из recent_dialogue, без «уточни какой»."""
    if not _INCIDENT_QUERY_RE.search(user_text or ""):
        return None
    try:
        from core.article_thread_followup import should_handle_article_thread_followup

        if should_handle_article_thread_followup(
            user_text or "", recent_dialogue, persisted
        ):
            return None
    except Exception:
        pass
    subject = extract_incident_subject_from_dialogue(recent_dialogue)
    if not subject:
        return None
    q = _incident_search_query(subject)
    if not q:
        return None
    try:
        from core.news_reply import _news_country_iso2, _search_pack, _user_facts_from_persisted
        from core.telegram_output_guard import format_news_from_search

        facts = _user_facts_from_persisted(persisted)
        pack = await _search_pack(
            q,
            country=_news_country_iso2(facts),
            user_id=str(user_id or ""),
            timeout=22.0,
            tag="incident_followup",
        )
        if not pack.get("ok"):
            return (
                f"По контексту («{subject[:120]}…») поиск сейчас не вернул сводку. "
                "Повторите запрос или уточните файл лога."
            )
        body = format_news_from_search(str(pack.get("summary") or ""), user_query=q)
        if body and str(body).strip():
            return str(body).strip()[:4500]
        summary = str(pack.get("summary") or "").strip()
        if summary:
            return summary[:4500]
    except Exception as e:
        logger.debug("incident_followup search: %s", e)
    return None


def build_incident_context_hint(
    user_text: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]] = None,
) -> str:
    try:
        from core.article_thread_followup import should_handle_article_thread_followup

        if should_handle_article_thread_followup(
            user_text or "", recent_dialogue, persisted
        ):
            return ""
    except Exception:
        pass
    if not _INCIDENT_QUERY_RE.search(user_text or ""):
        return ""
    subject = extract_incident_subject_from_dialogue(recent_dialogue)
    if not subject:
        return (
            "(Запрос про инцидент без уточнения: если в recent_dialogue уже была конкретная новость — "
            "используй её; иначе один короткий уточняющий вопрос.)"
        )
    return (
        f"(Контекст инцидента из недавнего диалога — приоритет: «{subject[:240]}». "
        "Не проси заново «какой инцидент», если тема уже обсуждалась выше.)"
    )
