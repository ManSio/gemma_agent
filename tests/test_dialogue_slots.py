"""Слоты диалога: погода-await-city, article_thread."""
from __future__ import annotations

import unittest

from core.dialogue_slots import (
    SLOT_ARTICLE_THREAD,
    SLOT_SPATIAL_PROJECT,
    SLOT_WEATHER_CITY,
    apply_slot_to_task_facts,
    get_active_slot,
    on_assistant_reply,
    resolve_slot_for_turn,
    set_slot,
    should_suppress_image_for_slot,
    user_refers_to_article_thread,
)


class TestDialogueSlots(unittest.TestCase):
    def test_weather_slot_after_clarify(self) -> None:
        rec: dict = {
            "recent_messages": [{"role": "user", "text": "Какая погода"}],
        }
        on_assistant_reply(
            rec,
            "Какой именно город вас интересует? Назовите населённый пункт.",
            user_text="Какая погода",
        )
        self.assertEqual(get_active_slot(rec).get("kind"), SLOT_WEATHER_CITY)
        dlg = [
            {"role": "user", "text": "Какая погода"},
            {"role": "assistant", "text": "Какой именно город?"},
        ]
        ctx = resolve_slot_for_turn("Минск", dlg, rec)
        self.assertTrue(ctx.force_weather)
        self.assertEqual(ctx.weather_city, "Минск")

    def test_task_facts_from_slot(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=3)
        prof: dict = {"is_weather": False}
        apply_slot_to_task_facts(
            prof,
            "Минск",
            [
                {"role": "user", "text": "погода"},
                {"role": "assistant", "text": "Какой город?"},
            ],
            rec,
        )
        self.assertTrue(prof.get("is_weather"))
        self.assertEqual(prof.get("weather_city"), "Минск")

    def test_article_thread_suppresses_image(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_ARTICLE_THREAD, {}, turns=5)
        paste = "«Коммерсант»: " + ("текст статьи. " * 80)
        self.assertTrue(
            should_suppress_image_for_slot(
                "Какова дальнейшая перспектива",
                [{"role": "user", "text": paste}],
                {"file_type": "image", "local_path": "/tmp/x.jpg"},
                persisted=rec,
            )
        )

    def test_user_refers_article(self) -> None:
        self.assertTrue(user_refers_to_article_thread("Я про статью", []))

    def test_on_assistant_does_not_overwrite_spatial_slot(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_SPATIAL_PROJECT, {"phase": "awaiting_feedback"}, turns=8)
        long_summary = "Исходя из вашего запроса " + ("планировка " * 40)
        on_assistant_reply(rec, long_summary, user_text="план по фото")
        self.assertEqual(get_active_slot(rec).get("kind"), SLOT_SPATIAL_PROJECT)
