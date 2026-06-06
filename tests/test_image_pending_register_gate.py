"""Не регистрировать фото в pending после расхода на image gen."""
from __future__ import annotations

from core.input_layer import InputLayer


def test_should_not_register_after_captioned_gen():
    fc = {"file_type": "image", "local_path": "/tmp/plan.jpg"}
    assert not InputLayer._should_register_image_pending_after_turn(
        file_context=fc,
        payload="сгенерируй картинку интерьера по этому плану",
        meta={},
    )


def test_should_not_register_interior_designer_caption():
    fc = {"file_type": "image", "local_path": "/tmp/plan.jpg"}
    payload = (
        "Действуй как профессиональный дизайнер интерьеров. "
        "Мне нужно продумать планировку"
    )
    assert not InputLayer._should_register_image_pending_after_turn(
        file_context=fc,
        payload=payload,
        meta={},
    )


def test_should_register_photo_only_waiting_for_text():
    fc = {"file_type": "image", "local_path": "/tmp/plan.jpg"}
    assert InputLayer._should_register_image_pending_after_turn(
        file_context=fc,
        payload="",
        meta={},
    )


def test_should_not_register_after_auto_attach():
    fc = {"file_type": "image", "local_path": "/tmp/plan.jpg"}
    assert not InputLayer._should_register_image_pending_after_turn(
        file_context=fc,
        payload="перерисуй в акварели",
        meta={"image_pending_auto_attach": True},
    )
