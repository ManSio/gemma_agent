"""Гонка фото → текст: early pending и ожидание attach."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.image_gen_nl import prose_wants_image_edit
from core.user_image_pending import has_pending_image, pop_pending_images, register_pending_image


def test_attach_pending_waits_for_registration(tmp_path: Path, monkeypatch):
    from core.input_layer import InputLayer

    monkeypatch.setenv("IMAGE_GEN_PENDING_WAIT_MS", "800")
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"a" * 100)

    async def _run():
        async def _late_register():
            await asyncio.sleep(0.15)
            register_pending_image(
                "u1",
                "c1",
                {"file_type": "image", "local_path": str(img), "original_name": "x.jpg"},
            )

        layer = InputLayer.__new__(InputLayer)
        task = asyncio.create_task(_late_register())
        fc = await layer._attach_pending_image_for_text(
            user_id="u1",
            chat_id="c1",
            text="перерисуй в пиксель-арт",
        )
        await task
        return fc

    fc = asyncio.run(_run())
    assert fc is not None
    assert fc.get("local_path")


def test_has_pending_without_pop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IMAGE_PENDING_TTL_SEC", "300")
    img = tmp_path / "y.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"b" * 50)
    assert not has_pending_image("u9", "c9")
    register_pending_image(
        "u9",
        "c9",
        {"file_type": "image", "local_path": str(img), "original_name": "y.jpg"},
    )
    assert has_pending_image("u9", "c9")
    pop_pending_images("u9", "c9", limit=1)
    assert not has_pending_image("u9", "c9")


def test_prose_edit_without_image_word():
    assert prose_wants_image_edit("перерисуй в акварели")
