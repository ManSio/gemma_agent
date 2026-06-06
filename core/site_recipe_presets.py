"""
Встроенные рецепты для популярных вёрсток (без отдельного learn).
Хосты: MDN, Wikipedia, GitHub (README), Stack Overflow, ReadTheDocs/Sphinx,
MkDocs Material (aiogram.dev), MkDocs (через SITE_RECIPE_MKDOCS_HOSTS),
law.example.com / law-archive.example.com (РБ), edu.example.com (учебники ГУО), Habr.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

from core.site_recipe_engine import host_matches, normalize_recipe

logger = logging.getLogger(__name__)


def _mkdocs_hosts() -> set:
    raw = os.getenv("SITE_RECIPE_MKDOCS_HOSTS", "").strip()
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _preset_pairs() -> List[Tuple[Callable[[str], bool], Dict[str, Any]]]:
    return [
        (
            lambda h: h == "stackoverflow.com" or h == "www.stackoverflow.com",
            {
                "main_selector": ".question .s-prose, .answer .s-prose",
                "title_selector": "h1[itemprop=name]",
                "strip_selectors": [".comments", ".js-post-menu", ".post-taglist", ".sidebar"],
                "confidence": 0.52,
                "source": "preset_stackoverflow",
            },
        ),
        (
            lambda h: (
                host_matches(h, "github.com")
                and not h.startswith("gist.")
                and not h.startswith("api.")
                and not h.startswith("raw.")
            ),
            {
                "main_selector": "article.markdown-body, .markdown-body",
                "title_selector": "h1",
                "strip_selectors": [
                    "nav",
                    "footer",
                    "aside",
                    ".Header",
                    ".footer",
                    ".pagehead-actions",
                    ".repository-content .file-navigation",
                ],
                "confidence": 0.52,
                "source": "preset_github",
            },
        ),
        (
            lambda h: h == "developer.mozilla.org" or h.endswith(".developer.mozilla.org"),
            {
                "main_selector": "article.main-page-content, .main-page-content, .section-content",
                "title_selector": "h1",
                "strip_selectors": [
                    "nav",
                    "footer",
                    "aside",
                    ".toc",
                    ".breadcrumbs",
                    ".sidebar",
                    ".page-actions",
                    ".feedback",
                ],
                "confidence": 0.52,
                "source": "preset_mdn",
            },
        ),
        (
            lambda h: h.endswith(".wikipedia.org") or h == "wikipedia.org",
            {
                "main_selector": "#mw-content-text",
                "title_selector": "h1#firstHeading",
                "strip_selectors": [
                    ".mw-editsection",
                    ".infobox",
                    ".navbox",
                    ".metadata",
                    "#toc",
                ],
                "confidence": 0.55,
                "source": "preset_wikipedia",
            },
        ),
        (
            lambda h: h.endswith(".readthedocs.io")
            or h.endswith(".rtfd.io")
            or h == "readthedocs.io",
            {
                "main_selector": "div.document, .rst-content, article",
                "title_selector": "h1",
                "strip_selectors": [
                    "nav",
                    "footer",
                    "aside",
                    ".sphinxsidebar",
                    ".related",
                    ".headerlink",
                    ".toc",
                    ".wy-nav-side",
                    ".wy-side-nav-search",
                ],
                "confidence": 0.5,
                "source": "preset_sphinx",
            },
        ),
        (
            lambda h: h == "aiogram.dev" or h.endswith(".aiogram.dev"),
            {
                "main_selector": "article.md-content__inner, .md-content__inner, .md-main__inner",
                "title_selector": "h1",
                "strip_selectors": [
                    "nav",
                    "footer",
                    "aside",
                    ".md-sidebar",
                    ".md-header",
                    ".md-footer",
                    ".md-tabs",
                ],
                "confidence": 0.53,
                "source": "preset_aiogram_dev",
                "notes": "MkDocs Material (aiogram docs).",
            },
        ),
        (
            lambda h: h in _mkdocs_hosts(),
            {
                "main_selector": "div.md-content, article",
                "title_selector": "h1",
                "strip_selectors": [".md-sidebar", ".md-footer", ".md-header"],
                "confidence": 0.5,
                "source": "preset_mkdocs",
            },
        ),
        (
            lambda h: h == "python.org" or h == "www.python.org",
            {
                "main_selector": "article, .main-content, .content-wrapper",
                "title_selector": "h1",
                "strip_selectors": [
                    "nav",
                    "footer",
                    "aside",
                    ".breadcrumbs",
                    ".sphinxsidebar",
                    ".related",
                    ".header",
                    ".footer",
                ],
                "confidence": 0.52,
                "source": "preset_python_org",
                "notes": "python.org / www: article + .main-content; Sphinx sidebar/breadcrumbs where present.",
            },
        ),
        (
            lambda h: h == "law.example.com" or h.endswith(".law.example.com"),
            {
                "main_selector": "main, article, .main, #content, .content",
                "title_selector": "h1",
                "strip_selectors": [
                    "nav",
                    "footer",
                    "header",
                    "aside",
                    "script",
                    "style",
                    ".sidebar",
                    ".menu",
                ],
                "confidence": 0.48,
                "source": "preset_pravo_by",
                "notes": "Национальный правовой портал РБ (вёрстка может отличаться по разделам).",
            },
        ),
        (
            lambda h: h == "law-archive.example.com" or h.endswith(".law-archive.example.com"),
            {
                # Карточка /document/: текст НПА в .Section1 внутри #userContent (не весь <main> — там шапка печати и тулбар).
                "main_selector": "#userContent .Section1",
                "title_selector": "title",
                "strip_selectors": [
                    "script",
                    "style",
                    "nav",
                    "footer",
                    "header",
                    "aside",
                    "#popup-search",
                    "#header",
                    "#burger-wrap",
                    "#burger-mobile-2",
                    ".search-detail-sticky-head",
                    "#docHeader",
                    "#docTitlePrint",
                    "#isSpecialTechnology",
                    ".callback-bt",
                    ".bg-remind",
                    "#scroll-top-button",
                    "iframe#ifmcontentstoprint",
                    ".explorer-warning-only",
                    ".sidebar",
                    ".md-sidebar",
                ],
                "confidence": 0.58,
                "source": "preset_etalonline",
                "notes": "ИПС ЭТАЛОН-ONLINE: страницы /document/ — #userContent .Section1; заголовок из <title>.",
            },
        ),
        (
            lambda h: h == "edu.example.com",
            {
                "main_selector": "#booklist, #book",
                "title_selector": "legend",
                "strip_selectors": [
                    "script",
                    "style",
                    ".alert",
                    ".ovwbtn",
                    "#letters-panel",
                    "nav",
                    "footer",
                ],
                "confidence": 0.5,
                "source": "preset_epadruchnik_adu",
                "notes": "Портал электронных учебников: таблица #booklist или карточка #book; PDF — AduPadruchnik.resolve_book / resolve_url.",
            },
        ),
    ]


def preset_recipe_for_host(hostname: str) -> Optional[Dict[str, Any]]:
    h = (hostname or "").lower().strip()
    if not h:
        return None

    for pred, raw in _preset_pairs():
        try:
            if pred(h):
                ok, rec, err = normalize_recipe(raw)
                if ok:
                    return dict(rec)
                logger.debug("[site_recipe_presets] skip %s: %s", raw.get("source"), err)
        except Exception:
            continue

    if h == "habr.com" or h.endswith(".habr.com"):
        ok, rec, _ = normalize_recipe(
            {
                "main_selector": ".tm-article-body",
                "title_selector": "h1",
                "strip_selectors": [
                    "script",
                    "style",
                    "nav",
                    "footer",
                    "aside",
                    ".tm-article-snippet",
                    ".tm-comment-form",
                    ".tm-article-presenter__header",
                    ".tm-article-presenter__meta",
                    ".tm-article-presenter__footer",
                    ".tm-comments-wrapper",
                ],
                "confidence": 0.55,
                "source": "preset_habr",
                "notes": "Habr article body.",
            }
        )
        return dict(rec) if ok else None

    return None
