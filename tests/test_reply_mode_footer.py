"""Admin mode footer v1."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.dialogue_slots import SLOT_ARTICLE_THREAD, SLOT_SPATIAL_PROJECT, set_slot
from core.reply_mode_footer import (
    append_mode_footer,
    build_mode_footer_fields,
    footer_visible_for_user,
    format_mode_footer,
    should_skip_mode_footer,
)


class TestReplyModeFooter(unittest.TestCase):
    def test_build_spatial_tag(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_SPATIAL_PROJECT, {"phase": "awaiting_feedback"}, turns=8)
        fields = build_mode_footer_fields(
            output_meta={"module": "spatial_design", "phase": "awaiting_feedback"},
            plan_module="spatial_design",
            route_context={"route_intent": "spatial_design"},
            persisted=rec,
            trace_id="abc123def456",
        )
        self.assertIn("Планировка", fields["human"])
        self.assertIn("сверка", fields["human"])
        tag = fields["machine_tag"]
        self.assertIn("gemma:mf", tag)
        self.assertIn("i=spatial_design", tag)
        self.assertIn("m=spatial_design", tag)
        self.assertIn("s=spatial_project", tag)
        self.assertIn("p=awaiting_feedback", tag)
        self.assertIn("t=abc123def456", tag)

    def test_append_once(self) -> None:
        fields = {"human": "диалог", "machine_tag": "[gemma:mf|v1|i=general]"}
        once = append_mode_footer("Привет", fields=fields)
        twice = append_mode_footer(once, fields=fields)
        self.assertEqual(once.count("[gemma:mf"), 1)
        self.assertEqual(twice.count("[gemma:mf"), 1)

    def test_admin_visibility(self) -> None:
        with patch.dict("os.environ", {"TELEGRAM_REPLY_MODE_FOOTER": "off"}, clear=False):
            self.assertFalse(footer_visible_for_user(user_id="1", is_admin=True))
        with patch.dict("os.environ", {"TELEGRAM_REPLY_MODE_FOOTER": "admin"}, clear=False):
            self.assertTrue(footer_visible_for_user(user_id="1", is_admin=True))
            self.assertFalse(footer_visible_for_user(user_id="2", is_admin=False))

    def test_spatial_meta_overrides_article_slot_in_footer(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_ARTICLE_THREAD, {"topic": "статья"}, turns=5)
        fields = build_mode_footer_fields(
            output_meta={"module": "spatial_design", "phase": "awaiting_feedback"},
            plan_module="spatial_design",
            route_context={"route_intent": "spatial_design"},
            persisted=rec,
            trace_id="trace1",
        )
        self.assertIn("Планировка", fields["human"])
        self.assertIn("s=spatial_project", fields["machine_tag"])

    def test_skip_confirmation(self) -> None:
        self.assertTrue(should_skip_mode_footer({"confirmation": True}))

    def test_format_has_separator(self) -> None:
        out = format_mode_footer({"human": "новости", "machine_tag": "[gemma:mf|i=news]"})
        self.assertIn("───", out)
        self.assertIn("Режим: новости", out)


if __name__ == "__main__":
    unittest.main()
