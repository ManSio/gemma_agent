"""Реестр плагина: HTTP-клиенты и сервис — core.brain (singleton)."""
from __future__ import annotations

from typing import Any, Dict, List

from core.models import Output


class ExternalApisModule:
    """Маркер загрузки пакета external_apis для PluginRegistry."""

    def __init__(self) -> None:
        pass

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        return [
            Output(
                type="text",
                payload=(
                    "external_apis — пакет без slash-команд. "
                    "Погода и HTTP: core.brain / modules.external_apis.clients."
                ),
                meta={"module": "external_apis", "shell": True},
            )
        ]
