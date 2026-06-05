"""
Единая реализация движка персонажей (хранение, команды, контекст оркестратора).

Раньше здесь была заглушка: оркестратор не видел персона из data/user_personas.json.
"""
from modules.persona_engine.module import PersonaEngineModule

__all__ = ["PersonaEngineModule"]
