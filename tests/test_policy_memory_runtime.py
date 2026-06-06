"""Policy memory runtime: slots, hints, turns telemetry."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.policy_memory_runtime import (
    build_policy_memory_hints,
    compute_memory_telemetry,
    detect_web_not_rss_preference,
    extract_policy_slots,
    hint_tags_from_text,
    update_policy_slots_on_user_turn,
)


class PolicyMemoryRuntimeTests(unittest.TestCase):
    def test_web_not_rss_detect(self) -> None:
        self.assertTrue(detect_web_not_rss_preference("новости из интернета, не через rss"))
        self.assertFalse(detect_web_not_rss_preference("как дела"))

    def test_recheck_slot_and_hint(self) -> None:
        recent = [
            {"role": "user", "text": "население Галаца"},
            {"role": "assistant", "text": "Около 250 тысяч."},
            {"role": "user", "text": "перепроверь последний вопрос"},
        ]
        rec: dict = {"routing_prefs": {}}
        update_policy_slots_on_user_turn(rec, "перепроверь последний вопрос", recent)
        slots = extract_policy_slots(rec, recent, "перепроверь последний вопрос")
        self.assertIn("recheck_anchor", slots)
        self.assertIn("галаца", slots["recheck_anchor"]["last_user_question"].lower())
        hint = build_policy_memory_hints("перепроверь последний вопрос", recent, persisted=rec)
        self.assertIn("RECHECK_ANCHOR", hint)
        tags = hint_tags_from_text(hint)
        self.assertIn("RECHECK_ANCHOR", tags)

    def test_pending_facts_in_slots(self) -> None:
        rec = {"pending_facts_confirmation": {"country": "Польша"}, "routing_prefs": {}}
        slots = extract_policy_slots(rec, [], "да")
        self.assertIn("pending_facts", slots)
        hint = build_policy_memory_hints("да", [], persisted=rec)
        self.assertIn("FACTS_PENDING", hint)

    def test_memory_telemetry_correction_pending(self) -> None:
        rec = {
            "routing_prefs": {
                "pending_correction": {"instruction": "короче", "turns_left": 2},
                "dialogue_slot": {"kind": "article_thread", "turns_left": 3, "meta": {}},
            }
        }
        tel = compute_memory_telemetry(
            persisted=rec,
            user_text="ок",
            recent_dialogue=[],
            external_hint="CORRECTION_PENDING: x\nARTICLE_THREAD: y",
        )
        self.assertTrue(tel.get("correction_pending"))
        self.assertEqual(tel.get("dialogue_slot_kind"), "article_thread")
        self.assertIn("ARTICLE_THREAD", tel.get("policy_hint_tags") or [])

    def test_image_edit_session_slot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "out.jpg"
            img.write_bytes(b"x")
            with patch.dict(
                "os.environ",
                {"GEMMA_PROJECT_ROOT": td, "IMAGE_EDIT_SESSION_ENABLED": "true"},
                clear=False,
            ):
                from core.image_edit_session import bind_image_output

                bind_image_output("u9", "c9", str(img))
                slots = extract_policy_slots({}, [], "переделай фон", user_id="u9", chat_id="c9")
        self.assertIn("image_edit_session", slots)


if __name__ == "__main__":
    unittest.main()
