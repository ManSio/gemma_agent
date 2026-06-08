"""Fetch validation for news articles — проверка HTML перед передачей в LLM."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from core.news_article_model import NewsArticle, build_news_article, _extract_domain

logger = logging.getLogger(__name__)

_EMPTY_HTML_RE = re.compile(
    r"^<(?!(?:!DOCTYPE|html))", re.I
)
_CLOUDFLARE_RE = re.compile(r"(?i)cloudflare|challenge-platform|cf-browser-verification")
_CAPTCHA_RE = re.compile(r"(?i)captcha|recaptcha|hcaptcha|turnstile")
_ROBOTS_RE = re.compile(r"(?i)<meta\s+name=[\"']robots[\"']")
_MIN_TEXT_LENGTH = 60


class FetchValidationResult:
    """Результат валидации HTML/текста, полученного с новостного URL."""

    __slots__ = ("valid", "confidence", "reason", "retry_suggested", "alternative_method")

    def __init__(
        self,
        valid: bool = False,
        confidence: float = 0.0,
        reason: str = "",
        retry_suggested: bool = False,
        alternative_method: Optional[str] = None,
    ) -> None:
        self.valid = valid
        self.confidence = max(0.0, min(1.0, confidence))
        self.reason = reason
        self.retry_suggested = retry_suggested
        self.alternative_method = alternative_method

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "confidence": self.confidence,
            "reason": self.reason,
            "retry_suggested": self.retry_suggested,
            "alternative_method": self.alternative_method,
        }


class NewsValidator:
    """Валидатор fetch: проверяет что URL действительно открылся с полезным контентом."""

    MIN_TEXT_LENGTH = _MIN_TEXT_LENGTH

    def validate_fetch(
        self,
        url: str,
        html: Optional[str] = None,
        text: Optional[str] = None,
        *,
        http_status: int = 200,
        content_type: str = "",
    ) -> FetchValidationResult:
        """
        Проверить HTML/текст, полученный с URL.

        Проверки:
        1. HTML не пуст (не <html></html>)
        2. Содержит текст (не только теги)
        3. Не cloudflare/captcha/robots.txt
        4. Content-Type: text/html (не PDF/XML)
        5. HTTP status 200 (не 404/403)

        Returns:
            FetchValidationResult с confidence и рекомендациями.
        """
        # 5 — HTTP status
        if http_status != 200:
            return FetchValidationResult(
                valid=False,
                confidence=0.0,
                reason=f"HTTP {http_status} (expected 200)",
                retry_suggested=http_status in (429, 503, 502),
                alternative_method="web_search" if http_status in (403, 404) else None,
            )

        # 4 — Content-Type
        ct = (content_type or "").lower()
        if ct and "text/html" not in ct and "text/plain" not in ct:
            return FetchValidationResult(
                valid=False,
                confidence=0.0,
                reason=f"Unexpected Content-Type: {content_type}",
                retry_suggested=False,
                alternative_method="web_search",
            )

        body = html or ""
        page_text = text or ""

        # 3 — Captcha / Cloudflare / robots
        combined = body + " " + page_text
        if _CLOUDFLARE_RE.search(combined):
            return FetchValidationResult(
                valid=False,
                confidence=0.0,
                reason="Cloudflare/WAF detected — content blocked",
                retry_suggested=False,
                alternative_method="web_search",
            )
        if _CAPTCHA_RE.search(combined):
            return FetchValidationResult(
                valid=False,
                confidence=0.0,
                reason="Captcha detected — content blocked",
                retry_suggested=False,
                alternative_method="web_search",
            )

        # Текстовая длина
        text_len = len(page_text.strip())
        if text_len < self.MIN_TEXT_LENGTH:
            # Пытаемся извлечь текст из HTML
            extracted = self._extract_text_from_html(body)
            text_len = max(text_len, len(extracted.strip()))

        if text_len < self.MIN_TEXT_LENGTH:
            return FetchValidationResult(
                valid=False,
                confidence=0.1,
                reason=f"Text too short: {text_len} chars (min {self.MIN_TEXT_LENGTH})",
                retry_suggested=True,
                alternative_method="web_search",
            )

        # Успех — вычисляем confidence
        confidence = self._compute_confidence(text_len, body)
        return FetchValidationResult(
            valid=True,
            confidence=confidence,
            reason=f"Valid article: {text_len} chars, confidence {confidence:.1%}",
            retry_suggested=False,
            alternative_method=None,
        )

    def _compute_confidence(self, text_length: int, html: str = "") -> float:
        """Вычислить confidence от 0.0 до 1.0 на основе длины текста."""
        if text_length >= 5000:
            return 0.95
        if text_length >= 2000:
            return 0.85
        if text_length >= 800:
            return 0.70
        if text_length >= 300:
            return 0.50
        return 0.30

    def _extract_text_from_html(self, html: str) -> str:
        """Примитивное извлечение текста из HTML (без парсера)."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 30:
            return ""
        return text

    async def fallback_fetch(
        self,
        url: str,
        original_html: str,
        *,
        user_id: str = "",
    ) -> Optional[str]:
        """
        Если основной парсер вернул мало — попробовать альтернативы:
        1. Regex-извлечение из HTML
        2. Abort + вернуть None
        """
        text = self._extract_text_from_html(original_html)
        if len(text.strip()) >= self.MIN_TEXT_LENGTH:
            logger.debug("fallback regex extract succeeded: %d chars from %s", len(text), url[:60])
            return text.strip()
        logger.debug("fallback fetch returned no usable text from %s", url[:60])
        return None

    @staticmethod
    def trusted_domains() -> List[str]:
        """Известные доверенные новостные домены."""
        return [
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
        ]

    @staticmethod
    def is_trusted_domain(url: str) -> bool:
        """True если URL относится к доверенному новостному домену."""
        domain = _extract_domain(url)
        if not domain:
            return False
        trusted = NewsValidator.trusted_domains()
        for td in trusted:
            if domain == td or domain.endswith("." + td):
                return True
        return False

    def validate_article(
        self, legacy: Dict[str, Any], *, fetch_method: str = "urlfetch"
    ) -> FetchValidationResult:
        """Валидировать legacy dict из _fetch_page_article."""
        text = str(legacy.get("text") or "")
        url = str(legacy.get("url") or "")
        html = str(legacy.get("_raw_html") or "")
        return self.validate_fetch(url, html=html, text=text)