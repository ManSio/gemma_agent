"""
Тексты из module.json → поле prompts: попадают в контекст мозга (plugin_manifest_prompts).

Так плагин может нести свои инструкции в манифесте; ядро только склеивает блок.
При отключении/удалении модуля строки из реестра не собираются.
"""

from __future__ import annotations

import os
from typing import Any, Callable, List, Optional


def _max_chars() -> int:
    try:
        return int((os.getenv("BRAIN_PLUGIN_MANIFEST_PROMPTS_MAX_CHARS") or "6000").strip())
    except ValueError:
        return 6000


def format_plugin_prompts_for_brain(
    registry: Any,
    *,
    module_filter: Optional[Callable[[str], bool]] = None,
) -> str:
    """
    Собрать непустые manifest.prompts загруженных плагинов.
    registry — PluginRegistry с .loaded_modules: name -> ModuleInstance.
    module_filter — если задан, пропускать плагины, для которых filter(name) == False.
    """
    max_total = _max_chars()
    if max_total <= 0:
        return ""

    loaded = getattr(registry, "loaded_modules", None)
    if not isinstance(loaded, dict) or not loaded:
        return ""

    sections: List[str] = []
    used = 0
    for key in sorted(loaded.keys()):
        if module_filter is not None and not module_filter(str(key)):
            continue
        inst = loaded.get(key)
        if inst is None:
            continue
        manifest = getattr(inst, "manifest", None)
        if manifest is None:
            continue
        raw = getattr(manifest, "prompts", None)
        if not isinstance(raw, dict) or not raw:
            continue
        pieces: List[str] = []
        mod_name = str(getattr(manifest, "name", key) or key)
        for pk, pv in sorted(raw.items()):
            pks = str(pk).strip()
            pvs = str(pv).strip() if pv is not None else ""
            if not pks or not pvs:
                continue
            pieces.append(f"**{pks}**:\n{pvs}")
        if not pieces:
            continue
        block = f"### plugin:{mod_name}\n" + "\n\n".join(pieces)
        sep = 2 if sections else 0
        if used + sep + len(block) > max_total:
            remain = max_total - used - sep - 40
            if remain < 120:
                break
            block = block[:remain] + "\n…"
        sections.append(block)
        used += sep + len(block)

    return "\n\n".join(sections)
