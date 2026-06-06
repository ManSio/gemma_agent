"""
Инструмент News.headlines — новости через Google News RSS (без API-ключа).
Подхватывается core.tools как News.headlines.
"""
from __future__ import annotations

from typing import Any, Dict

from modules.external_apis.clients import NewsAPIClient


class NewsModule:
    BRAIN_LITE_INCLUDE = True

    async def headlines(self, topic: str = "", country: str = "") -> Dict[str, Any]:
        """
        args:
          topic — тема новостей (оставь пустым для общих мировых новостей)
          country — код страны (опционально)
        """
        client = NewsAPIClient()
        return await client.headlines(topic=topic, country=country)
