"""Кнопки после успешного SelfProgramming.generate_module (тест slash + hot_install)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.telegram_inline_meta import META_KEY

logger = logging.getLogger(__name__)

PGEN_PREFIX = "pgen:"


def first_slash_from_commands(commands: Any) -> Optional[str]:
    if not isinstance(commands, list):
        return None
    for c in commands:
        if isinstance(c, dict):
            for key in ("trigger", "command", "name"):
                val = c.get(key)
                if isinstance(val, str) and val.strip():
                    t = val.strip()
                    return t if t.startswith("/") else f"/{t}"
        elif isinstance(c, str) and c.strip():
            t = c.strip()
            return t if t.startswith("/") else f"/{t}"
    return None


def build_post_module_gen_keyboard_rows(req: Dict[str, Any]) -> List[List[Dict[str, str]]]:
    folder = str(req.get("module_name") or "").strip()
    if not folder:
        return []
    cmd = first_slash_from_commands(req.get("commands"))
    if not cmd:
        return []
    cb_test = f"{PGEN_PREFIX}t:{folder}"
    cb_reload = f"{PGEN_PREFIX}r:{folder}"
    if len(cb_test.encode("utf-8")) > 64 or len(cb_reload.encode("utf-8")) > 64:
        logger.warning("post_module_gen_ui: callback too long for module %s", folder)
        return []
    return [
        [
            {"text": "🧪 Тест команды", "callback_data": cb_test},
            {"text": "♻️ В реестр", "callback_data": cb_reload},
        ]
    ]


def attach_post_module_gen_keyboard(context: Dict[str, Any], req: Dict[str, Any]) -> None:
    """Кладёт в context ключ для chat_orchestrator → Output.meta."""
    rows = build_post_module_gen_keyboard_rows(req)
    if not rows:
        return
    context[META_KEY] = rows


def first_slash_from_module_disk(modules_path: Path, folder: str) -> Optional[str]:
    p = modules_path / folder / "module.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("first_slash_from_module_disk %s: %s", p, e)
        return None
    return first_slash_from_commands(data.get("commands"))
