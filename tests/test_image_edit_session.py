"""Сессия правки одного изображения (input/output paths)."""
from __future__ import annotations

from pathlib import Path

from core.image_edit_session import (
    bind_image_input,
    bind_image_output,
    clear_image_edit_session,
    file_context_for_session_edit,
    get_image_edit_session,
)


def test_session_input_output_and_edit_ref(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    inp = tmp_path / "in.png"
    out = tmp_path / "out.png"
    inp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 40)
    bind_image_input("u1", "c1", str(inp))
    bind_image_output("u1", "c1", str(out))
    fc = file_context_for_session_edit("u1", "c1")
    assert fc is not None
    assert fc.get("local_path") == str(out)
    assert fc.get("image_edit_session_ref") == "output_path"
    clear_image_edit_session("u1", "c1")
    assert get_image_edit_session("u1", "c1") is None
