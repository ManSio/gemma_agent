"""
Gemma Agent Core - "Titanium Core"
Этот пакет содержит ядро модульной AI-платформы с титановым ядром и возможностью самопрограммирования
"""
from .models import Input, Output, Plan, PlanStep, ModuleState, SystemState
from .event_bus import bus
from .plugin_registry import PluginRegistry
from .orchestrator import Orchestrator
from .policy_engine import PolicyEngine
from .input_layer import InputLayer
from .self_programming import SelfProgrammingModule
from .self_healing import SelfHealingEngine
from .self_awareness import SelfAwareness, SelfAwarenessAPI
from .context_optimizer import ContextOptimizer
from .model_provider import LocalModelProvider
from .vision_layer import VisionLayer
from .emergency_mode import EmergencyMode
from .openrouter_provider import OpenRouterProvider

# ── Lazy-imported core facades (реализация в modules/*, импорт отложен) ──
# These are imported lazily to avoid circular imports (modules/*/module.py → core.models → core.__init__)
_LAZY_MODULES: dict = {
    "UserSystemModule": ".user_system",
    "PsychologyEngineModule": ".psychology_engine",
    "DigitalTwinModule": ".digital_twin",
    "GroupBehaviorModule": ".group_behavior",
    "PersonaEngineModule": ".persona_engine",
    "ScheduleModule": ".schedule_module",
    "BooksRAGModule": ".books_rag",
    "SecurityLayerModule": ".security_layer",
}


def __getattr__(name):
    if name in _LAZY_MODULES:
        import importlib
        mod = importlib.import_module(_LAZY_MODULES[name], __package__)
        cls = getattr(mod, name)
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)


__all__ = [
    'Input', 'Output', 'Plan', 'PlanStep', 'ModuleState', 'SystemState',
    'bus',
    'PluginRegistry', 'Orchestrator', 'PolicyEngine', 'InputLayer',
    'SelfProgrammingModule', 'SelfHealingEngine', 'SelfAwareness',
    'SelfAwarenessAPI', 'ContextOptimizer', 'LocalModelProvider',
    'VisionLayer', 'EmergencyMode',
    'UserSystemModule', 'PsychologyEngineModule', 'DigitalTwinModule',
    'GroupBehaviorModule', 'PersonaEngineModule',
    'ScheduleModule', 'BooksRAGModule', 'SecurityLayerModule',
    'OpenRouterProvider',
]
