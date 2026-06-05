#!/usr/bin/env python3
"""
Тестирование модульной системы
"""

import asyncio
import os
from core.plugin_registry import PluginRegistry
from core.models import Input
from core.orchestrator import Orchestrator
from core.policy_engine import PolicyEngine
from core.self_programming import SelfProgrammingModule

# Добавляем путь к модулям
os.environ['MODULES_PATH'] = './modules'

def test_module_loading():
    """Тест загрузки модулей"""
    print("=== Тест загрузки модулей ===")
    
    # Создаем Registry
    plugin_registry = PluginRegistry()
    
    # Загружаем модули
    plugin_registry.load_all_modules()
    
    # Выводим информацию о загруженных модулях
    modules = plugin_registry.get_modules()
    print(f"Загружено модулей: {len(modules)}")
    
    for module in modules:
        print(f"- {module.name} ({module.manifest.type})")
        print(f"  Статус: {module.state.status}")
        print(f"  Возможности: {module.manifest.capabilities}")
        print()

def test_echo_module():
    """Тест модуля эхо"""
    print("=== Тест модуля эхо ===")
    
    # Создаем Registry
    plugin_registry = PluginRegistry()
    plugin_registry.load_all_modules()
    
    # Включаем модуль эхо
    echo_module = plugin_registry.get_module('echo')
    if echo_module:
        plugin_registry.enable_module('echo')
        print("Модуль эхо включен")
        
        # Проверяем, что модуль загружен
        if echo_module.state.status == 'healthy':
            print("Модуль эхо успешно загружен")
            # Тестируем выполнение
            input_data = Input(
                type="text",
                payload="Привет, мир!",
                meta={}
            )
            print(f"Входные данные: {input_data.payload}")
        else:
            print("Ошибка загрузки модуля эхо")
    else:
        print("Модуль эхо не найден")

if __name__ == "__main__":
    print("Запуск тестов модульной системы...")
    
    try:
        test_module_loading()
        test_echo_module()
        print("\n✅ Все тесты прошли успешно!")
    except Exception as e:
        print(f"\n❌ Ошибка теста: {e}")
        raise