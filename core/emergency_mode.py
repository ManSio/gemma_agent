"""
Emergency Mode - Аварийный режим ядра
"""
from typing import List, Dict, Any
from core.models import SystemState, ModuleState
from core.plugin_registry import PluginRegistry
import asyncio

class EmergencyMode:
    """Класс управления аварийным режимом"""
    
    def __init__(self, plugin_registry: PluginRegistry, orchestrator=None):
        self.plugin_registry = plugin_registry
        self.orchestrator = orchestrator
        self.fallback_model = None  # Будет использоваться в случае падения моделей
        
    def activate_emergency_mode(self) -> SystemState:
        """Активировать аварийный режим"""
        # Отключаем все модули кроме основных
        modules = self.plugin_registry.get_modules()
        emergency_state = SystemState(
            mode="emergency",
            modules=[],
            resources={}
        )
        
        # Включаем только базовые модули
        for module in modules:
            if module.manifest.type in ["input", "tool"]:
                # Включаем модуль
                module.state.status = "healthy"
                emergency_state.modules.append(module.state)
            else:
                # Отключаем остальные
                module.state.status = "disabled"
                # Здесь не добавляем в список, чтобы модуль был отключен
        
        return emergency_state
    
    def deactivate_emergency_mode(self) -> SystemState:
        """Деактивировать аварийный режим"""
        # Восстанавливаем полное состояние
        return self.plugin_registry.get_system_state()
    
    def get_fallback_model(self) -> str:
        """Получить модель для аварийного режима"""
        # Возвращаем имя маленькой локальной модели 
        return "gemma-2b-it"  # Пример названия аварийной модели
    
    def check_emergency_conditions(self) -> bool:
        """Проверить условия для перехода в аварийный режим"""
        modules = self.plugin_registry.get_modules()
        healthy_count = sum(1 for m in modules if m.state.status == "healthy")
        total_count = len(modules)
        
        # Если меньше 30% модулей здоровы, активируем аварийный режим
        if total_count > 0 and healthy_count / total_count < 0.3:
            return True
        return False
    
    def handle_module_failure(self, module_name: str):
        """Обработка отказа модуля"""
        # Проверяем, нужно ли активировать аварийный режим
        if self.check_emergency_conditions():
            self.activate_emergency_mode()
            # Логируем событие
            if self.orchestrator:
                self.orchestrator.log(f"Emergency mode activated due to module failure: {module_name}")
            else:
                print(f"Emergency mode activated due to module failure: {module_name}")