"""Контракт единого pending-flow прерывания."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _fresh_pending_flow_module(monkeypatch):
    # Пересоздаём модуль на каждый тест, чтобы реестр был чистым.
    import core.pending_flow as pf
    importlib.reload(pf)
    yield pf


def test_is_negative_interrupt_basics():
    from core.pending_flow import is_negative_interrupt

    for s in ("нет", "Нет", "Стоп", " отмена ", "no", "STOP", "Cancel", "хватит"):
        assert is_negative_interrupt(s), s
    for s in ("да", "ok", "посчитай это", "нет, но в другом смысле", "сделай", ""):
        assert not is_negative_interrupt(s), s


def test_clear_all_pending_only_returns_actually_cleared():
    from core.pending_flow import clear_all_pending, register_pending_source

    state = {"a": True, "b": False}

    def clr_a(uid: str, cid: str) -> bool:
        out = state["a"]
        state["a"] = False
        return out

    def clr_b(uid: str, cid: str) -> bool:
        return state["b"]

    register_pending_source("a", clr_a)
    register_pending_source("b", clr_b)
    cleared = clear_all_pending("u1", "c1")
    assert cleared == ["a"]
    cleared2 = clear_all_pending("u1", "c1")
    assert cleared2 == []


def test_try_handle_negative_interrupt_returns_message_only_when_pending_existed():
    from core.pending_flow import register_pending_source, try_handle_negative_interrupt

    pending = {"once": True}

    def clr(uid: str, cid: str) -> bool:
        if pending["once"]:
            pending["once"] = False
            return True
        return False

    register_pending_source("dummy", clr)

    out = try_handle_negative_interrupt(text="нет", user_id="u", chat_id="c")
    assert out is not None
    msg, cleared = out
    assert "отмен" in msg.lower()
    assert cleared == ["dummy"]

    # Pending уже нет → даже на «нет» возвращаем None, чтобы дать обычной маршрутизации.
    out2 = try_handle_negative_interrupt(text="нет", user_id="u", chat_id="c")
    assert out2 is None


def test_try_handle_returns_none_for_non_negative_text():
    from core.pending_flow import register_pending_source, try_handle_negative_interrupt

    register_pending_source("dummy", lambda u, c: True)
    assert try_handle_negative_interrupt(text="посчитай 2+2", user_id="u", chat_id="c") is None
