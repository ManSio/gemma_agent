"""Resolve file paths under a trusted base directory (CodeQL path-injection guard)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_under(base: str | os.PathLike[str], *parts: str) -> str:
    """Return absolute path under base; raise ValueError on traversal."""
    root = Path(base).resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning("safe_paths rejected traversal: base=%s parts=%s", root, parts)
        raise ValueError("Path escapes trusted base directory")
    return str(candidate)
