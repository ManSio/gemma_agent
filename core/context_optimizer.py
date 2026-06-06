"""
Context Optimizer - Оптимизатор контекста
"""
import re
from typing import List, Dict, Any
from datetime import datetime
import hashlib

class ContextOptimizer:
    """Оптимизатор контекста для сжатия и кэширования текстов"""
    
    def __init__(self, max_tokens: int = 10000):
        """Инициализация оптимизатора"""
        self.max_tokens = max_tokens
        self.semantic_cache = {}
        self.message_history = []  # История сообщений
    
    def compress_context(self, context: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Сжать контекст, удалив дубликаты и сократив текст"""
        # Удаление дубликатов
        unique_messages = []
        seen_texts = set()
        
        for msg in context:
            # Создаем хеш для проверки дубликатов
            text_hash = hashlib.md5(msg.get('text', '').encode('utf-8')).hexdigest()
            if text_hash not in seen_texts:
                seen_texts.add(text_hash)
                unique_messages.append(msg)
        
        # Сокращение текстов, если контекст слишком длинный
        if len(unique_messages) > 0:
            # Кластеризация сообщений (упрощенная версия)
            clustered_messages = self._cluster_messages(unique_messages)
            return {
                "compressed": True,
                "original_count": len(context),
                "unique_count": len(unique_messages),
                "clustered_count": len(clustered_messages),
                "messages": clustered_messages[:self.max_tokens]
            }
        
        return {
            "compressed": False,
            "original_count": len(context),
            "unique_count": 0,
            "clustered_count": 0,
            "messages": []
        }
    
    def _cluster_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Кластеризация сообщений по смыслу"""
        # Упрощенная версия - группировка по длинным и коротким сообщениям
        clusters = {}
        
        for msg in messages:
            # Простая классификация
            text = msg.get('text', '')
            if len(text) < 10:
                cluster_key = "short"
            elif len(text) < 50:
                cluster_key = "medium"
            else:
                cluster_key = "long"
            
            if cluster_key not in clusters:
                clusters[cluster_key] = []
            clusters[cluster_key].append(msg)
        
        # Возвращаем отфильтрованный набор (для каждого кластера берём среднее)
        result = []
        for cluster_key, msgs in clusters.items():
            # Берём среднее сообщение из кластера
            if len(msgs) > 0:
                result.append(msgs[0])  # В реальной реализации здесь будет более сложная агрегация
        
        return result
    
    def add_to_cache(self, query: str, response: str):
        """Добавить в семантический кэш"""
        # Создаем ключ на основе хеша запроса
        query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()
        self.semantic_cache[query_hash] = {
            "query": query,
            "response": response,
            "timestamp": datetime.now().isoformat()
        }
    
    def get_from_cache(self, query: str) -> str:
        """Получить из семантического кэша"""
        # Ищем похожие запросы (упрощенная реализация)
        query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()
        if query_hash in self.semantic_cache:
            return self.semantic_cache[query_hash]["response"]
        
        # Поиск похожих запросов
        for stored_query_hash, cache_item in self.semantic_cache.items():
            if self._is_similar(query, cache_item["query"]):
                return cache_item["response"]
        
        return None
    
    def _is_similar(self, text1: str, text2: str) -> bool:
        """Проверить на схожесть (упрощенная реализация)"""
        # Сравниваем только по наличию общих слов
        words1 = set(re.findall(r'\w+', text1.lower()))
        words2 = set(re.findall(r'\w+', text2.lower()))
        
        if len(words1) == 0 or len(words2) == 0:
            return False
        
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        similarity = intersection / union if union > 0 else 0
        
        return similarity > 0.3  # 30% порог схожести
    
    def replace_context_with_rag(self, context: List[Dict[str, Any]], rag_content: str) -> List[Dict[str, Any]]:
        """Заменить длинный контекст на RAG-данные"""
        # Если история слишком длинная, заменяем на RAG-контент
        if len(str(context)) > self.max_tokens:
            return [{
                "type": "rag",
                "content": rag_content,
                "source": "context_optimizer"
            }]
        
        # В противном случае возвращаем оригинальный контекст
        return context