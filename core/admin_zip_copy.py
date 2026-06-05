"""Копия админских ZIP в data/tools — чтобы /zip_read bundle.json работал без ручного сохранения."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def admin_diagnostic_copy_to_tools_enabled() -> bool:
    raw = (os.getenv("ADMIN_DIAGNOSTIC_COPY_TO_TOOLS") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def copy_admin_zip_to_data_tools(zbytes: bytes, fname: str) -> str | None:
    """
    Пишет байты в data/tools/<fname>. Возвращает posix-путь при успехе, иначе None.
    """
    if not admin_diagnostic_copy_to_tools_enabled():
        return None
    fn = (fname or "").strip().replace("\\", "/").split("/")[-1]
    if not fn or ".." in fn:
        return None
    tools_dir = Path("data/tools")
    try:
        tools_dir.mkdir(parents=True, exist_ok=True)
        target = tools_dir / fn
        target.write_bytes(zbytes)
        return target.as_posix()
    except OSError as e:
        logger.warning("copy_admin_zip_to_data_tools: %s", e)
        return None
