"""
Books RAG Module - Книжный RAG
"""
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

from core.models import Output
from core.user_facing_plain import format_books_search_plain
from core.qdrant_rag import QdrantBooksIndex, chunk_book_text

logger = logging.getLogger(__name__)


def _slash_cmd_and_rest(payload: str) -> tuple[str, str]:
    """Как core.light_slash.parse_slash_args, но без импорта из core.* (избегаем цикла с brain.runtime)."""
    p = (payload or "").strip()
    if not p.startswith("/"):
        return "", p
    sp = p.split(maxsplit=1)
    head = sp[0].lstrip("/").split("@")[0].lower()
    tail = sp[1].strip() if len(sp) > 1 else ""
    return head, tail


def _text_from_document_intake(context: Any) -> str:
    if not isinstance(context, dict):
        return ""
    doc = context.get("document_intake")
    if not isinstance(doc, dict) or not doc.get("ok"):
        return ""
    if doc.get("text_layer_empty"):
        return ""
    return str(doc.get("text") or "").strip()


class BooksRAGModule:
    """Книжный RAG для работы с книгами"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Инициализация модуля"""
        self.config = config or {}
        self.storage_path = self.config.get("storage_path", "./data/books")
        self.library_path = self.config.get("library_path", "./data/library")
        self.books_file = os.path.join(self.storage_path, "books.json")
        self.index_file = os.path.join(self.storage_path, "index.json")
        self._qdrant = QdrantBooksIndex()
        self._ensure_storage_exists()
    
    def _ensure_storage_exists(self):
        """Обеспечить существование хранилища"""
        os.makedirs(self.storage_path, exist_ok=True)
        os.makedirs(self.library_path, exist_ok=True)
        if not os.path.exists(self.books_file):
            with open(self.books_file, 'w', encoding='utf-8') as f:
                json.dump({}, f)
        if not os.path.exists(self.index_file):
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump({}, f)
    
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        """Основной метод выполнения"""
        input_data = args.get("input", {})
        payload = input_data.get("payload", "")
        context = args.get("context") or {}

        cmd, rest = _slash_cmd_and_rest(str(payload or ""))
        if cmd == "add_book":
            rest = (rest or "").strip()
            if not rest:
                return [
                    Output(
                        type="text",
                        payload=(
                            "Добавление книги:\n"
                            "• Отправьте файл (PDF/DOCX) с подписью `/add_book Название` — текст возьмётся из файла.\n"
                            "• Или: `/add_book Название` и далее через пробел вставленный текст.\n"
                            "Поиск: `/search_book Название запрос`"
                        ),
                        meta={"module": "books_rag"},
                    )
                ]
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                book_title, book_content = parts[0], parts[1]
                success = await self.add_book(book_title, book_content)
                if success:
                    return [
                        Output(
                            type="text",
                            payload=f"Книга '{book_title}' успешно добавлена",
                            meta={"module": "books_rag", "action": "add_book"},
                        )
                    ]
                return [
                    Output(
                        type="text",
                        payload=f"Ошибка добавления книги '{book_title}'",
                        meta={"module": "books_rag", "action": "add_book", "error": "failed"},
                    )
                ]
            book_title = parts[0]
            body = _text_from_document_intake(context)
            if body:
                success = await self.add_book(book_title, body)
                if success:
                    return [
                        Output(
                            type="text",
                            payload=f"Книга '{book_title}' добавлена из вложения ({len(body)} симв. текста).",
                            meta={"module": "books_rag", "action": "add_book", "source": "attachment"},
                        )
                    ]
                return [
                    Output(
                        type="text",
                        payload=f"Ошибка добавления книги '{book_title}'",
                        meta={"module": "books_rag", "action": "add_book", "error": "failed"},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload=(
                        f"Нет текста для «{book_title}»: пришлите документ с этой же командой в подписи "
                        f"или укажите содержимое: `/add_book {book_title} <текст>`"
                    ),
                    meta={"module": "books_rag"},
                )
            ]
        if cmd == "search_book":
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                book_title = parts[0]
                query = parts[1]
                results = await self.search_book(book_title, query)
                return [Output(
                    type="text",
                    payload=format_books_search_plain(results),
                    meta={"module": "books_rag", "action": "search_book"}
                )]
            else:
                return [Output(
                    type="text",
                    payload="Использование: /search_book <title> <query>",
                    meta={"module": "books_rag"}
                )]
        return [
            Output(
                type="text",
                payload=(
                    "Команды books_rag:\n"
                    "/add_book — справка; с файлом в подписи: `/add_book Название`\n"
                    "/add_book Название фрагмент_текста…\n"
                    "/search_book Название запрос"
                ),
                meta={"module": "books_rag"},
            )
        ]
    
    async def add_book(self, title: str, content: str) -> bool:
        """Добавить книгу в библиотеку"""
        try:
            # Сохраняем книгу
            book_id = hashlib.md5(title.encode()).hexdigest()
            book_file = os.path.join(self.library_path, f"{book_id}.txt")
            
            with open(book_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Сохраняем информацию о книге
            with open(self.books_file, 'r', encoding='utf-8') as f:
                books = json.load(f)
            
            books[book_id] = {
                "title": title,
                "id": book_id,
                "file_path": book_file,
                "created_at": datetime.now().isoformat(),
                "word_count": len(content.split())
            }
            
            with open(self.books_file, 'w', encoding='utf-8') as f:
                json.dump(books, f, ensure_ascii=False, indent=2)
            
            # Обновляем индекс
            await self._update_index(book_id, title, content)

            try:
                from core.document_corpus_store import corpus_enabled, register_book_from_rag

                if corpus_enabled():
                    register_book_from_rag(
                        book_id=book_id,
                        title=title,
                        file_path=os.path.abspath(book_file),
                        content=content,
                    )
            except Exception as e:
                logger.debug("[books_rag] document_corpus register: %s", e)
            
            return True
        except Exception as e:
            logger.exception("[books_rag] add_book failed")
            return False
    
    async def _update_index(self, book_id: str, title: str, content: str):
        """Обновить локальный JSON-индекс и при необходимости Qdrant."""
        try:
            with open(self.index_file, 'r', encoding='utf-8') as f:
                index = json.load(f)
            
            if book_id not in index:
                index[book_id] = {
                    "title": title,
                    "book_id": book_id,
                    "chunks": []
                }
            
            chunks = chunk_book_text(content)
            index[book_id]["chunks"] = chunks[:200]
            
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)

            if self._qdrant.enabled:
                ok = await self._qdrant.upsert_book(book_id, title, content)
                if ok:
                    logger.info("books_rag: Qdrant indexed book_id=%s chunks=%s", book_id, len(chunks))
                else:
                    logger.warning(
                        "books_rag: Qdrant index failed for %s (проверь QDRANT_*, OPENROUTER_API_KEY, QDRANT_EMBEDDING_MODEL)",
                        book_id,
                    )
        except Exception as e:
            logger.exception("[books_rag] update_index failed")
    
    async def search_book(self, title: str, query: str) -> List[Dict[str, Any]]:
        """Поиск по книге"""
        # Поиск по заголовку книги
        try:
            with open(self.books_file, 'r', encoding='utf-8') as f:
                books = json.load(f)
            
            # Найдем книгу по названию
            matching_books = []
            for book_id, book_info in books.items():
                if title.lower() in book_info.get("title", "").lower():
                    matching_books.append(book_id)
            
            if not matching_books:
                return [{"error": "Книга не найдена"}]
            
            # Для упрощения - возвращаем контекст
            book_id = matching_books[0]

            results: List[Dict[str, Any]] = []
            if self._qdrant.enabled:
                results = self._qdrant.search(book_id, query)

            if not results:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    index = json.load(f)
                if book_id in index:
                    chunks = index[book_id]["chunks"]
                    for i, chunk in enumerate(chunks):
                        if query.lower() in chunk.lower():
                            results.append({
                                "chunk_id": i,
                                "content": chunk,
                                "match_type": "query_match",
                            })

            if not results:
                return [{"message": "Ничего не найдено по запросу", "query": query}]

            return results
        except Exception as e:
            logger.exception("[books_rag] search_book failed")
            return [{"error": str(e)}]
    
    def get_book_summary(self, title: str) -> Dict[str, Any]:
        """Получить краткое содержание книги"""
        try:
            with open(self.books_file, 'r', encoding='utf-8') as f:
                books = json.load(f)
            
            # Найдем книгу по названию
            for book_id, book_info in books.items():
                if title.lower() in book_info.get("title", "").lower():
                    return {
                        "title": book_info["title"],
                        "word_count": book_info.get("word_count", 0),
                        "book_id": book_id,
                        "summary": "Краткое содержание книги по запросу..."
                    }
            
            return {"error": "Книга не найдена"}
        except Exception as e:
            logger.exception("[books_rag] summary failed")
            return {"error": str(e)}
    
    def generate_quiz_from_book(self, title: str, subject: str) -> Dict[str, Any]:
        """Сгенерировать тест по книге"""
        try:
            # Простая эмуляция генерации теста
            return {
                "title": title,
                "subject": subject,
                "questions": [
                    {
                        "question": f"Какова главная тема книги '{title}'?",
                        "options": ["A", "B", "C", "D"],
                        "correct": "A"
                    },
                    {
                        "question": f"Кто главный персонаж в '{title}'?",
                        "options": ["Персонаж 1", "Персонаж 2", "Персонаж 3", "Персонаж 4"],
                        "correct": "Персонаж 1"
                    }
                ],
                "generated_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.exception("[books_rag] quiz failed")
            return {"error": str(e)}