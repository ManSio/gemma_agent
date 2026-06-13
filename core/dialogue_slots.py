"""
Слоты диалога — один механизм вместо разрозненных regex под каждую фразу.

Состояние в behavior_store.routing_prefs.dialogue_slot:
  kind, turns_left, meta, set_at

Слоты:
  weather_await_city — бот спросил город для погоды; короткий топоним заполняет слот.
  article_thread — обсуждаем присланную/пересказанную статью; вложение-картинка репоста не в vision.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.regex_safe import cap_regex_input, safe_re_search

from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

SLOT_WEATHER_CITY = "weather_await_city"
SLOT_ARTICLE_THREAD = "article_thread"
SLOT_SPATIAL_PROJECT = "spatial_project"

_ASSISTANT_ASK_CITY_RE = re.compile(
    r"(?i)(какой\s+именно\s+город|город\s+вас\s+интерес|населённ\w+\s+пункт|"
    r"назов\w+\s+населённ|покаж\w+\s+погод|уточн\w{0,20}[\s\S]{0,160}?город|напишите\s+город)"
)

_USER_REFERS_ARTICLE_RE = re.compile(
    r"(?i)(про\s+стать\w*|об\s+этой\s+стать|из\s+стать|в\s+статье|"
    r"текст\s+выше|то\s+что\s+я\s+прислал|я\s+про\s+стать)"
)

_ARTICLE_FOLLOWUP_RE = re.compile(
    r"(?i)(дальнейш\w*|перспектив\w*|что\s+дальше|куда\s+движ\w*|"
    r"развит\w+\s+событ|последств\w*|что\s+будет\s+дальше|каков\w*\s+дальнейш|"
    r"что\s+ещ[её]\s+известн?\w*|что\s+еще\s+известн?\w*|"
    r"что\s+ещ[её]\s+слышн\w*|что\s+еще\s+слышн\w*|"
    r"что\s+ещ[её]\s+говор\w*|что\s+еще\s+говор\w*|"
    r"что\s+ещ[её]\s+по\s+(?:эт\w+\s+)?тем\w*|"
    r"ещ[её]\s+подробн\w*|еще\s+подробн\w*|подробн\w*)"
)


def slots_enabled() -> bool:
    return effective_bool("DIALOGUE_SLOTS_ENABLED", default=True)


def _slot_turns_default() -> int:
    try:
        return max(1, min(20, int((os.getenv("DIALOGUE_SLOT_TURNS") or "6").strip())))
    except ValueError:
        return 6


def _weather_slot_turns() -> int:
    try:
        return max(1, min(10, int((os.getenv("DIALOGUE_SLOT_WEATHER_TURNS") or "3").strip())))
    except ValueError:
        return 3


def _article_slot_turns() -> int:
    try:
        return max(2, min(16, int((os.getenv("DIALOGUE_SLOT_ARTICLE_TURNS") or "8").strip())))
    except ValueError:
        return 8


def _spatial_slot_turns() -> int:
    try:
        return max(4, min(24, int((os.getenv("DIALOGUE_SLOT_SPATIAL_TURNS") or "12").strip())))
    except ValueError:
        return 12


def _routing_prefs(rec: Dict[str, Any]) -> Dict[str, Any]:
    rp = rec.get("routing_prefs")
    if isinstance(rp, dict):
        return rp
    return {}


def get_active_slot(rec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not slots_enabled() or not isinstance(rec, dict):
        return None
    raw = _routing_prefs(rec).get("dialogue_slot")
    if not isinstance(raw, dict):
        return None
    left = int(raw.get("turns_left") or 0)
    kind = str(raw.get("kind") or "").strip()
    if not kind or left <= 0:
        return None
    return raw


def set_slot(
    rec: Dict[str, Any],
    kind: str,
    meta: Optional[Dict[str, Any]] = None,
    *,
    turns: Optional[int] = None,
) -> None:
    if not slots_enabled() or not isinstance(rec, dict):
        return
    rp = dict(_routing_prefs(rec))
    t = turns if turns is not None else _slot_turns_default()
    if kind == SLOT_WEATHER_CITY:
        t = turns if turns is not None else _weather_slot_turns()
    elif kind == SLOT_ARTICLE_THREAD:
        t = turns if turns is not None else _article_slot_turns()
    elif kind == SLOT_SPATIAL_PROJECT:
        t = turns if turns is not None else _spatial_slot_turns()
    rp["dialogue_slot"] = {
        "kind": kind,
        "turns_left": t,
        "meta": dict(meta or {}),
        "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rec["routing_prefs"] = rp


def clear_slot(rec: Dict[str, Any]) -> None:
    if not isinstance(rec, dict):
        return
    rp = dict(_routing_prefs(rec))
    if "dialogue_slot" in rp:
        rp.pop("dialogue_slot", None)
        rec["routing_prefs"] = rp


def consume_slot_turn(rec: Dict[str, Any]) -> Optional[str]:
    """Уменьшить turns_left; вернуть kind если слот ещё активен."""
    slot = get_active_slot(rec)
    if not slot:
        return None
    kind = str(slot.get("kind") or "")
    left = int(slot.get("turns_left") or 0) - 1
    rp = dict(_routing_prefs(rec))
    if left <= 0:
        rp.pop("dialogue_slot", None)
        rec["routing_prefs"] = rp
        return kind
    slot = dict(slot)
    slot["turns_left"] = left
    rp["dialogue_slot"] = slot
    rec["routing_prefs"] = rp
    return kind


@dataclass
class SlotTurnContext:
    kind: str = ""
    force_weather: bool = False
    suppress_image: bool = False
    external_hint: str = ""
    weather_city: str = ""
    weather_country: str = ""


def _slot_turn_accepts(kind: str, user_text: str, recent_dialogue: Any = None, *, persisted: Any = None) -> bool:
    """Контракт слота: принимает ли реплика ожидаемый ввод для kind."""
    from core.slot_registry import slot_accepts_turn

    return slot_accepts_turn(kind, user_text, recent_dialogue, persisted=persisted)


def _turn_binds_weather_slot(user_text: str) -> bool:
    """Реплика относится к уточнению города или запросу погоды."""
    from core.brain.text_helpers import _user_text_looks_like_weather_query, safe_text

    t = safe_text(user_text).strip()
    if not t:
        return False
    if _message_looks_like_city_only(t):
        return True
    return bool(_user_text_looks_like_weather_query(t.lower()))


def _message_looks_like_city_only(user_text: str) -> bool:
    from core.brain.text_helpers import (
        _explicit_major_city_from_user_text,
        _user_text_looks_like_weather_query,
        _weather_city_token_key,
        _WEATHER_CITY_ALIASES,
        safe_text,
        weather_city_extract_from_message_only,
    )

    t = safe_text(user_text).strip()
    if not t or len(t) > 48:
        return False
    if _user_text_looks_like_weather_query(t.lower()):
        return False
    if _explicit_major_city_from_user_text(t)[0]:
        return True
    if _weather_city_token_key(t) in _WEATHER_CITY_ALIASES:
        return True
    return len(t.split()) <= 3 and bool(weather_city_extract_from_message_only(t)[0])


def _recent_has_pasted_article(recent_dialogue: Any, *, lookback: int = 10) -> bool:
    from core.brain.text_helpers import looks_like_pasted_news_article

    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for row in reversed(rows[-lookback:]):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        if str(row.get("role") or "") == "user" and looks_like_pasted_news_article(text):
            return True
        # assistant: только явные отсылки к разбору/обсуждению статьи,
        # не триггериться на новые статьи/новости бота (см. залипание article_thread)
        if str(row.get("role") or "") == "assistant" and len(text) >= 120:
            low = text.lower()
            if any(m in low for m in ("коммерсант", "bild", "nato", "статья (продолжение)")):
                return True
    return False


def user_refers_to_article_thread(user_text: str, recent_dialogue: Any = None) -> bool:
    low = (user_text or "").strip().lower()
    if _USER_REFERS_ARTICLE_RE.search(low):
        return True
    if len(low) > 140:
        return False
    if _ARTICLE_FOLLOWUP_RE.search(low) and _recent_has_pasted_article(recent_dialogue):
        return True
    return False


def should_suppress_image_for_slot(
    user_text: str,
    recent_dialogue: Any,
    file_context: Optional[Dict[str, Any]],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    if not isinstance(file_context, dict) or file_context.get("file_type") != "image":
        return False
    try:
        from core.image_gen_nl import attachment_wants_image_generation

        if attachment_wants_image_generation(file_context, user_text):
            return False
    except Exception:
        pass
    low = (user_text or "").lower()
    if any(k in low for k in ("фото", "картин", "изображен", "снимок", "на фото", "что на")):
        return False
    slot = get_active_slot(persisted) if persisted else None
    if slot and str(slot.get("kind")) == SLOT_SPATIAL_PROJECT:
        try:
            from core.spatial_design.feedback import classify_feedback

            if classify_feedback(user_text) == "confirm":
                return False
        except Exception:
            pass
        return True
    if slot and str(slot.get("kind")) == SLOT_ARTICLE_THREAD:
        return True
    if user_refers_to_article_thread(user_text, recent_dialogue):
        return True
    if _recent_has_pasted_article(recent_dialogue) and len(low) < 120:
        return True
    return False


def _article_thread_topic_line(recent_dialogue: Any, persisted: Optional[Dict[str, Any]]) -> str:
    try:
        from core.article_thread_followup import extract_article_thread_subject

        subj = extract_article_thread_subject(recent_dialogue, persisted)
        if subj and len(subj.strip()) >= 12:
            return f"ARTICLE_THREAD_TOPIC: {subj.strip()[:280]}"
    except Exception as e:
        logger.debug("article_thread topic hint: %s", e)
    return ""


def _pending_facts_hint(persisted: Optional[Dict[str, Any]]) -> str:
    if not isinstance(persisted, dict):
        return ""
    try:
        from core.user_facts import has_pending_facts_confirmation

        if not has_pending_facts_confirmation(persisted):
            return ""
        pending = persisted.get("pending_facts_confirmation") or {}
        if not isinstance(pending, dict):
            return ""
        country = str(pending.get("country") or pending.get("country_name") or "").strip()
        if country:
            return (
                f"FACTS_PENDING: ждём подтверждение «да/нет» для страны {country}; "
                "не уходи в новости/RSS без явной смены темы."
            )
        return "FACTS_PENDING: ждём подтверждение факта (да/нет); не переключай intent на news."
    except Exception as e:
        logger.debug("pending_facts hint: %s", e)
    return ""


def _image_edit_session_hint(
    user_text: str,
    *,
    user_id: str = "",
    chat_id: str = "",
) -> str:
    low = (user_text or "").lower()
    if not any(k in low for k in ("переделай", "доработ", "измени", "другой фон", "закат", "убери")):
        return ""
    uid = (user_id or "").strip()
    cid = (chat_id or "").strip()
    if not uid or not cid:
        return ""
    try:
        from core.image_edit_session import get_image_edit_session

        doc = get_image_edit_session(uid, cid)
        if not doc:
            return ""
        return (
            "IMAGE_EDIT_SESSION: в чате есть сохранённая картинка для правки; "
            "при «переделай» используй её, не склеивай с чужим pending multiref."
        )
    except Exception as e:
        logger.debug("image_edit_session hint: %s", e)
    return ""


def slot_external_hint(
    user_text: str,
    recent_dialogue: Any,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: str = "",
    chat_id: str = "",
) -> str:
    parts: List[str] = []
    slot = get_active_slot(persisted) if persisted else None
    art_topic = ""
    try:
        from core.article_thread_followup import (
            article_thread_context_active,
            looks_like_article_thread_clarification,
            looks_like_article_thread_opinion_followup,
        )
    except Exception:
        article_thread_context_active = None  # type: ignore
        looks_like_article_thread_clarification = None  # type: ignore
        looks_like_article_thread_opinion_followup = None  # type: ignore

    if callable(looks_like_article_thread_opinion_followup) and looks_like_article_thread_opinion_followup(
        user_text
    ) and (
        (slot and str(slot.get("kind")) == SLOT_ARTICLE_THREAD)
        or (callable(article_thread_context_active) and article_thread_context_active(recent_dialogue, persisted))
    ):
        parts.append(
            "ARTICLE_THREAD_OPINION: пользователь спрашивает о достоверности присланной/обсуждаемой статьи; "
            "ответь по сути материала из recent_messages (что заявлено официально, что спорно, где пробелы); "
            "не уходи в абстрактную философию про «истину вообще»."
        )
        art_topic = _article_thread_topic_line(recent_dialogue, persisted)
    elif callable(looks_like_article_thread_clarification) and looks_like_article_thread_clarification(
        user_text
    ):
        parts.append(
            "ARTICLE_THREAD_CLARIFY: пользователь уточняет, что речь о статье из диалога; "
            "ответь на его предыдущий вопрос, опираясь на пересказ и текст статьи в recent_messages."
        )
        art_topic = _article_thread_topic_line(recent_dialogue, persisted)
    elif slot and str(slot.get("kind")) == SLOT_ARTICLE_THREAD:
        parts.append(
            "ARTICLE_THREAD: продолжай обсуждение текста статьи из recent_messages; "
            "не описывай картинку репоста; не проси прислать jpg, если текст уже был в чате."
        )
        art_topic = _article_thread_topic_line(recent_dialogue, persisted)
    elif user_refers_to_article_thread(user_text, recent_dialogue) or _ARTICLE_FOLLOWUP_RE.search(
        user_text or ""
    ):
        parts.append(
            "ARTICLE_THREAD: ответь по смыслу статьи и последнему пересказу в диалоге; "
            "не переключайся на vision/файл без явной просьбы."
        )
        art_topic = _article_thread_topic_line(recent_dialogue, persisted)
    if art_topic:
        parts.append(art_topic)
    pf = _pending_facts_hint(persisted)
    if pf:
        parts.append(pf)
    img = _image_edit_session_hint(user_text, user_id=user_id, chat_id=chat_id)
    if img:
        parts.append(img)
    if slot and str(slot.get("kind")) == SLOT_WEATHER_CITY and _turn_binds_weather_slot(user_text):
        parts.append(
            "WEATHER_SLOT: пользователь уточняет город для прогноза — дай погоду по этому городу, "
            "без лишних вопросов."
        )
    if slot and str(slot.get("kind")) == SLOT_SPATIAL_PROJECT:
        sm = slot.get("meta") if isinstance(slot.get("meta"), dict) else {}
        phase = str(sm.get("phase") or "awaiting_feedback")
        parts.append(
            "SPATIAL_PROJECT: активна сверка планировки; не генерируй картинку и не уходи в общий чат "
            "пока пользователь не подтвердил («да»/«рисуй»). Учитывай правки по мм и расстановке."
        )
        if phase:
            parts.append(f"SPATIAL_PHASE: {phase}")
    try:
        from core.policy_memory_runtime import build_policy_memory_hints

        _policy = build_policy_memory_hints(
            user_text,
            recent_dialogue,
            persisted=persisted,
            user_id=user_id,
            chat_id=chat_id,
        )
        if _policy.strip():
            parts.append(_policy.strip())
    except Exception as e:
        logger.debug("policy_memory hints: %s", e)
    return "\n".join(parts)


def resolve_slot_for_turn(
    user_text: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> SlotTurnContext:
    """Контекст хода до brain/planner."""
    out = SlotTurnContext()
    if not slots_enabled():
        return out

    rec = persisted if isinstance(persisted, dict) else {}
    slot = get_active_slot(rec)
    kind = str(slot.get("kind") or "") if slot else ""

    if kind and not _slot_turn_accepts(kind, user_text, recent_dialogue, persisted=rec):
        clear_slot(rec)
        kind = ""
        slot = None

    if kind == SLOT_WEATHER_CITY and _message_looks_like_city_only(user_text):
        from core.brain.text_helpers import weather_city_country_resolve

        wc, wco = weather_city_country_resolve(user_text, {}, recent_dialogue)
        if wc:
            out.kind = kind
            out.force_weather = True
            out.weather_city = wc
            out.weather_country = wco
            consume_slot_turn(rec)

    if kind == SLOT_SPATIAL_PROJECT:
        out.kind = SLOT_SPATIAL_PROJECT
        out.suppress_image = should_suppress_image_for_slot(
            user_text, recent_dialogue, {"file_type": "image"}, persisted=rec
        )
        hint = slot_external_hint(user_text, recent_dialogue, persisted=rec)
        if hint:
            out.external_hint = hint

    if kind == SLOT_ARTICLE_THREAD or user_refers_to_article_thread(user_text, recent_dialogue):
        out.kind = SLOT_ARTICLE_THREAD if not out.kind else out.kind
        out.suppress_image = should_suppress_image_for_slot(
            user_text, recent_dialogue, {"file_type": "image"}, persisted=rec
        )
        hint = slot_external_hint(user_text, recent_dialogue, persisted=rec)
        if hint:
            out.external_hint = hint

    facts_home: Dict[str, Any] = {}
    if isinstance(rec.get("user_facts"), dict):
        facts_home = dict(rec["user_facts"])

    if not out.kind:
        try:
            from core.brain.text_helpers import (
                _user_text_looks_like_weather_query,
                recent_dialogue_has_location_context,
                user_text_weather_refs_saved_home,
                weather_city_country_resolve,
            )

            low_w = (user_text or "").strip().lower()
            if _user_text_looks_like_weather_query(low_w) and (
                user_text_weather_refs_saved_home(user_text)
                or recent_dialogue_has_location_context(recent_dialogue)
            ):
                wc, wco = weather_city_country_resolve(
                    user_text, facts_home, recent_dialogue
                )
                if wc:
                    out.force_weather = True
                    out.weather_city = wc
                    out.weather_country = wco
                    set_slot(rec, SLOT_WEATHER_CITY, {"source": "home"}, turns=_weather_slot_turns())
        except Exception as e:
            logger.debug("dialogue_slots weather_home: %s", e)

    if not out.kind and _message_looks_like_city_only(user_text):
        from core.brain.text_helpers import _user_text_looks_like_weather_query

        rows = recent_dialogue if isinstance(recent_dialogue, list) else []
        for row in reversed(rows[-8:]):
            if not isinstance(row, dict):
                continue
            if str(row.get("role") or "") == "assistant" and _ASSISTANT_ASK_CITY_RE.search(
                cap_regex_input(str(row.get("text") or ""), max_len=2048)
            ):
                from core.brain.text_helpers import weather_city_country_resolve

                wc, wco = weather_city_country_resolve(user_text, {}, recent_dialogue)
                if wc:
                    out.force_weather = True
                    out.weather_city = wc
                    out.weather_country = wco
                    set_slot(rec, SLOT_WEATHER_CITY, {}, turns=_weather_slot_turns())
                    consume_slot_turn(rec)
                break
            if str(row.get("role") or "") == "user" and _user_text_looks_like_weather_query(
                str(row.get("text") or "").lower()
            ):
                break

    return out


def on_assistant_reply(
    rec: Dict[str, Any],
    assistant_text: str,
    *,
    user_text: str = "",
) -> None:
    """После ответа бота — выставить слот, если бот спросил город или был paste статьи."""
    if not slots_enabled() or not isinstance(rec, dict):
        return
    at = (assistant_text or "").strip()
    ut = (user_text or "").strip()
    # Если диалог только что очищен (/new), не ставить слоты — контекст пуст
    recent = rec.get("recent_messages")
    if not isinstance(recent, list) or not recent:
        return
    if _ASSISTANT_ASK_CITY_RE.search(at):
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=_weather_slot_turns())
        return
    active = get_active_slot(rec)
    if active and str(active.get("kind") or "") == SLOT_SPATIAL_PROJECT:
        return
    try:
        from core.brain.text_helpers import looks_like_pasted_news_article

        topic = (ut.split("\n")[0] if ut else at.split("\n")[0] if at else "").strip()[:280]
        dlg = [{"role": "user", "text": ut}, {"role": "assistant", "text": at}]
        if looks_like_pasted_news_article(ut):
            set_slot(
                rec,
                SLOT_ARTICLE_THREAD,
                {"source": "user_paste", "topic": topic},
                turns=_article_slot_turns(),
            )
        elif _recent_has_pasted_article(dlg):
            sub = topic or at[:280]
            try:
                from core.article_thread_followup import extract_article_thread_subject

                extracted = extract_article_thread_subject(dlg, rec)
                if extracted and len(extracted) >= 12:
                    sub = extracted[:320]
            except Exception:
                pass
            set_slot(
                rec,
                SLOT_ARTICLE_THREAD,
                {"source": "summary", "topic": sub},
                turns=_article_slot_turns(),
            )
    except Exception as e:
        logger.debug("dialogue_slots on_assistant: %s", e)


def apply_slot_to_task_facts(
    profile: Dict[str, Any],
    user_text: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> None:
    """Дополнить task_fact_profile из активного слота."""
    ctx = resolve_slot_for_turn(user_text, recent_dialogue, persisted)
    if ctx.force_weather:
        profile["is_weather"] = True
        if ctx.weather_city:
            profile["weather_city"] = ctx.weather_city
            profile["weather_country"] = ctx.weather_country or ""
            profile["weather_geo_query"] = ctx.weather_city
            profile["weather_use_coords"] = False
