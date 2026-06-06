"""Vision payload: локальный файл → data-url части для OpenRouter."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def vision_mime_from_path(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def vision_image_parts_for_brain(file_context: Dict[str, Any]) -> Optional[List[Tuple[str, str]]]:
    """Один кадр data-url для OpenRouter vision; None если файла нет или слишком большой."""
    if not isinstance(file_context, dict):
        return None
    if file_context.get("file_type") != "image":
        return None
    path = (file_context.get("local_path") or "").strip()
    if not path or file_context.get("error"):
        return None
    if not os.path.isfile(path):
        return None
    try:
        max_b = int(os.getenv("BRAIN_VISION_MAX_BYTES", "6000000"))
    except ValueError:
        max_b = 6_000_000
    try:
        sz = os.path.getsize(path)
    except OSError:
        return None
    if sz > max_b:
        logger.warning("[brain] vision skipped: image too large (%s bytes > %s)", sz, max_b)
        return None
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError as e:
        logger.warning("[brain] vision skipped: read failed: %s", e)
        return None
    return [(vision_mime_from_path(path), b64)]
