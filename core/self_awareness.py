"""
Self-Awareness Layer - Слой самосознания системы
"""
from typing import Dict, Any, List
from core.models import SystemState, ModuleState
from core.plugin_registry import PluginRegistry
from core.event_bus import bus
import asyncio
import json

class SelfAwareness:
    """Слой самосознания системы"""
    
    def __init__(self, plugin_registry: PluginRegistry):
        """Инициализация"""
        self.plugin_registry = plugin_registry
        self.dependency_graph = {}
        self.system_errors = []
        self._build_dependency_graph()
    
    def _build_dependency_graph(self):
        """Построить граф зависимостей"""
        # В реальной реализации здесь будет анализ зависимостей модулей
        modules = self.plugin_registry.get_modules()
        for module in modules:
            # Здесь будет анализ связей между модулями
            self.dependency_graph[module.name] = {
                "type": module.manifest.type,
                "depends_on": [],
                "required_by": []
            }
    
    async def get_system_state(self) -> SystemState:
        """Получить текущее состояние системы"""
        return self.plugin_registry.get_system_state()
    
    async def get_modules_info(self) -> List[Dict[str, Any]]:
        """Получить информацию о модулях"""
        modules = self.plugin_registry.get_modules()
        return [
            {
                "name": module.name,
                "type": module.manifest.type,
                "status": module.state.status,
                "capabilities": module.manifest.capabilities,
                "input_types": module.manifest.input_types,
                "output_types": module.manifest.output_types
            }
            for module in modules
        ]
    
    async def get_dependencies(self) -> Dict[str, Any]:
        """Получить граф зависимостей"""
        return self.dependency_graph
    
    async def get_errors(self) -> List[Dict[str, Any]]:
        """Получить информацию об ошибках"""
        modules = self.plugin_registry.get_modules()
        errors = []
        for module in modules:
            if module.state.last_error:
                errors.append({
                    "module": module.name,
                    "error": module.state.last_error,
                    "timestamp": module.state.last_check.isoformat()
                })
        return errors
    
    def get_module_info(self, module_name: str) -> Dict[str, Any]:
        """Получить информацию о конкретном модуле"""
        module = self.plugin_registry.get_module(module_name)
        if module:
            return {
                "name": module.name,
                "type": module.manifest.type,
                "status": module.state.status,
                "last_error": module.state.last_error,
                "config": module.config,
                "capabilities": module.manifest.capabilities,
                "input_types": module.manifest.input_types,
                "output_types": module.manifest.output_types
            }
        return None
    
    async def generate_system_report(self) -> Dict[str, Any]:
        """Сгенерировать отчет о системе"""
        return {
            "system_state": await self.get_system_state(),
            "modules": await self.get_modules_info(),
            "dependencies": await self.get_dependencies(),
            "errors": await self.get_errors(),
            "timestamp": asyncio.get_event_loop().time()
        }

# Добавим API для доступа к данным самосознания
class SelfAwarenessAPI:
    """API для доступа к данным самосознания"""
    
    def __init__(self, self_awareness: SelfAwareness):
        self.self_awareness = self_awareness
    
    async def system_state(self) -> SystemState:
        """Получить состояние системы"""
        return await self.self_awareness.get_system_state()
    
    async def modules(self) -> List[Dict[str, Any]]:
        """Получить список модулей"""
        return await self.self_awareness.get_modules_info()
    
    async def dependencies(self) -> Dict[str, Any]:
        """Получить зависимости"""
        return await self.self_awareness.get_dependencies()
    
    async def errors(self) -> List[Dict[str, Any]]:
        """Получить ошибки"""
        return await self.self_awareness.get_errors()
    
    async def module_info(self, module_name: str) -> Dict[str, Any]:
        """Получить информацию о модуле"""
        return self.self_awareness.get_module_info(module_name)