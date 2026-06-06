"""Route memory hints in slot_external_hint (research → prod)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.dialogue_slots import SLOT_ARTICLE_THREAD, slot_external_hint


class DialogueSlotMemoryHintsTests(unittest.TestCase):
    def test_article_followup_includes_topic_line(self) -> None:
        long_paste = ("Аэропорт Мюнхена закрыли из-за беспилотника. " * 12).strip()
        recent = [
            {"role": "user", "text": long_paste},
            {"role": "assistant", "text": "Кратко: аэропорт Мюнхена закрыт."},
        ]
        persisted = {
            "routing_prefs": {
                "dialogue_slot": {
                    "kind": SLOT_ARTICLE_THREAD,
                    "turns_left": 5,
                    "meta": {"topic": "Мюнхен аэропорт"},
                }
            }
        }
        hint = slot_external_hint("что ещё известно?", recent, persisted=persisted)
        self.assertIn("ARTICLE_THREAD", hint)
        self.assertIn("ARTICLE_THREAD_TOPIC", hint)
        self.assertIn("мюнхен", hint.lower())

    def test_pending_facts_hint(self) -> None:
        persisted = {"pending_facts_confirmation": {"country": "Германия"}}
        hint = slot_external_hint("да", [], persisted=persisted)
        self.assertIn("FACTS_PENDING", hint)
        self.assertIn("германия", hint.lower())

    def test_image_edit_session_hint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "out.jpg"
            img.write_bytes(b"x")
            with patch.dict(
                "os.environ",
                {"GEMMA_PROJECT_ROOT": td, "IMAGE_EDIT_SESSION_ENABLED": "true"},
                clear=False,
            ):
                from core.image_edit_session import bind_image_output

                bind_image_output("u1", "c1", str(img))
                hint = slot_external_hint(
                    "переделай — добавь закат",
                    [],
                    user_id="u1",
                    chat_id="c1",
                )
        self.assertIn("IMAGE_EDIT_SESSION", hint)

    def test_web_not_rss_hint(self) -> None:
        rec = {"routing_prefs": {"policy_slots": {"user_pref": {"web_over_rss": True}}}}
        hint = slot_external_hint(
            "что нового",
            [],
            persisted=rec,
        )
        self.assertIn("WEB_NOT_RSS", hint)

    def test_recheck_hint_via_policy_layer(self) -> None:
        recent = [
            {"role": "user", "text": "сколько букв R в слове Google"},
            {"role": "assistant", "text": "Один."},
        ]
        hint = slot_external_hint("перепроверь", recent, persisted={"routing_prefs": {}})
        self.assertIn("RECHECK_ANCHOR", hint)


if __name__ == "__main__":
    unittest.main()
