"""Обратная совместимость: единая реализация в mem0_module."""
from .mem0_module import Mem0MemoryModule, load_mem0_config_from_env

__all__ = ["Mem0MemoryModule", "load_mem0_config_from_env"]
