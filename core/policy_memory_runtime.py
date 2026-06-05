"""
Policy-dependent memory (runtime): слоты + телеметрия для turns / offline parity.

Опора: EASMO-style policy memory (offline `core/research/policy_memory.py`),
write-manage-read loop (Memory for Autonomous LLM Agents, 2026), HITL correction bus.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HINT_TAG_PREFIXES: tuple[str, ...] = (
    "ARTICLE_THREAD",
    "ARTICLE_THREAD_TOPIC",
    "FACTS_PENDING",
    "IMAGE_EDIT_SESSION",
    "WEATHER_SLOT",
    "RECHECK_ANCHOR",
    "REMINDER_CONTEXT",
    "WEB_NOT_RSS",
    "USER_REMARK",
    "CORRECTION_PENDING",
)

_WEB_NOT_RSS_RE = re.compile(
    r"(?ui)(?:"
    r"не\s+(?:через\s+)?rss|"
    r"без\s+rss|"
    r"из\s+интернет|"
    r"в\s+сети|"
    r"web\s+search|"
    r"не\s+лент"
    r")"
)


def _routing_prefs(rec: Dict[str, Any]) -> Dict[str, Any]:
    rp = rec.get("routing_prefs")
    return dict(rp) if isinstance(rp, dict) else {}


def _policy_slots_bucket(rec: Dict[str, Any]) -> Dict[str, Any]:
    rp = _routing_prefs(rec)
    ps = rp.get("policy_slots")
    if isinstance(ps, dict):
        return dict(ps)
    return {}


def _save_policy_slots(rec: Dict[str, Any], slots: Dict[str, Any]) -> None:
    rp = _routing_prefs(rec)
    rp["policy_slots"] = slots
    rec["routing_prefs"] = rp


def detect_web_not_rss_preference(user_text: str) -> bool:
    return bool(_WEB_NOT_RSS_RE.search(user_text or ""))


def update_policy_slots_on_user_turn(
    rec: Dict[str, Any],
    user_text: str,
    recent_dialogue: Any = None,
    *,
    user_id: str = "",
) -> None:
    """Обновить routing_prefs.policy_slots после реплики пользователя (до ответа)."""
    if not isinstance(rec, dict):
        return
    slots = _policy_slots_bucket(rec)
    ut = (user_text or "").strip()
    changed = False

    if detect_web_not_rss_preference(ut):
        pref = dict(slots.get("user_pref") or {})
        if not pref.get("web_over_rss"):
            pref["web_over_rss"] = True
            slots["user_pref"] = pref
            changed = True

    try:
        from core.dialogue_recheck_anchor import (
            last_substantive_user_question,
            looks_like_recheck_last_answer,
        )

        if looks_like_recheck_last_answer(ut):
            last_q = last_substantive_user_question(recent_dialogue, skip_current=True)
            if last_q:
                slots["recheck_anchor"] = {"last_user_question": last_q[:320]}
                changed = True
    except Exception as e:
        logger.debug("policy_slots recheck: %s", e)

    uid = (user_id or "").strip()
    if uid:
        try:
            from core.reminder_dispatch import list_active_reminders_sorted

            active = list_active_reminders_sorted(uid)
            if active:
                top = active[0]
                text = str(top.get("text") or top.get("body") or "").strip()
                rid = str(top.get("id") or top.get("reminder_id") or "")
                if text or rid:
                    slots["active_reminder"] = {
                        "text": text[:200],
                        "id": rid[:32],
                    }
                    changed = True
            elif "active_reminder" in slots:
                slots.pop("active_reminder", None)
                changed = True
        except Exception as e:
            logger.debug("policy_slots reminder: %s", e)

    try:
        from core.dialogue_slots import get_active_slot, SLOT_ARTICLE_THREAD

        slot = get_active_slot(rec)
        if slot and str(slot.get("kind") or "") == SLOT_ARTICLE_THREAD:
            meta = slot.get("meta") if isinstance(slot.get("meta"), dict) else {}
            topic = str(meta.get("topic") or "").strip()
            if topic:
                slots["article_thread"] = {"topic": topic[:320]}
                changed = True
    except Exception as e:
        logger.debug("policy_slots article: %s", e)

    try:
        from core.user_facts import has_pending_facts_confirmation

        if has_pending_facts_confirmation(rec):
            pending = rec.get("pending_facts_confirmation") or {}
            if isinstance(pending, dict):
                country = str(pending.get("country") or pending.get("country_name") or "").strip()
                slots["pending_facts"] = {
                    "country": country[:120],
                    "awaiting": "да/нет",
                }
                changed = True
        elif "pending_facts" in slots:
            slots.pop("pending_facts", None)
            changed = True
    except Exception as e:
        logger.debug("policy_slots facts: %s", e)

    uf = rec.get("user_facts")
    if isinstance(uf, dict):
        city = str(uf.get("city") or uf.get("home_city") or "").strip()
        if city:
            geo = dict(slots.get("geo") or {})
            if geo.get("city") != city:
                geo["city"] = city[:120]
                slots["geo"] = geo
                changed = True

    if changed:
        _save_policy_slots(rec, slots)


def extract_policy_slots(
    rec: Optional[Dict[str, Any]],
    recent_dialogue: Any = None,
    user_text: str = "",
    *,
    user_id: str = "",
    chat_id: str = "",
) -> Dict[str, Any]:
    """Словарь слотов как в offline ACC / policy_memory."""
    out: Dict[str, Any] = {}
    if isinstance(rec, dict):
        out.update(_policy_slots_bucket(rec))

    try:
        from core.dialogue_slots import get_active_slot, SLOT_ARTICLE_THREAD

        if isinstance(rec, dict):
            slot = get_active_slot(rec)
            if slot and str(slot.get("kind") or "") == SLOT_ARTICLE_THREAD:
                meta = slot.get("meta") if isinstance(slot.get("meta"), dict) else {}
                topic = str(meta.get("topic") or "").strip()
                if topic:
                    out.setdefault("article_thread", {"topic": topic[:320]})
    except Exception as e:
        logger.debug("extract article slot: %s", e)

    try:
        from core.article_thread_followup import extract_article_thread_subject

        subj = extract_article_thread_subject(recent_dialogue, rec if isinstance(rec, dict) else None)
        if subj and len(subj.strip()) >= 12:
            cur = dict(out.get("article_thread") or {})
            cur["topic"] = subj.strip()[:320]
            out["article_thread"] = cur
    except Exception as e:
        logger.debug("extract article subject: %s", e)

    try:
        from core.dialogue_recheck_anchor import (
            last_substantive_user_question,
            looks_like_recheck_last_answer,
        )

        if looks_like_recheck_last_answer(user_text):
            last_q = last_substantive_user_question(recent_dialogue, skip_current=True)
            if last_q:
                out["recheck_anchor"] = {"last_user_question": last_q[:320]}
    except Exception as e:
        logger.debug("extract recheck: %s", e)

    try:
        from core.user_facts import has_pending_facts_confirmation

        if isinstance(rec, dict) and has_pending_facts_confirmation(rec):
            pending = rec.get("pending_facts_confirmation") or {}
            if isinstance(pending, dict):
                country = str(pending.get("country") or pending.get("country_name") or "").strip()
                out["pending_facts"] = {
                    "country": country[:120],
                    "awaiting": "да/нет",
                }
    except Exception as e:
        logger.debug("extract pending facts: %s", e)

    uid = (user_id or "").strip()
    cid = (chat_id or "").strip()
    if uid and cid:
        try:
            from core.image_edit_session import get_image_edit_session

            doc = get_image_edit_session(uid, cid)
            if doc:
                lp = str(doc.get("last_prompt") or doc.get("prompt") or "").strip()
                out["image_edit_session"] = {"last_prompt": lp[:240]} if lp else {"active": True}
        except Exception as e:
            logger.debug("extract image session: %s", e)

    if isinstance(rec, dict):
        uf = rec.get("user_facts")
        if isinstance(uf, dict) and uf:
            out["user_facts"] = {
                k: str(v)[:120]
                for k, v in list(uf.items())[:12]
                if v is not None and str(v).strip()
            }

    return out


def hint_tags_from_text(hint: str) -> List[str]:
    text = hint or ""
    tags: List[str] = []
    for prefix in HINT_TAG_PREFIXES:
        if prefix in text and prefix not in tags:
            tags.append(prefix)
    if "Обязательная правка после негативной оценки" in text and "CORRECTION_PENDING" not in tags:
        tags.append("CORRECTION_PENDING")
    if "(Перепроверка:" in text and "RECHECK_ANCHOR" not in tags:
        tags.append("RECHECK_ANCHOR")
    return tags


def _recheck_compact_hint(user_text: str, recent_dialogue: Any) -> str:
    try:
        from core.dialogue_recheck_anchor import (
            last_substantive_user_question,
            looks_like_recheck_last_answer,
        )

        if not looks_like_recheck_last_answer(user_text):
            return ""
        last_q = last_substantive_user_question(recent_dialogue, skip_current=True)
        if not last_q:
            return "RECHECK_ANCHOR: перепроверь последний ответ пользователя, не старую тему."
        return f"RECHECK_ANCHOR: приоритетный вопрос «{last_q[:280]}»."
    except Exception:
        return ""


def _reminder_context_hint(user_text: str, user_id: str) -> str:
    uid = (user_id or "").strip()
    if not uid:
        return ""
    try:
        from core.reminder_nl import looks_like_cancel_reminder_request
        from core.reminder_dispatch import list_active_reminders_sorted

        active = list_active_reminders_sorted(uid)
        if not active:
            return ""
        labels = [
            str(r.get("text") or r.get("body") or "").strip()[:80]
            for r in active[:3]
            if str(r.get("text") or r.get("body") or "").strip()
        ]
        if not labels:
            return ""
        joined = "; ".join(labels)
        if looks_like_cancel_reminder_request(user_text):
            return (
                f"REMINDER_CONTEXT: активные напоминания ({len(active)}): {joined}. "
                "Пользователь просит отмену — сними нужное, не создавай новое."
            )
        return f"REMINDER_CONTEXT: активные напоминания: {joined}."
    except Exception as e:
        logger.debug("reminder hint: %s", e)
        return ""


def _web_not_rss_hint(rec: Optional[Dict[str, Any]], user_text: str) -> str:
    slots = extract_policy_slots(rec, user_text=user_text)
    pref = slots.get("user_pref") if isinstance(slots.get("user_pref"), dict) else {}
    if pref.get("web_over_rss") or detect_web_not_rss_preference(user_text):
        return (
            "WEB_NOT_RSS: пользователь просил новости из интернета / без RSS; "
            "не подменяй web-search на ленту RSS."
        )
    if isinstance(rec, dict):
        rp = _routing_prefs(rec)
        ps = rp.get("policy_slots")
        if isinstance(ps, dict):
            up = ps.get("user_pref")
            if isinstance(up, dict) and up.get("web_over_rss"):
                return (
                    "WEB_NOT_RSS: в сессии зафиксировано предпочтение web over RSS; "
                    "не уходи в generic RSS digest."
                )
    return ""


def _user_remark_hint(rec: Optional[Dict[str, Any]], user_text: str) -> str:
    if not isinstance(rec, dict):
        return ""
    try:
        from core.dialogue_feedback_signals import build_user_remark_hint

        rp = _routing_prefs(rec)
        block = build_user_remark_hint(user_text=user_text, routing_prefs=rp)
        if not block.strip():
            return ""
        return f"USER_REMARK: {block.strip()[:400]}"
    except Exception as e:
        logger.debug("user remark hint: %s", e)
        return ""


def _pending_facts_hint_bridge(rec: Optional[Dict[str, Any]]) -> str:
    if not isinstance(rec, dict):
        return ""
    try:
        from core.user_facts import has_pending_facts_confirmation

        if not has_pending_facts_confirmation(rec):
            return ""
        pending = rec.get("pending_facts_confirmation") or {}
        if not isinstance(pending, dict):
            return "FACTS_PENDING: ждём подтверждение факта (да/нет); не переключай intent на news."
        country = str(pending.get("country") or pending.get("country_name") or "").strip()
        if country:
            return (
                f"FACTS_PENDING: ждём подтверждение «да/нет» для страны {country}; "
                "не уходи в новости/RSS без явной смены темы."
            )
        return "FACTS_PENDING: ждём подтверждение факта (да/нет); не переключай intent на news."
    except Exception as e:
        logger.debug("pending_facts hint bridge: %s", e)
        return ""


def _correction_pending_flag(rec: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(rec, dict):
        return False
    rp = _routing_prefs(rec)
    pending = rp.get("pending_correction")
    if not isinstance(pending, dict):
        return False
    return int(pending.get("turns_left") or 0) > 0


def build_policy_memory_hints(
    user_text: str,
    recent_dialogue: Any,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: str = "",
    chat_id: str = "",
) -> str:
    """Дополнительные policy-hints (вызывается из slot_external_hint)."""
    parts: List[str] = []
    rec = persisted if isinstance(persisted, dict) else {}

    rc = _recheck_compact_hint(user_text, recent_dialogue)
    if rc:
        parts.append(rc)
    rm = _reminder_context_hint(user_text, user_id)
    if rm:
        parts.append(rm)
    wn = _web_not_rss_hint(rec, user_text)
    if wn:
        parts.append(wn)
    ur = _user_remark_hint(rec, user_text)
    if ur:
        parts.append(ur)
    pf = _pending_facts_hint_bridge(rec)
    if pf:
        parts.append(pf)
    if _correction_pending_flag(rec):
        parts.append(
            "CORRECTION_PENDING: в сессии висит обязательная правка после 👎; "
            "следующий ответ должен её учесть (см. operator_corrections)."
        )
    return "\n".join(parts)


def compute_memory_telemetry(
    *,
    persisted: Optional[Dict[str, Any]],
    user_text: str,
    recent_dialogue: Any,
    external_hint: str = "",
    user_id: str = "",
    chat_id: str = "",
) -> Dict[str, Any]:
    """Поля для turn.outcome → turns.jsonl."""
    rec = persisted if isinstance(persisted, dict) else {}
    slot_kind = ""
    try:
        from core.dialogue_slots import get_active_slot

        slot = get_active_slot(rec)
        if slot:
            slot_kind = str(slot.get("kind") or "")[:64]
    except Exception:
        pass

    tags = hint_tags_from_text(external_hint or "")
    slots = extract_policy_slots(rec, recent_dialogue, user_text, user_id=user_id, chat_id=chat_id)
    slot_keys = sorted(k for k in slots.keys() if slots.get(k))

    last_fb: List[str] = []
    st = rec.get("session_task")
    if isinstance(st, dict):
        raw = st.get("last_feedback_applied")
        if isinstance(raw, list):
            last_fb = [str(x) for x in raw[:8]]

    return {
        "dialogue_slot_kind": slot_kind or None,
        "policy_hint_tags": tags[:12] or None,
        "policy_slot_keys": slot_keys[:12] or None,
        "correction_pending": _correction_pending_flag(rec),
        "last_feedback_applied": last_fb or None,
    }


def merge_memory_telemetry_into_turn_payload(payload: Dict[str, Any], telemetry: Optional[Dict[str, Any]]) -> None:
    if not isinstance(payload, dict) or not isinstance(telemetry, dict):
        return
    for key in (
        "dialogue_slot_kind",
        "policy_hint_tags",
        "policy_slot_keys",
        "correction_pending",
        "last_feedback_applied",
    ):
        val = telemetry.get(key)
        if val is not None and val != "" and val != []:
            payload[key] = val
