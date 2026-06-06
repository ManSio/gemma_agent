"""Реестр плагина: SkillRegistry — core.brain."""
from __future__ import annotations

from typing import Any, Dict, List

from core.models import Output


class SkillsModule:
    """Маркер загрузки пакета skills для PluginRegistry."""

    def __init__(self) -> None:
        pass

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        return [
            Output(
                type="text",
                payload=(
                    "skills — пакет подсказок для мозга (HINT), не slash-модуль. "
                    "Навыки: modules.skills.registry + builtin_skills."
                ),
                meta={"module": "skills", "shell": True},
            )
        ]
