"""
Текстовый бандл промпта для проверок CI: каждое семейство tools (префикс до точки)
должно встречаться в инструкциях или в BRAIN_TOOL_FAMILY_SUPPLEMENT (constants).
"""
from __future__ import annotations

from typing import List


def brain_prompt_text_bundle_parts() -> List[str]:
    from core.brain import constants as C

    names = (
        "AGENT_INSTRUCTION",
        "AGENT_INSTRUCTION_PRIORITIZE_DIRECT",
        "AGENT_INSTRUCTION_SELF_EXTEND",
        "AGENT_INSTRUCTION_CHAT_CORE",
        "BRAIN_CAPABILITY_HONESTY",
        "BRAIN_INFRASTRUCTURE_HONESTY",
        "AGENT_DOMAIN_UKA_COMPACT",
        "AGENT_DOMAIN_LAW_COMPACT",
        "AGENT_DOMAIN_DOCUMENT_CORPUS_COMPACT",
        "AGENT_DOMAIN_ADU_COMPACT",
        "AGENT_DOMAIN_TASKSCOUT_COMPACT",
        "BRAIN_TOOL_FAMILY_SUPPLEMENT",
    )
    out: List[str] = []
    for n in names:
        v = getattr(C, n, None)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def brain_prompt_text_bundle() -> str:
    return "\n\n".join(brain_prompt_text_bundle_parts())
