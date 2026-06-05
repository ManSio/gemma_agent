"""
Кэш эффективного рецепта по (hostname + отпечаток HTML): меньше эвристик при повторном том же документе.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def cache_dir() -> Path:
    p = Path(os.getenv("SITE_RECIPE_CACHE_DIR", os.path.join("data", "site_recipe_cache")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def html_fingerprint(html: str, limit: int = 500_000) -> str:
    chunk = (html or "")[:limit].encode("utf-8", errors="ignore")
    return hashlib.sha256(chunk).hexdigest()


def _safe_host(hostname: str) -> str:
    return re.sub(r"[^\w\.\-]", "_", (hostname or "unknown").lower())[:200]


def cache_path(hostname: str) -> Path:
    return cache_dir() / f"{_safe_host(hostname)}.json"


def cache_ttl_sec() -> int:
    try:
        return max(60, int(os.getenv("SITE_RECIPE_CACHE_TTL_SEC", str(7 * 24 * 3600))))
    except ValueError:
        return 7 * 24 * 3600


def cache_enabled() -> bool:
    return os.getenv("SITE_RECIPE_CACHE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def cache_skip() -> bool:
    return os.getenv("SITE_RECIPE_CACHE_SKIP", "").strip().lower() in {"1", "true", "yes", "on"}


def cache_get(hostname: str, html_hash: str) -> Optional[Dict[str, Any]]:
    if not cache_enabled() or cache_skip():
        return None
    path = cache_path(hostname)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("[site_recipe_cache] read %s: %s", path, e)
        return None
    if data.get("html_hash") != html_hash:
        return None
    try:
        saved = float(data.get("saved_at", 0))
    except (TypeError, ValueError):
        saved = 0
    if time.time() - saved > cache_ttl_sec():
        return None
    rec = data.get("recipe")
    if isinstance(rec, dict) and rec.get("main_selector"):
        return dict(rec)
    return None


def cache_set(hostname: str, html_hash: str, recipe: Dict[str, Any], sample_url: str = "") -> None:
    if not cache_enabled() or cache_skip():
        return
    path = cache_path(hostname)
    try:
        payload = {
            "html_hash": html_hash,
            "saved_at": time.time(),
            "recipe": dict(recipe),
            "sample_url": (sample_url or "")[:500],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[site_recipe_cache] write %s: %s", path, e)


def cache_invalidate(hostname: str) -> None:
    path = cache_path(hostname)
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        logger.debug("[site_recipe_cache] unlink %s: %s", path, e)
