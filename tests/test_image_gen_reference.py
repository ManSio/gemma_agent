"""Reference-image routing and payload for image_generator."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.image_gen_nl import (
    attachment_wants_image_generation,
    prose_wants_image_edit,
)
from modules.image_generator.module import ImageGeneratorModule


def test_prose_wants_image_edit_ru():
    assert prose_wants_image_edit("перерисуй в стиле аниме")
    assert prose_wants_image_edit("Edit this image to look cyberpunk")
    assert not prose_wants_image_edit("привет")


def test_attachment_wants_image_generation(tmp_path: Path, monkeypatch):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"x" * 200)
    fc = {"file_type": "image", "local_path": str(img)}
    monkeypatch.setenv("IMAGE_GEN_REFERENCE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_GEN_NL_ROUTE", "true")
    assert attachment_wants_image_generation(fc, "перерисуй в акварели")
    assert attachment_wants_image_generation(fc, "сгенерируй картинку космос")
    assert not attachment_wants_image_generation(fc, "что на фото?")


def test_collect_reference_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IMAGE_GEN_REFERENCE_ENABLED", "true")
    p1 = tmp_path / "one.png"
    p2 = tmp_path / "two.png"
    p1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    p2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 100)
    mod = ImageGeneratorModule()
    inp = {
        "meta": {
            "file_context": {
                "file_type": "image",
                "local_path": str(p2),
                "secondary_images": [{"file_type": "image", "local_path": str(p1)}],
            }
        }
    }
    paths = mod._collect_reference_paths(inp)
    assert len(paths) == 2
    assert paths[0] == p1
    assert paths[1] == p2


def test_build_chat_user_content_multimodal(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IMAGE_GEN_REFERENCE_ENABLED", "true")
    p = tmp_path / "ref.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"z" * 80)
    mod = ImageGeneratorModule()
    content = mod._build_chat_user_content("перерисуй в пиксель-арт", [p])
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(part.get("type") == "image_url" for part in content)
