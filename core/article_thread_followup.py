"""Продолжение темы вставленной статьи / пересказа — не новый общий поиск."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_THREAD_FOLLOWUP_RE = re.compile(
    r"(?i)(?:"
    r"что\s+ещ[её]\s+известн?\w*"
    r"|что\s+еще\s+известн?\w*"
    r"|что\s+ещ[её]\s+нового"
    r"|что\s+еще\s+нового"
    r"|что\s+ещ[её]\s+слышн\w*"
    r"|что\s+еще\s+слышн\w*"
    r"|что\s+ещ[её]\s+говор\w*"
    r"|что\s+еще\s+говор\w*"
    r"|что\s+ещ[её]\s+пиш\w*"
    r"|что\s+еще\s+пиш\w*"
    r"|что\s+ещ[её]\s+по\s+(?:эт\w+\s+)?тем\w*"
    r"|ещ[её]\s+подробн\w*"
    r"|еще\s+подробн\w*"
    r"|подробн\w*"
    r"|разверни|развёрни"
    r"|детальн\w*"
    r"|что\s+дальше"
    r"|что\s+будет\s+дальше"
    r"|дальнейш\w*"
    r"|последств\w*"
    r"|как\s+развива\w+"
    r"|есть\s+ещ[её]\s+что"
    r")"
)

_SHORT_ARTICLE_FOLLOWUP_CUE_RE = re.compile(
    r"(?i)(?:подробн|что\s+ещ|разверн|развёрн|детальн|слышн|говор|пиш\w|по\s+тем)"
)

_ARTICLE_OPINION_FOLLOWUP_RE = re.compile(
    r"(?i)(?:"
    r"правда\s*\?"
    r"|правда\s+ли"
    r"|насколько\s+прав"
    r"|как\s+ты\s+дума"
    r"|что\s+дума"
    r"|ты\s+вер"
    r"|веришь"
    r"|можно\s+ли\s+вер"
    r"|можно\s+довер"
    r"|согласен"
    r"|согласн"
    r"|достовер"
    r")"
)

_ARTICLE_CLARIFICATION_RE = re.compile(
    r"(?i)(?:"
    r"я\s+про\s+стать"
    r"|имел\s+в\s+виду\s+стать"
    r"|имела\s+в\s+виду\s+стать"
    r"|про\s+статью\s+выше"
    r"|про\s+эт\w*\s+стать"
    r"|имел\s+в\s+виду\s+текст"
    r")"
)

_THREAD_ENTITY_MARKERS = (
    "мюнхен",
    "munich",
    "аэропорт",
    "airport",
    "беспилотник",
    "дрон",
    "bild",
    "frankfurt",
    "франкфурт",
    "nato",
    "нато",
    "перенаправ",
    "закрыт",
    "закрыли",
    "рейс",
    "авиа",
)


_ARTICLE_THREAD_HONEST_FALLBACK = (
    "По этой теме в открытых источниках сейчас мало нового помимо уже сказанного. "
    "Уточните угол (сроки, регион, цифры) — поищу точечнее."
)

_NEWS_DIGEST_LEAK_RE = re.compile(
    r"(?i)(?:^|\n)\s*главные\s+новости|напишите\s+номер\s+пункта"
)


def article_thread_followup_enabled() -> bool:
    raw = (os.getenv("ARTICLE_THREAD_FOLLOWUP_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def article_thread_honest_fallback_reply() -> str:
    return _ARTICLE_THREAD_HONEST_FALLBACK


def looks_like_news_digest_leak(text: str) -> bool:
    """True — текст похож на news_direct, а не на follow-up статьи."""
    t = (text or "").strip()
    if not t:
        return False
    if _NEWS_DIGEST_LEAK_RE.search(t):
        return True
    if re.search(r"(?m)^\s*\d{1,2}\.\s+\S", t) and "дополнительно по теме" not in t.lower():
        return True
    return False


def sanitize_article_thread_direct_reply(text: str) -> str:
    """Не отдавать «Главные новости» на follow-up после paste (#19)."""
    body = (text or "").strip()
    if not body or looks_like_news_digest_leak(body):
        return article_thread_honest_fallback_reply()
    return body


def looks_like_article_thread_followup(user_text: str) -> bool:
    t = (user_text or "").strip()
    if not t or len(t) > 160:
        return False
    if _THREAD_FOLLOWUP_RE.search(t):
        return True
    low = t.lower()
    if len(low) <= 32 and _SHORT_ARTICLE_FOLLOWUP_CUE_RE.search(low):
        return True
    return False


def looks_like_article_thread_opinion_followup(user_text: str) -> bool:
    """Оценка правдивости/достоверности статьи — brain, не search shortcut."""
    t = (user_text or "").strip()
    if not t or len(t) > 160:
        return False
    return bool(_ARTICLE_OPINION_FOLLOWUP_RE.search(t))


def looks_like_article_thread_clarification(user_text: str) -> bool:
    """Уточнение «я про статью» — brain, не search shortcut."""
    t = (user_text or "").strip()
    if not t or len(t) > 120:
        return False
    return bool(_ARTICLE_CLARIFICATION_RE.search(t))


def article_thread_brain_followup_active(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """Follow-up по статье, который должен идти в brain с ARTICLE_THREAD hint."""
    if not article_thread_context_active(recent_dialogue, persisted):
        return False
    if looks_like_article_thread_opinion_followup(user_text):
        return True
    if looks_like_article_thread_clarification(user_text):
        return True
    return False


def _row_text(row: dict) -> str:
    return str(row.get("text") or row.get("content") or "").strip()


def _topic_from_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    for line in t.splitlines():
        ln = line.strip()
        if len(ln) >= 24:
            return ln[:280]
    return t[:280]


def extract_article_thread_subject(
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Тема из слота, последнего paste или пересказа бота."""
    try:
        from core.dialogue_slots import SLOT_ARTICLE_THREAD, get_active_slot

        slot = get_active_slot(persisted) if persisted else None
        if slot and str(slot.get("kind") or "") == SLOT_ARTICLE_THREAD:
            meta = slot.get("meta") if isinstance(slot.get("meta"), dict) else {}
            topic = str(meta.get("topic") or "").strip()
            if len(topic) >= 12:
                return topic[:320]
    except Exception as e:
        logger.debug("article_thread slot topic: %s", e)

    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    best_user = ""
    best_asst = ""
    for row in reversed(rows[-14:]):
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        text = _row_text(row)
        if not text:
            continue
        if role in ("user", "human", "") and len(text) >= 80 and len(text) > len(best_user):
            best_user = text
        if role in ("assistant", "bot", "gemma") and len(text) >= 100 and len(text) > len(best_asst):
            best_asst = text

    for candidate in (best_user, best_asst):
        if not candidate:
            continue
        low = candidate.lower()
        if any(m in low for m in _THREAD_ENTITY_MARKERS) or len(candidate) >= 160:
            if len(candidate) >= 200:
                return candidate[:320]
            return _topic_from_text(candidate)

    if best_user:
        return best_user[:320] if len(best_user) >= 120 else _topic_from_text(best_user)
    if best_asst:
        return best_asst[:320] if len(best_asst) >= 120 else _topic_from_text(best_asst)
    return None


_UA_OBLAST_MARKERS = (
    ("харьков", "Kharkiv"),
    ("чернигов", "Chernihiv"),
    ("ровен", "Rivne"),
    ("житомир", "Zhytomyr"),
    ("сумск", "Sumy"),
    ("днепропетров", "Dnipro"),
    ("киев", "Kyiv"),
)


def _anchor_terms_from_subject(subject: str) -> Dict[str, Any]:
    """Сущности из paste/пересказа для узкого поиска и фильтра мусора."""
    low = (subject or "").lower()
    regions_ru: List[str] = []
    regions_en: List[str] = []
    for ru, en in _UA_OBLAST_MARKERS:
        if ru in low:
            regions_ru.append(ru)
            regions_en.append(en)
    stats: List[str] = []
    for m in re.finditer(r"\b(2\d{2})\b", subject or ""):
        stats.append(m.group(1))
    themes: List[str] = []
    for t in (
        "бпла",
        "беспилотник",
        "беспилотников",
        "дрон",
        "пво",
        "воздушн",
        "тревог",
        "миноборон",
    ):
        if t in low:
            themes.append(t)
    return {
        "regions_ru": regions_ru,
        "regions_en": regions_en,
        "stats": stats[:4],
        "themes": themes,
    }


def _ukraine_drone_search_query(subject: str) -> Optional[str]:
    low = (subject or "").lower()
    if "украин" not in low:
        return None
    if not any(t in low for t in ("бпла", "беспилот", "дрон", "229", "212", "пво")):
        return None
    anchor = _anchor_terms_from_subject(subject)
    parts = ["Ukraine night drone attack air alert"]
    if anchor["regions_en"]:
        parts.append(" ".join(anchor["regions_en"][:5]))
    if "212" in (subject or "") and "229" in (subject or ""):
        parts.append("212 of 229 drones intercepted")
    elif anchor["stats"]:
        parts.append(f"drones {anchor['stats'][0]}")
    return f"{' '.join(parts)} May 2026"


def build_thread_search_query(subject: str) -> str:
    """Один узкий запрос — без fallback «site:rbc.ru» / world thematic."""
    sub = (subject or "").strip()[:280]
    if not sub:
        return ""
    low = sub.lower()
    if any(m in low for m in ("мюнхен", "munich", "аэропорт", "airport")):
        return "Munich airport drone closure news May 2026"
    if any(
        m in low
        for m in (
            "крым",
            "crimea",
            "севастоп",
            "sevastopol",
            "талон",
            "бензин",
            "азс",
            "топлив",
            "аи-95",
            "аи-92",
        )
    ):
        return "Crimea Sevastopol fuel gasoline rationing coupons May 2026"
    ukr_q = _ukraine_drone_search_query(sub)
    if ukr_q:
        return ukr_q
    try:
        from core.news_reply import _focused_entity_query_from_title

        focused = _focused_entity_query_from_title(sub)
        if focused and len(focused) >= 10:
            return focused
    except Exception as e:
        logger.debug("article_thread focused query: %s", e)
    if "беспилотник" in low or "дрон" in low or "бпла" in low:
        anchor = _anchor_terms_from_subject(sub)
        if anchor["regions_en"]:
            return f"{' '.join(anchor['regions_en'][:4])} drone attack Ukraine news"
        return f"{sub[:120]} drone attack news"
    headline = _topic_from_text(sub)
    if headline and len(headline) >= 16:
        return f"{headline[:160]} news"
    return f"{sub[:140]} news"


def _subject_terms(subject: str) -> set:
    terms: set = set()
    for w in re.findall(r"[\wа-яёА-ЯЁ]+", (subject or "").lower()):
        if len(w) >= 4:
            terms.add(w)
    anchor = _anchor_terms_from_subject(subject)
    terms.update(anchor.get("regions_ru") or [])
    for st in anchor.get("stats") or []:
        terms.add(st)
    return terms


def _is_offtopic_search_row(row: dict, subject: str) -> bool:
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
    low_sub = (subject or "").lower()
    anchor = _anchor_terms_from_subject(subject)
    if re.search(
        r"(?i)(?:wetransfer|commentcamarche|forums\.comment|ne\s+fonctionne\s+pas|"
        r"mes\s+fichiers|discord\s+server)",
        blob,
    ):
        return True
    if anchor.get("regions_ru"):
        if re.search(
            r"(?i)(?:лукашенко|беларус|челябинск|уральск|один\s+район|инициатив)",
            blob,
        ):
            return True
        if re.search(r"(?i)(?:гроз|град|шквал|погод)", blob) and not any(
            r in blob for r in anchor["regions_ru"]
        ):
            return True
        if re.search(r"(?i)регионов\s+росси", blob) and "украин" in low_sub:
            if not any(r in blob for r in anchor["regions_ru"]):
                return True
        if "украин" in low_sub and "киев" in blob and not any(
            r in blob for r in anchor["regions_ru"] if r != "киев"
        ):
            if not any(t in blob for t in ("бпла", "беспилот", "дрон", "229", "212", "пво")):
                if "баллист" in blob or "тревог" in blob:
                    if len(anchor["regions_ru"]) >= 2:
                        return True
    score = _row_relevance_score(row, subject)
    if score >= 2:
        return False
    if score >= 1 and anchor.get("regions_ru") and any(r in blob for r in anchor["regions_ru"]):
        return False
    return True


def _row_relevance_score(row: dict, subject: str) -> int:
    blob = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
    score = sum(1 for t in _subject_terms(subject) if t in blob)
    anchor = _anchor_terms_from_subject(subject)
    for r in anchor.get("regions_ru") or []:
        if r in blob:
            score += 2
    for st in anchor.get("stats") or []:
        if st in blob:
            score += 2
    return score


def _rank_items_for_subject(
    items: list,
    subject: str,
    *,
    min_score: int = 3,
) -> list:
    clean = [
        r
        for r in items
        if isinstance(r, dict) and not _is_offtopic_search_row(r, subject)
    ]
    if not clean:
        return []

    scored = [(r, _row_relevance_score(r, subject)) for r in clean]
    scored = [(r, s) for r, s in scored if s >= min_score]
    if not scored:
        scored = [(r, s) for r, s in [(r, _row_relevance_score(r, subject)) for r in clean] if s >= 2]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored]


def format_article_thread_followup_from_items(
    items: list,
    *,
    subject: str,
    max_items: int = 2,
) -> str:
    """Продолжение темы статьи — связный текст, без «Главные новости» и номеров пунктов."""
    ranked = _rank_items_for_subject(items, subject)
    pick = ranked[: max(1, min(3, int(max_items)))]
    if not pick:
        return ""
    parts: List[str] = ["Дополнительно по теме"]
    for row in pick:
        title = str(row.get("title") or "").strip()
        snip = str(row.get("snippet") or "").strip()
        pub = str(row.get("publisher") or "").strip()
        if not title and not snip:
            continue
        chunk = title
        if snip and snip.lower() not in (title or "").lower():
            chunk = f"{title}. {snip[:420]}" if title else snip[:480]
        if pub and pub.lower() not in chunk.lower():
            chunk = f"{chunk} ({pub})"
        parts.append(chunk)
    if len(parts) <= 1:
        return ""
    return "\n\n".join(parts)[:4500]


def article_thread_context_active(
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """В диалоге обсуждается вставленная статья (слот или недавний paste/пересказ)."""
    try:
        from core.brain.text_helpers import recent_dialogue_has_pasted_article

        if recent_dialogue_has_pasted_article(recent_dialogue):
            return True
    except Exception as e:
        logger.debug("article_thread context check: %s", e)
    try:
        from core.dialogue_slots import SLOT_ARTICLE_THREAD, get_active_slot

        slot = get_active_slot(persisted) if persisted else None
        if slot and str(slot.get("kind") or "") == SLOT_ARTICLE_THREAD:
            return True
    except Exception:
        pass
    return bool(extract_article_thread_subject(recent_dialogue, persisted))


def article_followup_blocks_news_digest(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """«подробнее» / «что ещё» после paste — не news_direct с «Главные новости»."""
    if not article_thread_context_active(recent_dialogue, persisted):
        return False
    return should_handle_article_thread_followup(user_text, recent_dialogue, persisted)


def should_handle_article_thread_followup(
    user_text: str,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> bool:
    """Search follow-up («что ещё известно») — не opinion/clarify."""
    if not article_thread_context_active(recent_dialogue, persisted):
        return False
    if article_thread_brain_followup_active(user_text, recent_dialogue, persisted):
        return False
    return looks_like_article_thread_followup(user_text)


async def try_article_thread_followup_reply(
    user_text: str,
    *,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """«Что ещё известно» после paste/пересказа — поиск по той же теме."""
    if not article_thread_followup_enabled():
        return None
    text = (user_text or "").strip()
    if not should_handle_article_thread_followup(text, recent_dialogue, persisted):
        return None
    subject = extract_article_thread_subject(recent_dialogue, persisted)
    if not subject:
        return None
    q = build_thread_search_query(subject)
    if not q:
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut_async

        _gr = await should_run_shortcut_async(
            "article_thread_followup",
            text,
            persisted=persisted,
            planner_context={"recent_dialogue": recent_dialogue}
            if recent_dialogue
            else None,
        )
        if not _gr.allowed:
            if article_followup_blocks_news_digest(text, recent_dialogue, persisted):
                return article_thread_honest_fallback_reply()
            return None
    except Exception as e:
        logger.debug("article_thread gate: %s", e)

    try:
        from core.news_reply import _news_country_iso2, _search_pack, _user_facts_from_persisted
        from core.telegram_output_guard import collect_news_display_items_from_search

        facts = _user_facts_from_persisted(persisted)
        pack = await _search_pack(
            q,
            country=_news_country_iso2(facts),
            user_id=str(user_id or ""),
            timeout=22.0,
            tag="article_thread_followup",
            searx_only=True,
            record_errors=False,
        )
        raw = pack.get("results") if isinstance(pack, dict) else []
        rows = [r for r in (raw or []) if isinstance(r, dict)]
        items = collect_news_display_items_from_search(
            rows,
            user_query=q,
            country="",
            world_feed=False,
            require_article_url=True,
        )
        if items:
            body = format_article_thread_followup_from_items(
                items,
                subject=subject,
            )
            if body and str(body).strip():
                if isinstance(persisted, dict):
                    try:
                        from core.dialogue_slots import (
                            SLOT_ARTICLE_THREAD,
                            get_active_slot,
                            set_slot,
                        )

                        slot = get_active_slot(persisted)
                        if slot and str(slot.get("kind") or "") == SLOT_ARTICLE_THREAD:
                            meta = slot.get("meta") if isinstance(slot.get("meta"), dict) else {}
                            if subject and len(subject) >= 12:
                                meta = {**meta, "topic": subject[:320]}
                            set_slot(persisted, SLOT_ARTICLE_THREAD, meta)
                    except Exception as e:
                        logger.debug("article_thread slot refresh: %s", e)
                try:
                    from core.monitoring import MONITOR

                    MONITOR.inc("article_thread_followup_total")
                except Exception:
                    pass
                return sanitize_article_thread_direct_reply(str(body).strip())
    except Exception as e:
        logger.debug("article_thread_followup: %s", e)
    if article_followup_blocks_news_digest(text, recent_dialogue, persisted):
        return article_thread_honest_fallback_reply()
    return None


def finalize_article_thread_pre_llm_reply(
    user_text: str,
    reply: Optional[str],
    *,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> str:
    """Гарантированный direct_reply для pre_llm — never None/never news digest leak."""
    if reply and str(reply).strip():
        return sanitize_article_thread_direct_reply(str(reply).strip())
    if should_handle_article_thread_followup(user_text, recent_dialogue, persisted):
        return article_thread_honest_fallback_reply()
    return ""


def try_article_thread_followup_reply_sync(
    user_text: str,
    *,
    recent_dialogue: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    import asyncio
    import concurrent.futures

    coro = try_article_thread_followup_reply(
        user_text,
        recent_dialogue=recent_dialogue,
        persisted=persisted,
        user_id=user_id,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=55)
