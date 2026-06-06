"""
Пер-хост «плагины» парсинга: JSON-рецепты (CSS), обучение по HTML (LLM или эвристика).
Без произвольного Python.

Один HTTP-запрос: при SITE_RECIPE_AUTO_LEARN_ON_MISS страница качается один раз,
рецепт строится по уже полученному HTML и сразу применяется.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from core.openrouter_provider import get_openrouter_provider
from core.site_recipe_engine import (
    apply_recipe,
    bump_recipe_stats,
    heuristic_recipe_from_html,
    host_key,
    load_recipe,
    normalize_recipe,
    parse_llm_recipe_json,
    recipe_path_for_host,
    save_recipe,
)
from core.site_recipe_cache import (
    cache_enabled,
    cache_get,
    cache_invalidate,
    cache_set,
    cache_skip,
    html_fingerprint,
)
from core.url_fetch import _body_to_text, _max_chars_out, safe_fetch_raw

logger = logging.getLogger(__name__)

_llm = get_openrouter_provider()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _learn_admins() -> Set[str]:
    raw = os.getenv("SITE_RECIPE_LEARN_ADMINS", "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _can_learn(user_id: str) -> bool:
    if not _env_flag("SITE_RECIPE_LEARN_ENABLED", False):
        return False
    admins = _learn_admins()
    if not admins:
        return True
    return str(user_id or "").strip() in admins


def _html_bytes_to_str(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    if "charset=" in (content_type or "").lower():
        m = re.search(r"charset=([\w\-]+)", content_type, re.I)
        if m:
            charset = m.group(1).strip() or charset
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


async def _llm_recipe_from_html(html_str: str) -> Tuple[Dict[str, Any], str]:
    """Возвращает (нормализованный рецепт или {}, 'llm'|'')."""
    max_html = int(os.getenv("SITE_RECIPE_LLM_HTML_CHARS", "100000"))
    snippet = html_str[:max_html]
    prompt = f"""По фрагменту HTML страницы предложи CSS-селекторы для извлечения основного текста статьи/документации.
Верни ТОЛЬКО JSON без пояснений:
{{"main_selector":"один селектор корневого блока контента","title_selector":"селектор заголовка или пустая строка","strip_selectors":["nav","footer","aside","script","style"],"confidence":0.0}}
Правила: только простые CSS-селекторы (теги, классы, #id, [attr], пробел, > , запятая). Без javascript.
Фрагмент HTML:
{snippet}
"""
    try:
        out = await _llm.generate(
            prompt=prompt,
            system_prompt="Ты возвращаешь только валидный JSON объекта.",
            max_tokens=400,
            temperature=0.1,
        )
        raw_json = (out.get("content") or "").strip() if out.get("success") else ""
        parsed = parse_llm_recipe_json(raw_json) if raw_json else None
        if parsed:
            ok, norm, err = normalize_recipe({**parsed, "source": "llm"})
            if ok:
                return norm, "llm"
            logger.info("[site_recipe] llm recipe invalid: %s", err)
    except Exception as e:
        logger.warning("[site_recipe] llm learn failed: %s", e)
    return {}, ""


def _collect_download_links(html_str: str, page_url: str, *, limit: int = 8) -> List[str]:
    """Прямые https-ссылки на вложения с той же страницы (PDF/DOC…), для /filefrom."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    out: List[str] = []
    seen: Set[str] = set()
    try:
        soup = BeautifulSoup(html_str or "", "html.parser")
    except Exception:
        return []
    try:
        base_host = (urlparse(page_url).hostname or "").lower()
    except Exception:
        base_host = ""

    def _same_site(link_host: str) -> bool:
        lh = (link_host or "").lower()
        if not base_host or not lh:
            return False
        if lh == base_host:
            return True
        return lh.endswith("." + base_host)

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        try:
            full = urljoin(page_url, href)
        except Exception:
            continue
        if not full.lower().startswith("https://"):
            continue
        try:
            lu = urlparse(full)
            link_host = lu.hostname or ""
        except Exception:
            continue
        if not _same_site(link_host):
            continue
        lowp = full.lower()
        if not (
            any(lowp.split("?", 1)[0].endswith(ext) for ext in (".pdf", ".doc", ".docx", ".rtf", ".odt"))
            or "/download" in lowp
            or "format=pdf" in lowp
        ):
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
        if len(out) >= max(1, min(limit, 20)):
            break
    return out


def _recipe_meta(fr: Dict[str, Any], ctype: str) -> Dict[str, Any]:
    raw = fr.get("raw") or b""
    return {
        "content_type": (ctype.split(";")[0] or "").strip(),
        "bytes_read": len(raw) if isinstance(raw, (bytes, bytearray)) else 0,
        "http_status": int(fr.get("http_status") or 0),
    }


class SiteRecipeModule:
    """Инструменты: SiteRecipe.learn_recipe, SiteRecipe.parse_with_recipe."""

    async def learn_recipe(self, url: str, user_id: str = "") -> Dict[str, Any]:
        if not _env_flag("SITE_RECIPE_ENABLED", True):
            return {"error": "SITE_RECIPE_ENABLED=false"}
        if not _can_learn(str(user_id)):
            return {
                "error": "learning disabled: set SITE_RECIPE_LEARN_ENABLED=true "
                "(and SITE_RECIPE_LEARN_ADMINS if you restrict by Telegram user_id)"
            }

        url = (url or "").strip()
        if not url:
            return {"error": "url required"}

        fr = await safe_fetch_raw(url)
        if fr.get("error"):
            return fr

        html_str = _html_bytes_to_str(fr["raw"], fr.get("content_type") or "")
        host = host_key(fr["url"])

        rec: Dict[str, Any] = {}
        source = "heuristic"

        use_llm = _env_flag("SITE_RECIPE_USE_LLM", True) and bool(os.getenv("OPENROUTER_API_KEY"))
        if use_llm:
            rec, src = await _llm_recipe_from_html(html_str)
            if rec:
                source = src

        if not rec:
            rec = heuristic_recipe_from_html(html_str, host)
            source = rec.get("source", "heuristic")

        rec["sample_url"] = fr["url"]
        rec["source"] = source
        if not save_recipe(host, rec):
            return {"error": "failed to save recipe", "host": host}

        try:
            cache_invalidate(host)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'site_recipe_module', e, exc_info=True)
        return {
            "ok": True,
            "host": host,
            "recipe_path": recipe_path_for_host(host),
            "source": source,
            "main_selector": rec.get("main_selector"),
            "title_selector": rec.get("title_selector"),
            "hint": "Теперь вызывай SiteRecipe.parse_with_recipe с тем же хостом для извлечения текста по сохранённому рецепту.",
        }

    async def parse_with_recipe(self, url: str, user_id: str = "") -> Dict[str, Any]:
        if not _env_flag("SITE_RECIPE_ENABLED", True):
            return {"error": "SITE_RECIPE_ENABLED=false"}

        url = (url or "").strip()
        if not url:
            return {"error": "url required"}

        fr = await safe_fetch_raw(url)
        if fr.get("error"):
            return fr

        host = host_key(fr["url"])
        recipe = load_recipe(host)
        raw = fr["raw"]
        ctype = fr.get("content_type") or ""
        html_str = _html_bytes_to_str(raw, ctype)
        max_out = _max_chars_out()
        meta = _recipe_meta(fr, ctype)
        just_auto_learned = False

        if not recipe and _env_flag("SITE_RECIPE_AUTO_LEARN_ON_MISS", False):
            rec: Dict[str, Any] = {}
            src = "auto_heuristic"
            use_llm = (
                _env_flag("SITE_RECIPE_AUTO_LEARN_WITH_LLM", False)
                and _can_learn(str(user_id))
                and _env_flag("SITE_RECIPE_USE_LLM", True)
                and bool(os.getenv("OPENROUTER_API_KEY"))
            )
            if use_llm:
                rec_llm, src_llm = await _llm_recipe_from_html(html_str)
                if rec_llm:
                    rec = rec_llm
                    src = f"auto_{src_llm}"
            if not rec:
                rec = heuristic_recipe_from_html(html_str, host)
                src = "auto_heuristic"
            rec["sample_url"] = fr["url"]
            rec["source"] = src
            if save_recipe(host, rec):
                recipe = load_recipe(host) or rec
                just_auto_learned = True

        if not recipe:
            text = _body_to_text(raw, ctype)
            truncated = len(text) > max_out
            text_out = (text[:max_out] + "\n… [truncated]") if truncated else text
            durls = _collect_download_links(html_str, fr["url"])
            return {
                "ok": True,
                "mode": "generic_fallback",
                "host": host,
                "url": fr["url"],
                "text": text_out,
                "truncated": truncated,
                "download_urls": durls,
                "hint": "Нет рецепта. Включи SITE_RECIPE_AUTO_LEARN_ON_MISS или вызови SiteRecipe.learn_recipe.",
                **meta,
            }

        title, text = apply_recipe(html_str, recipe)
        success = len((text or "").strip()) > 80
        bump_recipe_stats(host, success=success)
        if success:
            try:
                cache_set(host, fp, dict(recipe), sample_url=fr["url"])
            except Exception as e:
                logger.debug('%s optional failed: %s', 'site_recipe_module', e, exc_info=True)
        else:
            try:
                cache_invalidate(host)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'site_recipe_module', e, exc_info=True)
        truncated = len(text) > max_out
        text_out = (text[:max_out] + "\n… [truncated]") if truncated else text

        mode = "recipe_auto_learned" if just_auto_learned else "recipe"
        durls = _collect_download_links(html_str, fr["url"])

        return {
            "ok": True,
            "mode": mode,
            "host": host,
            "url": fr["url"],
            "title": title,
            "text": text_out,
            "truncated": truncated,
            "download_urls": durls,
            "recipe_source": recipe.get("source"),
            "stats": recipe.get("stats"),
            **meta,
        }
