from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from core.plugin_registry import PluginRegistry

logger = logging.getLogger(__name__)

PREFIX = "mbtn:"


def encode_manifest_callback(module_key: str, button_name: str) -> str:
    """Telegram callback_data max 64 bytes."""
    raw = f"{PREFIX}{module_key}:{button_name}"
    if len(raw.encode("utf-8")) <= 64:
        return raw
    short = f"{PREFIX}{module_key}:{button_name}"[:60]
    return short


def merge_manifest_buttons_keyboards(
    registry: Any,
    module_keys: List[str],
    *,
    max_row: int = 3,
    max_total_buttons: int = 32,
) -> Optional[InlineKeyboardMarkup]:
    """
    Склеивает кнопки из нескольких модулей (по порядку), без дубликатов callback_data.
    Нужен для диалога: первый шаг плана почти всегда chat-orchestrator с пустым buttons,
    а кнопки лежат в math, echo, vision_ocr, …
    """
    if not module_keys:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    seen_cb: set[str] = set()
    count = 0
    for mkey in module_keys:
        if not mkey or mkey == "__fallback__":
            continue
        mod = None
        if hasattr(registry, "loaded_modules"):
            mod = registry.loaded_modules.get(mkey)
        if mod is None and hasattr(registry, "modules"):
            mod = registry.modules.get(mkey)
        if mod is None or not getattr(mod, "manifest", None):
            continue
        buttons = getattr(mod.manifest, "buttons", None)
        kb = manifest_buttons_keyboard(mkey, buttons, max_row=max_row)
        if not kb or not kb.inline_keyboard:
            continue
        for row in kb.inline_keyboard:
            out_row: List[InlineKeyboardButton] = []
            for btn in row:
                cb = btn.callback_data or ""
                if not cb or cb in seen_cb:
                    continue
                seen_cb.add(cb)
                out_row.append(btn)
                count += 1
                if count > max_total_buttons:
                    break
            if out_row:
                rows.append(out_row)
            if count > max_total_buttons:
                break
        if count > max_total_buttons:
            break
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manifest_buttons_keyboard(module_key: str, buttons: Any, *, max_row: int = 3) -> Optional[InlineKeyboardMarkup]:
    if not buttons or not isinstance(buttons, list):
        return None
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for b in buttons:
        if not isinstance(b, dict):
            continue
        label = str(b.get("label") or b.get("name") or "?").strip()[:64]
        name = str(b.get("name") or "").strip()
        if not name:
            continue
        try:
            cb = encode_manifest_callback(module_key, name)
            if len(cb.encode("utf-8")) > 64:
                logger.warning("manifest button callback too long: %s %s", module_key, name)
                continue
            row.append(InlineKeyboardButton(text=label, callback_data=cb))
            if len(row) >= max_row:
                rows.append(row)
                row = []
        except Exception as e:
            logger.debug("skip button %s: %s", b, e)
    if row:
        rows.append(row)
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def resolve_button_simulated_text(registry: Any, module_key: str, button_name: str) -> Optional[str]:
    """Текст, который нужно подставить в пайплайн (как сообщение пользователя)."""
    mod = None
    if hasattr(registry, "loaded_modules"):
        mod = registry.loaded_modules.get(module_key)
    if mod is None and hasattr(registry, "modules"):
        mod = registry.modules.get(module_key)
    if mod is None or not getattr(mod, "manifest", None):
        return None
    manifest = mod.manifest
    raw_buttons = getattr(manifest, "buttons", None) or []
    commands = getattr(manifest, "commands", None) or []

    for b in raw_buttons:
        if not isinstance(b, dict):
            continue
        if str(b.get("name") or "") != button_name:
            continue
        st = b.get("simulate_text")
        if isinstance(st, str) and st.strip():
            return st.strip()
        tr = b.get("trigger") or b.get("command")
        if isinstance(tr, str) and tr.strip():
            t = tr.strip()
            return t if t.startswith("/") else f"/{t}"

    for c in commands:
        if isinstance(c, dict):
            nm = str(c.get("name") or "")
            if nm == button_name or nm.upper() == button_name.upper():
                tr = c.get("trigger") or c.get("command") or c.get("name")
                if isinstance(tr, str) and tr.strip():
                    t = tr.strip()
                    return t if t.startswith("/") else f"/{t}"
        elif isinstance(c, str) and c.strip():
            t = c.strip()
            return t if t.startswith("/") else f"/{t}"

    return None


def parse_mbtn_callback(data: str) -> Optional[tuple[str, str]]:
    if not data.startswith(PREFIX):
        return None
    rest = data[len(PREFIX) :]
    if ":" not in rest:
        return None
    module_key, button_name = rest.split(":", 1)
    if not module_key or not button_name:
        return None
    return module_key, button_name
