"""
Контроллер плагинов: политика маршрутизации и склейка контента для мозга.

PluginRegistry остаётся источником истины по загрузке/enable; контроллер добавляет
слой «что разрешено к исполнению и подсказкам» без правок каждого модуля.

Отключить отдельные плагины от маршрутизации и manifest prompts:
  PLUGIN_CONTROLLER_DENYLIST=heavy_plugin,other
(имена как в module.json name, без учёта регистра)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from core.plugin_registry import ModuleInstance, PluginRegistry


class PluginController:
    def __init__(self, registry: PluginRegistry):
        self._registry = registry
        self._deny_lc: Optional[frozenset[str]] = None

    @property
    def registry(self) -> PluginRegistry:
        return self._registry

    def _denylist_lower(self) -> frozenset[str]:
        if self._deny_lc is None:
            raw = (os.getenv("PLUGIN_CONTROLLER_DENYLIST") or "").strip()
            self._deny_lc = frozenset(x.strip().lower() for x in raw.split(",") if x.strip())
        return self._deny_lc

    def is_routable(self, module_name: str) -> bool:
        """Можно ли направлять на плагин запрос (slash / intent)."""
        n = (module_name or "").strip().lower()
        return bool(n) and n not in self._denylist_lower()

    def filter_module_keys(self, keys: Set[str]) -> Set[str]:
        d = self._denylist_lower()
        if not d:
            return set(keys)
        return {k for k in keys if (k or "").strip().lower() not in d}

    def format_manifest_prompts_for_brain(self) -> str:
        from core.plugin_prompts import format_plugin_prompts_for_brain

        return format_plugin_prompts_for_brain(
            self._registry,
            module_filter=self.is_routable,
        )

    def snapshot_loaded(self) -> Dict[str, Any]:
        """Снимок для диагностики и админки (без секретов)."""
        rows: List[Dict[str, Any]] = []
        deny = self._denylist_lower()
        for name in sorted(self._registry.loaded_modules.keys()):
            inst = self._registry.loaded_modules.get(name)
            if inst is None:
                continue
            manifest = getattr(inst, "manifest", None)
            st = getattr(inst, "state", None)
            nl = (name or "").strip().lower()
            rows.append(
                {
                    "name": name,
                    "routable": nl not in deny,
                    "status": getattr(st, "status", None),
                    "type": getattr(manifest, "type", None) if manifest else None,
                    "commands": list(manifest.iter_command_tokens()) if manifest else [],
                    "capabilities": list(getattr(manifest, "capabilities", []) or []) if manifest else [],
                }
            )
        return {
            "denylist": sorted(deny),
            "loaded_count": len(rows),
            "modules": rows,
        }

    def iter_loaded_routable(self) -> List[tuple[str, ModuleInstance]]:
        """Пары (имя, инстанс) только для плагинов не из denylist."""
        out: List[tuple[str, ModuleInstance]] = []
        for name, inst in self._registry.loaded_modules.items():
            if self.is_routable(name):
                out.append((name, inst))
        return out
