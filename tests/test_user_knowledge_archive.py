"""Тесты личного архива знаний и сверки."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core import user_knowledge_archive_module as uka


class UserKnowledgeArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_list_read(self):
        with tempfile.TemporaryDirectory() as d:
            idx = os.path.join(d, "idx.jsonl")
            with patch.dict(
                "os.environ",
                {
                    "USER_KNOWLEDGE_ARCHIVE_INDEX": idx,
                    "USER_KNOWLEDGE_ARCHIVE_DIR": os.path.join(d, "arc"),
                },
            ):
                mod = uka.UserKnowledgeArchiveModule()
                r = await mod.archive_store(
                    title="Тест",
                    body="Содержимое документа для архива.",
                    user_id="42",
                    source_type="document",
                    source_url="https://example.com/a",
                    tags="тест, демо",
                )
                self.assertTrue(r.get("ok"))
                eid = r.get("entry_id")
                self.assertTrue(eid)

                lst = await mod.archive_list(user_id="42", query="архив", limit=10)
                self.assertTrue(lst.get("ok"))
                self.assertGreaterEqual(lst.get("count", 0), 1)

                rd = await mod.archive_read(entry_id=eid, user_id="42", max_chars=5000)
                self.assertTrue(rd.get("ok"))
                self.assertIn("Содержимое", rd.get("body", ""))

                long_body = ("прочерк " * 80) + "ответственность за тишину в жилом доме" + (" конец" * 20)
                r2 = await mod.archive_store(title="Длинная", body=long_body, user_id="42")
                self.assertTrue(r2.get("ok"))
                sr = await mod.archive_search(
                    user_id="42", query="ответственность за тишину", limit=5
                )
                self.assertTrue(sr.get("ok"))
                self.assertGreaterEqual(sr.get("count", 0), 1)
                ids = {it.get("entry_id") for it in sr.get("items", [])}
                self.assertIn(r2.get("entry_id"), ids)

    async def test_store_accepts_text_alias_instead_of_body(self):
        with tempfile.TemporaryDirectory() as d:
            idx = os.path.join(d, "idx.jsonl")
            with patch.dict(
                "os.environ",
                {
                    "USER_KNOWLEDGE_ARCHIVE_INDEX": idx,
                    "USER_KNOWLEDGE_ARCHIVE_DIR": os.path.join(d, "arc"),
                },
            ):
                mod = uka.UserKnowledgeArchiveModule()
                r = await mod.archive_store(
                    title="Модель ИИ",
                    text="DeepSeek 4 Flash",
                    user_id="99",
                )
                self.assertTrue(r.get("ok"))
                rd = await mod.archive_read(entry_id=r["entry_id"], user_id="99")
                self.assertIn("DeepSeek", rd.get("body", ""))

    async def test_cross_check_uses_search(self):
        with tempfile.TemporaryDirectory() as d:
            idx = os.path.join(d, "idx.jsonl")
            with patch.dict(
                "os.environ",
                {
                    "USER_KNOWLEDGE_ARCHIVE_INDEX": idx,
                    "USER_KNOWLEDGE_ARCHIVE_DIR": os.path.join(d, "arc"),
                },
            ):
                mod = uka.UserKnowledgeArchiveModule()
                st = await mod.archive_store(
                    title="Факт",
                    body="Минск столица Беларуси.",
                    user_id="7",
                    source_type="site_text",
                    source_url="https://example.com/page",
                )
                eid = st["entry_id"]

                fake_search = AsyncMock(
                    return_value={"ok": True, "summary": "независимая выдержка", "source": "test"}
                )
                with patch.object(uka.UniversalSearchModule, "search", fake_search):
                    cc = await mod.archive_cross_check(user_id="7", entry_id=eid)
                self.assertTrue(cc.get("ok"))
                self.assertGreaterEqual(len(cc.get("runs", [])), 1)
                fake_search.assert_awaited()

    async def test_cross_check_claim_only(self):
        mod = uka.UserKnowledgeArchiveModule()
        fake_search = AsyncMock(return_value={"ok": True, "summary": "x"})
        with tempfile.TemporaryDirectory() as d:
            idx = os.path.join(d, "idx2.jsonl")
            with patch.dict(
                "os.environ",
                {
                    "USER_KNOWLEDGE_ARCHIVE_INDEX": idx,
                    "USER_KNOWLEDGE_ARCHIVE_DIR": os.path.join(d, "arc2"),
                },
            ):
                with patch.object(uka.UniversalSearchModule, "search", fake_search):
                    cc = await mod.archive_cross_check(
                        user_id="1", entry_id="", claim="Земля круглая", focus_query="Earth shape"
                    )
                self.assertTrue(cc.get("ok"))
                self.assertIsNone(cc.get("entry_id"))

    def test_count_archive_entries_for_user(self):
        with tempfile.TemporaryDirectory() as d:
            arc = os.path.join(d, "arc")
            with patch.dict(
                "os.environ",
                {
                    "USER_KNOWLEDGE_ARCHIVE_DIR": arc,
                    "USER_KNOWLEDGE_ARCHIVE_ENABLED": "true",
                },
            ):
                self.assertEqual(uka.count_archive_entries_for_user("u1"), 0)
                uroot = uka._user_root("u1")
                os.makedirs(uroot, exist_ok=True)
                with open(os.path.join(uroot, "abc.txt"), "w", encoding="utf-8") as f:
                    f.write("x")
                with open(os.path.join(uroot, "note.md"), "w", encoding="utf-8") as f:
                    f.write("y")
                self.assertEqual(uka.count_archive_entries_for_user("u1"), 1)

    async def test_personal_library_list_read(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "ulib")
            with patch.dict("os.environ", {"USER_LIBRARY_DIR": lib}):
                mod = uka.UserKnowledgeArchiveModule()
                udir = os.path.join(lib, "77")
                os.makedirs(udir, exist_ok=True)
                doc_path = os.path.join(udir, "P32500095_demo.txt")
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write("Указ №95 тест.")

                lst = await mod.personal_library_list(user_id="77", limit=10)
                self.assertTrue(lst.get("ok"))
                self.assertEqual(lst.get("count"), 1)
                self.assertEqual(lst["items"][0]["filename"], "P32500095_demo.txt")

                rd = await mod.personal_library_read(
                    user_id="77", filename="P32500095_demo.txt", max_chars=500
                )
                self.assertTrue(rd.get("ok"))
                self.assertIn("Указ", rd.get("body", ""))

                bad = await mod.personal_library_read(user_id="77", filename="../etc/passwd")
                self.assertFalse(bad.get("ok"))

    async def test_archive_search_personal_library(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "ulib")
            idx = os.path.join(d, "idx.jsonl")
            with patch.dict(
                "os.environ",
                {
                    "USER_LIBRARY_DIR": lib,
                    "USER_KNOWLEDGE_ARCHIVE_INDEX": idx,
                    "USER_KNOWLEDGE_ARCHIVE_DIR": os.path.join(d, "arc"),
                },
            ):
                mod = uka.UserKnowledgeArchiveModule()
                udir = os.path.join(lib, "88")
                os.makedirs(udir, exist_ok=True)
                with open(os.path.join(udir, "note_demo.txt"), "w", encoding="utf-8") as f:
                    f.write("В тексте упоминается ответственность за тишину ночью.")

                sr = await mod.archive_search(
                    user_id="88", query="ответственность за тишину", scope="library", limit=5
                )
                self.assertTrue(sr.get("ok"))
                self.assertEqual(sr.get("count"), 1)
                self.assertEqual(sr["items"][0].get("filename"), "note_demo.txt")


if __name__ == "__main__":
    unittest.main()
