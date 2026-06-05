"""
Инструмент Wikipedia.scan — извлечение текста статьи через MediaWiki API (без скрейпа HTML).

Поддерживается: фраза поиска → opensearch → первая статья; точное название; URL *wikipedia.org/wiki/...
Язык: WIKIPEDIA_LANG / WIKIPEDIA_API_ENDPOINT, полный URL *NN*.wikipedia.org/wiki/... или опциональный args lang (код вики: be, ru, en).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from modules.external_apis.clients import WikipediaClient


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _lang_from_wikipedia_url(url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u.lower().startswith("http"):
        return None
    m = re.search(r"https?://([a-z]{2,12})\.wikipedia\.org", u, re.I)
    return m.group(1).lower() if m else None


def _title_from_wikipedia_url(url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u.lower().startswith("http"):
        return None
    try:
        p = urlparse(u)
    except Exception:
        return None
    host = (p.netloc or "").lower()
    if ".wikipedia.org" not in host:
        return None
    if "/wiki/" not in u:
        return None
    part = u.split("/wiki/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    part = unquote(part)
    t = part.replace("_", " ").strip()
    return t or None


class WikipediaModule:
    async def scan(self, query: str, user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        """
        args:
          query — тема, название статьи или полный URL википедии
          intro_only — опционально true: только вступление (короче)
          lang — опционально код языкового раздела (be, ru, en, …), если query не URL с явным поддоменом
        """
        _ = user_id
        intro_kw = kwargs.get("intro_only")
        intro_only = bool(intro_kw) if intro_kw is not None else _truthy("WIKIPEDIA_SCAN_INTRO_ONLY", False)

        q = (query or "").strip()
        if not q:
            return {
                "ok": False,
                "error": "query required",
                "hint": "Передай тему, название статьи или URL вида https://ru.wikipedia.org/wiki/...",
            }

        raw_lang = kwargs.get("lang") if kwargs.get("lang") is not None else kwargs.get("wiki_lang")
        explicit_lang: Optional[str] = None
        if raw_lang is not None and str(raw_lang).strip():
            cand = str(raw_lang).strip().lower()
            if re.fullmatch(r"[a-z]{2,12}", cand):
                explicit_lang = cand

        url_title = _title_from_wikipedia_url(q)
        url_lang = _lang_from_wikipedia_url(q) if url_title else None
        client_lang = url_lang or explicit_lang
        client = WikipediaClient(lang=client_lang) if client_lang else WikipediaClient()
        if not client.is_configured():
            return {"ok": False, "error": "wikipedia client not configured"}

        ex: Dict[str, Any]
        used: str

        if url_title:
            ex = await client.article_extract(url_title, intro_only=intro_only)
            used = "url"
        else:
            direct = await client.article_extract(q, intro_only=intro_only)
            if direct.get("configured"):
                ex = direct
                used = "direct_title"
            else:
                titles = await client.opensearch(q, limit=8)
                if not titles:
                    return {
                        "ok": False,
                        "error": "wikipedia: no articles for query",
                        "query": q,
                        "hint": "Уточни формулировку, задай WIKIPEDIA_LANG в .env или вызови снова с args lang (например be, ru).",
                    }
                ex = await client.article_extract(titles[0], intro_only=intro_only)
                used = "opensearch"

        if not ex.get("configured"):
            return {
                "ok": False,
                "error": ex.get("error") or "wikipedia extract failed",
                "query": q,
            }

        text = str(ex.get("extract") or "").strip()
        page_url = str(ex.get("page_url") or client.page_url_for_title(str(ex.get("title") or "")))

        return {
            "ok": True,
            "source": "wikipedia_api",
            "wiki_lang": client.wiki_lang(),
            "title": ex.get("title"),
            "page_url": page_url,
            "text": text,
            "truncated": bool(ex.get("truncated")),
            "intro_only": bool(ex.get("intro_only")),
            "resolved_via": used,
            "hint": "Текст из MediaWiki extracts; для правок и сносок открой page_url. Не-вики сайты — UrlFetch.fetch_page.",
        }
