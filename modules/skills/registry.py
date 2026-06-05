from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from modules.skills.skill_interface import Skill

logger = logging.getLogger(__name__)
_ENABLED_LOCK = threading.Lock()

_ENABLE_TOKENS = frozenset(
    {"on", "enable", "enabled", "true", "1", "вкл", "включить", "включен", "включён"}
)
_DISABLE_TOKENS = frozenset(
    {"off", "disable", "disabled", "false", "0", "выкл", "выключить", "выключен", "выключён"}
)


def parse_skill_toggle_args(raw: str) -> Tuple[str, Optional[bool]]:
    """
    «translator enabled» → (translator, True); «translator» → (translator, None) — toggle.
    """
    parts = (raw or "").strip().split()
    if not parts:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    last = parts[-1].lower()
    if last in _ENABLE_TOKENS:
        return " ".join(parts[:-1]).strip(), True
    if last in _DISABLE_TOKENS:
        return " ".join(parts[:-1]).strip(), False
    return (raw or "").strip(), None


def _enabled_state_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "skill_registry_enabled.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._enabled: Dict[str, bool] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        self._enabled.setdefault(skill.name, True)

    def load_persisted_enabled(self) -> None:
        """Восстановить on/off после рестарта (data/runtime/skill_registry_enabled.json)."""
        path = _enabled_state_path()
        if not path.is_file():
            return
        try:
            with _ENABLED_LOCK:
                raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("skill_registry enabled load: %s", e)
            return
        if not isinstance(raw, dict):
            return
        for name, flag in raw.items():
            if name in self._skills and isinstance(flag, bool):
                self._enabled[name] = flag

    def _persist_enabled(self) -> None:
        path = _enabled_state_path()
        payload = {k: bool(self._enabled.get(k, True)) for k in self._skills}
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with _ENABLED_LOCK:
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(path)
        except OSError as e:
            logger.warning("skill_registry enabled save failed: %s", e)

    def get(self, name: str) -> Optional[Skill]:
        if not self._enabled.get(name, True):
            return None
        return self._skills.get(name)

    def all_enabled(self) -> Dict[str, Skill]:
        return {k: v for k, v in self._skills.items() if self._enabled.get(k, True)}

    def toggle(self, name: str) -> bool:
        if name not in self._skills:
            raise KeyError(name)
        self._enabled[name] = not self._enabled.get(name, True)
        self._persist_enabled()
        return self._enabled[name]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        if name not in self._skills:
            raise KeyError(name)
        self._enabled[name] = bool(enabled)
        self._persist_enabled()
        return self._enabled[name]

    def status(self) -> Dict[str, bool]:
        return {k: self._enabled.get(k, True) for k in self._skills.keys()}
