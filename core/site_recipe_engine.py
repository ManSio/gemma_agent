"""
Декларативные «плагины» парсинга: JSON с CSS-селекторами на хост (без исполнения произвольного Python).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_RECIPE_VERSION = 1
_MAX_SELECTOR_LEN = 220
_MAX_STRIP = 24


def recipe_dir() -> str:
    return os.getenv("SITE_RECIPE_DIR", os.path.join("data", "site_recipes"))


def host_key(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
        return h or "unknown"
    except Exception as exc:
        logger.warning("[site_recipe] host_key parse failed: %s", exc)
        return "unknown"


def host_matches(url_or_host: str, domain: str) -> bool:
    """True if url_or_host's hostname equals domain or is a subdomain."""
    raw = (url_or_host or "").strip()
    d = (domain or "").lower().strip()
    if not d:
        return False
    h = host_key(raw) if "://" in raw or "/" in raw else raw.lower()
    return h == d or h.endswith("." + d)


def recipe_path_for_host(hostname: str) -> str:
    safe = re.sub(r"[^\w\.\-]", "_", hostname)[:200]
    os.makedirs(recipe_dir(), exist_ok=True)
    return os.path.join(recipe_dir(), f"{safe}.json")


def load_recipe(hostname: str) -> Optional[Dict[str, Any]]:
    path = recipe_path_for_host(hostname)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                ok, rec, err = normalize_recipe(raw)
                if ok:
                    out = dict(rec)
                    for k in ("stats", "version", "sample_url", "host"):
                        if k in raw and raw[k] is not None:
                            out[k] = raw[k]
                    return out
                logger.warning("[site_recipe] invalid recipe in %s: %s", path, err or "normalize failed")
                return raw
        except Exception as e:
            logger.warning("[site_recipe] load failed %s: %s", path, e)
    try:
        from core.site_recipe_presets import preset_recipe_for_host

        preset = preset_recipe_for_host(hostname)
        if preset:
            return dict(preset)
    except Exception as e:
        logger.debug("[site_recipe] preset: %s", e)
    return None


def save_recipe(hostname: str, recipe: Dict[str, Any]) -> bool:
    path = recipe_path_for_host(hostname)
    try:
        recipe = dict(recipe)
        recipe["version"] = _RECIPE_VERSION
        recipe["host"] = hostname
        prev = load_recipe(hostname)
        if prev and isinstance(prev.get("stats"), dict) and "stats" not in recipe:
            recipe["stats"] = prev["stats"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning("[site_recipe] save failed %s: %s", path, e)
        return False


def _sanitize_selector(sel: str) -> Tuple[bool, str]:
    s = (sel or "").strip()
    if not s or len(s) > _MAX_SELECTOR_LEN:
        return False, ""
    low = s.lower()
    for bad in ("javascript:", "url(", "@import", "expression(", "behavior:", "<script"):
        if bad in low:
            return False, ""
    # Разрешаем типичный подмножество CSS-селекторов (в т.ч. списки через запятую, [attr=name])
    if not re.match(r"^[a-zA-Z0-9_\-#.:\[\]=\s,\"'\*,>+~,]+$", s):
        return False, ""
    return True, s


def _strip_selector_inputs(raw: Dict[str, Any]) -> List[str]:
    """strip_selectors / strip (list) или remove (список либо одна строка с селекторами через запятую)."""
    out: List[str] = []
    val: Any = None
    for key in ("strip_selectors", "strip", "remove"):
        if key in raw and raw.get(key) is not None:
            val = raw.get(key)
            break
    if val is None:
        return out
    if isinstance(val, str):
        for part in val.split(","):
            p = part.strip()
            if p:
                out.append(p)
    elif isinstance(val, list):
        for x in val:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
    return out


def normalize_recipe(raw: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    main = str(raw.get("main_selector") or raw.get("main") or raw.get("content") or "").strip()
    ok, main_s = _sanitize_selector(main)
    if not ok:
        return False, {}, "invalid main_selector"

    title_sel = str(raw.get("title_selector") or raw.get("title") or "").strip()
    title_s = ""
    if title_sel:
        ok_t, title_s = _sanitize_selector(title_sel)
        if not ok_t:
            return False, {}, "invalid title_selector"

    strips: List[str] = []
    for x in _strip_selector_inputs(raw):
        ok_s, sx = _sanitize_selector(x)
        if ok_s and sx:
            strips.append(sx)
        if len(strips) >= _MAX_STRIP:
            break

    out: Dict[str, Any] = {
        "main_selector": main_s,
        "title_selector": title_s,
        "strip_selectors": strips or ["script", "style", "nav", "footer"],
        "notes": str(raw.get("notes") or "")[:500],
        "source": str(raw.get("source") or "unknown")[:64],
    }
    try:
        conf = float(raw.get("confidence", 0.5))
        out["confidence"] = max(0.0, min(1.0, conf))
    except Exception:
        out["confidence"] = 0.5
    if raw.get("extract_code") is False:
        out["extract_code"] = False
    return True, out, ""


def _max_code_block_chars() -> int:
    try:
        v = os.getenv("URL_FETCH_MAX_CODE_CHARS") or os.getenv("SITE_RECIPE_MAX_CODE_CHARS") or "8000"
        return max(200, int(v))
    except ValueError:
        return 8000


def _max_code_total_chars() -> int:
    try:
        return max(2000, int(os.getenv("SITE_RECIPE_MAX_CODE_TOTAL_CHARS", "50000")))
    except ValueError:
        return 50000


def _env_extract_code_enabled() -> bool:
    return os.getenv("SITE_RECIPE_EXTRACT_CODE", "true").strip().lower() in {"1", "true", "yes", "on"}


def _tag_classes_lower(tag: Any) -> str:
    c = tag.get("class") if tag else None
    if not c:
        return ""
    if isinstance(c, list):
        return " ".join(str(x) for x in c).lower()
    return str(c).lower()


def _pre_in_skipped_container(pre: Any) -> bool:
    p = pre
    for _ in range(14):
        if p is None:
            break
        tag = (p.name or "").lower()
        if tag in ("aside", "noscript", "iframe"):
            return True
        cls = _tag_classes_lower(p)
        pid = (p.get("id") or "").lower()
        if "comment" in cls or "comment" in pid:
            return True
        if "discussion" in cls or "sidebar" in cls:
            return True
        p = getattr(p, "parent", None)
    return False


def _lang_from_code_classes(classes: Any) -> str:
    if not classes:
        return ""
    seq = classes if isinstance(classes, list) else [classes]
    for c in seq:
        cs = str(c)
        if cs.startswith("language-"):
            return cs[9:][:48]
        if cs.startswith("lang-"):
            return cs[5:][:48]
    return ""


def format_code_blocks_append(html: str, prose_text: str) -> str:
    """Добавляет в конец блоки из <pre><code> (сырой HTML страницы)."""
    if not html or not _env_extract_code_enabled():
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    max_each = _max_code_block_chars()
    max_total = _max_code_total_chars()
    prose_compact = " ".join((prose_text or "").split())
    chunks: List[str] = []
    total = 0

    for pre in soup.find_all("pre"):
        if _pre_in_skipped_container(pre):
            continue
        code_el = pre.find("code")
        if code_el:
            lang = _lang_from_code_classes(code_el.get("class"))
            body = code_el.get_text()
        else:
            lang = ""
            body = pre.get_text()
        body = (body or "").strip()
        if len(body) < 2:
            continue
        if len(body) <= 400 and body in prose_compact:
            continue
        if len(body) > max_each:
            body = body[: max_each - 1] + "…"
        block = f"--- CODE BLOCK ({lang or 'text'}) ---\n{body}\n{'-' * 27}"
        if total + len(block) > max_total:
            break
        chunks.append(block)
        total += len(block)

    return "\n\n".join(chunks)


def apply_recipe(html: str, recipe: Dict[str, Any]) -> Tuple[str, str]:
    """Возвращает (title, main_text)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for sel in recipe.get("strip_selectors") or []:
        try:
            for tag in soup.select(sel):
                tag.decompose()
        except Exception as exc:
            logger.warning("[site_recipe] apply_recipe strip selector failed: %s | %s", sel, exc)
            continue

    title = ""
    ts = recipe.get("title_selector") or ""
    if ts:
        try:
            el = soup.select_one(ts)
            if el:
                title = el.get_text(" ", strip=True)
        except Exception as exc:
            logger.warning("[site_recipe] title selector %s failed: %s", ts, exc)
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    main = ""
    ms = recipe.get("main_selector") or "body"
    try:
        if "," in ms:
            chunks: List[str] = []
            seen_ids: set = set()
            for sel in [s.strip() for s in ms.split(",") if s.strip()]:
                for el in soup.select(sel):
                    eid = id(el)
                    if eid in seen_ids:
                        continue
                    seen_ids.add(eid)
                    chunks.append(el.get_text("\n", strip=True))
            main = "\n\n".join(c for c in chunks if c)
        else:
            node = soup.select_one(ms)
            if node:
                main = node.get_text("\n", strip=True)
    except Exception as exc:
        logger.warning("[site_recipe] main selector %s failed: %s", ms, exc)
    if not main:
        main = soup.get_text("\n", strip=True)

    main = re.sub(r"\n{3,}", "\n\n", main)
    if recipe.get("extract_code", True) is not False:
        extra = format_code_blocks_append(html, main)
        if extra:
            main = main.rstrip() + "\n\n" + extra
    return title[:500], main


def heuristic_recipe_from_html(html: str, hostname: str = "") -> Dict[str, Any]:
    """Без LLM: выбираем контейнер с максимумом текста среди типичных селекторов."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    h = (hostname or "").lower()
    habr_first: List[str] = []
    if host_matches(h, "habr.com"):
        habr_first = [
            ".tm-article-body",
            ".article-formatted-body",
            "[data-test-id=\"article-body\"]",
            "article .tm-article-body",
        ]
    candidates = habr_first + [
        "article",
        "main",
        '[role="main"]',
        ".tm-article-body",
        ".article-formatted-body",
        ".post__text",
        "#post-content",
        ".content",
        ".entry-content",
        "#content",
    ]
    best_sel = ""
    best_len = 0
    for sel in candidates:
        try:
            for el in soup.select(sel):
                t = el.get_text(" ", strip=True)
                if len(t) > best_len:
                    best_len = len(t)
                    best_sel = sel
        except Exception as exc:
            logger.warning("[site_recipe] heuristic recipe selector %s failed: %s", sel, exc)
            continue

    if best_sel and best_len > 200:
        ok, rec, _ = normalize_recipe(
            {
                "main_selector": best_sel,
                "title_selector": "h1",
                "strip_selectors": ["script", "style", "nav", "footer", "aside", "header"],
                "confidence": 0.45,
                "source": "heuristic",
            }
        )
        if ok:
            return rec

    ok, rec, _ = normalize_recipe(
        {
            "main_selector": "body",
            "title_selector": "title",
            "strip_selectors": ["script", "style", "nav", "footer"],
            "confidence": 0.25,
            "source": "heuristic_fallback",
        }
    )
    if ok:
        return rec
    return {
        "main_selector": "body",
        "title_selector": "",
        "strip_selectors": ["script", "style"],
        "confidence": 0.1,
        "source": "minimal",
    }


def parse_llm_recipe_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    # Снимаем markdown-ограждение
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None


def bump_recipe_stats(hostname: str, *, success: bool) -> None:
    rec = load_recipe(hostname)
    if not rec:
        return
    st = rec.get("stats") if isinstance(rec.get("stats"), dict) else {}
    if success:
        st["parse_ok"] = int(st.get("parse_ok", 0)) + 1
    else:
        st["parse_fail"] = int(st.get("parse_fail", 0)) + 1
    rec["stats"] = st
    save_recipe(hostname, rec)
