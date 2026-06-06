import os
from pathlib import Path

from core.image_gen_nl import (
    prose_wants_new_image_project,
    attachment_wants_image_generation,
    image_gen_nl_route_enabled,
    prose_wants_image_composite,
    prose_wants_image_edit,
    prose_wants_image_generation,
    prose_wants_image_pending_followup,
    prose_wants_image_scene_on_photo,
    prose_wants_image_style_transform,
    text_eligible_for_pending_image_attach,
    strip_nl_imagine_boilerplate,
)


def test_prose_wants_new_image_project_ru():
    assert prose_wants_new_image_project("новый проект, другая планировка")
    assert prose_wants_new_image_project("Начнём заново с другого плана")
    assert not prose_wants_new_image_project("переделай стол")


def test_prose_wants_ru_typo_and_plain():
    assert prose_wants_image_generation("Сгенирируешь изображения для бота в телеграме")
    assert prose_wants_image_generation("сгенерируй картинку космос и ракета")
    assert prose_wants_image_generation("Generate an image of a red apple")
    assert not prose_wants_image_generation("")
    assert not prose_wants_image_generation("/imagine cat")


def test_prose_rejects_dev_context():
    t = "сгенерируй картинку и добавь в module.json entrypoint"
    assert not prose_wants_image_generation(t)


def test_strip_boilerplate():
    s = strip_nl_imagine_boilerplate("Сгенерируй картинку для бота в стиле киберпанк")
    assert "киберпанк" in s.lower()
    assert "сгенерируй" not in s.lower()


def test_style_transform_caption():
    assert prose_wants_image_style_transform("🖼 сделай как мультик")
    assert prose_wants_image_style_transform("сделай как мультик")
    assert prose_wants_image_style_transform("в стиле аниме")
    assert not prose_wants_image_style_transform("что на фото")


def test_scene_on_photo_racing_boy():
    cap = "🖼 сделай мальчика на фото в гоночной машине на треке"
    assert prose_wants_image_scene_on_photo(cap)
    txt = "🖼 сгенерируй мальчика на фото что он в гоночной машине на треке"
    assert prose_wants_image_scene_on_photo(txt)
    assert not prose_wants_image_scene_on_photo("что на фото?")


def test_attachment_scene_on_photo(tmp_path: Path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    fc = {"file_type": "image", "local_path": str(img)}
    cap = "🖼 сделай мальчика на фото в гоночной машине на треке"
    assert attachment_wants_image_generation(fc, cap)


def test_composite_phrases():
    assert prose_wants_image_composite("замени фон на гоночный трек")
    assert prose_wants_image_composite("перенеси мальчика с первого фото на второе")


def test_attachment_style_transform(tmp_path: Path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    fc = {"file_type": "image", "local_path": str(img)}
    assert attachment_wants_image_generation(fc, "🖼 сделай как мультик")


def test_scene_from_photografii_and_swat_followup():
    assert prose_wants_image_scene_on_photo("Сделай из фотографий спец подразделения swat")
    assert prose_wants_image_pending_followup("Сделай его чтоб он стал из спец подразделения swat")
    assert prose_wants_image_pending_followup("Сделай с фото как в фильме Форсаж")
    assert text_eligible_for_pending_image_attach("Сделай из фотографий спец подразделения swat")
    assert not prose_wants_image_pending_followup("что на фото?")


def test_prose_rejects_abstract_change_not_image_edit():
    assert not prose_wants_image_edit("что мы можем изменить в мире?")
    assert not prose_wants_image_edit("как изменить жизнь к лучшему")
    assert prose_wants_image_edit("изменить фон на фото")
    assert prose_wants_image_edit("перерисуй в акварели")


def test_route_disabled_via_env(monkeypatch):
    monkeypatch.setenv("IMAGE_GEN_NL_ROUTE", "false")
    assert image_gen_nl_route_enabled() is False
    monkeypatch.delenv("IMAGE_GEN_NL_ROUTE", raising=False)
    assert image_gen_nl_route_enabled() is True
