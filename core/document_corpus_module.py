"""
DocumentCorpus.* — единый локальный поиск по закэшированным НПА и книгам (слепки чанков + FTS).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.document_corpus_store import (
    corpus_catalog,
    corpus_enabled,
    corpus_stats,
    get_document_outline,
    get_original_for_telegram,
    sync_all_law_cache_entries,
    sync_shared_knowledge_ingest_dir,
    unified_search,
)

logger = logging.getLogger(__name__)


class DocumentCorpusModule:
    """Инструменты общего корпуса документов (ядро)."""

    BRAIN_LITE_INCLUDE = True

    async def unified_search(
        self,
        query: str,
        kinds: str = "",
        max_results: int = 16,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """
        Полнотекстовый поиск по слепкам чанков (НПА из law_act_cache, книги BooksRAG, shared_ingest с общей базы).
        kinds: через запятую law_act, book, shared_ingest (пусто = все).
        """
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false", "hits": []}
        q = (query or "").strip()
        if len(q) < 2:
            return {"ok": False, "error": "query too short", "hits": []}
        kind_list: List[str] = []
        raw_k = (kinds or "").strip()
        if raw_k:
            kind_list = [x.strip() for x in raw_k.split(",") if x.strip()]
        lim = max(1, min(int(max_results) or 16, 48))
        return unified_search(q, kinds=kind_list or None, limit=lim)

    async def document_outline(
        self,
        document_id: str,
        max_chunks: int = 60,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """Оглавление документа в корпусе: заголовки чанков и краткие превью (без полного текста в ответе)."""
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
        cap = max(1, min(int(max_chunks) or 60, 200))
        return get_document_outline((document_id or "").strip(), max_chunks=cap)

    async def sync_law_cache(self, user_id: str = "") -> Dict[str, Any]:
        """Догнать индекс корпуса из всех файлов law_act_cache/entries (миграция, админ)."""
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
        try:
            return sync_all_law_cache_entries()
        except Exception as e:
            logger.exception("[document_corpus] sync_law_cache: %s", e)
            return {"ok": False, "error": str(e)}

    async def sync_shared_ingest(self, user_id: str = "") -> Dict[str, Any]:
        """Проиндексировать файлы из data/shared_knowledge/ingest в корпус (после обновления бота или ручной догон)."""
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
        try:
            return sync_shared_knowledge_ingest_dir()
        except Exception as e:
            logger.exception("[document_corpus] sync_shared_ingest: %s", e)
            return {"ok": False, "error": str(e)}

    async def stats(self, user_id: str = "") -> Dict[str, Any]:
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
        return corpus_stats()

    async def list_catalog(
        self,
        mode: str = "books",
        limit: int = 200,
        offset: int = 0,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """
        Список id документов в корпусе без поиска по тексту.
        mode: books | documents | docs | all — в Telegram см. /corpus_books и /corpus_docs.
        """
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false", "items": [], "total": 0}
        lim = max(1, min(int(limit) or 200, 500))
        off = max(0, int(offset) or 0)
        return corpus_catalog(mode=mode, limit=lim, offset=off)

    async def resolve_original(self, document_id: str, user_id: str = "") -> Dict[str, Any]:
        """
        Оригинал документа из корпуса (копия в data/document_corpus/originals или разрешённый путь).
        В Telegram пользователь выполняет /corpus_doc <document_id> — путь сервера в ответ инструмента не подставляй.
        """
        if not corpus_enabled():
            return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
        doc_id = (document_id or "").strip()
        r = get_original_for_telegram(doc_id)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", "failed"), "document_id": doc_id}
        return {
            "ok": True,
            "document_id": doc_id,
            "kind": r.get("kind"),
            "title": r.get("title"),
            "filename": r.get("filename"),
            "telegram_command": f"/corpus_doc {doc_id}",
            "hint": "Сообщи пользователю выполнить эту slash-команду в чате с ботом — файл отправится из локального хранилища.",
        }
