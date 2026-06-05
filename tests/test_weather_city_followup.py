"""Погода: ответ только городом после уточнения; статья vs картинка в репосте."""
from __future__ import annotations

import unittest

from core.brain.text_helpers import (
    should_suppress_image_for_text_thread,
    task_fact_profile,
    user_refers_to_prior_article,
    weather_pending_city_reply,
)

_KOMMERSANT_SNIPPET = (
    "«Коммерсант»: страны Персидского залива намерены обсудить с США дальнейший статус "
    "американских военных баз в регионе. " * 8
)


class TestWeatherCityFollowup(unittest.TestCase):
    def test_minsk_after_weather_clarify_is_weather(self) -> None:
        dlg = [
            {"role": "user", "text": "Какая погода"},
            {
                "role": "assistant",
                "text": "Какой именно город вас интересует? Назовите населённый пункт.",
            },
            {"role": "user", "text": "Минск"},
        ]
        self.assertTrue(weather_pending_city_reply("Минск", dlg))
        prof = task_fact_profile("Минск", {}, dlg)
        self.assertTrue(prof.get("is_weather"))
        self.assertEqual(prof.get("weather_city"), "Минск")
        self.assertEqual(prof.get("weather_country"), "BY")

    def test_random_minsk_not_weather_without_context(self) -> None:
        self.assertFalse(weather_pending_city_reply("Минск", None))


class TestArticleThreadFollowup(unittest.TestCase):
    def test_followup_after_pasted_article(self) -> None:
        dlg = [
            {"role": "user", "text": _KOMMERSANT_SNIPPET},
            {"role": "assistant", "text": "Страны Персидского залива обсудят с США статус баз…" * 3},
            {"role": "user", "text": "Какова дальнейшего действия"},
        ]
        self.assertTrue(user_refers_to_prior_article("Какова дальнейшего действия", dlg))

    def test_explicit_about_article(self) -> None:
        self.assertTrue(user_refers_to_prior_article("Я про статью", []))

    def test_suppress_image_when_continuing_article(self) -> None:
        dlg = [
            {"role": "user", "text": _KOMMERSANT_SNIPPET},
            {"role": "assistant", "text": "Коммерсант сообщает о переговорах…" * 4},
        ]
        fc = {"file_type": "image", "local_path": "/tmp/x.jpg"}
        self.assertTrue(
            should_suppress_image_for_text_thread("Какова дальнейшего действия", dlg, fc)
        )
