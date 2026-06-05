"""
RAG Module - Модуль памяти и семантического поиска
"""
from typing import Any, Dict, List
from core.models import Output
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class RAGModule:
    """Модуль для работы с памятью и семантическим поиском"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Инициализация модуля"""
        self.config = config or {}
        self.database_path = self.config.get("database_path", "./data/rag")
        self.memory_file = os.path.join(self.database_path, "memory.json")
        self._ensure_database_exists()
    
    def _ensure_database_exists(self):
        """Обеспечить существование базы данных"""
        os.makedirs(self.database_path, exist_ok=True)
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
    
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        """Основной метод выполнения"""
        input_data = args.get("input", {})
        payload = input_data.get("payload", "")
        
        if payload.startswith("/store "):
            # Хранение текста
            content = payload[7:].strip()
            self._store_content(content)
            return [Output(
                type="text",
                payload=f"Содержимое сохранено: {content[:50]}...",
                meta={"module": "rag", "action": "store"}
            )]
        elif payload.startswith("/search "):
            # Поиск по контенту
            query = payload[8:].strip()
            results = self._search_content(query)
            if results:
                return [Output(
                    type="text",
                    payload=f"Результаты поиска:\n{chr(10).join(results)}",
                    meta={"module": "rag", "action": "search", "query": query}
                )]
            else:
                return [Output(
                    type="text",
                    payload="Ничего не найдено",
                    meta={"module": "rag", "action": "search", "query": query}
                )]
        elif payload.startswith("/clear"):
            # Очистка памяти
            self._clear_memory()
            return [Output(
                type="text",
                payload="Память очищена",
                meta={"module": "rag", "action": "clear"}
            )]
        else:
            return [Output(
                type="text",
                payload="Команды:\n/store <текст> - сохранить\n/search <запрос> - найти\n/clear - очистить",
                meta={"module": "rag"}
            )]
    
    def _store_content(self, content: str):
        """Сохранить контент в память"""
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                memory = json.load(f)
            
            memory.append({
                "content": content,
                "timestamp": datetime.now().isoformat()
            })
            
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(memory, f)
        except Exception as e:
            logger.exception("[rag] save_content failed")
    
    def _search_content(self, query: str) -> List[str]:
        """Поиск контента по запросу"""
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                memory = json.load(f)
            
            # Простой поиск по содержимому (в реальном приложении здесь будет семантический поиск)
            results = []
            for item in memory:
                if query.lower() in item["content"].lower():
                    results.append(item["content"])
            
            return results[:5]  # Возвращаем максимум 5 результатов
        except Exception as e:
            logger.exception("[rag] search_content failed")
            return []
    
    def _clear_memory(self):
        """Очистить память"""
        try:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
        except Exception as e:
            logger.exception("[rag] clear_memory failed")