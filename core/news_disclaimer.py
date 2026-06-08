"""Auto-disclaimer generator for news replies — depends on source confidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from core.news_article_model import NewsSource


class NewsDisclaimerGenerator:
    """Генератор дисклеймера в зависимости от качества источников."""

    TRUSTED_DOMAINS: Set[str] = {
        "reuters.com",
        "bbc.com",
        "bbc.co.uk",
        "ap.org",
        "apnews.com",
        "tass.ru",
        "interfax.ru",
        "kommersant.ru",
        "rbc.ru",
        "ria.ru",
        "unian.ua",
        "pravda.com.ua",
    }

    @classmethod
    def is_trusted_domain(cls, domain: str) -> bool:
        """True если домен (или его суффикс) в списке доверенных."""
        d = (domain or "").strip().lower()
        if not d:
            return False
        if d in cls.TRUSTED_DOMAINS:
            return True
        for td in cls.TRUSTED_DOMAINS:
            if d.endswith("." + td):
                return True
        return False

    def generate(
        self,
        sources: List[Dict[str, Any]],
        reply: str = "",
    ) -> str:
        """
        Вернуть дисклеймер в зависимости от качества источников.

        Confidence HIGH (все источники от известных агентств):
          "Источник: [domain]. Информация из открытых источников."

        Confidence MEDIUM (смешанные источники):
          "⚠️ Информация собрана из веб-поиска. Рекомендуем проверить критичные факты."

        Confidence LOW (нет источников / парсинг прошел плохо):
          "⚠️ Ответ основан на автоматическом поиске. Проверьте источники самостоятельно."
        """
        if not sources:
            return "⚠️ Ответ основан на автоматическом поиске. Проверьте источники самостоятельно."

        avg_conf, trusted_count, domains = self._analyze_sources(sources)
        all_trusted = trusted_count == len(sources)
        high_conf = avg_conf >= 0.7
        medium_conf = avg_conf >= 0.3

        if all_trusted and high_conf:
            domain_list = ", ".join(sorted(domains)[:3])
            return f"📰 Источник: {domain_list}. Информация из открытых источников."

        if medium_conf:
            trusted_hint = ""
            if trusted_count > 0 and trusted_count < len(sources):
                trusted_hint = f" ({trusted_count}/{len(sources)} от доверенных изданий)"
            return (
                f"⚠️ Информация собрана из веб-поиска{trusted_hint}. "
                f"Рекомендуем проверить критичные факты."
            )

        return "⚠️ Ответ основан на автоматическом поиске. Проверьте источники самостоятельно."

    def generate_for_single_source(
        self,
        url: str,
        domain: str,
        *,
        confidence: float = 0.0,
        fetch_success: bool = True,
    ) -> str:
        """Короткий дисклеймер для одного источника."""
        if not fetch_success:
            return "⚠️ Источник временно недоступен. Информация может быть неполной."
        trusted = self.is_trusted_domain(domain)
        if trusted and confidence >= 0.5:
            return f"📰 Источник: {domain}"
        if confidence >= 0.5:
            return f"📰 Источник: {domain}. Информация из открытых источников."
        return f"⚠️ Источник: {domain}. Рекомендуем проверить факты."

    def _analyze_sources(
        self,
        sources: List[Dict[str, Any]],
    ) -> tuple:
        """
        Проанализировать список источников.

        Returns:
            (avg_confidence, trusted_count, set_of_domains)
        """
        total_conf = 0.0
        trusted = 0
        domains: Set[str] = set()
        for s in sources:
            if not isinstance(s, dict):
                continue
            conf = float(s.get("parsing_confidence", 0.0) or s.get("confidence", 0.0))
            total_conf += max(0.0, min(1.0, conf))
            domain = str(s.get("domain", "") or "")
            if self.is_trusted_domain(domain):
                trusted += 1
            if domain:
                domains.add(domain)
        n = len(sources) or 1
        return total_conf / n, trusted, domains


def format_news_with_disclaimer(
    body: str,
    sources: List[Dict[str, Any]],
    *,
    user_query: str = "",
) -> str:
    """Форматировать новостной ответ с дисклеймером в конце."""
    if not body or not body.strip():
        return ""
    disclaimer = NewsDisclaimerGenerator().generate(sources)
    return f"{body.strip()}\n\n{disclaimer}"