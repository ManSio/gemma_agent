from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from modules.external_apis.clients import (
    CurrencyAPIClient,
    GenericSearchClient,
    NewsAPIClient,
    WeatherAPIClient,
    WikipediaClient,
    _text_has_cyrillic,
    fetch_wttr_in_j1_summary,
)


def _expand_search_query_variants(q: str) -> List[str]:
    """
    Несколько формулировок для веб-поиска: снять «как приготовить», добавить «рецепт», укоротить хвост «из …».
    Помогает при узком Instant Answer и частично при пустой выдаче по длинной фразе.
    """
    q0 = (q or "").strip()
    if not q0:
        return []
    out: List[str] = []

    def add(s: str) -> None:
        t = (s or "").strip()
        if t and t not in out:
            out.append(t)

    add(q0)
    low = q0.lower().rstrip("?").strip()
    stripped = re.sub(
        r"^(как\s+приготовить|как\s+сделать|как\s+готовить|расскажи\s+как\s+приготовить|скажи\s+как\s+приготовить)\s+",
        "",
        low,
        flags=re.IGNORECASE,
    ).strip()
    if stripped and stripped != low:
        add(stripped)
        add(re.sub(r"\s+из\s+", " ", stripped))
    base = stripped if stripped else low
    if "рецепт" not in base:
        add(f"{base} рецепт")
    simple = re.sub(r"\s+из\s+[а-яёa-z][а-яёa-z\s]{2,80}$", "", base, flags=re.IGNORECASE).strip()
    if simple and simple != base:
        add(simple)
    stop = {
        "как",
        "приготовить",
        "сделать",
        "готовить",
        "из",
        "рецепт",
        "расскажи",
        "скажи",
        "дай",
        "нужен",
        "хочу",
    }
    words = [w for w in re.split(r"\s+", base) if w and w.lower().strip("?!.") not in stop]
    if len(words) >= 2:
        add(" ".join(words[:5]))
    return out[:8]


class ExternalAPIService:
    def __init__(self) -> None:
        self.weather = WeatherAPIClient()
        self.currency = CurrencyAPIClient()
        self.news = NewsAPIClient()
        self.wikipedia = WikipediaClient()
        self.search_client = GenericSearchClient()

    async def weather_or_fallback(
        self,
        city: str,
        country: str,
        *,
        admin1_hint: str = "",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        label: str = "",
    ) -> Dict[str, Any]:
        if latitude is not None and longitude is not None:
            return await self.weather.get_current_at_coords(
                latitude,
                longitude,
                label=label,
                admin1=admin1_hint,
                country=country,
            )
        return await self.weather.get_current(
            city=city,
            country=country,
            admin1_hint=admin1_hint,
        )

    async def wttr_in_eager_summary(self, city: str, country: str, *, forecast_day_index: int = 0) -> Optional[str]:
        """Серверный запасной прогноз wttr.in (без TOOL_CALL в LLM)."""
        return await fetch_wttr_in_j1_summary(city, country, forecast_day_index=forecast_day_index)

    async def currency_or_fallback(self, base: str, quote: str) -> Dict[str, Any]:
        return await self.currency.get_rate(base=base, quote=quote)

    async def lookup_or_fallback(self, query: str, country: str = "") -> Dict[str, Any]:
        q0 = (query or "").strip()

        def _wiki_variants(q: str) -> List[str]:
            out: List[str] = []
            tail = q.split()[0].strip() if _text_has_cyrillic(q) and " " in q else ""
            for part in (q, tail):
                if part and part not in out:
                    out.append(part)
            return out

        async def _first_wiki_hit(client: WikipediaClient, variants: List[str]) -> Optional[Dict[str, Any]]:
            for v in variants:
                w = await client.summary(v)
                if w.get("configured"):
                    return w
            return None

        variants = _wiki_variants(q0)
        wiki = await _first_wiki_hit(self.wikipedia, variants)
        if wiki:
            return {"source": "wikipedia", "data": wiki}
        if _text_has_cyrillic(q0):
            wiki_ru = await _first_wiki_hit(WikipediaClient(lang="ru"), variants)
            if wiki_ru:
                return {"source": "wikipedia", "data": wiki_ru}
        search_queries = _expand_search_query_variants(q0)
        sr = await self.search_client.search_variants(search_queries)
        if sr.get("configured"):
            return {"source": "search", "data": sr}
        news = await self.news.headlines(topic=query, country=country)
        return {"source": "news", "data": news}
