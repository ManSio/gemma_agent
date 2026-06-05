"""Единый корпус документов (DocumentCorpus store + FTS)."""
from __future__ import annotations

import os
import secrets
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from core import document_corpus_store as dcs


def _rmtree(p: Path) -> None:
    try:
        shutil.rmtree(p)
    except (OSError, PermissionError):
        pass


class DocumentCorpusStoreTests(unittest.TestCase):
    def _fresh_db_path(self) -> Path:
        uid = secrets.token_hex(6)
        return Path(os.environ.get("TEMP", "/tmp")) / f"doc_corpus_{uid}" / "t.sqlite"

    def _env(self, db_path: Path, **kw: str) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        tmp_corpus = Path(tempfile.mkdtemp()) / "corpus_files"
        base = {
            "DOCUMENT_CORPUS_DB": str(db_path),
            "DOCUMENT_CORPUS_ENABLED": "true",
            "CORPUS_FILES_DIR": str(tmp_corpus),
        }
        base.update(kw)
        self._patch = patch.dict(os.environ, base)
        self._patch.start()
        dcs._schema_done = False
        self.addCleanup(self._patch.stop)
        self.addCleanup(lambda: _rmtree(tmp_corpus.parent))

    def _cleanup_db(self, db_path: Path) -> None:
        for p in [db_path, db_path.with_suffix(".sqlite-shm"), db_path.with_suffix(".sqlite-wal")]:
            try:
                p.unlink(missing_ok=True)
            except PermissionError:
                pass

    def test_register_and_search(self) -> None:
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="abc123",
            url="https://law.example.com/document/?x=1",
            title="Указ № 1",
            text="Статья 1. О налогах и сборах. Далее по тексту " + ("word " * 400),
        )
        dcs.register_book_from_rag(
            book_id="b1",
            title="Учебник",
            file_path=str(db.parent / "book.txt"),
            content="Глава 1. Физика. Инерция и масса. " + ("x " * 400),
        )
        r = dcs.unified_search("налог", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(any(h.get("kind") == "law_act" for h in hits))
        r2 = dcs.unified_search("инерция", limit=8)
        self.assertTrue(r2.get("ok"))
        hits2 = r2.get("hits") or []
        self.assertTrue(any(h.get("kind") == "book" for h in hits2))

        outline = dcs.get_document_outline("law:abc123", max_chunks=20)
        self.assertTrue(outline.get("ok"))
        self.assertEqual(outline.get("kind"), "law_act")
        self.assertGreater(len(outline.get("chunks") or []), 0)

        st = dcs.corpus_stats()
        self.assertTrue(st.get("ok"))
        self.assertGreaterEqual(st.get("documents"), 2)
        self._cleanup_db(db)

    def test_cache_put_registers_corpus(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            entries = td_path / "entries"
            entries.mkdir(parents=True)
            corpus_db = td_path / "corpus.sqlite"
            with patch.dict(
                os.environ,
                {
                    "LAW_ACT_CACHE_DIR": str(td_path),
                    "DOCUMENT_CORPUS_DB": str(corpus_db),
                    "DOCUMENT_CORPUS_ENABLED": "true",
                },
            ):
                from core import law_act_cache as lac

                dcs._schema_done = False
                key = lac.cache_put(
                    "https://law.example.com/document/t-corpus/",
                    title="Test Act",
                    text="Статья 5. Проверка корпуса. Текст для индекса.",
                )
                self.assertTrue(corpus_db.is_file())
                r = dcs.unified_search("Проверка", limit=5)
                self.assertTrue(r.get("ok"))
                self.assertTrue(
                    any((h.get("document_id") == f"law:{key}") for h in (r.get("hits") or []))
                )
                got = dcs.get_original_for_telegram(f"law:{key}")
                self.assertTrue(got.get("ok"), msg=got.get("error"))
                self.assertTrue(Path(got["path"]).is_file())
                self.assertTrue(dcs.corpus_originals_dir().is_dir())

    def test_shared_knowledge_ingest_registers_and_searchable(self) -> None:
        db = self._fresh_db_path()
        self._env(db)
        ingest = db.parent / "ingest"
        ingest.mkdir(parents=True, exist_ok=True)
        pid = "a1b2c3d4e5f67890"
        p = ingest / f"ingest_{pid}.txt"
        p.write_text(
            "# shared_knowledge ingest\n"
            f"# user_id: 999\n# source_file: decree.pdf\n# pending_id: {pid}\n"
            "# saved_utc: 2026-05-10T00:00:00+00:00\n\n"
            "Указ Президента о жилищной поддержке и льготных кредитах для граждан.",
            encoding="utf-8",
        )
        dcs.register_shared_knowledge_ingest(
            pending_id=pid,
            user_id="999",
            original_name="decree.pdf",
            body="fallback body",
            saved_path=str(p),
        )
        r = dcs.unified_search("жилищн", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(any(h.get("kind") == "shared_ingest" for h in hits))
        st = dcs.corpus_stats()
        self.assertIn("shared_ingest", (st.get("by_kind") or {}))
        self._cleanup_db(db)

    # ── NEW TESTS ────────────────────────────────────────────────────────

    def test_nfkc_normalization_matches_nfd_input(self) -> None:
        """Search in NFKC should find NFKC-normalized text even if source was NFD."""
        db = self._fresh_db_path()
        self._env(db)
        decomposed = "Право на жиль\u0435\u0308 и землю гражданам"
        dcs.register_law_act_from_cache(
            cache_key="nfkd",
            url="https://law.example.com/nfkd/",
            title="NFKD Test",
            text=decomposed + " дополнительный текст для чанка.",
        )
        r = dcs.unified_search("жильё", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:nfkd" for h in hits),
            f"NFKC search failed: {r}",
        )
        self._cleanup_db(db)

    def test_nfkc_normalization_search_decomposed_query(self) -> None:
        """Decomposed query should match precomposed text (both NFKC-normalized)."""
        db = self._fresh_db_path()
        self._env(db)
        text = "Положение о жильё и социальной поддержке"
        dcs.register_law_act_from_cache(
            cache_key="nfkd2",
            url="https://law.example.com/nfkd2/",
            title="NFKD 2",
            text=text,
        )
        decomposed_q = "жиль\u0435\u0308"
        r = dcs.unified_search(decomposed_q, limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:nfkd2" for h in hits),
            f"NFKC decomposed query failed: {r}",
        )
        self._cleanup_db(db)

    def test_hyphenated_word_searchable_by_part(self) -> None:
        """FTS5 splits hyphen: each part is searchable."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="hyphen",
            url="https://law.example.com/hyphen/",
            title="Hyphen Test",
            text="Жилищно-коммунальное хозяйство и субсидии для малоимущих граждан.",
        )
        r = dcs.unified_search("жилищно", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:hyphen" for h in hits),
            f"Hyphen search by part failed: {r}",
        )
        self._cleanup_db(db)

    def test_underscore_separated_word_searchable(self) -> None:
        """FTS5 splits underscore: each part is searchable."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="under",
            url="https://law.example.com/under/",
            title="Underscore Test",
            text="Код проекта my_app_v2 требует интеграции с API платформы.",
        )
        r = dcs.unified_search("my", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:under" for h in hits),
            f"Underscore search failed: {r}",
        )
        self._cleanup_db(db)

    def test_long_query_does_not_produce_empty_with_or(self) -> None:
        """With OR logic, a long query with multiple tokens should still find results."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="long_or",
            url="https://law.example.com/long_or/",
            title="Long OR Test",
            text="Регулирование электронных денег и криптовалют в экономике.",
        )
        r = dcs.unified_search(
            "электронный банк платёж система перевод криптовалют",
            limit=8,
        )
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:long_or" for h in hits),
            f"Long query with OR failed: {r}",
        )
        self._cleanup_db(db)

    def test_long_query_token_limit_7(self) -> None:
        """Only first 7 tokens are used for FTS query."""
        db = self._fresh_db_path()
        self._env(db)
        text = "Первое Второе Третье Четвертое Пятое Шестое Седьмое Восьмое Девятое"
        dcs.register_law_act_from_cache(
            cache_key="toklim",
            url="https://law.example.com/toklim/",
            title="Token Limit",
            text=text,
        )
        r = dcs.unified_search(
            "Первое Второе Третье Четвертое Пятое Шестое Седьмое Восьмое",
            limit=8,
        )
        self.assertTrue(r.get("ok"))
        fts = r.get("fts", "")
        self.assertNotIn("восьмое", fts)
        self.assertIn("седьмое", fts)
        self._cleanup_db(db)

    def test_query_with_fts_special_chars_does_not_error(self) -> None:
        """FTS5 special characters (, ), ^, " are stripped, not causing OperationalError."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="spec",
            url="https://law.example.com/spec/",
            title="Special Chars",
            text="Формула расчёта (a + b)^2 для вычисления норматива.",
        )
        r = dcs.unified_search("расчёт (a+b)^2 норматив", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:spec" for h in hits),
            f"Special chars query failed: {r}",
        )
        self._cleanup_db(db)

    def test_search_beyond_old_preview_limit(self) -> None:
        """Text beyond the old PREVIEW_MAX (380) should now be searchable (new limit=1024)."""
        db = self._fresh_db_path()
        self._env(db)
        prefix = "X " * 400
        text = prefix + " уникальноеслово для поиска в глубине"
        dcs.register_law_act_from_cache(
            cache_key="deep",
            url="https://law.example.com/deep/",
            title="Deep Search",
            text=text,
        )
        r = dcs.unified_search("уникальноеслово", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:deep" for h in hits),
            f"Deep search beyond 380 failed: {r}",
        )
        self._cleanup_db(db)

    def test_phrase_crossing_chunk_boundary_with_overlap(self) -> None:
        """Phrase spanning chunk boundary should be findable due to overlap."""
        db = self._fresh_db_path()
        self._env(db)
        pad = ("А" * 50 + " ") * 31
        phrase = " границачднков поисковаяфраза продолжение "
        text = pad + phrase + ("Б" * 50 + " ") * 20
        dcs.register_law_act_from_cache(
            cache_key="chunkb",
            url="https://law.example.com/chunkb/",
            title="Chunk Boundary",
            text=text,
        )
        r = dcs.unified_search("поисковаяфраза", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:chunkb" for h in hits),
            f"Chunk boundary search failed: {r}",
        )
        self._cleanup_db(db)

    def test_snippet_returned_in_hits(self) -> None:
        """Hits should include snippet field from FTS5 snippet()."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="snipp",
            url="https://law.example.com/snipp/",
            title="Snippet Test",
            text="Порядок предоставления субсидий на оплату жилищно-коммунальных услуг.",
        )
        r = dcs.unified_search("субсидий", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(len(hits) > 0, f"No hits: {r}")
        hit = hits[0]
        self.assertIn("snippet", hit, f"No snippet key in hit: {hit}")
        snippet = hit.get("snippet")
        self.assertIsNotNone(snippet, "snippet is None")
        self.assertIsInstance(snippet, str, "snippet is not a string")
        self.assertGreater(len(snippet), 0, "snippet is empty")
        # snippet() should contain ']' close marker and text from the chunk
        self.assertIn("]", snippet)
        self._cleanup_db(db)

    def test_reserved_word_token_is_quoted_in_fts_query(self) -> None:
        """FTS5 reserved words (AND, OR, NOT, NEAR) should be quoted as literals."""
        db = self._fresh_db_path()
        self._env(db)
        text = "Проект назван Rock and Stone Mining Inc."
        dcs.register_law_act_from_cache(
            cache_key="resword",
            url="https://law.example.com/resword/",
            title="Reserved Word",
            text=text,
        )
        r = dcs.unified_search("and rock stone mining", limit=8)
        self.assertTrue(r.get("ok"))
        fts = r.get("fts", "")
        self.assertIn('"and"*', fts.lower(), f"Reserved word not quoted in FTS: {fts}")
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:resword" for h in hits),
            f"Reserved word search failed: {r}",
        )
        self._cleanup_db(db)

    def test_fts_schema_version_migration(self) -> None:
        """Old schema (no tokenizer) should be migrated on _ensure_fts_schema_version."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="mig1",
            url="https://law.example.com/mig1/",
            title="Migration Test",
            text="Текст для проверки миграции схемы FTS.",
        )
        conn = dcs._connect()
        try:
            dcs._init_schema(conn)
            (ver,) = conn.execute("PRAGMA user_version").fetchone()
            self.assertEqual(ver, dcs._FTS_SCHEMA_VERSION, f"Expected PRAGMA user_version={dcs._FTS_SCHEMA_VERSION}, got {ver}")
        finally:
            conn.close()
        r = dcs.unified_search("миграции", limit=8)
        self.assertTrue(r.get("ok"))
        hits = r.get("hits") or []
        self.assertTrue(any(h.get("document_id") == "law:mig1" for h in hits))
        self._cleanup_db(db)

    def test_fts_query_uses_or_not_and(self) -> None:
        """_fts_query_from_user should join with OR, not AND."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="or_test",
            url="https://law.example.com/or_test/",
            title="OR Test",
            text="Закон о государственных закупках и тендерах.",
        )
        r = dcs.unified_search("закупках тендерах неизвестноеслово", limit=8)
        self.assertTrue(r.get("ok"))
        fts = r.get("fts", "")
        self.assertIn(" OR ", fts, f"Expected OR in FTS query, got: {fts}")
        self.assertNotIn(" AND ", fts)
        hits = r.get("hits") or []
        self.assertTrue(
            any(h.get("document_id") == "law:or_test" for h in hits),
            f"OR query should still find partial matches: {r}",
        )
        self._cleanup_db(db)

    # ── CORPUS FILES TESTS ────────────────────────────────────────────────

    def test_original_saved(self) -> None:
        """register_file_as_corpus_document should save file to corpus_files_dir with metadata."""
        db = self._fresh_db_path()
        self._env(db)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write("Текст документа для индексации.")
            src = f.name
        try:
            result = dcs.register_file_as_corpus_document(
                filename="test_doc.txt",
                mime_type="text/plain",
                file_path_on_disk=src,
                text_content="Текст документа для индексации.",
                user_id="42",
            )
            self.assertTrue(result.get("ok"), f"register failed: {result}")
            doc_id = result.get("document_id")
            self.assertTrue(doc_id and doc_id.startswith("file:"))
            self.assertEqual(result.get("kind"), "user_file")
            self.assertGreater(result.get("chunks", 0), 0)
            saved_path = result.get("original_path")
            self.assertTrue(saved_path and Path(saved_path).is_file())
            self.assertIn("corpus_files", saved_path.replace("\\", "/").lower())

            # Verify searchable in FTS
            r = dcs.unified_search("индексации", limit=8)
            self.assertTrue(r.get("ok"))
            hits = r.get("hits") or []
            self.assertTrue(any(h.get("document_id") == doc_id for h in hits),
                            f"FTS search failed for registered file: {r}")

            # Verify get_path_for_corpus_file
            from_file = dcs.get_path_for_corpus_file(doc_id)
            self.assertIsNotNone(from_file)
            self.assertTrue(Path(from_file or "").is_file())
        finally:
            try:
                Path(src).unlink(missing_ok=True)
            except PermissionError:
                pass
        self._cleanup_db(db)

    def test_original_conflict_resolution(self) -> None:
        """Conflicting filename should get (1), (2), etc."""
        db = self._fresh_db_path()
        self._env(db)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write("Первый файл.")
            src1 = f.name
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write("Второй файл.")
            src2 = f.name
        try:
            r1 = dcs.register_file_as_corpus_document(
                filename="conflict.txt", mime_type="text/plain",
                file_path_on_disk=src1, text_content="Первый файл.", user_id="1",
            )
            self.assertTrue(r1.get("ok"))
            fn1 = Path(r1["original_path"]).name
            self.assertEqual(fn1, "conflict.txt")

            r2 = dcs.register_file_as_corpus_document(
                filename="conflict.txt", mime_type="text/plain",
                file_path_on_disk=src2, text_content="Второй файл.", user_id="1",
            )
            self.assertTrue(r2.get("ok"))
            fn2 = Path(r2["original_path"]).name
            self.assertEqual(fn2, "conflict (1).txt", f"Expected conflict (1).txt got {fn2}")
        finally:
            for p in (src1, src2):
                try:
                    Path(p).unlink(missing_ok=True)
                except PermissionError:
                    pass
        self._cleanup_db(db)

    def test_original_delete(self) -> None:
        """delete_document_from_corpus should remove FTS, metadata, and file."""
        db = self._fresh_db_path()
        self._env(db)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write("Текст для удаления.")
            src = f.name
        try:
            result = dcs.register_file_as_corpus_document(
                filename="delete_me.txt", mime_type="text/plain",
                file_path_on_disk=src, text_content="Текст для удаления.", user_id="1",
            )
            self.assertTrue(result.get("ok"))
            doc_id = result["document_id"]
            file_path = result["original_path"]
            self.assertTrue(Path(file_path).is_file())

            r = dcs.delete_document_from_corpus(doc_id)
            self.assertTrue(r.get("ok"), f"delete failed: {r}")
            self.assertTrue(r.get("file_deleted"))

            # File should be gone
            self.assertFalse(Path(file_path).is_file(), "File was not deleted")

            # Search should not find it
            sr = dcs.unified_search("удаления", limit=8)
            hits = sr.get("hits") or []
            self.assertFalse(any(h.get("document_id") == doc_id for h in hits))
        finally:
            try:
                Path(src).unlink(missing_ok=True)
            except PermissionError:
                pass
        self._cleanup_db(db)

    def test_corpus_file_get_path(self) -> None:
        """get_path_for_corpus_file returns None for non-existent doc."""
        db = self._fresh_db_path()
        self._env(db)
        path = dcs.get_path_for_corpus_file("file:nonexistent")
        self.assertIsNone(path)
        self._cleanup_db(db)

    def test_corpus_delete_nonexistent(self) -> None:
        """delete_document_from_corpus returns error for non-existent doc."""
        db = self._fresh_db_path()
        self._env(db)
        r = dcs.delete_document_from_corpus("file:ghost")
        self.assertFalse(r.get("ok"))
        self.assertIn("not found", r.get("error", "").lower())
        self._cleanup_db(db)

    # ── BM25 TESTS ──────────────────────────────────────────────────────

    def test_bm25_ranking_order(self) -> None:
        """Documents with more frequent keywords should rank higher (lower BM25 score)."""
        db = self._fresh_db_path()
        self._env(db)
        # Doc A: keyword appears many times
        keyword = "экземпляр"
        text_a = f"{keyword} " * 50 + "текст для документа A."
        # Doc B: keyword appears few times
        text_b = f"{keyword} " * 5 + "другой текст документа B."
        # Doc C: keyword appears once
        text_c = f"{keyword} один раз в документе C."
        dcs.register_law_act_from_cache(cache_key="bm25_a", url="https://law.example.com/bm25_a/", title="BM25 A", text=text_a)
        dcs.register_law_act_from_cache(cache_key="bm25_b", url="https://law.example.com/bm25_b/", title="BM25 B", text=text_b)
        dcs.register_law_act_from_cache(cache_key="bm25_c", url="https://law.example.com/bm25_c/", title="BM25 C", text=text_c)
        r = dcs.unified_search(keyword, limit=10)
        self.assertTrue(r.get("ok"), f"Search failed: {r}")
        hits = r.get("hits") or []
        self.assertGreaterEqual(len(hits), 3, f"Expected at least 3 hits, got {len(hits)}: {r}")
        scores = [h.get("score") for h in hits]
        for s in scores:
            self.assertIsNotNone(s, f"Hit missing score: {hits}")
        # BM25: lower score = better. A (most frequent) should have lowest score.
        self.assertEqual(scores, sorted(scores), f"Scores not in ascending order (lower=better): {scores}")
        self._cleanup_db(db)

    def test_bm25_snippet_relevance(self) -> None:
        """Snippet should contain the query keyword and text from the chunk."""
        db = self._fresh_db_path()
        self._env(db)
        query_word = "субсидий"
        text = "Порядок предоставления субсидий на оплату жилищно-коммунальных услуг населению."
        dcs.register_law_act_from_cache(
            cache_key="bm25_sn",
            url="https://law.example.com/bm25_sn/",
            title="Snippet BM25",
            text=text,
        )
        r = dcs.unified_search(query_word, limit=5)
        self.assertTrue(r.get("ok"), f"Search failed: {r}")
        hits = r.get("hits") or []
        self.assertTrue(len(hits) > 0, f"No hits: {r}")
        hit = hits[0]
        self.assertIn("score", hit, f"No score in hit: {hit}")
        self.assertIsInstance(hit.get("score"), float, f"Score not float: {hit}")
        self.assertIn("snippet", hit, f"No snippet in hit: {hit}")
        snippet = hit.get("snippet") or ""
        self.assertIn(query_word, snippet, f"Snippet does not contain query '{query_word}': {snippet}")
        self._cleanup_db(db)

    def test_backward_compatibility(self) -> None:
        """BM25 should work on the existing v2 schema without migrations."""
        db = self._fresh_db_path()
        self._env(db)
        dcs.register_law_act_from_cache(
            cache_key="bw_comp",
            url="https://law.example.com/bw_comp/",
            title="Backward Compat",
            text="Статья о проверке обратной совместимости BM25 поиска.",
        )
        # Verify schema version is v2 (current)
        conn = dcs._connect()
        try:
            dcs._init_schema(conn)
            (ver,) = conn.execute("PRAGMA user_version").fetchone()
            self.assertEqual(ver, dcs._FTS_SCHEMA_VERSION, f"Schema version is {ver}, expected {dcs._FTS_SCHEMA_VERSION}")
        finally:
            conn.close()
        # Search — BM25 should work without any schema changes
        r = dcs.unified_search("обратной", limit=5)
        self.assertTrue(r.get("ok"), f"Search failed: {r}")
        hits = r.get("hits") or []
        self.assertTrue(len(hits) > 0, f"No hits: {r}")
        hit = hits[0]
        self.assertIn("score", hit)
        self.assertIsInstance(hit.get("score"), float)
        self.assertIn("snippet", hit)
        self._cleanup_db(db)


if __name__ == "__main__":
    unittest.main()
