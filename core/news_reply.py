"""Дайджест новостей: UniversalSearch/SearX + LLM; RSS только если явно включён (legacy)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.brain.text_helpers import (
    looks_like_news_access_refusal,
    looks_like_news_headlines_request,
    looks_like_pasted_news_article,
    looks_like_tool_execution_report_leak,
    parse_news_item_pick_index,
    resolve_news_item_pick_index,
    task_fact_profile,
    wants_expanded_news_digest,
)
from core.resilience import with_retry

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_META_TITLE_RE = re.compile(
    r"(?i)google\s+новости\s*-\s*в\s+мире|приложени[ея]\s+[\"']?google\s+новости",
)


def _rss_items_are_google_meta_only(items: List[Dict[str, Any]]) -> bool:
    """RSS отдал только заглушки Google News — не использовать как дайджест."""
    if not items:
        return False
    bad = sum(
        1
        for it in items
        if isinstance(it, dict)
        and _GOOGLE_NEWS_META_TITLE_RE.search(str(it.get("title") or ""))
    )
    return bad >= max(1, len(items) - 1)


def _env_truthy(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off"}:
        return False
    return raw in {"1", "true", "yes", "on"}


def news_direct_reply_enabled() -> bool:
    """Search-only digest или planner allow — иначе try_news_reply мёртв при ALLOW_NEWS=false."""
    try:
        from core.brain_own_turn import news_digest_search_only_enabled, planner_direct_allowed

        if news_digest_search_only_enabled():
            return True
        return planner_direct_allowed("news")
    except Exception:
        return _env_truthy("NEWS_DIRECT_REPLY_ENABLED", default=False)


def apply_news_prefetch_fallback_if_needed(
    reply: str,
    *,
    search_body: str,
    user_query: str,
    task_facts: Optional[Dict[str, Any]] = None,
    brain_profile: str = "",
    prefer_news_direct: bool = False,
) -> str:
    """
    Если LLM отказал «нет доступа к новостям», а prefetch-поиск уже дал сводку — отдать форматированный дайджест.
    """
    body = (search_body or "").strip()
    if not body:
        return reply or ""
    tf = task_facts if isinstance(task_facts, dict) else {}
    prof = (brain_profile or "").strip().lower()
    is_news_turn = bool(
        tf.get("is_news")
        or prof == "news_brief"
        or prefer_news_direct
    )
    if not is_news_turn:
        return reply or ""
    cur = (reply or "").strip()
    if cur and not looks_like_news_access_refusal(cur) and not looks_like_tool_execution_report_leak(cur):
        return reply or ""
    try:
        from core.telegram_output_guard import (
            format_news_from_search,
            format_news_loose_from_summary,
        )

        uq = user_query or ""
        formatted = format_news_from_search(body, user_query=uq)
        if not (formatted or "").strip():
            formatted = format_news_loose_from_summary(body, user_query=uq)
        if (formatted or "").strip():
            try:
                from core.monitoring import MONITOR

                MONITOR.inc("brain_news_prefetch_fallback_total")
            except Exception:
                pass
            return formatted.strip()
    except Exception as e:
        logger.debug("news prefetch fallback: %s", e)
    return reply or ""


async def repair_news_tool_narration_reply(
    reply: str,
    *,
    user_query: str,
    search_body: str = "",
    user_id: str = "",
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Сырой пересказ UniversalSearch → нормальный дайджест без LLM."""
    if not looks_like_tool_execution_report_leak(reply or ""):
        return None
    if not looks_like_news_headlines_request(user_query or ""):
        return None
    body = (search_body or "").strip()
    if not body:
        facts = _user_facts_from_persisted(persisted)
        news_co = _news_country_iso2(facts)
        pack = await _search_pack(
            user_query or "новости сегодня",
            country=news_co,
            user_id=str(user_id or ""),
            timeout=22.0,
            tag="news_tool_leak_repair",
        )
        if pack.get("ok"):
            res = pack.get("results")
            if isinstance(res, list) and res:
                from core.telegram_output_guard import (
                    collect_news_display_items_from_search,
                    format_news_from_displayed,
                )

                facts = _user_facts_from_persisted(persisted)
                news_co = _news_country_iso2(facts)
                shown = collect_news_display_items_from_search(
                    [r for r in res if isinstance(r, dict)],
                    user_query=user_query or "",
                    country=news_co,
                )
                if shown:
                    body = format_news_from_displayed(
                        shown, user_query=user_query or ""
                    )
            if not body:
                body = str(pack.get("summary") or "").strip()
    if not body:
        return None
    try:
        from core.telegram_output_guard import (
            format_news_from_search,
            format_news_loose_from_summary,
        )

        uq = user_query or ""
        formatted = format_news_from_search(body, user_query=uq)
        if not (formatted or "").strip():
            formatted = format_news_loose_from_summary(body, user_query=uq)
        if (formatted or "").strip():
            try:
                from core.monitoring import MONITOR

                MONITOR.inc("brain_news_tool_narration_repair_total")
            except Exception:
                pass
            return formatted.strip()
    except Exception as e:
        logger.debug("news tool narration repair: %s", e)
    return None


def repair_news_tool_narration_reply_sync(
    reply: str,
    *,
    user_query: str,
    search_body: str = "",
    user_id: str = "",
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    import asyncio
    import concurrent.futures

    coro = repair_news_tool_narration_reply(
        reply,
        user_query=user_query,
        search_body=search_body,
        user_id=user_id,
        persisted=persisted,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=35)


def news_item_pick_enabled() -> bool:
    """Пункт дайджеста: pipeline/tool path или legacy plan direct."""
    try:
        from core.brain_own_turn import (
            brain_news_item_reply_enabled,
            planner_direct_allowed,
        )

        if brain_news_item_reply_enabled():
            return True
        return planner_direct_allowed("news_item")
    except Exception:
        return _env_truthy("NEWS_ITEM_PICK_ENABLED", default=True)


def _news_item_full_article_enabled() -> bool:
    """Полный текст статьи (поиск + url_fetch), не короткий LLM-пересказ."""
    return _env_truthy("NEWS_ITEM_FULL_ARTICLE", default=True)


def _news_item_detail_max_chars() -> int:
    if _news_item_full_article_enabled():
        try:
            n = int(
                (os.getenv("NEWS_ITEM_FULL_MAX_CHARS") or os.getenv("URL_FETCH_MAX_CHARS_RESPONSE") or "12000").strip()
            )
        except ValueError:
            n = 12000
        return max(2000, min(n, 14000))
    try:
        n = int((os.getenv("NEWS_ITEM_DETAIL_MAX_CHARS") or "1200").strip())
    except ValueError:
        n = 1200
    return max(400, min(n, 2400))


def _news_item_max_images() -> int:
    try:
        n = int((os.getenv("NEWS_ITEM_MAX_IMAGES") or "3").strip())
    except ValueError:
        n = 3
    return max(0, min(n, 5))


def _news_item_fetch_timeout_sec() -> float:
    try:
        n = float((os.getenv("NEWS_ITEM_FETCH_TIMEOUT_SEC") or "12").strip())
    except ValueError:
        n = 12.0
    return max(4.0, min(n, 28.0))


def _news_digest_llm_enabled() -> bool:
    """LLM-дайджест: вкл. при search-only и при NEWS_DIGEST_LLM_SUMMARY=true."""
    if not _env_truthy("NEWS_DIGEST_LLM_SUMMARY", default=True):
        return False
    try:
        from core.brain_own_turn import news_digest_search_only_enabled

        if news_digest_search_only_enabled():
            return True
    except Exception:
        pass
    try:
        from core.brain_own_turn import news_rss_fallback_enabled

        if news_rss_fallback_enabled():
            return True
    except Exception:
        pass
    return _env_truthy("NEWS_DIGEST_LLM_SUMMARY", default=True)


def _news_digest_format() -> str:
    """
    Формат дайджеста: narrative — один развёрнутый текст «что обсуждают»;
    list — нумерованные заголовки + сниппеты (как раньше).
    """
    raw = (os.getenv("NEWS_DIGEST_FORMAT") or "narrative").strip().lower()
    return "narrative" if raw == "narrative" else "list"


def _is_world_news_query(user_query: str) -> bool:
    q = (user_query or "").strip().lower()
    if _LOCAL_NEWS_QUERY_RE.search(q):
        return False
    return any(
        k in q
        for k in (
            "в мире",
            "миров",
            "международ",
            "world news",
            "global",
            "главные новости",
            "какие новости",
        )
    )


def _news_digest_narrative_style() -> str:
    """Сырое значение env (без авто world_brief для мировой ленты)."""
    raw = (os.getenv("NEWS_DIGEST_NARRATIVE_STYLE") or "per_item").strip().lower()
    if raw in {"flow", "world_brief", "per_item"}:
        return raw
    return "per_item"


def _resolve_narrative_style(*, user_query: str = "", world_feed: bool = False) -> str:
    """
    per_item + мировая лента → world_brief (обзор с лидом и датой в шапке).
    Явный NEWS_DIGEST_NARRATIVE_STYLE=world_brief|flow|per_item — как в env.
    """
    raw = _news_digest_narrative_style()
    if raw == "flow":
        return "flow"
    if raw == "world_brief":
        return "world_brief"
    if raw == "per_item" and (world_feed or _is_world_news_query(user_query)):
        return "world_brief"
    return "per_item"


def _news_digest_sentences_per_item() -> int:
    try:
        n = int((os.getenv("NEWS_DIGEST_NARRATIVE_SENTENCES_PER_ITEM") or "4").strip())
    except ValueError:
        n = 4
    return max(2, min(n, 7))


def news_story_deep_followup_enabled() -> bool:
    return _env_truthy("NEWS_STORY_DEEP_FOLLOWUP_ENABLED", default=True)


_LOCAL_NEWS_QUERY_RE = re.compile(
    r"(?i)(?:"
    r"беларус|belarus|минск|minsk|"
    r"новости\s+(?:беларуси|рб)\b|"
    r"в\s+беларуси|по\s+беларуси|"
    r"украин|ukrain|киев|kyiv|"
    r"новости\s+россии|росси[ия]\s+новост|"
    r"в\s+россии|по\s+россии"
    r")"
)


def news_digest_local_only(user_text: str) -> bool:
    """
    Явный запрос региональной ленты.
    «Какие новости» без региона — общий дайджест (RU/мир), не только .by из профиля.
    """
    t = (user_text or "").strip()
    if not t:
        return False
    try:
        from modules.external_apis.clients import NewsAPIClient

        if NewsAPIClient.wants_world_news(t):
            return False
    except Exception:
        pass
    return bool(_LOCAL_NEWS_QUERY_RE.search(t))


def _news_digest_filter_country(user_text: str, profile_country: str) -> str:
    """Страна для ранжирования/фильтра collect — только при явном локальном запросе."""
    if news_digest_local_only(user_text):
        return (profile_country or "").strip().upper()
    return ""


def refine_news_digest_search_query(
    user_text: str,
    *,
    country: str = "",
    world_feed: bool = False,
) -> str:
    """«Какие новости» → конкретный поисковый запрос, не главная ria.ru."""
    t = (user_text or "").strip()
    low = t.lower()
    if re.match(r"(?i)^(?:какие|что)\s+новост", low) or low in {
        "новости",
        "что нового",
        "какие новости",
        "что в новостях",
    }:
        if world_feed or re.search(r"(?i)мир|world|международ", low):
            return "site:reuters.com OR site:bbc.com world news today"
        if news_digest_local_only(t):
            co = (country or "").strip().upper()
            if co == "BY" or "беларус" in low:
                return "local news today"
            if co == "UA" or "украин" in low:
                return "site:unian.ua новости события сегодня"
            if co == "RU" or "росси" in low:
                return "site:tass.ru OR site:rbc.ru новости сегодня"
        return "site:tass.ru OR site:kommersant.ru OR site:rbc.ru новости сегодня"
    return t or "главные новости сегодня"


def news_digest_search_queries(
    user_text: str,
    *,
    country: str = "",
    world_feed: bool = False,
) -> List[str]:
    """Основной и запасные запросы — если SearX отдал только главные порталов."""
    primary = refine_news_digest_search_query(
        user_text, country=country, world_feed=world_feed
    )
    out: List[str] = []
    seen: set = set()

    def _add(q: str) -> None:
        qn = re.sub(r"\s+", " ", (q or "").strip())
        if qn and qn.lower() not in seen:
            seen.add(qn.lower())
            out.append(qn)

    _add(primary)
    co = (country or "").strip().upper()
    local_only = news_digest_local_only(user_text)
    if world_feed:
        for q in (
            "site:reuters.com world news today",
            "site:bbc.com news world headlines",
            "site:apnews.com latest headlines",
            "international news Reuters AP today",
        ):
            _add(q)
        if _env_truthy("NEWS_DIGEST_WORLD_THEMATIC_QUERIES", default=True):
            try:
                tmax = int((os.getenv("NEWS_DIGEST_WORLD_THEMATIC_MAX") or "5").strip())
            except ValueError:
                tmax = 5
            tmax = max(2, min(tmax, 8))
            for q in _world_digest_thematic_queries()[:tmax]:
                _add(q)
    elif not local_only:
        for q in (
            "site:tass.ru политика экономика",
            "site:kommersant.ru/doc/ новости",
            "site:rbc.ru short_news",
            "site:interfax.ru world",
            "международные новости события сегодня",
        ):
            _add(q)
    elif co == "BY":
        for q in (
            "news.example.com local news today",
            "site:news3.example.com новости",
            "news2.example.com local news",
            "новости Минск беларусь",
        ):
            _add(q)
    elif co == "UA":
        for q in (
            "site:unian.ua новости",
            "site:pravda.com.ua новости",
        ):
            _add(q)
    else:
        for q in (
            "site:rbc.ru новости сегодня",
            "site:interfax.ru последние",
            "site:ria.ru политика экономика",
        ):
            _add(q)
    return out


def _has_cached_news_digest(persisted: Optional[Dict[str, Any]]) -> bool:
    """Есть ли в сессии пункты ленты (в т.ч. после narrative без нумерации в чате)."""
    ds = _dialogue_state(persisted)
    items = ds.get("last_news_digest_items")
    return isinstance(items, list) and len(items) >= 2


def _news_enrich_on_digest() -> bool:
    return _env_truthy("NEWS_ENRICH_ON_DIGEST", default=False)


def _focused_entity_query_from_title(title: str) -> str:
    """Имена/события из длинной строки дайджеста («арест певца Алексея Хлестова»)."""
    t = (title or "").strip()
    if not t:
        return ""
    low = t.lower()
    m = re.search(
        r"(?i)(?:арест|задержан\w*)\s+"
        r"(?:(?:певц\w*|акт[её]р\w*|музыкант\w*)\s+)?"
        r"([А-ЯЁ][а-яё\-]+\s+[А-ЯЁ][а-яё\-]+)",
        t,
    )
    if m:
        name = m.group(1).strip()
        if "арест" in low or "задерж" in low:
            return f"{name} арест новости"
        return f"{name} новости"
    m2 = re.search(
        r"(?i)([А-ЯЁ][а-яё\-]+\s+[А-ЯЁ][а-яё\-]+)\s*(?:\s|$|—|,)",
        t,
    )
    if m2 and len(m2.group(1)) >= 6:
        return f"{m2.group(1).strip()} новости"
    return ""


def _item_focus_search_title(title: str, publisher: str = "") -> str:
    """SEO «Какие данные…» → тема для поиска статьи."""
    t = (title or "").strip()
    pub = (publisher or "").strip()
    m = re.match(
        r"(?i)^какие\s+(?:данные|виды|способы|события|робот\w*)\s+(.+?)(?:\s+требуют|\s+нельзя|\s+наиболее|\?|$)",
        t,
    )
    if m:
        topic = m.group(1).strip(" .")
        if pub:
            return f"{topic} {pub}"[:200]
        return topic[:200]
    m2 = re.search(r"(?i)(?:напомнили|рассказал[io]?),?\s+какие\s+(.+)", t)
    if m2:
        return m2.group(1).strip()[:200]
    return t


def _build_news_search_query(title: str, publisher: str = "") -> str:
    """Поиск статьи: заголовок + издание; «Как …» — в кавычках, чтобы не Викисловарь."""
    t = _item_focus_search_title(title, publisher)
    pub = (publisher or "").strip()
    if not t:
        return ""
    focused = _focused_entity_query_from_title(t)
    if focused:
        if pub and pub.lower() not in focused.lower():
            return f"{focused} {pub}"[:220]
        return focused[:220]
    q = t
    if pub and pub.lower() not in t.lower():
        q = f"{t} {pub}"
    if re.match(r"(?i)^как\s+\S", t) and len(t) > 20:
        q = f'"{t[:120]}"'
        if pub:
            q = f"{q} {pub}"
    return q[:220]


def _news_llm_model() -> str:
    explicit = (os.getenv("NEWS_DIGEST_LLM_MODEL") or "").strip()
    if explicit:
        return explicit
    item = (os.getenv("NEWS_ITEM_LLM_MODEL") or "").strip()
    if item:
        return item
    main = (os.getenv("OPENROUTER_MODEL") or "").strip()
    if not main:
        main = (os.getenv("BRAIN_LLM_FREE_MODEL") or "").strip()
    if not main:
        tier = (os.getenv("DEFAULT_MODEL") or "free").strip().lower()
        if tier == "premium":
            main = (os.getenv("BRAIN_LLM_PREMIUM_MODEL") or "").strip()
        elif tier == "dev":
            main = (os.getenv("OPENROUTER_MODEL_DEV") or "").strip()
    router = (os.getenv("ROUTER_LLM_MODEL") or "").strip()
    if router and re.search(r"1\.2b|1b-instruct", router, re.I) and main:
        return main
    return main or router


def _news_sources_max() -> int:
    """Max source rows attached to a news reply log."""
    try:
        return max(1, min(16, int((os.getenv("NEWS_SOURCES_LOG_MAX") or "8").strip())))
    except ValueError:
        return 8


def _sources_from_search_results(
    results: Optional[List[Dict[str, Any]]],
    *,
    max_items: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build NewsSource dicts from UniversalSearch rows."""
    from core.news_article_model import build_news_source

    cap = max_items if max_items is not None else _news_sources_max()
    out: List[Dict[str, Any]] = []
    for row in results or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("link") or "").strip()
        if not url.startswith("http"):
            continue
        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or row.get("content") or row.get("body") or "")
        conf = 0.55 if len(snippet) > 80 else 0.35
        out.append(
            build_news_source(
                url,
                fetch_method="web_search",
                title_used=title,
                fetch_success=True,
                text_length=len(snippet),
                parsing_confidence=conf,
            )
        )
        if len(out) >= cap:
            break
    return out


def _sources_from_displayed(
    displayed: Optional[List[Dict[str, Any]]],
    *,
    max_items: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build NewsSource dicts from digest display rows."""
    from core.news_article_model import build_news_source

    cap = max_items if max_items is not None else _news_sources_max()
    out: List[Dict[str, Any]] = []
    for row in displayed or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("link") or "").strip()
        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or "")
        pub = str(row.get("publisher") or "")
        conf = float(row.get("parsing_confidence") or 0.0)
        if url.startswith("http"):
            if conf <= 0:
                conf = 0.65 if len(snippet) > 60 else 0.4
            method = "urlfetch" if row.get("page_enriched") or row.get("enriched") else "web_search"
            out.append(
                build_news_source(
                    url,
                    fetch_method=method,
                    title_used=title,
                    fetch_success=True,
                    text_length=len(snippet),
                    parsing_confidence=conf,
                )
            )
        elif title and pub:
            out.append(
                build_news_source(
                    url or f"rss://{pub}",
                    fetch_method="rss",
                    title_used=title,
                    fetch_success=True,
                    text_length=len(snippet),
                    parsing_confidence=0.5,
                )
            )
        if len(out) >= cap:
            break
    return out


def _source_from_fetched_article(
    article: Dict[str, Any],
    *,
    title: str = "",
) -> Optional[Dict[str, Any]]:
    """Build one NewsSource from _fetch_page_article result."""
    from core.news_article_model import build_news_source

    if not isinstance(article, dict):
        return None
    url = str(article.get("url") or "").strip()
    text = str(article.get("text") or "")
    if not url.startswith("http"):
        return None
    conf = float(article.get("parsing_confidence") or 0.0)
    if conf <= 0:
        conf = 0.7 if len(text) > 200 else 0.3
    return build_news_source(
        url,
        fetch_method="urlfetch",
        title_used=title or url,
        fetch_success=bool(text.strip()),
        text_length=len(text),
        parsing_confidence=conf,
    )


def _news_self_verify_enabled() -> bool:
    """Whether narrative news replies run self-verify with source_context."""
    return _env_truthy("NEWS_SELF_VERIFY_ENABLED", default=True)


def _source_context_for_verify(sources: Optional[List[Dict[str, Any]]]) -> str:
    """Compact allowed-facts block for news self-verify."""
    lines: List[str] = []
    for row in sources or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title_used") or row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title and not url:
            continue
        conf = float(row.get("parsing_confidence") or 0.0)
        lines.append(
            f"- {title[:160]} | {url[:220]} | confidence={conf:.0%}"
        )
        if len(lines) >= _news_sources_max():
            break
    return "\n".join(lines)


def _news_consistency_check_enabled() -> bool:
    """Whether news replies run dialogue consistency check (log-only)."""
    return _env_truthy("NEWS_CONSISTENCY_CHECK_ENABLED", default=True)


def _dialogue_rows_for_consistency(recent_dialogue: Any) -> List[Dict[str, Any]]:
    """Map recent_dialogue to checker rows {user, bot, index}."""
    if not isinstance(recent_dialogue, list):
        return []
    out: List[Dict[str, Any]] = []
    if recent_dialogue and isinstance(recent_dialogue[0], dict) and (
        "bot" in recent_dialogue[0] or "user" in recent_dialogue[0]
    ):
        for i, row in enumerate(recent_dialogue):
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "user": str(row.get("user") or ""),
                    "bot": str(row.get("bot") or ""),
                    "index": int(row.get("index") if row.get("index") is not None else i),
                }
            )
        return out
    user_buf = ""
    idx = 0
    for turn in recent_dialogue:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        text = str(turn.get("text") or turn.get("content") or "").strip()
        if role == "user":
            user_buf = text
        elif role == "assistant" and text:
            out.append({"user": user_buf, "bot": text, "index": idx})
            idx += 1
            user_buf = ""
    return out


def _consistency_log_kwargs(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map consistency checker result to news_generation_log fields."""
    if not isinstance(result, dict):
        return {}
    return {
        "consistency_checked": True,
        "consistency_ok": bool(result.get("consistent", True)),
        "consistency_conflicts_count": len(result.get("conflicts") or []),
        "consistency_recommendation": str(result.get("recommendation") or "safe"),
    }


async def _run_news_consistency_check(
    reply: str,
    *,
    user_id: str,
    query: str,
    sources: Optional[List[Dict[str, Any]]],
    recent_dialogue: Any,
) -> Optional[Dict[str, Any]]:
    """Log-only consistency check against recent dialogue turns."""
    if not _news_consistency_check_enabled():
        return None
    text = (reply or "").strip()
    if len(text) < 60:
        return None
    rows = _dialogue_rows_for_consistency(recent_dialogue)
    if not rows:
        return None
    try:
        from core.news_consistency_checker import NewsConsistencyChecker

        result = await NewsConsistencyChecker().check_dialogue_consistency(
            user_id=str(user_id or ""),
            recent_dialogue=rows,
            new_reply=text,
            new_sources=list(sources or []),
        )
        if not result.get("consistent"):
            logger.warning(
                "news consistency conflict uid=%s rec=%s conflicts=%s query=%.80s",
                user_id,
                result.get("recommendation"),
                len(result.get("conflicts") or []),
                query,
            )
            try:
                from core.monitoring import MONITOR

                MONITOR.inc("news_consistency_conflict_total")
            except Exception:
                pass
        return result
    except Exception as exc:
        logger.debug("news consistency check: %s", exc)
        return None


async def _apply_news_self_verify(
    reply: str,
    *,
    user_query: str,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> tuple[str, str]:
    """Run self-verify grounded in NewsSource rows; return (reply, verify_result)."""
    text = (reply or "").strip()
    if not text or not _news_self_verify_enabled():
        return text, "N/A"
    ctx = _source_context_for_verify(sources)
    if not ctx.strip():
        return text, "N/A"
    try:
        from core.brain.self_verify_pass import run_self_verify, self_verify_fix_quality
        from core.openrouter_provider import get_openrouter_provider

        ver = await run_self_verify(
            text,
            user_query or "новости",
            get_openrouter_provider(),
            source_context=ctx,
        )
        if ver.startswith("fix:"):
            fix_text = ver[4:].strip()
            if fix_text and self_verify_fix_quality(fix_text):
                return fix_text, ver
        return text, ver if ver else "ok"
    except Exception as exc:
        logger.debug("news self_verify: %s", exc)
        return text, "N/A"


def _emit_news_generation_log(
    *,
    user_id: str,
    query: str,
    sources: Optional[List[Dict[str, Any]]],
    reply: str,
    llm_model: str = "",
    self_verify_run: bool = False,
    self_verify_result: str = "N/A",
    consistency_checked: bool = False,
    consistency_ok: bool = True,
    consistency_conflicts_count: int = 0,
    consistency_recommendation: str = "safe",
) -> None:
    """Append news_generation row to llm_usage.jsonl."""
    if not (reply or "").strip():
        return
    try:
        from core.llm_usage_store import append_record, news_generation_log

        append_record(
            news_generation_log(
                user_id=str(user_id or ""),
                query=(query or "")[:500],
                sources=list(sources or []),
                reply=str(reply).strip(),
                llm_model=llm_model or _news_llm_model(),
                self_verify_run=bool(self_verify_run),
                self_verify_result=str(self_verify_result or "N/A")[:500],
                consistency_checked=bool(consistency_checked),
                consistency_ok=bool(consistency_ok),
                consistency_conflicts_count=int(consistency_conflicts_count),
                consistency_recommendation=str(consistency_recommendation or "safe")[:80],
            )
        )
    except Exception as exc:
        logger.debug("news_generation_log: %s", exc)


async def _return_news_with_telemetry(
    reply: Optional[str],
    *,
    user_id: str = "",
    query: str = "",
    sources: Optional[List[Dict[str, Any]]] = None,
    recent_dialogue: Any = None,
    llm_model: str = "",
    self_verify_run: Optional[bool] = None,
    self_verify_result: str = "N/A",
) -> Optional[str]:
    """Normalize news reply, run consistency check, persist generation telemetry."""
    if not reply or not str(reply).strip():
        return None
    text = str(reply).strip()
    consistency = await _run_news_consistency_check(
        text,
        user_id=user_id,
        query=query,
        sources=sources,
        recent_dialogue=recent_dialogue,
    )
    _emit_news_generation_log(
        user_id=user_id,
        query=query,
        sources=sources,
        reply=text,
        llm_model=llm_model,
        self_verify_run=bool(self_verify_run) if self_verify_run is not None else False,
        self_verify_result=self_verify_result,
        **_consistency_log_kwargs(consistency),
    )
    return text


def _telemetry_log_kwargs(telemetry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map compose telemetry dict to news_generation_log fields."""
    if not isinstance(telemetry, dict):
        return {}
    return {
        "self_verify_run": telemetry.get("self_verify_run"),
        "self_verify_result": str(telemetry.get("self_verify_result") or "N/A"),
    }


def _set_last_news_picked_index(persisted: Optional[Dict[str, Any]], pick: int) -> None:
    if not isinstance(persisted, dict) or pick < 1:
        return
    ds = _dialogue_state(persisted)
    if "dialogue_state" not in persisted or not isinstance(persisted.get("dialogue_state"), dict):
        persisted["dialogue_state"] = ds
    ds["last_news_picked_index"] = pick


def _parse_llm_digest_paragraphs(llm_text: str, n_items: int) -> Dict[int, str]:
    """Из ответа LLM: номер пункта → абзац сути."""
    out: Dict[int, str] = {}
    text = (llm_text or "").strip()
    if not text:
        return out
    for m in re.finditer(r"(?ms)^\s*(\d{1,2})\.\s+(.+?)(?=^\s*\d{1,2}\.\s+|\Z)", text):
        try:
            idx = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if idx < 1 or idx > max(n_items, 12):
            continue
        block = (m.group(2) or "").strip()
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        para_parts: List[str] = []
        for ln in lines:
            if re.match(r"^·\s", ln):
                break
            if para_parts and re.match(r"^\d+\.\s", ln):
                break
            if len(ln) > 28 and not ln.startswith("·"):
                para_parts.append(ln)
        if not para_parts and len(lines) > 1:
            para_parts = lines[1:]
        para = " ".join(para_parts).strip()
        if len(para) >= 36:
            out[idx] = para
    return out


async def _llm_digest_summaries(
    displayed: List[Dict[str, Any]],
    *,
    expanded: bool = False,
    user_id: str = "",
) -> List[Dict[str, Any]]:
    """3–4 (или 4–6) предложения на пункт только по заголовку и изданию."""
    if not displayed or not _news_digest_llm_enabled():
        return displayed
    model = _news_llm_model()
    if not model:
        return displayed
    cap_items = min(len(displayed), 7)
    rows = [r for r in displayed[:cap_items] if isinstance(r, dict)]
    if not rows:
        return displayed
    lines_in: List[str] = []
    for row in rows:
        n = int(row.get("index") or len(lines_in) + 1)
        title = str(row.get("title") or "").strip()
        pub = str(row.get("publisher") or "").strip()
        if not title:
            continue
        chunk = f"{n}. {title}"
        if pub:
            chunk += f"\n   Издание: {pub}"
        lines_in.append(chunk)
    if not lines_in:
        return displayed
    sent_hint = "4–6 предложений" if expanded else "3–4 предложения"
    sys_p = (
        "Ты редактор новостной ленты в Telegram. По каждому пункту (только заголовок и издание) "
        f"напиши {sent_hint} на русском: о чём новость, контекст, почему важно. "
        "Не выдумывай факты, цифры и имена, которых нет в заголовке. "
        "Формат строго для каждого N:\n"
        "N. [заголовок одной строкой]\n"
        "[абзац без маркированных списков]\n"
        "Пустая строка между пунктами. Без URL."
    )
    user_p = "Материалы:\n\n" + "\n\n".join(lines_in)
    try:
        mt = int((os.getenv("NEWS_DIGEST_LLM_MAX_TOKENS") or "1400").strip())
    except ValueError:
        mt = 1400
    mt = max(400, min(mt, 2400))
    try:
        from core.llm_tiered import llm_generate_tiered
        from core.openrouter_provider import get_openrouter_provider

        pack = await with_retry(
            lambda: llm_generate_tiered(
                get_openrouter_provider(),
                tag="news_digest_llm" if not expanded else "news_digest_llm_expanded",
                prompt=user_p,
                system_prompt=sys_p,
                model=model,
                max_tokens=mt,
                temperature=0.35,
                base_timeout=22.0 if expanded else 18.0,
                task_tier="fast",
            ),
            retries=0,
            timeout_sec=26.0 if expanded else 22.0,
            tag="news_digest_llm",
        )
        body = str(pack.get("content") or pack.get("text") or "").strip()
    except Exception as e:
        logger.debug("news_digest llm: %s", e)
        return displayed
    parsed = _parse_llm_digest_paragraphs(body, len(rows))
    if not parsed:
        return displayed
    from core.telegram_output_guard import (
        _clip_words,
        _looks_like_disambiguation_snippet,
        _news_snippet_max_chars,
    )

    out_rows: List[Dict[str, Any]] = []
    for row in displayed:
        r = dict(row)
        try:
            idx = int(r.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        para = parsed.get(idx, "")
        title = str(r.get("title") or "")
        if para and not _looks_like_disambiguation_snippet(para, title):
            r["snippet"] = _clip_words(para, _news_snippet_max_chars())
        out_rows.append(r)
    return out_rows


def _text_predominantly_latin(text: str) -> bool:
    """Заголовки из SearX (world) часто на EN — для bypass overlap с RU narrative."""
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < 8:
        return False
    latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
    return latin / len(letters) >= 0.65


def _text_has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яё]", text or "", re.I))


def _skip_narrative_title_token_overlap(
    body: str, displayed: Optional[List[Dict[str, Any]]]
) -> bool:
    """EN-заголовки + RU-пересказ LLM: overlap по токенам всегда ~0 — не отбрасывать."""
    if not displayed or not _text_has_cyrillic(body):
        return False
    titles = " ".join(
        str(row.get("title") or "")
        for row in displayed[:8]
        if isinstance(row, dict)
    )
    return _text_predominantly_latin(titles)


def _narrative_digest_body_usable(
    body: str,
    displayed: Optional[List[Dict[str, Any]]] = None,
    *,
    narrative_style: str = "",
) -> bool:
    b = (body or "").strip()
    style = (narrative_style or _news_digest_narrative_style()).strip().lower()
    min_len = 350 if style == "world_brief" else 160
    if len(b) < min_len:
        return False
    first = b.split("\n", 1)[0].strip()
    if re.match(r"^\s*\d{1,2}\.\s+\S", first) and len(b) < 400:
        return False
    low = b.lower()
    if re.search(
        r"(?i)(типичные темы|линия ленты|состояние информации|"
        r"пользователь всегда был обновлен|кратко из заголовков видно|"
        r"резюме:|критически замечание:|повседневной жизни|"
        r"качество информации для пользователей|данные и технологии играют|"
        r"пользователь просит|план поиска|найдено\s+\d+\s+веб|просмотр\s+\d+\s+страниц|"
        r"после выполнения всех поисковых|в первом раунде я выполню)",
        low,
    ):
        return False
    if style == "world_brief":
        if b.count("\n\n") < 2 and len(b) < 700:
            return False
    elif style == "per_item" and b.count("\n\n") < 1:
        if len(b) < 400:
            return False
    if displayed and not _skip_narrative_title_token_overlap(b, displayed):
        try:
            from core.telegram_output_guard import _token_set

            title_tokens: set = set()
            for row in displayed[:8]:
                if isinstance(row, dict):
                    title_tokens |= _token_set(str(row.get("title") or ""))
            title_tokens -= {
                "новости",
                "новость",
                "главные",
                "последние",
                "сегодня",
                "мире",
                "мира",
                "world",
                "news",
            }
            if len(title_tokens) >= 3:
                body_tokens = _token_set(b)
                overlap = len(title_tokens & body_tokens)
                if overlap < min(2, max(1, len(title_tokens) // 5)):
                    return False
        except Exception as e:
            logger.debug("narrative digest usability tokens: %s", e)
    return True


def _finish_narrative_digest(
    body: str,
    *,
    user_query: str,
    world_feed: bool,
    narrative_style: str,
) -> str:
    """Шапка (мир + дата), тело, футер открытых источников / follow-up."""
    from core.telegram_output_guard import (
        _finish_news_digest,
        _news_digest_header,
        _news_narrative_footer,
    )

    text = (body or "").strip()
    if not text:
        return ""
    if narrative_style == "world_brief":
        head = _news_digest_header(user_query)
        if head and head.strip().lower() not in text[: min(120, len(text))].lower():
            text = head + text
    foot = _news_narrative_footer(world_feed=world_feed, user_query=user_query)
    if foot and foot.lower() not in text.lower():
        text = f"{text}\n\n{foot}"
    return _finish_news_digest(text, add_brief_footer=False)


async def _llm_digest_narrative_brief(
    displayed: List[Dict[str, Any]],
    *,
    user_query: str = "",
    expanded: bool = False,
    user_id: str = "",
    world_feed: bool = False,
    sources: Optional[List[Dict[str, Any]]] = None,
    telemetry: Optional[Dict[str, Any]] = None,
) -> str:
    """Один связный дайджест по заголовкам RSS — без нумерованного списка в чате."""
    if not displayed or not _news_digest_llm_enabled():
        return ""
    model = _news_llm_model()
    if not model:
        return ""
    narr_style = _resolve_narrative_style(user_query=user_query, world_feed=world_feed)
    try:
        cap_hi = int((os.getenv("NEWS_DIGEST_WORLD_MAX_ITEMS") or "10").strip())
    except ValueError:
        cap_hi = 10
    cap_hi = max(6, min(cap_hi, 12))
    cap_items = min(len(displayed), cap_hi if narr_style == "world_brief" else 8)
    rows = [r for r in displayed[:cap_items] if isinstance(r, dict)]
    if not rows:
        return ""
    lines_in: List[str] = []
    for row in rows:
        n = int(row.get("index") or len(lines_in) + 1)
        title = str(row.get("title") or "").strip()
        pub = str(row.get("publisher") or "").strip()
        sn = str(row.get("snippet") or "").strip()
        if not title:
            continue
        chunk = f"{n}. {title}"
        if pub:
            chunk += f"\n   Издание: {pub}"
        if sn and len(sn) >= 40 and len(sn) <= 420:
            chunk += f"\n   Кратко: {sn}"
        lines_in.append(chunk)
    if not lines_in:
        return ""
    sent_n = _news_digest_sentences_per_item()
    if narr_style == "world_brief":
        paras = "5–7 абзацев" if expanded else "4–6 абзацев"
        sys_p = (
            "Ты автор ежедневного обзора «Главные мировые новости» для Telegram. "
            "По списку заголовков и строк «Кратко» ниже напиши связный дайджест на русском.\n"
            "Структура:\n"
            "- НЕ пиши заголовок с датой (его добавит система).\n"
            "- Первое предложение — лид: «Сегодня в центре мировых событий — …» "
            "(1–2 главные темы дня из списка).\n"
            f"- Далее {paras} по крупным темам (геополитика, конфликты, экономика, наука/космос, спорт — "
            "только если есть в списке). Близкие заголовки объединяй в один абзац.\n"
            f"- В каждом абзаце {sent_n}–{sent_n + 2} предложений: факт, контекст, значение. "
            "Имена, цифры, места — только из заголовков или «Кратко».\n"
            "- Без нумерации, без URL, без перечисления названий СМИ подряд.\n"
            "- Тон — нейтральный обзор открытых источников, не таблоид и не SEO-пересказ меню сайтов.\n"
            "- Запрещено: план поиска, «пользователь просит», «найдено N страниц», мета про инструменты — "
            "только готовый дайджест для читателя."
        )
    elif narr_style == "per_item":
        sent_hi = sent_n + 1 if not expanded else sent_n + 2
        sys_p = (
            "Ты ведущий новостного дайджеста в Telegram. По каждой теме из списка ниже — "
            f"отдельный абзац из {sent_n}–{sent_hi} предложений: что произошло, контекст, "
            "почему это заметно в ленте (только из заголовка и строки «Кратко»). "
            "Между абзацами — пустая строка. Без нумерации «1.» «2.» и без URL. "
            "Не перечисляй издания подряд. Стиль — живой обзор, чтобы читателю не нужно "
            "было открывать статьи за каждой строкой. Не выдумывай факты, цифры и имена."
        )
    else:
        paras = "4–6 абзацев" if expanded else "3–5 абзацев"
        sys_p = (
            "Ты ведущий дайджеста в Telegram. По списку заголовков ниже напиши на русском "
            f"{paras} связного текста: что сейчас в тренде в ленте, какие темы пересекаются, "
            "короткий контекст там, где он явно следует из заголовка. "
            "Стиль — разговорный, как живой обзор «сейчас в интернете обсуждают…», без нумерованного списка "
            "и без строк вида «1. …», «2. …». Не перечисляй издания подряд и не цитируй URL. "
            "Не выдумывай факты, цифры и имена, которых нет в заголовках или в строке «Кратко». "
            "Если заголовок выглядит как SEO-рубрика без события — не раздувай его."
        )
    uq = (user_query or "").strip()
    user_p = (
        (f"Запрос пользователя: {uq}\n\n" if uq else "")
        + "Заголовки из ленты:\n\n"
        + "\n\n".join(lines_in)
    )
    try:
        mt_default = "2800" if narr_style == "world_brief" else "2200"
        if expanded:
            mt_default = "3200" if narr_style == "world_brief" else "2600"
        mt = int((os.getenv("NEWS_DIGEST_NARRATIVE_MAX_TOKENS") or mt_default).strip())
    except ValueError:
        mt = 2800 if narr_style == "world_brief" else 2200
    mt = max(800, min(mt, 4000))
    try:
        chars_default = "4600" if expanded else "4200"
        max_chars = int((os.getenv("NEWS_DIGEST_NARRATIVE_MAX_CHARS") or chars_default).strip())
    except ValueError:
        max_chars = 4200
    max_chars = max(1200, min(max_chars, 6000))
    try:
        from core.telegram_progress import telegram_progress_pulse

        await telegram_progress_pulse("Готовлю новостной обзор…", force=True)
    except Exception as e:
        logger.debug("news narrative progress pulse: %s", e)
    try:
        from core.llm_tiered import llm_generate_tiered
        from core.openrouter_provider import get_openrouter_provider
        from core.telegram_output_guard import _clip_words

        llm_base_to, llm_outer_to = _resolve_narrative_llm_timeouts(
            expanded=expanded,
            narr_style=narr_style,
            prompt=user_p,
            max_tokens=mt,
        )
        pack = await with_retry(
            lambda: llm_generate_tiered(
                get_openrouter_provider(),
                tag="news_digest_llm_narrative_expanded" if expanded else "news_digest_llm_narrative",
                prompt=user_p,
                system_prompt=sys_p,
                model=model,
                max_tokens=mt,
                temperature=0.38 if narr_style == "world_brief" else 0.42,
                base_timeout=llm_base_to,
                task_tier="fast",
            ),
            retries=0,
            timeout_sec=llm_outer_to,
            tag="news_digest_llm_narrative",
            record_errors=False,
        )
        body = str(pack.get("content") or pack.get("text") or "").strip()
        body = _clip_words(body, max_chars)
    except Exception as e:
        logger.debug("news_digest narrative llm: %s", e)
        return ""
    if not _narrative_digest_body_usable(body, displayed=rows, narrative_style=narr_style):
        return ""
    src = sources if sources is not None else _sources_from_displayed(rows)
    verified, ver_result = await _apply_news_self_verify(
        body,
        user_query=uq or user_query,
        sources=src,
    )
    if isinstance(telemetry, dict):
        telemetry["self_verify_run"] = ver_result != "N/A"
        telemetry["self_verify_result"] = ver_result
    return verified


def _news_country_iso2(facts: Dict[str, Any]) -> str:
    """ISO2 для Google News RSS (BY, RU, …) из user_facts."""
    raw = str(facts.get("country_code") or facts.get("country") or "").strip()
    if not raw:
        return ""
    up = raw.upper()
    if re.fullmatch(r"[A-Z]{2}", up):
        return up
    low = raw.lower()
    if "беларус" in low or "belarus" in low or low in {"by", "rb", "рб"}:
        return "BY"
    if "росси" in low or "russia" in low:
        return "RU"
    if "казах" in low or "kazakh" in low:
        return "KZ"
    if "украин" in low or "ukraine" in low:
        return "UA"
    return ""


def _user_facts_from_persisted(persisted: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(persisted, dict):
        return {}
    facts = persisted.get("user_facts")
    return facts if isinstance(facts, dict) else {}


def _dialogue_state(persisted: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(persisted, dict):
        return {}
    ds = persisted.get("dialogue_state")
    return ds if isinstance(ds, dict) else {}


def stash_news_digest_context(
    persisted: Optional[Dict[str, Any]],
    rss_items: List[Dict[str, Any]],
    *,
    query: str = "",
    country: str = "",
    world_feed: bool = False,
) -> List[Dict[str, Any]]:
    """Сохранить пункты дайджеста (как в Telegram) + метаданные для refetch по номеру."""
    if not isinstance(persisted, dict) or not rss_items:
        return []
    from core.telegram_output_guard import collect_news_display_items

    displayed = collect_news_display_items(rss_items, user_query=query)
    if not displayed:
        return []
    ds = _dialogue_state(persisted)
    if "dialogue_state" not in persisted or not isinstance(persisted.get("dialogue_state"), dict):
        persisted["dialogue_state"] = ds
    ds["last_news_digest_items"] = displayed
    ds["last_news_digest_meta"] = {
        "query": (query or "").strip()[:200],
        "country": (country or "").strip().upper()[:8],
        "world_feed": bool(world_feed),
    }
    return displayed


def _persist_news_digest_displayed(
    persisted: Optional[Dict[str, Any]],
    displayed: List[Dict[str, Any]],
    *,
    query: str = "",
    country: str = "",
    world_feed: bool = False,
) -> List[Dict[str, Any]]:
    if not isinstance(persisted, dict) or not displayed:
        return []
    ds = _dialogue_state(persisted)
    if "dialogue_state" not in persisted or not isinstance(persisted.get("dialogue_state"), dict):
        persisted["dialogue_state"] = ds
    ds["last_news_digest_items"] = displayed
    ds["last_news_digest_meta"] = {
        "query": (query or "").strip()[:200],
        "country": (country or "").strip().upper()[:8],
        "world_feed": bool(world_feed),
        "source": "web_search",
    }
    return displayed


def stash_news_digest_from_search_results(
    persisted: Optional[Dict[str, Any]],
    search_results: List[Dict[str, Any]],
    *,
    query: str = "",
    country: str = "",
    world_feed: bool = False,
) -> List[Dict[str, Any]]:
    """Stash пунктов дайджеста из веб-поиска (без RSS)."""
    if not isinstance(persisted, dict) or not search_results:
        return []
    from core.telegram_output_guard import collect_news_display_items_from_search

    displayed = collect_news_display_items_from_search(
        search_results, user_query=query, country=country, world_feed=world_feed
    )
    if not displayed:
        return []
    return _persist_news_digest_displayed(
        persisted,
        displayed,
        query=query,
        country=country,
        world_feed=world_feed,
    )


async def stash_news_digest_context_async(
    persisted: Optional[Dict[str, Any]],
    rss_items: List[Dict[str, Any]],
    *,
    query: str = "",
    country: str = "",
    world_feed: bool = False,
    user_id: str = "",
) -> List[Dict[str, Any]]:
    """collect → per-item enrich → stash (для «1»/«2»/«3»)."""
    if not isinstance(persisted, dict) or not rss_items:
        return []
    from core.telegram_output_guard import collect_news_display_items

    displayed = collect_news_display_items(rss_items, user_query=query)
    if not displayed:
        return []
    if _news_enrich_on_digest():
        displayed = await _enrich_rss_items_per_headline(
            displayed,
            country=country,
            user_id=str(user_id or ""),
        )
    ds = _dialogue_state(persisted)
    if "dialogue_state" not in persisted or not isinstance(persisted.get("dialogue_state"), dict):
        persisted["dialogue_state"] = ds
    ds["last_news_digest_items"] = displayed
    ds["last_news_digest_meta"] = {
        "query": (query or "").strip()[:200],
        "country": (country or "").strip().upper()[:8],
        "world_feed": bool(world_feed),
    }
    return displayed


def stash_news_digest_items(persisted: Optional[Dict[str, Any]], rss_items: List[Dict[str, Any]]) -> None:
    """Обратная совместимость — см. stash_news_digest_context."""
    stash_news_digest_context(persisted, rss_items)


def sync_news_digest_persisted(
    context: Optional[Dict[str, Any]],
    persisted: Optional[Dict[str, Any]],
) -> None:
    """После RSS-дайджеста: stash в context.dialogue_state и на диск (для «1»/«2» на след. ход)."""
    if not isinstance(persisted, dict) or not isinstance(context, dict):
        return
    ds = persisted.get("dialogue_state")
    if not isinstance(ds, dict) or not ds.get("last_news_digest_items"):
        return
    cur = context.get("dialogue_state")
    merged = dict(cur) if isinstance(cur, dict) else {}
    merged["last_news_digest_items"] = ds.get("last_news_digest_items")
    if ds.get("last_news_digest_meta"):
        merged["last_news_digest_meta"] = ds.get("last_news_digest_meta")
    context["dialogue_state"] = merged
    uid = str(context.get("user_id") or "").strip()
    if not uid:
        return
    try:
        from core.behavior_store import BehaviorStore

        bs = BehaviorStore()
        gid = context.get("group_id")
        rec = bs.load(uid, gid)
        rds = dict(rec.get("dialogue_state") or {})
        rds["last_news_digest_items"] = merged["last_news_digest_items"]
        if merged.get("last_news_digest_meta"):
            rds["last_news_digest_meta"] = merged["last_news_digest_meta"]
        rec["dialogue_state"] = rds
        bs.save(uid, gid, rec)
    except Exception as e:
        logger.debug("sync_news_digest behavior: %s", e)


def _last_assistant_digest_text(recent_dialogue: Any) -> str:
    from core.brain.text_helpers import _body_looks_like_news_digest

    rows = recent_dialogue if isinstance(recent_dialogue, list) else []
    for turn in reversed(rows[-10:]):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in ("assistant", "bot", "gemma"):
            continue
        body = str(turn.get("text") or turn.get("content") or turn.get("payload") or "").strip()
        if _body_looks_like_news_digest(body):
            return body
    return ""


def persist_news_digest_from_assistant_reply(
    reply: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """После ответа с дайджестом — сохранить пункты для «4» / «развёрнуто» на следующем ходе."""
    body = (reply or "").strip()
    if not body or not isinstance(persisted, dict):
        return
    try:
        from core.brain.text_helpers import _body_looks_like_news_digest
        from core.telegram_output_guard import parse_numbered_news_digest_items

        parsed_n = parse_numbered_news_digest_items(body)
        if not _body_looks_like_news_digest(body) and len(parsed_n) < 2:
            ds = _dialogue_state(persisted)
            if isinstance(ds.get("last_news_digest_items"), list) and len(
                ds.get("last_news_digest_items") or []
            ) >= 2:
                meta = ds.get("last_news_digest_meta")
                if not isinstance(meta, dict):
                    meta = {}
                meta = dict(meta)
                meta["narrative_visible"] = True
                ds["last_news_digest_meta"] = meta
                if isinstance(context, dict):
                    sync_news_digest_persisted(context, persisted)
            return
        stash_parsed_digest_from_assistant(persisted, body)
        if isinstance(context, dict):
            sync_news_digest_persisted(context, persisted)
    except Exception as e:
        logger.debug("persist_news_digest_from_assistant_reply: %s", e)


def _normalize_digest_title(title: str) -> str:
    """Убрать markdown из заголовка пункта дайджеста."""
    t = (title or "").strip()
    if not t:
        return ""
    t = re.sub(r"\*+", "", t).strip()
    t = re.sub(r"^[—–-]\s*", "", t).strip()
    return t


def _parsed_digest_items_from_text(body: str) -> List[Dict[str, Any]]:
    from core.telegram_output_guard import parse_numbered_news_digest_items

    parsed = parse_numbered_news_digest_items(body)
    out: List[Dict[str, Any]] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        title = _normalize_digest_title(str(row.get("title") or ""))
        if not title:
            continue
        out.append(
            {
                "index": row.get("index"),
                "title": title,
                "publisher": str(row.get("publisher") or "").strip(),
                "snippet": str(row.get("snippet") or "").strip(),
                "link": str(row.get("link") or "").strip(),
            }
        )
    return out


def _news_title_stop_tokens() -> frozenset[str]:
    return frozenset(
        {
            "новости",
            "новость",
            "россия",
            "россии",
            "украина",
            "украины",
            "москва",
            "москве",
            "киев",
            "киеве",
            "ракетный",
            "ракетного",
            "удар",
            "удара",
            "удары",
            "зданию",
            "здания",
            "объект",
            "объекта",
            "гражданской",
            "инфраструктуры",
            "военных",
            "военные",
            "данным",
            "источников",
            "событий",
            "ключевых",
            "международные",
            "международный",
            "решение",
            "вызвало",
            "обсуждают",
            "назвал",
            "назвала",
            "призвал",
            "выступил",
            "зафиксировано",
            "отмечает",
        }
    )


def _title_anchor_substrings(title: str) -> List[str]:
    """Уникальные якоря из заголовка (город, организация) — не общие слова ленты."""
    t = re.sub(r"\*+", "", (title or "").lower())
    raw = re.findall(r"[a-zа-яё]{5,}", t)
    stop = _news_title_stop_tokens()
    anchors: List[str] = []
    seen: set[str] = set()
    for w in raw:
        if w in stop or w in seen:
            continue
        stem = w[:9]
        if len(stem) < 5:
            continue
        seen.add(w)
        anchors.append(stem)
    return anchors[:6]


def _anchors_satisfied(title: str, blob: str, *, url: str = "") -> bool:
    """Если в заголовке есть явные якоря — они должны быть в тексте/URL статьи."""
    if not _env_truthy("NEWS_ITEM_REQUIRE_TITLE_ANCHORS", default=True):
        return True
    anchors = _title_anchor_substrings(title)
    if len(anchors) < 2:
        return True
    hay = f"{blob} {url}".lower()
    hits = sum(1 for a in anchors if a in hay)
    return hits >= max(1, (len(anchors) + 1) // 2)


def _item_has_stashed_link(item: Dict[str, Any]) -> bool:
    for key in ("link", "source_url", "google_link"):
        u = str(item.get(key) or "").strip()
        if u.startswith("http") and (_url_looks_like_article(u) or _is_google_news_url(u)):
            return True
    return False


def _merge_parsed_digest_with_stash(
    parsed: List[Dict[str, Any]],
    cached: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Текст из чата + ссылки RSS по номеру пункта (не терять URL при LLM-переписывании заголовков)."""
    if not parsed:
        return []
    if not cached:
        return parsed
    from core.telegram_output_guard import _jaccard

    by_index: Dict[int, Dict[str, Any]] = {}
    for row in cached:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if idx >= 1:
            by_index[idx] = row
    merged: List[Dict[str, Any]] = []
    for i, prow in enumerate(parsed):
        if not isinstance(prow, dict):
            continue
        out = dict(prow)
        out["title"] = _normalize_digest_title(str(out.get("title") or ""))
        try:
            pick = int(out.get("index") or 0)
        except (TypeError, ValueError):
            pick = 0
        if pick < 1:
            pick = i + 1
            out["index"] = pick
        stash = by_index.get(pick)
        if not isinstance(stash, dict):
            merged.append(out)
            continue
        title = str(out.get("title") or "")
        stash_title = str(stash.get("title") or "")
        if stash_title and title and _jaccard(title, stash_title) < 0.12:
            logger.debug(
                "news digest merge: index %s title drift (chat vs stash), keep links only",
                pick,
            )
        for key in ("link", "google_link", "source_url", "snippet"):
            if not str(out.get(key) or "").strip() and str(stash.get(key) or "").strip():
                out[key] = stash.get(key)
        merged.append(out)
    return merged


def stash_parsed_digest_from_assistant(
    persisted: Optional[Dict[str, Any]],
    assistant_text: str,
) -> List[Dict[str, Any]]:
    """Синхронизировать last_news_digest_items с тем, что пользователь видел в чате (LLM-дайджест)."""
    items = _parsed_digest_items_from_text(assistant_text)
    if len(items) < 2 or not isinstance(persisted, dict):
        return items
    ds = _dialogue_state(persisted)
    cached = ds.get("last_news_digest_items")
    if isinstance(cached, list) and cached:
        items = _merge_parsed_digest_with_stash(items, cached)
    if "dialogue_state" not in persisted or not isinstance(persisted.get("dialogue_state"), dict):
        persisted["dialogue_state"] = ds
    ds["last_news_digest_items"] = items
    meta = ds.get("last_news_digest_meta")
    if not isinstance(meta, dict):
        meta = {}
    meta = dict(meta)
    meta["assistant_titles"] = True
    if _item_has_stashed_link(items[0] if items else {}):
        meta.pop("source", None)
    else:
        meta["source"] = "assistant_parse"
    ds["last_news_digest_meta"] = meta
    return items


def _digest_items_from_dialogue(
    persisted: Optional[Dict[str, Any]],
    recent_dialogue: Any,
) -> List[Dict[str, Any]]:
    body = _last_assistant_digest_text(recent_dialogue)
    if body:
        parsed = _parsed_digest_items_from_text(body)
        if len(parsed) >= 2:
            if isinstance(persisted, dict):
                stash_parsed_digest_from_assistant(persisted, body)
            return parsed
    ds = _dialogue_state(persisted)
    cached = ds.get("last_news_digest_items")
    if isinstance(cached, list) and cached:
        return [x for x in cached if isinstance(x, dict)]
    return []


def _pick_item_by_index(items: List[Dict[str, Any]], index: int) -> Optional[Dict[str, Any]]:
    if index < 1:
        return None
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        explicit = row.get("index")
        if explicit is not None:
            try:
                if int(explicit) == index:
                    return row
            except (TypeError, ValueError):
                pass
        if i + 1 == index:
            return row
    if 1 <= index <= len(items):
        row = items[index - 1]
        return row if isinstance(row, dict) else None
    return None


def _news_item_rss_resolve_enabled() -> bool:
    try:
        from core.brain_own_turn import news_digest_search_only_enabled

        if news_digest_search_only_enabled():
            return False
    except Exception:
        pass
    raw = (os.getenv("NEWS_ITEM_RSS_RESOLVE_ENABLED") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _digest_is_world_news(
    recent_dialogue: Any, persisted: Optional[Dict[str, Any]] = None
) -> bool:
    if isinstance(persisted, dict):
        meta = _dialogue_state(persisted).get("last_news_digest_meta") or {}
        if isinstance(meta, dict) and meta.get("world_feed"):
            return True
    body = _last_assistant_digest_text(recent_dialogue).lower()
    return "мировые новости" in body[:120] or "world news" in body[:120]


def _rss_lookup_query(title: str) -> str:
    words = re.findall(r"[\wёЁа-яА-Я]+", (title or "").strip())
    if len(words) > 12:
        return " ".join(words[:12])
    return (title or "").strip()[:140]


def _best_rss_match(
    title: str,
    publisher: str,
    items: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    from core.telegram_output_guard import _jaccard, _publisher_label, _split_google_news_title

    if not title or not items:
        return None
    want_pub = (publisher or "").strip().lower()
    best_row: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for row in items:
        if not isinstance(row, dict):
            continue
        raw_title = str(row.get("title") or "").strip()
        if not raw_title:
            continue
        headline, pub_from_title = _split_google_news_title(
            raw_title,
            str(row.get("source_name") or ""),
        )
        score = _jaccard(title, headline)
        pub = (_publisher_label(row) or pub_from_title or "").lower()
        if want_pub and pub and (want_pub in pub or pub in want_pub):
            score += 0.18
        if score > best_score:
            best_score = score
            best_row = row
    try:
        min_score = float((os.getenv("NEWS_ITEM_RSS_MATCH_MIN_SCORE") or "0.32").strip())
    except ValueError:
        min_score = 0.32
    if best_row and best_score >= min_score:
        return best_row
    return None


def _url_path_depth(url: str) -> int:
    u = (url or "").strip()
    if not u.startswith("http"):
        return 0
    path = re.sub(r"^https?://(?:www\.)?[^/]+", "", u).strip("/")
    if not path:
        return 0
    return len([p for p in path.split("/") if p])


def _is_google_news_url(url: str) -> bool:
    return "news.google." in (url or "").strip().lower()


def _url_looks_like_article(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    low = u.lower()
    if any(
        x in low
        for x in (
            "consent.google",
            "translate.google",
            "accounts.google",
            "support.google",
        )
    ):
        return False
    depth = _url_path_depth(u)
    if depth >= 2:
        return True
    path = re.sub(r"^https?://(?:www\.)?[^/]+", "", u).strip("/").lower()
    if not path:
        return False
    if path in {"news", "index.html", "index.php", "ru", "en"}:
        return False
    return len(path) > 18 or bool(re.search(r"\d{4}|\d{5,}", path))


def _urls_from_rss_row(row: Dict[str, Any]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    g = str(row.get("google_link") or row.get("link") or row.get("url") or "").strip()
    src = str(row.get("source") or row.get("source_url") or "").strip()
    for u in (g, src):
        if not u.startswith("http") or u in seen:
            continue
        if u == src and not _url_looks_like_article(u) and not _is_google_news_url(u):
            continue
        seen.add(u)
        out.append(u)
    return out


def _google_news_urls_from_item(item: Dict[str, Any]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for key in ("google_link", "link"):
        u = str(item.get(key) or "").strip()
        if u.startswith("http") and _is_google_news_url(u) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def _enrich_rss_items_for_direct(
    items: List[Dict[str, Any]],
    *,
    query: str,
    country: str,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Сниппеты из поиска к RSS (как в brain pipeline) — для пункта «3» без url_fetch."""
    if not items:
        return items
    raw = (os.getenv("NEWS_ENRICH_SEARCH_SNIPPETS") or "true").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return items
    try:
        from core.telegram_output_guard import enrich_news_items_with_snippets

        pack = await _search_pack(
            query or "новости",
            country=country,
            user_id=str(user_id or ""),
            timeout=18.0,
            tag="news_direct_enrich",
        )
        results = pack.get("results") if isinstance(pack, dict) else None
        if pack.get("ok") and isinstance(results, list) and results:
            return enrich_news_items_with_snippets(items, [r for r in results if isinstance(r, dict)])
    except Exception as e:
        logger.debug("news_direct enrich: %s", e)
    return items


def _news_enrich_per_item_enabled() -> bool:
    return _env_truthy("NEWS_ENRICH_PER_ITEM", default=True)


def _news_enrich_per_item_max() -> int:
    try:
        n = int((os.getenv("NEWS_ENRICH_PER_ITEM_MAX") or "4").strip())
    except ValueError:
        n = 4
    return max(0, min(n, 8))


def _news_enrich_per_item_timeout_sec() -> float:
    try:
        n = float((os.getenv("NEWS_ENRICH_PER_ITEM_TIMEOUT_SEC") or "10").strip())
    except ValueError:
        n = 10.0
    return max(5.0, min(n, 18.0))


def _snippet_usable_for_title(title: str, body: str, *, publisher: str = "") -> bool:
    b = (body or "").strip()
    if len(b) < 36 or not (title or "").strip():
        return False
    if _looks_like_homepage_chrome(b) or _looks_like_multi_headline_blob(b):
        return False
    return _title_match_score(title, b, publisher=publisher) >= 0.10


async def _snippet_from_aggregate(headline: str, pub: str, *, country: str) -> str:
    if not _env_truthy("NEWS_ENRICH_AGGREGATE_FALLBACK", default=True):
        return ""
    q = (headline or "").strip()
    if pub and pub.lower() not in q.lower():
        q = f"{q} {pub}"
    if not q:
        return ""
    try:
        from core.telegram_output_guard import _clip_words, _news_snippet_max_chars
        from modules.external_apis.service import ExternalAPIService

        pack = await with_retry(
            lambda: ExternalAPIService().lookup_or_fallback(q[:220], country=(country or "").strip()),
            retries=0,
            timeout_sec=12.0,
            tag="news_enrich_aggregate",
        )
        data = pack.get("data") if isinstance(pack, dict) else None
        if not isinstance(data, dict) or not data.get("configured"):
            return ""
        summary = str(data.get("summary") or "").strip()
        if _looks_like_multi_headline_blob(summary):
            summary = _pick_best_segment_for_title(summary, headline, publisher=pub) or ""
        if summary and _snippet_usable_for_title(headline, summary, publisher=pub):
            return _clip_words(summary, _news_snippet_max_chars())
    except Exception as e:
        logger.debug("news_enrich aggregate: %s", e)
    return ""


async def _enrich_one_rss_row(
    row: Dict[str, Any],
    *,
    country: str,
    user_id: str,
) -> Dict[str, Any]:
    from core.telegram_output_guard import enrich_news_items_with_snippets, _split_google_news_title

    out = dict(row)
    if str(out.get("snippet") or "").strip():
        return out
    raw_title = str(row.get("title") or "").strip()
    pub = str(row.get("publisher") or row.get("source_name") or "").strip()
    if not pub:
        _, pub_from = _split_google_news_title(raw_title, str(row.get("source_name") or ""))
        pub = pub_from
    headline = raw_title
    if " — " in raw_title or " - " in raw_title:
        headline, pub2 = _split_google_news_title(raw_title, pub)
        if pub2:
            pub = pub or pub2
    q = _build_news_search_query(headline or raw_title, pub)
    pack = await _search_pack(
        q,
        country=country,
        user_id=user_id,
        timeout=_news_enrich_per_item_timeout_sec(),
        tag="news_enrich_item",
    )
    results = pack.get("results") if isinstance(pack, dict) else None
    if pack.get("ok") and isinstance(results, list) and results:
        enriched = enrich_news_items_with_snippets([out], [r for r in results if isinstance(r, dict)])
        if enriched and isinstance(enriched[0], dict) and str(enriched[0].get("snippet") or "").strip():
            return enriched[0]
    if pack.get("ok"):
        composed = _compose_search_detail(pack, title=headline, publisher=pub)
        if composed and _snippet_usable_for_title(headline, composed, publisher=pub):
            from core.telegram_output_guard import _clip_words, _news_snippet_max_chars

            out["snippet"] = _clip_words(composed, _news_snippet_max_chars())
            return out
    agg = await _snippet_from_aggregate(headline, pub, country=country)
    if agg:
        out["snippet"] = agg
    return out


async def _enrich_rss_items_per_headline(
    items: List[Dict[str, Any]],
    *,
    country: str,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Точечный поиск по заголовку — сниппеты в stash для «1»/«2»/«3»."""
    if not items or not _news_enrich_per_item_enabled():
        return items
    cap = _news_enrich_per_item_max()
    if cap <= 0:
        return items
    head, tail = items[:cap], items[cap:]
    tasks = [
        _enrich_one_rss_row(row, country=country, user_id=str(user_id or ""))
        for row in head
        if isinstance(row, dict)
    ]
    if not tasks:
        return items
    done = await asyncio.gather(*tasks, return_exceptions=True)
    enriched_head: List[Dict[str, Any]] = []
    for i, res in enumerate(done):
        if isinstance(res, dict):
            enriched_head.append(res)
        elif isinstance(head[i], dict):
            enriched_head.append(head[i])
    return enriched_head + tail


def _title_keyword_overlap(title: str, body: str) -> float:
    from core.telegram_output_guard import _jaccard

    return _jaccard(title, body[:800])


def _looks_like_consent_wall(text: str) -> bool:
    low = (text or "").lower()
    if not low:
        return False
    if "consent.google" in low or "consent.youtube" in low:
        return True
    if "важная информация" in low and (
        "все языки" in low or "войти в аккаунт" in low or low.count("english") >= 2
    ):
        return True
    if "войти в аккаунт" in low and low.count("english") >= 2:
        return True
    lang_markers = (
        "deutsch",
        "español",
        "français",
        "italiano",
        "afrikaans",
        "português",
        "简体中文",
        "繁體中文",
        "日本語",
        "한국어",
    )
    lang_hits = sum(1 for m in lang_markers if m in low)
    if lang_hits >= 4 and (low.count("english") >= 3 or "русский" in low):
        return True
    if low.count("english") >= 4 and "deutsch" in low:
        return True
    return False


_PORTAL_NAV_MARKERS = (
    "утренний обзор",
    "новости компаний",
    "дивиденды",
    "криптоновости",
    "нейросети и ии",
    "новости международных рынков",
    "комментарии и",
)


def _looks_like_portal_nav_blob(text: str) -> bool:
    """Меню агрегатора (Финам и т.п.) вместо текста статьи."""
    low = (text or "").lower()
    if not low:
        return False
    hits = sum(1 for m in _PORTAL_NAV_MARKERS if m in low)
    if hits >= 2:
        return True
    if hits >= 1 and ("финам" in low or "finam" in low):
        return True
    return False


def _looks_like_homepage_chrome(text: str) -> bool:
    low = (text or "").lower()
    if not low:
        return False
    if _looks_like_consent_wall(low):
        return True
    if _looks_like_portal_nav_blob(low):
        return True
    chrome_markers = (
        "что будем искать",
        "прайс-лист",
        "медиакит",
        "условия использования",
        "редакция",
        "реклама",
    )
    hits = sum(1 for m in chrome_markers if m in low)
    if hits >= 2:
        return True
    if low.count("новости") >= 10 and hits >= 1:
        return True
    return False


def _short_headline_from_title(title: str, *, max_len: int = 100) -> str:
    t = (title or "").strip()
    if len(t) <= max_len:
        return t
    m = re.match(r"^(.+?[.!?…])(?:\s+|$)", t)
    if m and len(m.group(1)) >= 24:
        return m.group(1).strip()
    cut = t[:max_len].rstrip()
    if cut and cut[-1].isalnum():
        cut += "…"
    return cut


def _body_redundant_with_title(body: str, title: str) -> bool:
    b = (body or "").strip()
    t = (title or "").strip()
    if not b or not t:
        return False
    if b.lower().startswith(t.lower()[: min(len(t), 96)]):
        return True
    if len(t) >= 80 and _title_keyword_overlap(t, b) >= 0.52:
        return True
    return False


def _item_has_digest_paragraph(item: Dict[str, Any]) -> bool:
    """Пункт дайджеста из search: весь текст в title, без отдельного snippet."""
    title = str(item.get("title") or "").strip()
    sn = str(item.get("snippet") or "").strip()
    return len(title) >= 100 and len(sn) < 48


def _title_match_score(title: str, body: str, *, publisher: str = "") -> float:
    t = (title or "").strip()
    b = (body or "").strip()
    if not t or not b:
        return 0.0
    score = _title_keyword_overlap(t, b)
    pub = (publisher or "").strip().lower()
    if pub and pub in b.lower():
        score += 0.12
    words = [w for w in re.findall(r"[a-zA-Zа-яёЁ]{4,}", t.lower()) if len(w) >= 4]
    if words:
        hit = sum(1 for w in words[:8] if w in b.lower())
        score += min(0.35, hit * 0.06)
    return score


def _text_relevant_to_title(title: str, body: str, *, url: str = "") -> bool:
    t = (title or "").strip()
    b = (body or "").strip()
    if len(b) < 50 or not t:
        return False
    if _looks_like_homepage_chrome(b):
        return False
    if not _anchors_satisfied(t, b, url=url):
        return False
    if _title_match_score(t, b) >= 0.18:
        return True
    words = [w for w in re.findall(r"[a-zA-Zа-яёЁ]{4,}", t.lower()) if len(w) >= 4]
    if not words:
        return False
    hit = sum(1 for w in words[:8] if w in b.lower())
    return hit >= max(2, min(4, len(words) // 2))


def _looks_like_multi_headline_blob(text: str) -> bool:
    """DDG/агрегатор склеил несколько заголовков дайджеста в одну строку."""
    t = (text or "").strip()
    if len(t) < 100:
        return False
    if t.count(";") >= 2:
        return True
    if t.count(" - ") >= 3:
        return True
    if t.count("·") >= 3:
        return True
    if len(t) > 280 and t.count(" - ") >= 2:
        return True
    return False


def _segments_from_search_blob(text: str) -> List[str]:
    parts = re.split(r"[;\n]+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 24]


def _pick_best_segment_for_title(text: str, title: str, *, publisher: str = "") -> str:
    """Один фрагмент из склеенной выдачи — с max overlap с заголовком пункта."""
    try:
        min_score = float((os.getenv("NEWS_ITEM_SEGMENT_MIN_SCORE") or "0.14").strip())
    except ValueError:
        min_score = 0.14
    segments = _segments_from_search_blob(text)
    if not segments:
        return text.strip() if _text_relevant_to_title(title, text) else ""
    best = ""
    best_score = 0.0
    for seg in segments:
        score = _title_match_score(title, seg, publisher=publisher)
        if score > best_score:
            best_score = score
            best = seg
    if best_score >= min_score:
        return best
    return ""


def _sanitize_item_detail(
    detail: str,
    title: str,
    *,
    publisher: str = "",
    trust_source: bool = False,
) -> str:
    """На выходе: никогда не отдавать склеенную ленту вместо одного пункта."""
    from core.telegram_output_guard import _clip_words

    d = (detail or "").strip()
    if not d:
        return d
    if _looks_like_consent_wall(d) or _looks_like_homepage_chrome(d):
        return ""
    if trust_source:
        return _clip_words(d, _news_item_detail_max_chars())
    if _looks_like_multi_headline_blob(d):
        picked = _pick_best_segment_for_title(d, title, publisher=publisher)
        d = picked or ""
    if d and title and not _text_relevant_to_title(title, d):
        if _title_match_score(title, d, publisher=publisher) < 0.14:
            return ""
    return _clip_words(d, _news_item_detail_max_chars()) if d else ""


async def _resolve_rss_row_for_item(
    item: Dict[str, Any],
    *,
    country: str,
    world_feed: bool,
) -> Optional[Dict[str, Any]]:
    if not _news_item_rss_resolve_enabled():
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    pub = str(item.get("publisher") or "").strip()
    query = _rss_lookup_query(title)
    try:
        from modules.external_apis.clients import NewsAPIClient

        client = NewsAPIClient()
        topic = query or title[:100]
        if world_feed:
            rss = await with_retry(
                lambda: client.headlines(topic=topic, country=country),
                retries=0,
                timeout_sec=14.0,
                tag="news_item_rss_resolve",
            )
        else:
            rss = await with_retry(
                lambda: client.headlines(topic=topic, country=country),
                retries=0,
                timeout_sec=14.0,
                tag="news_item_rss_resolve",
            )
        if not rss.get("configured"):
            return None
        items = [r for r in (rss.get("items") or []) if isinstance(r, dict)]
        return _best_rss_match(title, pub, items)
    except Exception as e:
        logger.debug("news_item rss resolve: %s", e)
        return None


def _compose_search_detail(
    pack: Dict[str, Any],
    *,
    title: str = "",
    publisher: str = "",
) -> str:
    """Один абзац по выбранному пункту — не вся склеенная лента из search.summary."""
    if not isinstance(pack, dict) or not pack.get("ok"):
        return ""
    from core.telegram_output_guard import _clip_words

    cap = _news_item_detail_max_chars()
    best = ""
    best_score = 0.0
    results = pack.get("results")
    if isinstance(results, list):
        for row in results[:8]:
            if not isinstance(row, dict):
                continue
            row_title = str(row.get("title") or "").strip()
            sn = str(row.get("snippet") or row.get("content") or "").strip()
            blob = f"{row_title}. {sn}".strip() if row_title and sn else (sn or row_title)
            if not blob:
                continue
            if _looks_like_multi_headline_blob(blob):
                blob = _pick_best_segment_for_title(blob, title, publisher=publisher) or ""
            if not blob or not _text_relevant_to_title(title, blob):
                continue
            score = _title_match_score(title, blob, publisher=publisher)
            if score > best_score:
                best_score = score
                best = blob
    summary = str(pack.get("summary") or "").strip()
    if summary:
        candidate = summary
        if _looks_like_multi_headline_blob(summary):
            candidate = _pick_best_segment_for_title(summary, title, publisher=publisher)
        elif title and not _text_relevant_to_title(title, summary):
            candidate = ""
        if candidate:
            score = _title_match_score(title, candidate, publisher=publisher)
            if score > best_score:
                best = candidate
    if not best or _looks_like_portal_nav_blob(best):
        return ""
    return _clip_words(best, cap)


def _article_urls_from_search(pack: Dict[str, Any], *, title: str) -> List[str]:
    if not isinstance(pack, dict):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for row in pack.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        if not _url_looks_like_article(url):
            continue
        sn = str(row.get("snippet") or row.get("content") or "").strip()
        if title and sn and not _text_relevant_to_title(title, sn, url=url):
            continue
        if title and not _anchors_satisfied(title, sn, url=url):
            continue
        seen.add(url)
        out.append(url)
    return out


def _page_text_usable(text: str, title: str, *, url: str = "") -> bool:
    body = (text or "").strip()
    if len(body) < 60:
        return False
    if _looks_like_consent_wall(body) or _looks_like_homepage_chrome(body):
        return False
    if body.count("English") >= 4 and body.count("Deutsch") >= 2:
        return False
    return _text_relevant_to_title(title, body, url=url)


def _news_digest_search_timeout_sec(*, world_feed: bool) -> float:
    key = (
        "NEWS_DIGEST_WORLD_SEARCH_TIMEOUT_SEC"
        if world_feed
        else "NEWS_DIGEST_SEARCH_TIMEOUT_SEC"
    )
    default = "22.0" if world_feed else "18.0"
    try:
        t = float((os.getenv(key) or os.getenv("NEWS_DIGEST_SEARCH_TIMEOUT_SEC") or default).strip())
    except ValueError:
        t = 22.0 if world_feed else 18.0
    return max(12.0, min(t, 35.0))


def _news_digest_narrative_outer_margin_sec() -> float:
    try:
        m = float((os.getenv("NEWS_DIGEST_NARRATIVE_OUTER_MARGIN_SEC") or "6").strip())
    except ValueError:
        m = 6.0
    return max(4.0, min(m, 20.0))


def _news_digest_narrative_timeouts(*, expanded: bool, narr_style: str) -> tuple[float, float]:
    """(llm base_timeout, with_retry outer) — outer всегда > base."""
    if narr_style == "world_brief":
        try:
            outer = float((os.getenv("NEWS_DIGEST_NARRATIVE_WORLD_TIMEOUT_SEC") or "52").strip())
            base = float((os.getenv("NEWS_DIGEST_NARRATIVE_WORLD_BASE_TIMEOUT_SEC") or "38").strip())
        except ValueError:
            outer, base = 52.0, 38.0
        outer_cap = 75.0
    elif expanded:
        outer, base = 40.0, 28.0
        outer_cap = 50.0
    else:
        outer, base = 36.0, 24.0
        outer_cap = 50.0
    outer = max(24.0, min(outer, outer_cap))
    base = max(18.0, min(base, outer - 4.0))
    return base, outer


def _resolve_narrative_llm_timeouts(
    *,
    expanded: bool,
    narr_style: str,
    prompt: str,
    max_tokens: int,
) -> tuple[float, float]:
    """(base, outer) — outer не ниже adaptive free timeout tiered + margin."""
    base, outer = _news_digest_narrative_timeouts(expanded=expanded, narr_style=narr_style)
    try:
        from core.llm_tiered import estimate_tiered_timeouts

        est = estimate_tiered_timeouts(
            tag="news_digest_llm_narrative",
            prompt=prompt,
            max_tokens=max_tokens,
            base_timeout=base,
            task_tier="fast",
        )
        t_free = float(est.get("free_timeout_sec") or base)
    except Exception:
        t_free = base
    margin = _news_digest_narrative_outer_margin_sec()
    outer = max(outer, t_free + margin)
    cap = 75.0 if narr_style == "world_brief" else 50.0
    outer = min(outer, max(cap, t_free + margin))
    return base, outer


async def _search_pack(
    query: str,
    *,
    country: str,
    user_id: str,
    timeout: float,
    tag: str,
    searx_only: bool = False,
    record_errors: bool = True,
) -> Dict[str, Any]:
    """UniversalSearch; для digest refine — только SearX (без DDG fallback → меньше timeout-шума)."""
    q = (query or "").strip()
    if not q:
        return {"ok": False}

    async def _ddg_pack() -> Dict[str, Any]:
        from modules.external_apis.clients import GenericSearchClient

        sr = await with_retry(
            lambda: GenericSearchClient().search(q),
            retries=0,
            timeout_sec=min(timeout, 22.0),
            tag=f"{tag}_ddg",
            record_errors=record_errors,
        )
        if isinstance(sr, dict) and sr.get("configured"):
            return {
                "ok": True,
                "summary": str(sr.get("summary") or "").strip(),
                "results": [r for r in (sr.get("results") or []) if isinstance(r, dict)],
            }
        return {"ok": False}

    async def _universal_pack() -> Dict[str, Any]:
        from core.universal_search_module import UniversalSearchModule

        searx_cats = "general" if (tag or "").startswith("news") else ""
        pack = await with_retry(
            lambda: UniversalSearchModule().search(
                q,
                country=country,
                user_id=user_id,
                searx_categories=searx_cats,
            ),
            retries=0,
            timeout_sec=timeout,
            tag=tag,
            record_errors=record_errors,
        )
        if isinstance(pack, dict) and pack.get("ok"):
            return pack
        return {"ok": False}

    def _pack_usable(pack: Dict[str, Any]) -> bool:
        if not isinstance(pack, dict) or not pack.get("ok"):
            return False
        res = pack.get("results")
        if isinstance(res, list) and res:
            return True
        # Summary-only DDG instant answer — SEO-мусор для news_*; нужны structured results.
        if (tag or "").startswith("news"):
            return False
        return len(str(pack.get("summary") or "").strip()) >= 80

    ddg_first = (
        not searx_only
        and not (tag or "").startswith("news")
        and _env_truthy("NEWS_SEARCH_DDG_FIRST", default=True)
    )
    try:
        if searx_only:
            got = await _universal_pack()
            return got if isinstance(got, dict) else {"ok": False}
        if ddg_first:
            got = await _ddg_pack()
            if _pack_usable(got):
                return got
            alt = await _universal_pack()
            if _pack_usable(alt):
                return alt
            return got if isinstance(got, dict) and got.get("ok") else alt
        got = await _universal_pack()
        if _pack_usable(got):
            return got
        got2 = await _ddg_pack()
        if _pack_usable(got2):
            return got2
        return got if isinstance(got, dict) and got.get("ok") else got2
    except Exception as e:
        logger.debug("%s search_pack: %s", tag, e)
        return {"ok": False}


async def _fetch_page_article(
    url: str,
    *,
    user_id: str,
    title: str,
    timeout: float,
) -> Dict[str, Any]:
    """Текст страницы + og:image (для пункта новостей)."""
    empty: Dict[str, Any] = {"text": "", "images": [], "url": ""}
    if not url.startswith("http"):
        return empty
    if _is_google_news_url(url):
        return empty
    low_url = url.lower()
    if "consent.google" in low_url or "translate.google" in low_url:
        return empty
    if not _url_looks_like_article(url):
        return empty
    try:
        from core.url_fetch import UrlFetchModule

        pack = await with_retry(
            lambda: UrlFetchModule().fetch_page(
                url,
                user_id=str(user_id or ""),
                include_images=True,
            ),
            retries=0,
            timeout_sec=timeout,
            tag="news_item_url_fetch",
        )
        if isinstance(pack, dict) and pack.get("ok"):
            body = str(pack.get("text") or pack.get("content") or "").strip()
            page_url = str(pack.get("url") or url).strip()
            try:
                http_status = int(pack.get("http_status") or 200)
            except (TypeError, ValueError):
                http_status = 200
            content_type = str(pack.get("content_type") or "")
            try:
                from core.news_validator import NewsValidator

                val = NewsValidator().validate_fetch(
                    page_url,
                    html="",
                    text=body,
                    http_status=http_status,
                    content_type=content_type,
                )
                if not val.valid:
                    logger.warning(
                        "news_item fetch invalid %s: %s",
                        page_url[:80],
                        val.reason,
                    )
                    return empty
                parsing_confidence = val.confidence
            except Exception as exc:
                logger.debug("news_item fetch validate: %s", exc)
                parsing_confidence = 0.0
            if _page_text_usable(body, title, url=page_url):
                imgs = [u for u in (pack.get("images") or []) if isinstance(u, str) and u.startswith("http")]
                return {
                    "text": body,
                    "images": imgs[: _news_item_max_images()],
                    "url": page_url,
                    "parsing_confidence": parsing_confidence,
                }
    except Exception as e:
        logger.debug("news_item url_fetch %s: %s", url[:60], e)
    return empty


async def _fetch_page_text(
    url: str,
    *,
    user_id: str,
    title: str,
    timeout: float,
) -> str:
    got = await _fetch_page_article(
        url, user_id=user_id, title=title, timeout=timeout
    )
    return str(got.get("text") or "")


async def _fetch_detail_via_search(
    title: str,
    pub: str,
    *,
    user_id: str,
    country: str,
    timeout: float,
) -> Dict[str, Any]:
    """Поиск → url_fetch статей; лучший по релевантности заголовку, не по длине."""
    empty: Dict[str, Any] = {"text": "", "images": [], "url": ""}
    q = _build_news_search_query(title, pub)
    pack = await _search_pack(
        q,
        country=country,
        user_id=user_id,
        timeout=min(timeout + 10.0, 28.0),
        tag="news_item_search",
    )
    if not pack.get("ok"):
        return empty
    best: Dict[str, Any] = dict(empty)
    best_score = 0.0
    try:
        min_score = float((os.getenv("NEWS_ITEM_FETCH_MIN_SCORE") or "0.20").strip())
    except ValueError:
        min_score = 0.20
    for url in _article_urls_from_search(pack, title=title)[:6]:
        got = await _fetch_page_article(
            url, user_id=user_id, title=title, timeout=timeout
        )
        body = str(got.get("text") or "")
        page_url = str(got.get("url") or url)
        if not body:
            continue
        score = _title_match_score(title, body, publisher=pub)
        if not _anchors_satisfied(title, body, url=page_url):
            continue
        if score < min_score:
            continue
        if score > best_score or (
            score >= min_score and len(body) > len(str(best.get("text") or ""))
        ):
            best_score = score
            best = {
                "text": body,
                "images": list(got.get("images") or []),
                "url": page_url,
            }
        if score >= 0.42 and len(body) >= 1200:
            break
    if len(str(best.get("text") or "")) >= 80:
        return best
    composed = _compose_search_detail(pack, title=title, publisher=pub)
    if composed:
        best["text"] = composed
    return best


async def _llm_expand_news_item(
    title: str,
    pub: str,
    *,
    user_id: str,
    expanded: bool = False,
    source_material: str = "",
) -> str:
    """Пересказ пункта: по тексту статьи или заголовку (дешёвая модель)."""
    if not _env_truthy("NEWS_ITEM_LLM_FALLBACK_ENABLED", default=True):
        return ""
    t = (title or "").strip()
    if len(t) < 12:
        return ""
    model = _news_llm_model()
    if not model:
        return ""
    try:
        cap = int((os.getenv("NEWS_ITEM_LLM_MAX_TOKENS") or "220").strip())
    except ValueError:
        cap = 220
    cap = max(80, min(cap, 400))
    if expanded:
        try:
            cap = int((os.getenv("NEWS_ITEM_EXPAND_LLM_MAX_TOKENS") or "420").strip())
        except ValueError:
            cap = 420
        cap = max(200, min(cap, 600))
    sys_p = (
        "Ты редактор в Telegram. Пользователь просит новость подробнее. "
        "Напиши на русском "
        + ("5–7 предложений с деталями из материала ниже." if expanded else "3–4 предложения.")
        + " Только факты из заголовка и материала; не выдумывай. Без списков и URL."
    )
    src = (source_material or "").strip()
    if src:
        from core.telegram_output_guard import _clip_words

        src = _clip_words(src, 2800 if expanded else 1600)
        user_p = f"Заголовок: {t}\nИздание: {pub or 'не указано'}\n\nМатериал:\n{src}"
    else:
        user_p = f"Заголовок: {t}\nИздание: {pub or 'не указано'}"
    try:
        from core.llm_tiered import llm_generate_tiered
        from core.openrouter_provider import get_openrouter_provider

        pack = await with_retry(
            lambda: llm_generate_tiered(
                get_openrouter_provider(),
                tag="news_item_llm_expand",
                prompt=user_p,
                system_prompt=sys_p,
                model=model,
                max_tokens=cap,
                temperature=0.35,
                base_timeout=14.0,
                task_tier="fast",
            ),
            retries=0,
            timeout_sec=18.0,
            tag="news_item_llm_expand",
        )
        body = str(pack.get("content") or pack.get("text") or "").strip()
        if len(body) >= 40 and not _looks_like_homepage_chrome(body):
            return body
    except Exception as e:
        logger.debug("news_item llm expand: %s", e)
    return ""


def _headline_only_fallback(title: str, pub: str) -> str:
    """Последний запас без LLM — честно по заголовку ленты."""
    t = (title or "").strip()
    if len(t) < 8:
        return ""
    pub_s = (pub or "").strip()
    tail = f" Источник: {pub_s}." if pub_s and pub_s.lower() not in t.lower() else ""
    return (
        f"По заголовку из новостной ленты: {t}.{tail} "
        "Полный текст статьи сейчас не открылся (сайт или поиск); "
        "для развёрнутого обзора всех пунктов напишите «развёрнуто»."
    )


async def _refetch_display_item_at_index(
    index: int,
    *,
    country: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    ds = _dialogue_state(persisted)
    meta = ds.get("last_news_digest_meta")
    if not isinstance(meta, dict):
        meta = {}
    query = str(meta.get("query") or "").strip()
    if not query:
        query = "международные новости" if _digest_is_world_news(recent_dialogue, persisted) else "новости"
    co = str(meta.get("country") or country or "").strip()
    try:
        from modules.external_apis.clients import NewsAPIClient
        from core.telegram_output_guard import collect_news_display_items

        client = NewsAPIClient()
        rss = await with_retry(
            lambda: client.headlines(topic=query, country=co),
            retries=0,
            timeout_sec=14.0,
            tag="news_item_refetch_digest",
        )
        if not rss.get("configured"):
            return None
        raw = [r for r in (rss.get("items") or []) if isinstance(r, dict)]
        displayed = collect_news_display_items(raw, user_query=query)
        return _pick_item_by_index(displayed, index)
    except Exception as e:
        logger.debug("news_item refetch digest: %s", e)
        return None


async def _resolve_pick_item(
    item: Dict[str, Any],
    pick: int,
    *,
    country: str,
    recent_dialogue: Any,
    persisted: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    link = str(item.get("link") or "").strip()
    if link.startswith("http"):
        return item
    title = str(item.get("title") or "").strip()
    meta = _dialogue_state(persisted).get("last_news_digest_meta")
    from_assistant = (
        isinstance(meta, dict) and str(meta.get("source") or "") == "assistant_parse"
    )
    # Не подменять пункт из текста чата чужим RSS-слотом (типичная причина «п.4 → лента ООН»).
    if isinstance(meta, dict) and (from_assistant or meta.get("assistant_titles")) and (
        len(title) >= 24 or _item_has_stashed_link(item)
    ):
        return item
    ref = await _refetch_display_item_at_index(
        pick,
        country=country,
        recent_dialogue=recent_dialogue,
        persisted=persisted,
    )
    if isinstance(ref, dict) and str(ref.get("link") or "").startswith("http"):
        return ref
    return item


def _format_item_detail_reply(
    index: int,
    item: Dict[str, Any],
    article: Dict[str, Any],
) -> str:
    from core.telegram_output_guard import _clip_words

    title = str(item.get("title") or "").strip()
    headline = _short_headline_from_title(title) if len(title) > 110 else title
    pub = str(item.get("publisher") or "").strip()
    sn0 = str(item.get("snippet") or "").strip()
    detail = str(article.get("text") or "").strip()
    source_url = str(article.get("url") or "").strip()
    body = (detail or sn0 or "").strip()
    if body and _looks_like_portal_nav_blob(body):
        picked = _pick_best_segment_for_title(body, headline, publisher=pub)
        body = picked or _sanitize_item_detail(title, headline, publisher=pub, trust_source=True)
    if body and _body_redundant_with_title(body, title):
        cap = _news_item_detail_max_chars()
        clipped = _clip_words(body, cap)
        if clipped and not _body_redundant_with_title(clipped, title):
            out_lines = [f"Пункт {index}."]
            if pub and pub.lower() not in clipped.lower():
                out_lines.append(f"· {pub}")
            if source_url.startswith("http"):
                out_lines.append(f"Источник: {source_url}")
            out_lines.extend(["", clipped])
            if article.get("truncated"):
                out_lines.extend(["", "(текст обрезан по лимиту; полная версия — по ссылке источника)"])
            return "\n".join(out_lines).strip()
    lines = [f"{index}. {headline}"]
    if pub and pub.lower() not in headline.lower():
        lines.append(f"· {pub}")
    if source_url.startswith("http"):
        lines.append(f"Источник: {source_url}")
    if body:
        cap = _news_item_detail_max_chars()
        clipped = _clip_words(body, cap)
        if clipped and not (
            len(headline) > 40 and clipped.lower().startswith(headline.lower()[: min(len(headline), 72)])
        ):
            lines.append("")
            lines.append(clipped)
        elif clipped and len(clipped) > len(headline) + 40:
            extra = clipped
            if extra.lower().startswith(headline.lower()):
                extra = extra[len(headline) :].lstrip(" .—-")
            if extra:
                lines.append("")
                lines.append(extra)
        if article.get("truncated"):
            lines.append("")
            lines.append("(текст обрезан по лимиту; полная версия — по ссылке источника)")
    elif not sn0 and not body:
        ho = _headline_only_fallback(title, pub)
        if ho:
            lines.append("")
            lines.append(ho)
        else:
            lines.append("")
            lines.append(
                "Не удалось открыть статью (сайт или поиск). "
                "Повторите через минуту или откройте издание вручную."
            )
    return "\n".join(lines).strip()


def _merge_article_fetch(
    current: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    title: str,
    publisher: str = "",
) -> Dict[str, Any]:
    """Оставить более длинный релевантный текст и объединить картинки."""
    out = {
        "text": str(current.get("text") or ""),
        "images": [u for u in (current.get("images") or []) if isinstance(u, str)],
        "url": str(current.get("url") or ""),
        "truncated": bool(current.get("truncated")),
    }
    cand_text = str(candidate.get("text") or "").strip()
    if cand_text:
        cleaned = _sanitize_item_detail(cand_text, title, publisher=publisher)
        if cleaned and len(cleaned) > len(out["text"]):
            out["text"] = cleaned
            out["url"] = str(candidate.get("url") or out["url"])
            out["truncated"] = "[truncated]" in cand_text.lower()
    seen = set(out["images"])
    for u in candidate.get("images") or []:
        if isinstance(u, str) and u.startswith("http") and u not in seen:
            seen.add(u)
            out["images"].append(u)
    out["images"] = out["images"][: _news_item_max_images()]
    return out


async def _fetch_news_item_article(
    item: Dict[str, Any],
    *,
    user_id: str,
    country: str,
    recent_dialogue: Any = None,
    expanded: bool = False,
    persisted: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Полный текст статьи: поиск → url_fetch; картинки с страницы."""
    title = str(item.get("title") or "").strip()
    pub = str(item.get("publisher") or "").strip()
    sn0 = str(item.get("snippet") or "").strip()
    timeout = _news_item_fetch_timeout_sec() * (1.5 if expanded else 1.0)
    world = _digest_is_world_news(recent_dialogue, persisted)
    article: Dict[str, Any] = {"text": "", "images": [], "url": "", "truncated": False}
    full_mode = _news_item_full_article_enabled()
    min_good = 400 if full_mode else 80
    has_stash_link = _item_has_stashed_link(item)
    headline = _short_headline_from_title(title) if len(title) > 110 else title

    if _item_has_digest_paragraph(item):
        base = _sanitize_item_detail(title, headline, publisher=pub, trust_source=True)
        if len(base) >= 80:
            article["text"] = base
            if _env_truthy("NEWS_ITEM_LLM_FALLBACK_ENABLED", default=True):
                llm_body = await _llm_expand_news_item(
                    headline,
                    pub,
                    user_id=user_id,
                    expanded=expanded,
                    source_material=title,
                )
                if llm_body:
                    expanded_txt = _sanitize_item_detail(
                        llm_body, headline, publisher=pub, trust_source=True
                    )
                    if expanded_txt and len(expanded_txt) >= len(base) - 20:
                        article["text"] = expanded_txt
            for u in (
                str(item.get("link") or "").strip(),
                str(item.get("source_url") or "").strip(),
            ):
                if u.startswith("http"):
                    article["url"] = u
                    break
            return article

    urls: List[str] = []
    for u in (
        str(item.get("link") or "").strip(),
        str(item.get("source_url") or "").strip(),
    ):
        if u.startswith("http") and u not in urls and _url_looks_like_article(u):
            urls.append(u)
    for u in _google_news_urls_from_item(item):
        if u not in urls:
            urls.insert(0, u)

    if (
        not expanded
        and sn0
        and len(sn0) >= 48
        and _text_relevant_to_title(title, sn0)
        and not _is_seo_kakie_listicle_title(title)
    ):
        article["text"] = _sanitize_item_detail(sn0, title, publisher=pub)
        if len(article["text"]) >= 120:
            return article

    if _news_item_rss_resolve_enabled() and not has_stash_link and not urls:
        row = await _resolve_rss_row_for_item(item, country=country, world_feed=world)
        if isinstance(row, dict):
            stash_row = {
                "google_link": str(row.get("link") or ""),
                "link": str(row.get("link") or ""),
                "source": str(row.get("source") or ""),
            }
            for u in _google_news_urls_from_item(stash_row):
                if u not in urls:
                    urls.insert(0, u)
            for u in _urls_from_rss_row(row):
                if u not in urls:
                    urls.append(u)

    for url in urls[:6]:
        if len(str(article.get("text") or "")) >= 3500:
            break
        got = await _fetch_page_article(
            url, user_id=user_id, title=title, timeout=timeout
        )
        article = _merge_article_fetch(article, got, title=title, publisher=pub)

    if full_mode and len(str(article.get("text") or "")) < min_good:
        article = _merge_article_fetch(
            article,
            await _fetch_detail_via_search(
                title, pub, user_id=user_id, country=country, timeout=timeout
            ),
            title=title,
            publisher=pub,
        )
    elif not full_mode:
        article = _merge_article_fetch(
            article,
            await _fetch_detail_via_search(
                title, pub, user_id=user_id, country=country, timeout=timeout
            ),
            title=title,
            publisher=pub,
        )

    detail = str(article.get("text") or "")
    if len(detail) < 80:
        agg = await _lookup_aggregate_detail(title, country=country) or ""
        if agg and _text_relevant_to_title(title, agg):
            article["text"] = _sanitize_item_detail(agg, title, publisher=pub)

    detail = str(article.get("text") or "")
    if (
        len(detail) < 60
        and sn0
        and len(sn0) >= 48
        and _title_keyword_overlap(title, sn0) >= 0.08
    ):
        cleaned_sn = _sanitize_item_detail(sn0, title, publisher=pub)
        if cleaned_sn:
            article["text"] = cleaned_sn

    detail = str(article.get("text") or "")
    skip_llm = full_mode and len(detail) >= min_good
    try:
        from core.telegram_output_guard import _is_seo_kakie_listicle_title
    except Exception:
        _is_seo_kakie_listicle_title = lambda _t: False  # type: ignore
    force_llm = _is_seo_kakie_listicle_title(title) and len(detail) < 200
    if force_llm or (not skip_llm and (len(detail) < 80 or (expanded and len(detail) < 200))):
        llm_body = await _llm_expand_news_item(
            title,
            pub,
            user_id=user_id,
            expanded=expanded,
            source_material=detail or sn0,
        )
        if llm_body:
            article["text"] = _sanitize_item_detail(
                llm_body, title, publisher=pub, trust_source=True
            )
    if not str(article.get("text") or "").strip():
        ho = _headline_only_fallback(title, pub)
        if ho:
            article["text"] = _sanitize_item_detail(ho, title, publisher=pub, trust_source=True)

    cap = _news_item_detail_max_chars()
    body = str(article.get("text") or "")
    if len(body) > cap:
        from core.telegram_output_guard import _clip_words

        article["text"] = _clip_words(body, cap)
        article["truncated"] = True
    return article


async def _fetch_news_item_detail(
    item: Dict[str, Any],
    *,
    user_id: str,
    country: str,
    recent_dialogue: Any = None,
    expanded: bool = False,
    persisted: Optional[Dict[str, Any]] = None,
) -> str:
    """Обратная совместимость — только текст."""
    pack = await _fetch_news_item_article(
        item,
        user_id=user_id,
        country=country,
        recent_dialogue=recent_dialogue,
        expanded=expanded,
        persisted=persisted,
    )
    return str(pack.get("text") or "")


async def _lookup_aggregate_detail(title: str, *, country: str) -> str:
    """DDG/Wikipedia/агрегатор — когда url_fetch и UniversalSearch пусты (VPS без Tavily)."""
    q = _rss_lookup_query(title) or title
    if not q:
        return ""
    try:
        from modules.external_apis.service import ExternalAPIService

        pack = await with_retry(
            lambda: ExternalAPIService().lookup_or_fallback(q, country=(country or "").strip()),
            retries=0,
            timeout_sec=16.0,
            tag="news_item_aggregate",
        )
        if not isinstance(pack, dict):
            return ""
        data = pack.get("data")
        if not isinstance(data, dict) or not data.get("configured"):
            return ""
        body = str(data.get("summary") or "").strip()
        if len(body) >= 40 and _text_relevant_to_title(title, body):
            return body
    except Exception as e:
        logger.debug("news_item aggregate lookup: %s", e)
    return ""


def _backfill_stash_snippet(
    persisted: Optional[Dict[str, Any]],
    pick: int,
    item: Dict[str, Any],
    detail: str,
) -> None:
    """После удачного ответа на «N» — сохранить выдержку в stash для повторного «N»."""
    if not _env_truthy("NEWS_ITEM_BACKFILL_STASH_SNIPPET", default=True):
        return
    if not isinstance(persisted, dict) or pick < 1:
        return
    title = str(item.get("title") or "").strip()
    pub = str(item.get("publisher") or "").strip()
    body = (detail or "").strip()
    if len(body) < 60 or not title:
        return
    if _looks_like_multi_headline_blob(body):
        body = _pick_best_segment_for_title(body, title, publisher=pub) or body
    if not _snippet_usable_for_title(title, body, publisher=pub):
        return
    try:
        from core.telegram_output_guard import _clip_words, _news_snippet_max_chars

        sn = _clip_words(body, _news_snippet_max_chars())
    except Exception:
        sn = body[:520]
    ds = _dialogue_state(persisted)
    items = ds.get("last_news_digest_items")
    if not isinstance(items, list):
        return
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get("index") or 0) == pick:
                row["snippet"] = sn
                return
        except (TypeError, ValueError):
            continue


def _is_news_headlines_request(
    user_text: str,
    facts: Dict[str, Any],
    recent_dialogue: Any,
) -> bool:
    if looks_like_pasted_news_article(user_text):
        return False
    prof = task_fact_profile(user_text, facts, recent_dialogue)
    if prof.get("is_pasted_article"):
        return False
    if prof.get("is_news") or looks_like_news_headlines_request(user_text):
        return True
    return False


def _extract_story_search_query(user_text: str) -> str:
    t = (user_text or "").strip()
    patterns = (
        r"(?i)^(?:расскаж\w*|подробн\w*|разверн\w*|развёрн\w*|раскрой|разбери|узнай)\s+(?:про|о|об)\s+(.+)$",
        r"(?i)^(?:что\s+извест|что\s+случил)\s+(?:про|о|об)\s+(.+)$",
        r"(?i)^(?:а\s+)?что\s+с\s+(.+)$",
        r"(?i)меня\s+интересует\s+(.+)$",
        r"(?i)хочу\s+узнать\s+(?:про|о|об)\s+(.+)$",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            return m.group(1).strip(" ?.!")
    return t


def _match_digest_item_by_user_query(
    user_text: str,
    items: List[Dict[str, Any]],
    digest_body: str,
) -> Optional[Dict[str, Any]]:
    """Сопоставить фразу «про беспилотник в Румынии» с пунктом дайджеста или абзацем."""
    from core.telegram_output_guard import _jaccard

    q = _extract_story_search_query(user_text)
    if len(q) < 8:
        return None
    candidates: List[tuple[str, Optional[Dict[str, Any]]]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if title:
            candidates.append((title, row))
        sn = str(row.get("snippet") or "").strip()
        if len(sn) >= 40:
            candidates.append((sn, row))
    for para in re.split(r"\n\s*\n", digest_body or ""):
        p = para.strip()
        if len(p) >= 40:
            candidates.append((p, None))
    for line in (digest_body or "").splitlines():
        ln = line.strip()
        if len(ln) >= 48:
            candidates.append((ln, None))
    best_row: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for text, row in candidates:
        score = _jaccard(q, text)
        if score > best_score:
            best_score = score
            if row is not None:
                best_row = dict(row)
            else:
                best_row = {"title": text[:280], "snippet": text, "publisher": ""}
    try:
        min_sc = float((os.getenv("NEWS_STORY_MATCH_MIN_SCORE") or "0.14").strip())
    except ValueError:
        min_sc = 0.14
    if best_row and best_score >= min_sc:
        return best_row
    return None


async def try_news_story_deep_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """После дайджеста: «расскажи про …» — полная статья/сводка, не заголовок."""
    if not news_story_deep_followup_enabled():
        return None
    text = (user_text or "").strip()
    if not text:
        return None
    try:
        from core.article_thread_followup import should_handle_article_thread_followup

        if should_handle_article_thread_followup(text, recent_dialogue, persisted):
            return None
    except Exception:
        pass
    try:
        from core.brain.text_helpers import looks_like_news_story_deep_followup

        if not looks_like_news_story_deep_followup(text, recent_dialogue):
            return None
    except Exception as e:
        logger.debug("news_story_deep gate import: %s", e)
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut_async

        _gr = await should_run_shortcut_async(
            "news_story_deep",
            text,
            persisted=persisted,
            planner_context={"recent_dialogue": recent_dialogue}
            if recent_dialogue
            else None,
        )
        if not _gr.allowed:
            return None
    except Exception as e:
        logger.debug("news_story_deep gate: %s", e)

    items = _digest_items_from_dialogue(persisted, recent_dialogue)
    digest_body = _last_assistant_digest_text(recent_dialogue)
    item = _match_digest_item_by_user_query(text, items, digest_body)
    q = _extract_story_search_query(text)
    if not item:
        item = {"title": q, "publisher": "", "snippet": ""}
    facts = _user_facts_from_persisted(persisted)
    country = _news_country_iso2(facts)
    article = await _fetch_news_item_article(
        item,
        user_id=str(user_id or ""),
        country=country,
        recent_dialogue=recent_dialogue,
        expanded=True,
        persisted=persisted,
    )
    body = _sanitize_item_detail(
        str(article.get("text") or ""),
        str(item.get("title") or q),
        publisher=str(item.get("publisher") or ""),
    )
    if len(body) < 160:
        return None
    try:
        from core.monitoring import MONITOR

        MONITOR.inc("news_story_deep_followup_total")
    except Exception:
        pass
    headline = _short_headline_from_title(str(item.get("title") or q))
    pub = str(item.get("publisher") or "").strip()
    parts = [headline]
    if pub and pub.lower() not in body.lower()[:240]:
        parts.append(f"· {pub}")
    parts.extend(["", body])
    url = str(article.get("url") or "").strip()
    if url.startswith("http") and url not in body:
        parts.append(f"\n({url})")
    try:
        cap = int((os.getenv("NEWS_ITEM_FULL_MAX_CHARS") or "12000").strip())
    except ValueError:
        cap = 12000
    return "\n".join(parts).strip()[: max(2000, min(cap, 14000))]


async def try_news_item_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """Развёрнутый пункт дайджеста по номеру («2») или «подробнее» — без полного brain."""
    if not news_item_pick_enabled():
        return None
    user_query = (user_text or "").strip()
    if not user_query:
        return None
    pick = resolve_news_item_pick_index(user_query, recent_dialogue, persisted)
    if pick is None:
        if wants_expanded_news_digest(user_query, recent_dialogue):
            return None
        return None
    expand_same = parse_news_item_pick_index(user_query, recent_dialogue, persisted) is None and bool(
        wants_expanded_news_digest(user_query, recent_dialogue)
    )
    try:
        from core.heuristic_context_gate import should_run_shortcut_async

        _gr = await should_run_shortcut_async(
            "news_item_pick",
            user_query,
            persisted=persisted,
            planner_context={"recent_dialogue": recent_dialogue}
            if recent_dialogue
            else None,
        )
        if not _gr.allowed:
            return None
    except Exception as e:
        logger.debug("news_item_pick gate: %s", e)

    items = _digest_items_from_dialogue(persisted, recent_dialogue)
    if not items:
        return (
            "Не вижу свежего списка новостей в диалоге. "
            "Сначала попросите «главные новости» или «что в новостях»."
        )
    item = _pick_item_by_index(items, pick)
    if not item:
        return f"В последнем дайджесте нет пункта {pick}. Напишите номер от 1 до {len(items)}."

    facts = _user_facts_from_persisted(persisted)
    country = _news_country_iso2(facts)
    item = await _resolve_pick_item(
        item,
        pick,
        country=country,
        recent_dialogue=recent_dialogue,
        persisted=persisted,
    )
    article = await _fetch_news_item_article(
        item,
        user_id=str(user_id or ""),
        country=country,
        recent_dialogue=recent_dialogue,
        expanded=expand_same,
        persisted=persisted,
    )
    _backfill_stash_snippet(persisted, pick, item, str(article.get("text") or ""))
    _set_last_news_picked_index(persisted, pick)
    text = _format_item_detail_reply(pick, item, article)
    if isinstance(persisted, dict):
        ds = _dialogue_state(persisted)
        if "dialogue_state" not in persisted:
            persisted["dialogue_state"] = ds
        ds["last_news_item_article"] = {
            "pick": pick,
            "image_urls": list(article.get("images") or []),
            "source_url": str(article.get("url") or ""),
        }
    src = _source_from_fetched_article(article, title=str(item.get("title") or ""))
    sources = [src] if src else []
    return await _return_news_with_telemetry(
        text,
        user_id=str(user_id or ""),
        query=user_query,
        sources=sources,
        recent_dialogue=recent_dialogue,
        llm_model=_news_llm_model(),
    )


async def try_news_item_reply_pack(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[Dict[str, Any]]:
    """Текст + URL картинок для orchestrator (полная статья)."""
    text = await try_news_item_reply(
        user_text,
        persisted=persisted,
        user_id=user_id,
        recent_dialogue=recent_dialogue,
    )
    if not text or not str(text).strip():
        return None
    images: List[str] = []
    source_url = ""
    if isinstance(persisted, dict):
        ds = _dialogue_state(persisted)
        stash = ds.get("last_news_item_article")
        if isinstance(stash, dict):
            images = [
                u
                for u in (stash.get("image_urls") or [])
                if isinstance(u, str) and u.startswith("http")
            ]
            source_url = str(stash.get("source_url") or "")
    return {
        "text": str(text).strip(),
        "image_urls": images[: _news_item_max_images()],
        "source_url": source_url,
    }


def news_item_outputs_from_pack(pack: Dict[str, Any]) -> List[Any]:
    """Список Output: фото (если есть) + текст статьи."""
    from core.models import Output

    text = str(pack.get("text") or "").strip()
    imgs = [
        u.strip()
        for u in (pack.get("image_urls") or [])
        if isinstance(u, str) and u.startswith("http")
    ][: _news_item_max_images()]
    outs: List[Any] = []
    title_line = (text.split("\n")[0] if text else "")[:900]
    for i, url in enumerate(imgs):
        cap = title_line if i == 0 and title_line else None
        meta: Dict[str, Any] = {
            "module": "__fallback__",
            "reason": "news_item_photo",
            "image_url": url,
        }
        if cap:
            meta["caption"] = cap
        outs.append(Output(type="image", payload=url, meta=meta))
    if text:
        outs.append(
            Output(
                type="text",
                payload=text,
                meta={"module": "__fallback__", "reason": "news_item_direct"},
            )
        )
    return outs


def try_news_item_reply_sync(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    pack = try_news_item_reply_pack_sync(
        user_text,
        persisted=persisted,
        user_id=user_id,
        recent_dialogue=recent_dialogue,
    )
    if not pack:
        return None
    return str(pack.get("text") or "").strip() or None


def try_news_item_reply_pack_sync(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[Dict[str, Any]]:
    import asyncio
    import concurrent.futures

    coro = try_news_item_reply_pack(
        user_text,
        persisted=persisted,
        user_id=user_id,
        recent_dialogue=recent_dialogue,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=55)


def _world_digest_thematic_queries() -> List[str]:
    """
    Узкие запросы по сферам (как план «мир / конфликты / экономика / космос / спорт» у агента с browse),
    без открытия десятков страниц — только SearX → заголовки и сниппеты.
    """
    return [
        "Iran US deal Hormuz negotiations news today",
        "Ukraine Russia strikes drones missiles news today",
        "UN climate clean energy Europe heat record today",
        "IMF World Bank energy supply Middle East economy",
        "Blue Origin New Glenn NASA space launch news",
        "UEFA Champions League final sports news today",
        "Trump international diplomacy news headlines today",
        "международные новости политика конфликт сегодня",
    ]


def _news_digest_max_search_queries(*, world_feed: bool = False) -> int:
    key = "NEWS_DIGEST_WORLD_MAX_SEARCH_QUERIES" if world_feed else "NEWS_DIGEST_MAX_SEARCH_QUERIES"
    default = "6" if world_feed else "5"
    cap = 8 if world_feed else 8
    try:
        n = int((os.getenv(key) or os.getenv("NEWS_DIGEST_MAX_SEARCH_QUERIES") or default).strip())
    except ValueError:
        n = 8 if world_feed else 5
    return max(2, min(n, cap))


def _world_digest_page_enrich_enabled() -> bool:
    """Как «просмотр N страниц» у browse-агента — 2–3 статьи в сниппет перед LLM."""
    return _env_truthy("NEWS_DIGEST_WORLD_PAGE_ENRICH", default=True)


def _world_digest_page_enrich_max() -> int:
    try:
        n = int((os.getenv("NEWS_DIGEST_WORLD_PAGE_ENRICH_MAX") or "2").strip())
    except ValueError:
        n = 3
    return max(1, min(n, 5))


def _world_digest_page_enrich_timeout_sec() -> float:
    try:
        t = float((os.getenv("NEWS_DIGEST_WORLD_PAGE_ENRICH_TIMEOUT_SEC") or "8").strip())
    except ValueError:
        t = 12.0
    return max(6.0, min(t, 25.0))


def _gather_early_stop_enabled(*, world_feed: bool) -> bool:
    """Мировой дайджест: не держать 6–8 SearX подряд, если уже есть статьи."""
    if not world_feed:
        return False
    return _env_truthy("NEWS_DIGEST_GATHER_EARLY_STOP", default=True)


async def _gather_digest_search_rows(
    queries: List[str],
    *,
    country: str,
    user_id: str,
    world_feed: bool,
    user_query: str = "",
) -> List[Dict[str, Any]]:
    """Собрать SearX по запросам последовательно (как browse-агент по шагам, без лавины timeout)."""
    if not queries:
        return []
    tag = "news_digest_refine_search"
    timeout = _news_digest_search_timeout_sec(world_feed=world_feed)
    quiet_errors = world_feed or _env_truthy("NEWS_DIGEST_SEARCH_QUIET_ERRORS", default=False)
    early_stop = _gather_early_stop_enabled(world_feed=world_feed)
    uq = (user_query or "").strip() or ("новости в мире" if world_feed else "новости")
    filter_co = _news_digest_filter_country(uq, country)
    require_article = _digest_collect_require_article_url()

    all_raw: List[Dict[str, Any]] = []
    for idx, q in enumerate(queries):
        try:
            pack = await _search_pack(
                q,
                country=country,
                user_id=user_id,
                timeout=timeout,
                tag=tag,
                searx_only=True,
                record_errors=not quiet_errors,
            )
        except Exception as e:
            logger.debug("digest search query failed %r: %s", (q or "")[:48], e)
            continue
        res = pack.get("results") if isinstance(pack, dict) else []
        if isinstance(res, list) and res:
            all_raw = _dedupe_search_raw_rows(
                all_raw + [r for r in res if isinstance(r, dict)]
            )
        if early_stop and all_raw:
            try:
                from core.telegram_output_guard import collect_news_display_items_from_search

                merged = collect_news_display_items_from_search(
                    all_raw,
                    user_query=uq,
                    country=filter_co,
                    world_feed=world_feed,
                    require_article_url=require_article,
                )
                if _digest_quality_sufficient(
                    merged,
                    country=filter_co,
                    world_feed=world_feed,
                    min_items=2,
                ):
                    logger.info(
                        "news_gather early_stop at %d/%d queries items=%d",
                        idx + 1,
                        len(queries),
                        len(merged),
                    )
                    break
            except Exception as e:
                logger.debug("news_gather early_stop check: %s", e)
    return all_raw


async def _enrich_world_digest_from_pages(
    items: List[Dict[str, Any]],
    *,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Текст 2–3 статей → расширенный snippet для narrative (без вывода URL в чат)."""
    if not items or not _world_digest_page_enrich_enabled():
        return items
    try:
        from core.telegram_output_guard import _clip_words, _url_looks_like_article
    except Exception:
        return items

    cap = _world_digest_page_enrich_max()
    timeout = _world_digest_page_enrich_timeout_sec()
    targets: List[Dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict) or len(targets) >= cap:
            continue
        link = str(row.get("link") or row.get("source_url") or "").strip()
        title = str(row.get("title") or "").strip()
        if link.startswith("http") and _url_looks_like_article(link):
            targets.append(row)

    if not targets:
        return items

    async def _one(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        link = str(row.get("link") or row.get("source_url") or "").strip()
        title = str(row.get("title") or "").strip()
        got = await _fetch_page_article(
            link, user_id=user_id, title=title, timeout=timeout
        )
        body = str(got.get("text") or "").strip()
        if len(body) < 120:
            return out
        excerpt = _clip_words(body, 900)
        prev = str(out.get("snippet") or "").strip()
        if prev:
            out["snippet"] = f"{prev}\n\n{excerpt}"[:1200]
        else:
            out["snippet"] = excerpt[:1200]
        return out

    done = await asyncio.gather(
        *[_one(r) for r in targets],
        return_exceptions=True,
    )
    by_link = {
        str(r.get("link") or r.get("source_url") or ""): r
        for r in done
        if isinstance(r, dict)
    }
    out_items: List[Dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        link = str(row.get("link") or row.get("source_url") or "").strip()
        out_items.append(by_link.get(link) or row)
    return out_items


def _dedupe_search_raw_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_urls: set = set()
    seen_titles: set = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("link") or "").strip().lower()
        title = re.sub(r"\s+", " ", str(row.get("title") or "").strip().lower())
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        out.append(row)
    return out


def _seed_search_raw_from_prefetch(
    search_results: List[Dict[str, Any]],
    search_summary: str,
) -> List[Dict[str, Any]]:
    """Первый UniversalSearch из try_news_reply + summary → не терять результаты."""
    rows: List[Dict[str, Any]] = [r for r in search_results if isinstance(r, dict)]
    try:
        from core.telegram_output_guard import _parse_summary_to_search_rows

        rows.extend(_parse_summary_to_search_rows(search_summary))
    except Exception as e:
        logger.debug("news seed summary parse: %s", e)
    return _dedupe_search_raw_rows(rows)


def _digest_collect_require_article_url() -> bool:
    try:
        from core.brain_own_turn import news_digest_search_only_enabled

        return news_digest_search_only_enabled()
    except Exception:
        return True


def _digest_quality_sufficient(
    items: List[Dict[str, Any]],
    *,
    country: str,
    world_feed: bool,
    min_items: int,
    relaxed: bool = False,
) -> bool:
    """BY/RU: не считать дайджест готовым, если только порталы/SEO без URL статей."""
    effective_min = 2 if relaxed else min_items
    if len(items) < effective_min:
        return False
    try:
        from core.telegram_output_guard import (
            _url_looks_like_article,
            is_search_portal_junk,
        )
    except Exception:
        _url_looks_like_article = lambda _u: False  # type: ignore
        is_search_portal_junk = lambda *_a, **_k: False  # type: ignore

    article_rows = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "")
        link = str(it.get("link") or it.get("source_url") or "")
        snippet = str(it.get("snippet") or "")
        if is_search_portal_junk(title, snippet, link):
            continue
        if _url_looks_like_article(link):
            article_rows += 1
    need_articles = 1 if relaxed else max(2, min(min_items, 3))
    if article_rows < need_articles:
        return False
    if world_feed or not (country or "").strip():
        return True
    co = (country or "").strip().upper()
    if co == "BY":
        local = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            pub = str(it.get("publisher") or "").lower()
            title = str(it.get("title") or "").lower()
            link = str(it.get("link") or it.get("source_url") or "").lower()
            blob = f"{title} {link}"
            if is_search_portal_junk(
                str(it.get("title") or ""),
                str(it.get("snippet") or ""),
                link,
            ):
                continue
            if not _url_looks_like_article(link):
                continue
            if pub.endswith(".by") or "belta" in pub or "беларус" in blob or "belarus" in blob:
                local += 1
        return local >= 1
    return True


async def compose_news_digest_from_search(
    user_text: str,
    *,
    search_results: List[Dict[str, Any]],
    search_summary: str = "",
    persisted: Optional[Dict[str, Any]] = None,
    user_id: str = "",
    expanded: bool = False,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """Дайджест из SearX/DDG: stash + narrative LLM (без RSS)."""
    text = (user_text or "").strip()
    if not text:
        return None
    facts = _user_facts_from_persisted(persisted)
    profile_co = _news_country_iso2(facts)
    filter_co = _news_digest_filter_country(text, profile_co)
    world_feed = False
    try:
        from modules.external_apis.clients import NewsAPIClient

        world_feed = bool(NewsAPIClient().wants_world_news(text))
    except Exception:
        pass
    min_items = 2
    try:
        min_items = max(2, min(5, int((os.getenv("NEWS_DIGEST_MIN_ITEMS") or "2").strip())))
    except ValueError:
        min_items = 2
    from core.telegram_output_guard import collect_news_display_items_from_search

    require_article = _digest_collect_require_article_url()
    all_raw = _seed_search_raw_from_prefetch(search_results, search_summary)
    shown: List[Dict[str, Any]] = []
    queries = news_digest_search_queries(text, country=profile_co, world_feed=world_feed)[
        : _news_digest_max_search_queries(world_feed=world_feed)
    ]
    uid = str(user_id or "")
    if world_feed:
        extra = await _gather_digest_search_rows(
            queries,
            country=profile_co,
            user_id=uid,
            world_feed=True,
            user_query=text,
        )
        if extra:
            all_raw = _dedupe_search_raw_rows(all_raw + extra)
        merged = collect_news_display_items_from_search(
            all_raw,
            user_query=text,
            country=filter_co,
            world_feed=world_feed,
            require_article_url=require_article,
        )
        if merged:
            shown = _persist_news_digest_displayed(
                persisted,
                merged,
                query=text,
                country=filter_co,
                world_feed=world_feed,
            )
    else:
        for q2 in queries:
            try:
                pack2 = await _search_pack(
                    q2,
                    country=profile_co,
                    user_id=uid,
                    timeout=_news_digest_search_timeout_sec(world_feed=False),
                    tag="news_digest_refine_search",
                    searx_only=True,
                )
            except Exception as e:
                logger.debug("digest search query failed %r: %s", (q2 or "")[:48], e)
                continue
            res2 = pack2.get("results") if isinstance(pack2, dict) else []
            if isinstance(res2, list) and res2:
                all_raw = _dedupe_search_raw_rows(
                    all_raw + [r for r in res2 if isinstance(r, dict)]
                )
            if not all_raw:
                continue
            merged = collect_news_display_items_from_search(
                all_raw,
                user_query=text,
                country=filter_co,
                world_feed=world_feed,
                require_article_url=require_article,
            )
            if merged and (
                len(merged) >= min_items
                and _digest_quality_sufficient(
                    merged, country=filter_co, world_feed=world_feed, min_items=min_items
                )
            ):
                shown = _persist_news_digest_displayed(
                    persisted,
                    merged,
                    query=text,
                    country=filter_co,
                    world_feed=world_feed,
                )
                break
            if len(merged) > len(shown):
                shown = _persist_news_digest_displayed(
                    persisted,
                    merged,
                    query=text,
                    country=filter_co,
                    world_feed=world_feed,
                )
    quality_ok = _digest_quality_sufficient(
        shown, country=filter_co, world_feed=world_feed, min_items=min_items
    )
    if not quality_ok and len(shown) >= 2:
        quality_ok = _digest_quality_sufficient(
            shown,
            country=filter_co,
            world_feed=world_feed,
            min_items=2,
            relaxed=True,
        )
    if len(shown) < 2 or not quality_ok:
        return (
            "Не удалось собрать нормальный дайджест из поиска (только заголовки порталов). "
            "Попробуйте «какие новости в мире» или уточните регион."
        )
    if world_feed and shown:
        shown = await _enrich_world_digest_from_pages(shown, user_id=uid)
        shown = _persist_news_digest_displayed(
            persisted,
            shown,
            query=text,
            country=filter_co,
            world_feed=world_feed,
        )
    sources = _sources_from_displayed(shown) or _sources_from_search_results(all_raw)
    telemetry: Dict[str, Any] = {}
    reply = await _compose_digest_reply(
        shown,
        user_query=text,
        expanded=expanded,
        user_id=uid,
        country=filter_co,
        world_feed=world_feed,
        sources=sources,
        telemetry=telemetry,
    )
    return await _return_news_with_telemetry(
        reply,
        user_id=uid,
        query=text,
        sources=sources,
        recent_dialogue=recent_dialogue,
        llm_model=_news_llm_model() if _news_digest_llm_enabled() else "",
        **_telemetry_log_kwargs(telemetry),
    )


def _reply_looks_like_portal_digest(reply: str) -> bool:
    """Ответ похож на список главных порталов, а не заголовков статей."""
    s = (reply or "").strip()
    if not s:
        return False
    low = s.lower()
    if "instagram" in low and ("followers" in low or "@" in low):
        return True
    if "новости по теме:" in low:
        return True
    if re.search(r"(?i)новости\s+беларуси\s*\|\s*белта", s):
        return True
    if low.count("· news.example.com") >= 2 or low.count("· news3.example.com") >= 2:
        return True
    return False


async def _compose_digest_reply(
    displayed: List[Dict[str, Any]],
    *,
    user_query: str,
    expanded: bool = False,
    user_id: str = "",
    country: str = "",
    world_feed: bool = False,
    sources: Optional[List[Dict[str, Any]]] = None,
    telemetry: Optional[Dict[str, Any]] = None,
) -> str:
    from core.telegram_output_guard import format_news_from_displayed

    if not displayed:
        return ""
    shown = list(displayed)
    src = sources if sources is not None else _sources_from_displayed(shown)
    digest_fmt = _news_digest_format()
    narr_style = _resolve_narrative_style(user_query=user_query, world_feed=world_feed)
    fp = ""
    cache_key = ""
    try:
        from core.news_digest_cache import (
            cache_key as _digest_cache_key,
            get_cached_compose,
            items_fingerprint,
            put_cached_compose,
        )

        fp = items_fingerprint(shown)
        cache_key = _digest_cache_key(
            user_query=user_query,
            country=country,
            world_feed=world_feed,
            expanded=expanded,
            digest_format=digest_fmt,
            narrative_style=narr_style,
        )
        if _news_digest_llm_enabled() and fp:
            cached = get_cached_compose(cache_key, fp)
            if cached:
                try:
                    from core.monitoring import MONITOR

                    MONITOR.inc("news_digest_llm_cache_hit_total")
                except Exception:
                    pass
                if digest_fmt == "narrative":
                    return _finish_narrative_digest(
                        cached.strip(),
                        user_query=user_query,
                        world_feed=world_feed,
                        narrative_style=narr_style,
                    )
                return cached.strip()
    except Exception as e:
        logger.debug("news_digest cache read: %s", e)

    if digest_fmt == "narrative" and _news_digest_llm_enabled():
        narr = await _llm_digest_narrative_brief(
            shown,
            user_query=user_query,
            expanded=expanded,
            user_id=str(user_id or ""),
            world_feed=world_feed,
            sources=src,
            telemetry=telemetry,
        )
        if narr:
            out = _finish_narrative_digest(
                narr.strip(),
                user_query=user_query,
                world_feed=world_feed,
                narrative_style=narr_style,
            )
            if fp and cache_key:
                try:
                    from core.news_digest_cache import put_cached_compose

                    put_cached_compose(cache_key, fp, out)
                except Exception as e:
                    logger.debug("news_digest cache write narrative: %s", e)
            return out
    try:
        from core.brain_own_turn import news_digest_search_only_enabled

        _search_only = news_digest_search_only_enabled()
    except Exception:
        _search_only = True
    # SEARCH_ONLY + narrative: не отдавать сырой SearX-список порталов (см. DEV_DIARY 2026-05-30).
    if digest_fmt == "narrative" and _search_only:
        list_out = format_news_from_displayed(shown, user_query=user_query, sources=src) if shown else ""
        if list_out and str(list_out).strip() and not _reply_looks_like_portal_digest(list_out):
            try:
                from core.monitoring import MONITOR

                MONITOR.inc("news_digest_narrative_list_fallback_total")
            except Exception:
                pass
            return list_out.strip()
        return (
            "Лента сейчас шумная — не собрал связный обзор. "
            "Через минуту: «новости в мире» или тема: спорт, экономика, политика."
        )
    if _news_digest_llm_enabled():
        shown = await _llm_digest_summaries(
            shown, expanded=expanded, user_id=str(user_id or "")
        )
    out = format_news_from_displayed(shown, user_query=user_query, sources=src) if shown else ""
    if out and fp and cache_key and _news_digest_llm_enabled():
        try:
            from core.news_digest_cache import put_cached_compose

            put_cached_compose(cache_key, fp, out)
        except Exception as e:
            logger.debug("news_digest cache write list: %s", e)
    return out


async def try_news_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    if not news_direct_reply_enabled():
        return None
    text = (user_text or "").strip()
    if not text:
        return None
    try:
        from core.brain_own_turn import news_respect_user_search_over_rss_enabled
        from core.brain.text_helpers import user_prefers_web_search_over_news_rss

        if news_respect_user_search_over_rss_enabled() and user_prefers_web_search_over_news_rss(text):
            logger.info(
                "news_direct skipped: user prefers web search over RSS (defer to brain) uid=%s",
                user_id,
            )
            try:
                from core.monitoring import MONITOR

                MONITOR.inc("news_direct_skipped_search_preference_total")
            except Exception:
                pass
            return None
    except Exception as e:
        logger.debug("news_direct search-preference check: %s", e)
    try:
        from core.article_thread_followup import article_followup_blocks_news_digest

        if article_followup_blocks_news_digest(text, recent_dialogue, persisted):
            return None
    except Exception as e:
        logger.debug("news_direct article_thread skip: %s", e)
    expanded_digest = wants_expanded_news_digest(text, recent_dialogue, persisted)
    if expanded_digest and resolve_news_item_pick_index(text, recent_dialogue, persisted):
        return None
    facts = _user_facts_from_persisted(persisted)
    if not expanded_digest and not _is_news_headlines_request(text, facts, recent_dialogue):
        return None
    if expanded_digest and not (
        _last_assistant_digest_text(recent_dialogue) or _has_cached_news_digest(persisted)
    ):
        if not _is_news_headlines_request(text, facts, recent_dialogue):
            return None
    _has_prior_digest = bool(_last_assistant_digest_text(recent_dialogue)) or _has_cached_news_digest(
        persisted
    )
    if not (expanded_digest and _has_prior_digest):
        try:
            from core.heuristic_context_gate import should_run_shortcut_async

            _gr = await should_run_shortcut_async(
                "news_direct",
                text,
                persisted=persisted,
                planner_context={"recent_dialogue": recent_dialogue}
                if recent_dialogue
                else None,
            )
            if not _gr.allowed:
                return None
        except Exception as e:
            logger.debug("news_direct gate: %s", e)

    news_q = text or "последние новости"
    news_co = _news_country_iso2(facts)
    rss_items: List[Dict[str, Any]] = []
    search_body = ""
    world_feed = False

    if expanded_digest:
        cached = _digest_items_from_dialogue(persisted, recent_dialogue)
        if cached:
            _wc_exp = False
            if isinstance(persisted, dict):
                meta = _dialogue_state(persisted).get("last_news_digest_meta") or {}
                if isinstance(meta, dict):
                    _wc_exp = bool(meta.get("world_feed"))
            sources_cached = _sources_from_displayed(cached)
            telemetry: Dict[str, Any] = {}
            reply = await _compose_digest_reply(
                cached,
                user_query=news_q,
                expanded=True,
                user_id=str(user_id or ""),
                country=news_co,
                world_feed=_wc_exp,
                sources=sources_cached,
                telemetry=telemetry,
            )
            if reply and str(reply).strip():
                return await _return_news_with_telemetry(
                    reply,
                    user_id=str(user_id or ""),
                    query=text,
                    sources=sources_cached,
                    recent_dialogue=recent_dialogue,
                    llm_model=_news_llm_model() if _news_digest_llm_enabled() else "",
                    **_telemetry_log_kwargs(telemetry),
                )

    enrich = (os.getenv("NEWS_ENRICH_SEARCH_SNIPPETS") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    async def _fetch_search_pack() -> Dict[str, Any]:
        if not enrich:
            return {"ok": False}
        try:
            from core.universal_search_module import UniversalSearchModule

            pack = await with_retry(
                lambda: UniversalSearchModule().search(
                    news_q,
                    country=news_co,
                    user_id=str(user_id or ""),
                    searx_categories="general",
                ),
                retries=0,
                timeout_sec=18.0,
                tag="news_direct_search",
            )
            if isinstance(pack, dict) and pack.get("ok"):
                return pack
        except Exception as e:
            logger.debug("news_direct search: %s", e)
        return {"ok": False}

    search_pack: Dict[str, Any] = {"ok": False}
    search_prefetch_results: List[Dict[str, Any]] = []

    try:
        from core.brain_own_turn import news_digest_search_only_enabled, news_rss_fallback_enabled
        from modules.external_apis.clients import NewsAPIClient

        client = NewsAPIClient()
        world_feed = bool(client.wants_world_news(news_q))

        if world_feed or news_digest_search_only_enabled():
            search_pack = await _fetch_search_pack()
            search_body = str(search_pack.get("summary") or "").strip()
            search_prefetch_results = [
                r for r in (search_pack.get("results") or []) if isinstance(r, dict)
            ]

        if (
            not news_digest_search_only_enabled()
            and news_rss_fallback_enabled()
            and not (world_feed and search_body)
        ):
            if world_feed:
                pass
            elif not news_co and len((news_q or "").strip()) < 12:
                news_co = _news_country_iso2(facts)
            rss = await with_retry(
                lambda: client.headlines(topic=news_q, country=news_co),
                retries=0,
                timeout_sec=14.0,
                tag="news_direct_rss",
            )
            if rss.get("configured") and isinstance(rss.get("items"), list):
                rss_items = [r for r in rss.get("items") if isinstance(r, dict)]
            if rss_items and _rss_items_are_google_meta_only(rss_items):
                try:
                    from core.monitoring import MONITOR

                    MONITOR.inc("news_direct_rss_google_meta_skip_total")
                except Exception:
                    pass
                rss_items = []
                if not search_body:
                    search_pack = await _fetch_search_pack()
                    search_body = str(search_pack.get("summary") or "").strip()
                    search_prefetch_results = [
                        r for r in (search_pack.get("results") or []) if isinstance(r, dict)
                    ]
    except Exception as e:
        logger.warning("news_direct rss failed uid=%s: %s", user_id, e)

    if rss_items and _news_enrich_on_digest():
        rss_items = await _enrich_rss_items_for_direct(
            rss_items,
            query=text,
            country=news_co,
            user_id=str(user_id or ""),
        )

    if not rss_items and not search_body and not search_prefetch_results:
        search_pack = await _fetch_search_pack()
        search_body = str(search_pack.get("summary") or "").strip()
        search_prefetch_results = [
            r for r in (search_pack.get("results") or []) if isinstance(r, dict)
        ]

    from core.telegram_output_guard import format_news_from_search

    try:
        from core.brain_own_turn import news_digest_search_only_enabled
    except Exception:
        news_digest_search_only_enabled = lambda: True  # type: ignore

    if news_digest_search_only_enabled():
        reply: Optional[str] = None
        try:
            reply = await compose_news_digest_from_search(
                text,
                search_results=search_prefetch_results,
                search_summary=search_body,
                persisted=persisted,
                user_id=str(user_id or ""),
                expanded=expanded_digest,
                recent_dialogue=recent_dialogue,
            )
            if reply and str(reply).strip() and not _reply_looks_like_portal_digest(reply):
                return reply.strip()
        except Exception as e:
            logger.debug("news search-only digest: %s", e)
        return (
            "Свежие заголовки из поиска сейчас не собрались. "
            "Попробуйте «новости в мире» или повторите через минуту."
        )

    sources: List[Dict[str, Any]] = []
    telemetry: Dict[str, Any] = {}
    if rss_items:
        shown = await stash_news_digest_context_async(
            persisted,
            rss_items,
            query=text,
            country=news_co,
            world_feed=bool(world_feed),
            user_id=str(user_id or ""),
        )
        sources = _sources_from_displayed(shown)
        reply = await _compose_digest_reply(
            shown,
            user_query=text,
            expanded=expanded_digest,
            user_id=str(user_id or ""),
            country=news_co,
            world_feed=bool(world_feed),
            sources=sources,
            telemetry=telemetry,
        )
    elif search_body:
        sources = _sources_from_search_results(search_prefetch_results)
        reply = format_news_from_search(
            search_body,
            user_query=text,
            country=news_co,
            world_feed=bool(world_feed),
            sources=sources,
        )
    else:
        reply = (
            "Заголовки из ленты сейчас не собрались. "
            "Попробуйте уточнить регион или повторить через минуту."
        )

    return await _return_news_with_telemetry(
        reply,
        user_id=str(user_id or ""),
        query=text,
        sources=sources,
        recent_dialogue=recent_dialogue,
        llm_model=_news_llm_model() if _news_digest_llm_enabled() else "",
        **_telemetry_log_kwargs(telemetry),
    )


async def try_web_news_digest_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """«не rss» / brain owns news: дайджест только из веб-поиска, без RSS и без LLM."""
    text = (user_text or "").strip()
    if not text:
        return None
    try:
        from core.brain_own_turn import news_respect_user_search_over_rss_enabled

        if not news_respect_user_search_over_rss_enabled():
            return None
        from core.brain.text_helpers import user_prefers_web_search_over_news_rss

        if not user_prefers_web_search_over_news_rss(text):
            return None
    except Exception as e:
        logger.debug("web_news_digest gate: %s", e)
        return None
    facts = _user_facts_from_persisted(persisted)
    if not _is_news_headlines_request(text, facts, recent_dialogue):
        return None
    news_co = _news_country_iso2(facts)
    pack = await _search_pack(
        text,
        country=news_co,
        user_id=str(user_id or ""),
        timeout=22.0,
        tag="news_web_digest",
    )
    if not pack.get("ok"):
        return (
            "Не удалось получить сводку из поиска. "
            "Проверьте SEARXNG_INSTANCE_URL на сервере или повторите запрос позже."
        )
    from core.telegram_output_guard import format_news_from_search

    results = [r for r in (pack.get("results") or []) if isinstance(r, dict)]
    sources = _sources_from_search_results(results)
    body = format_news_from_search(str(pack.get("summary") or ""), user_query=text, sources=sources)
    if body and str(body).strip():
        return await _return_news_with_telemetry(
            str(body).strip()[:4500],
            user_id=str(user_id or ""),
            query=text,
            sources=sources,
            recent_dialogue=recent_dialogue,
        )
    summary = str(pack.get("summary") or "").strip()
    if summary:
        return await _return_news_with_telemetry(
            summary[:4500],
            user_id=str(user_id or ""),
            query=text,
            sources=sources,
            recent_dialogue=recent_dialogue,
        )
    return None


async def try_affirmative_search_reply(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """«да» после «могу перепроверить поиск» — реальный поиск, не idle-ack фактов."""
    from core.brain.text_helpers import resolve_affirmative_search_query

    q = resolve_affirmative_search_query(user_text, recent_dialogue, persisted)
    if not q:
        return None
    facts = _user_facts_from_persisted(persisted)
    country = _news_country_iso2(facts)
    pack = await _search_pack(
        q,
        country=country,
        user_id=str(user_id or ""),
        timeout=22.0,
        tag="affirmative_search",
    )
    if not pack.get("ok"):
        return (
            f"Поиск по запросу «{q}» сейчас не вернул результатов. "
            "Попробуйте уточнить формулировку или повторить через минуту."
        )
    from core.telegram_output_guard import format_news_from_search

    results = [r for r in (pack.get("results") or []) if isinstance(r, dict)]
    sources = _sources_from_search_results(results)
    body = format_news_from_search(str(pack.get("summary") or ""), user_query=q, sources=sources)
    if body and str(body).strip():
        return await _return_news_with_telemetry(
            str(body).strip()[:4500],
            user_id=str(user_id or ""),
            query=q,
            sources=sources,
            recent_dialogue=recent_dialogue,
        )
    summary = str(pack.get("summary") or "").strip()
    if summary:
        return await _return_news_with_telemetry(
            summary[:4500],
            user_id=str(user_id or ""),
            query=q,
            sources=sources,
            recent_dialogue=recent_dialogue,
        )
    return (
        f"Поиск по «{q}» выполнен, но сводка пустая. "
        "Уточните, что именно нужно найти."
    )


def try_web_news_digest_reply_sync(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    import asyncio
    import concurrent.futures

    coro = try_web_news_digest_reply(
        user_text,
        persisted=persisted,
        user_id=user_id,
        recent_dialogue=recent_dialogue,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=35)


def try_affirmative_search_reply_sync(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    import asyncio
    import concurrent.futures

    coro = try_affirmative_search_reply(
        user_text,
        persisted=persisted,
        user_id=user_id,
        recent_dialogue=recent_dialogue,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=35)


def try_news_reply_sync(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    recent_dialogue: Any = None,
) -> Optional[str]:
    """Синхронная обёртка для orchestrator.plan."""
    import asyncio
    import concurrent.futures

    coro = try_news_reply(
        user_text,
        persisted=persisted,
        user_id=user_id,
        recent_dialogue=recent_dialogue,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=32)
