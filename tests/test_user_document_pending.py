import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_register_load_delete_personal_and_shared(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setenv("USER_LIBRARY_DIR", str(tmp_path / "ul"))
    monkeypatch.setenv("SHARED_KNOWLEDGE_DIR", str(tmp_path / "sk"))
    monkeypatch.setenv("SHARED_KNOWLEDGE_UPLOAD", "admin")

    from core.user_document_pending import (
        delete_pending,
        load_pending_body,
        load_pending_meta,
        register_pending_if_enabled,
        _save_personal_library,
        _save_shared_knowledge,
    )

    pid = register_pending_if_enabled(
        user_id="111",
        chat_id="222",
        filename="Тест.pdf",
        body="hello кириллица",
    )
    assert pid and len(pid) >= 8
    meta = load_pending_meta(pid)
    assert meta and meta.get("user_id") == "111"
    assert load_pending_body(pid) == "hello кириллица"

    ppath = _save_personal_library("111", "Тест.pdf", "hello кириллица")
    assert Path(ppath).is_file()
    delete_pending(pid)
    assert load_pending_meta(pid) is None

    pid2 = register_pending_if_enabled(
        user_id="111",
        chat_id="222",
        filename="x.docx",
        body="shared body",
    )
    assert pid2
    spath = _save_shared_knowledge("111", "x.docx", "shared body", pid2)
    text = Path(spath).read_text(encoding="utf-8")
    assert "shared body" in text
    assert "user_id: 111" in text
    delete_pending(pid2)


def test_pending_keyboard_three_rows() -> None:
    from core.user_document_pending import pending_document_keyboard_rows

    rows = pending_document_keyboard_rows("a1b2c3d4e5f6a7b8")
    assert len(rows) == 3
    assert len(rows[0]) == 1 and "Личное" in rows[0][0]["text"]
    assert len(rows[1]) == 1 and "Общая база" in rows[1][0]["text"]
    assert rows[1][0]["callback_data"].startswith("udoc:k:")
    assert "Удалить" in rows[2][0]["text"]


def test_handle_udoc_wrong_owner(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path / "rt"))
    from core.user_document_pending import register_pending_if_enabled, handle_udoc_callback

    pid = register_pending_if_enabled(
        user_id="999",
        chat_id="1",
        filename="a.txt",
        body="x",
    )
    assert pid
    cb = MagicMock()
    cb.data = f"udoc:x:{pid}"
    cb.from_user.id = 888
    cb.message = None
    cb.bot = MagicMock()
    cb.answer = AsyncMock()
    layer = MagicMock()

    asyncio.run(handle_udoc_callback(layer, cb))
    cb.answer.assert_awaited()


def test_handle_udoc_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path / "rt"))
    from core.user_document_pending import register_pending_if_enabled, handle_udoc_callback, load_pending_meta

    pid = register_pending_if_enabled(
        user_id="42",
        chat_id="100",
        filename="a.txt",
        body="to delete",
    )
    assert pid
    cb = MagicMock()
    cb.data = f"udoc:x:{pid}"
    cb.from_user.id = 42
    msg = MagicMock()
    msg.chat.id = 100
    cb.message = msg
    cb.bot = MagicMock()
    cb.bot.send_message = AsyncMock()
    cb.answer = AsyncMock()
    layer = MagicMock()
    layer._admin_module.is_admin = MagicMock(return_value=False)
    asyncio.run(handle_udoc_callback(layer, cb))
    assert load_pending_meta(pid) is None
    cb.answer.assert_awaited()


def test_intake_storable_plain_uses_tables() -> None:
    from core.document_intake import intake_storable_plain

    doc = {
        "ok": True,
        "text": "",
        "tables": [{"sheet": "S1", "rows": [["a", "b"], [1, 2]]}],
    }
    t = intake_storable_plain(doc)
    assert "S1" in t
    assert "a" in t
