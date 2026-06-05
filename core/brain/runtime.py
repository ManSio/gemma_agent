"""Синглтоны провайдеров и памяти, общие для конвейера brain."""
from __future__ import annotations

from typing import Optional

from core.openrouter_provider import get_openrouter_provider
from core.mem0_memory.mem0_module import Mem0MemoryModule, load_mem0_config_from_env
from modules.skills.registry import SkillRegistry
from modules.skills.builtin_skills import default_skills
from modules.skills.image_skill import ImageSkill
from modules.persona_engine.module import PersonaEngineModule
from core.digital_twin import DigitalTwinModule
from modules.external_apis.service import ExternalAPIService

_llm = get_openrouter_provider()
_memory = Mem0MemoryModule(load_mem0_config_from_env())
_persona = PersonaEngineModule()
_twin = DigitalTwinModule()
_skills = SkillRegistry()
for _s in default_skills():
    _skills.register(_s)
_skills.register(ImageSkill())
_skills.load_persisted_enabled()
_external_apis = ExternalAPIService()


def get_memory() -> Mem0MemoryModule:
    """Текущий Mem0 (после configure_brain_memory — тот же, что у orchestrator)."""
    return _memory


def configure_brain_memory(mem0: Optional[Mem0MemoryModule] = None) -> None:
    """Один экземпляр Mem0 с orchestrator/input_layer (важно для облака и _pending_user)."""
    global _memory
    if mem0 is not None:
        _memory = mem0
