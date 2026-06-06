import asyncio
import json
import os
import tempfile

from modules.books_rag.module import BooksRAGModule


def test_add_book_from_document_intake_context():
    async def _run():
        with tempfile.TemporaryDirectory() as td:
            mod = BooksRAGModule(
                config={"storage_path": os.path.join(td, "books"), "library_path": os.path.join(td, "lib")}
            )
            out = await mod.execute(
                {
                    "input": {"payload": "/add_book ManualTitle"},
                    "context": {
                        "document_intake": {"ok": True, "text": "chapter one body", "text_layer_empty": False}
                    },
                }
            )
            assert len(out) == 1
            assert "добавлена из вложения" in out[0].payload
            with open(mod.books_file, "r", encoding="utf-8") as f:
                books = json.load(f)
            assert len(books) == 1

    asyncio.run(_run())


def test_add_book_empty_layer_skips_attachment():
    async def _run():
        with tempfile.TemporaryDirectory() as td:
            mod = BooksRAGModule(
                config={"storage_path": os.path.join(td, "books"), "library_path": os.path.join(td, "lib")}
            )
            out = await mod.execute(
                {
                    "input": {"payload": "/add_book OnlyTitle"},
                    "context": {"document_intake": {"ok": True, "text": "", "text_layer_empty": True}},
                }
            )
            assert "Нет текста" in out[0].payload

    asyncio.run(_run())
