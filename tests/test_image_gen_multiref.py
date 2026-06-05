"""Мультифото: порядок референсов, композит, промпт."""
from __future__ import annotations

from pathlib import Path

from core.image_gen_multiref import (
    build_reference_user_prompt,
    collect_reference_paths_chronological,
    merge_pending_file_contexts,
    multiref_identity_mode,
    prose_wants_identity_preservation,
    prose_wants_multiref_pending_merge,
)
from core.image_gen_nl import (
    attachment_wants_image_generation,
    prose_wants_image_composite,
    prose_wants_image_gen_or_edit,
)
from modules.image_generator.module import ImageGeneratorModule


def test_multiref_merge_gate_interior_plan():
    """Новый план + промпт без «2-е фото» — не склеивать с pending."""
    t = (
        "Действуй как профессиональный дизайнер интерьеров. "
        "Мне нужно продумать планировку кухни"
    )
    assert not prose_wants_multiref_pending_merge(t)


def test_multiref_merge_gate_explicit_second_photo():
    assert prose_wants_multiref_pending_merge("замени фон со второго фото")
    assert prose_wants_multiref_pending_merge("сохрани черты лица, 3 фото с разных ракурсов")
    assert prose_wants_multiref_pending_merge("Сделай с фото как в фильме Форсаж")


def test_prose_wants_composite_ru():
    assert prose_wants_image_composite("замени фон со второго фото")
    assert prose_wants_image_composite("перенеси человека с первого на второе фото")
    assert prose_wants_image_gen_or_edit("замени фон со второго фото")
    assert not prose_wants_image_composite("привет")


def test_merge_pending_chronological():
    a = {"file_type": "image", "local_path": "/tmp/a.jpg"}
    b = {"file_type": "image", "local_path": "/tmp/b.jpg"}
    c = {"file_type": "image", "local_path": "/tmp/c.jpg"}
    merged = merge_pending_file_contexts([c, b, a])
    assert merged is not None
    assert merged["local_path"] == "/tmp/c.jpg"
    sec = merged.get("secondary_images")
    assert isinstance(sec, list) and len(sec) == 2
    assert sec[0]["local_path"] == "/tmp/a.jpg"
    assert sec[1]["local_path"] == "/tmp/b.jpg"


def test_collect_paths_chronological():
    fc = {
        "file_type": "image",
        "local_path": "/z/new.jpg",
        "secondary_images": [
            {"file_type": "image", "local_path": "/x/old.jpg"},
            {"file_type": "image", "local_path": "/y/mid.jpg"},
        ],
    }
    assert collect_reference_paths_chronological(fc) == [
        "/x/old.jpg",
        "/y/mid.jpg",
        "/z/new.jpg",
    ]


def test_build_multiref_prompt():
    p = build_reference_user_prompt("замени фон", ref_count=2)
    assert "Reference image 1" in p
    assert "Reference image 2" in p
    assert "first photo" in p


def test_identity_mode_three_refs():
    assert multiref_identity_mode("сделай как в форсаже", ref_count=3)
    p = build_reference_user_prompt("сделай как в форсаже", ref_count=3)
    assert "identity preservation" in p
    assert "facial structure" in p
    assert "three references" in p


def test_identity_markers_ru():
    t = "сохрани черты лица и форму тела, сделай в стиле кино"
    assert prose_wants_identity_preservation(t)
    assert multiref_identity_mode(t, ref_count=2)
    p = build_reference_user_prompt(t, ref_count=2)
    assert "likeness" in p


def test_attachment_composite(tmp_path: Path, monkeypatch):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"x" * 200)
    fc = {"file_type": "image", "local_path": str(img)}
    monkeypatch.setenv("IMAGE_GEN_REFERENCE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_GEN_NL_ROUTE", "true")
    assert attachment_wants_image_generation(fc, "замени фон с первого фото")


def test_multimodal_three_refs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IMAGE_GEN_REFERENCE_ENABLED", "true")
    paths = []
    for name in ("one.png", "two.png", "three.png"):
        p = tmp_path / name
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 80)
        paths.append(p)
    fc = {
        "file_type": "image",
        "local_path": str(paths[2]),
        "secondary_images": [
            {"file_type": "image", "local_path": str(paths[0])},
            {"file_type": "image", "local_path": str(paths[1])},
        ],
    }
    mod = ImageGeneratorModule()
    collected = mod._collect_reference_paths({"meta": {"file_context": fc}})
    assert len(collected) == 3
    content = mod._build_chat_user_content("перенеси с 1 на 2", collected)
    assert isinstance(content, list)
    assert sum(1 for part in content if part.get("type") == "image_url") == 3
