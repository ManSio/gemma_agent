from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class SkillResult:
    result: Dict[str, Any]
    hint: str


class Skill:
    name: str = "base_skill"

    async def run(
        self,
        *,
        intent: str,
        user_text: str,
        context: Dict[str, Any],
        user_facts: Dict[str, Any],
        digital_twin: Dict[str, Any],
    ) -> SkillResult:
        return SkillResult(result={}, hint="")
