"""Реформа 2026-05: один brain на ход — planner direct_reply только по явному allow."""
from __future__ import annotations

import os
from typing import Dict

# При BRAIN_OWN_TURN_ENABLED=true по умолчанию direct выключен (ход в brain + tools).
_PLANNER_DIRECT_DEFAULTS: Dict[str, bool] = {
    "news": False,
    "news_item": False,
    "weather": False,
    "geo_nearby": False,
    "affirmative_search": False,
}

_LEGACY_DIRECT_ENV: Dict[str, str] = {
    "news": "NEWS_DIRECT_REPLY_ENABLED",
    "news_item": "NEWS_ITEM_PICK_ENABLED",
    "weather": "WEATHER_DIRECT_REPLY_ENABLED",
}


def _env_truthy(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off"}:
        return False
    return raw in {"1", "true", "yes", "on"}


def brain_own_turn_enabled() -> bool:
    return _env_truthy("BRAIN_OWN_TURN_ENABLED", default=True)


def planner_direct_allowed(kind: str) -> bool:
    """Разрешить orchestrator/__fallback__ direct_reply для kind."""
    k = (kind or "").strip().lower()
    if not k:
        return False
    if not brain_own_turn_enabled():
        legacy = _LEGACY_DIRECT_ENV.get(k)
        if legacy:
            return _env_truthy(legacy, default=True)
        return True
    allow_env = f"BRAIN_OWN_TURN_ALLOW_{k.upper()}"
    return _env_truthy(allow_env, default=_PLANNER_DIRECT_DEFAULTS.get(k, False))


def news_rss_fallback_enabled() -> bool:
    """Google News RSS в news_direct — по умолчанию выкл. при brain-own."""
    if news_digest_search_only_enabled():
        return False
    if not planner_direct_allowed("news"):
        return False
    return _env_truthy("NEWS_RSS_FALLBACK_ENABLED", default=False)


def news_respect_user_search_over_rss_enabled() -> bool:
    """
    True — если пользователь явно просит не RSS / «из интернета», не отдавать news_direct
    и не подмешивать Google News RSS в pipeline-hint (отдаём ход brain + UniversalSearch). G1.
    """
    return _env_truthy("NEWS_RESPECT_USER_SEARCH_OVER_RSS", default=True)


def news_digest_search_only_enabled() -> bool:
    """Дайджест и пункты — только UniversalSearch/SearX, без Google News RSS."""
    return _env_truthy("NEWS_DIGEST_SEARCH_ONLY", default=True)


def pipeline_news_emergency_rss_on_search_fail_enabled() -> bool:
    """
    Если UniversalSearch не дал сводку на news-запрос — подтянуть Google News RSS в pipeline,
    даже при BRAIN_OWN_TURN_ALLOW_NEWS=false (аварийный fallback, не plan bypass).
    """
    if news_digest_search_only_enabled():
        return False
    return _env_truthy("NEWS_PIPELINE_RSS_ON_SEARCH_FAIL", default=False)


def pipeline_news_rss_fetch_enabled(user_text: str = "") -> bool:
    """
    Google News RSS в brain pipeline (hint / BRAIN_NEWS_DIRECT_FROM_SEARCH) — только если
    разрешён planner news и NEWS_RSS_FALLBACK, и пользователь не просил веб-поиск (G1).
    При BRAIN_OWN_TURN_ALLOW_NEWS=false — всегда False (только UniversalSearch + LLM).
    """
    if news_digest_search_only_enabled():
        return False
    if not news_rss_fallback_enabled():
        return False
    if not (user_text or "").strip():
        return True
    if not news_respect_user_search_over_rss_enabled():
        return True
    try:
        from core.brain.text_helpers import user_prefers_web_search_over_news_rss

        if user_prefers_web_search_over_news_rss(user_text):
            return False
    except Exception:
        pass
    return True


def brain_weather_api_enabled() -> bool:
    """Open-Meteo/wttr внутри brain (short-circuit + external_hint), не planner bypass."""
    return _env_truthy("BRAIN_WEATHER_API_ENABLED", default=True)


def brain_news_item_reply_enabled() -> bool:
    """Пункт дайджеста («4») в pipeline — не plan bypass, а tool-path внутри brain."""
    return _env_truthy("BRAIN_NEWS_ITEM_REPLY_ENABLED", default=True)


def brain_pipeline_shortcut_allowed(kind: str) -> bool:
    """Ранний выход в pipeline.py (news/weather/item) — только если разрешён planner direct."""
    return planner_direct_allowed(kind)


def brain_pipeline_news_short_circuit_enabled() -> bool:
    """Ответ до LLM по новостям в pipeline — search-only вкл. по умолчанию."""
    if news_digest_search_only_enabled():
        return _env_truthy("BRAIN_PIPELINE_NEWS_SHORT_CIRCUIT", default=True)
    if not planner_direct_allowed("news"):
        return False
    return _env_truthy("BRAIN_PIPELINE_NEWS_SHORT_CIRCUIT", default=False)


def brain_pipeline_news_item_short_circuit_enabled() -> bool:
    if not planner_direct_allowed("news_item"):
        return False
    return _env_truthy("BRAIN_PIPELINE_NEWS_ITEM_SHORT_CIRCUIT", default=False)


# Семантические bypass в orchestrator.plan (не NL-команды) — при brain_own_turn выкл.
PLANNER_SEMANTIC_DIRECT_KINDS = frozenset(
    {"news", "news_item", "weather", "geo_nearby", "affirmative_search"}
)


def record_planner_semantic_deferred(kind: str) -> None:
    """Метрика: plan хотел direct_reply, ход ушёл в brain."""
    k = (kind or "").strip().lower()
    if k not in PLANNER_SEMANTIC_DIRECT_KINDS:
        return
    try:
        from core.monitoring import MONITOR

        MONITOR.inc(f"brain_own_turn_deferred_{k}_total")
    except Exception as e:
        import logging

        logging.getLogger(__name__).debug("record_planner_semantic_deferred: %s", e)


# NL-команды и pin геолокации — вне реформы (явные команды, не «угадай intent»).
PLANNER_COMMAND_BYPASS_KINDS: frozenset = frozenset(
    {
        "nl_reminder",
        "nl_cancel_reminder",
        "nl_weekly_schedule",
        "telegram_location",
        "empty_payload",
        "math_ambiguous",
    }
)


def planner_command_bypass_allowed(kind: str) -> bool:
    return (kind or "").strip().lower() in PLANNER_COMMAND_BYPASS_KINDS
