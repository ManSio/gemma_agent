import json
import os
import tempfile
import unittest

from core.message_archive import (
    append_turn_to_message_archive,
    load_message_archive_items,
    maybe_backfill_context_recent_dialogue,
    should_backfill_dialogue_from_archive,
)


class MessageArchiveTests(unittest.TestCase):
    _ENV_KEYS = (
        "BEHAVIOR_DATA_DIR",
        "DIALOGUE_MESSAGE_ARCHIVE_ENABLED",
        "DIALOGUE_MESSAGE_ARCHIVE_MAX",
        "DIALOGUE_ARCHIVE_BACKFILL_ENABLED",
    )

    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp()
        self._env_saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        os.environ["BEHAVIOR_DATA_DIR"] = self._dir

    def tearDown(self) -> None:
        for k, v in self._env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_append_and_trim(self):
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_MAX"] = "6"
        uid = "u1"
        for i in range(5):
            append_turn_to_message_archive(
                uid,
                None,
                {"role": "user", "text": f"m{i}", "telegram_ts": 1000 + i},
                f"a{i}",
            )
        items = load_message_archive_items(uid, None)
        self.assertEqual(len(items), 6)
        self.assertIn("m4", json.dumps(items))
        self.assertNotIn("m0", json.dumps(items))

    def test_disabled(self):
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "false"
        append_turn_to_message_archive("u2", None, {"role": "user", "text": "x"}, "y")
        self.assertEqual(load_message_archive_items("u2", None), [])

    def test_backfill_disabled_does_not_replace(self):
        """При DIALOGUE_ARCHIVE_BACKFILL_ENABLED=false recent_dialogue не подменяется."""
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        os.environ["DIALOGUE_ARCHIVE_BACKFILL_ENABLED"] = "false"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_MAX"] = "80"
        uid = "ubf"
        for i in range(12):
            append_turn_to_message_archive(
                uid,
                None,
                {"role": "user", "text": f"u{i}"},
                f"a{i}",
            )
        ctx = {"recent_dialogue": [{"role": "user", "text": "u9"}, {"role": "assistant", "text": "a9"}]}
        maybe_backfill_context_recent_dialogue(
            ctx,
            user_id=uid,
            group_id=None,
            user_text="continue",
            input_meta={},
        )
        rd = ctx.get("recent_dialogue") or []
        self.assertEqual(len(rd), 2)
        self.assertEqual(rd[0]["text"], "u9")

    def test_backfill_enabled_replaces_thin_recent_dialogue(self):
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        os.environ["DIALOGUE_ARCHIVE_BACKFILL_ENABLED"] = "true"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_MAX"] = "80"
        uid = "ubf_on"
        for i in range(8):
            append_turn_to_message_archive(
                uid,
                None,
                {"role": "user", "text": f"turn_u{i}"},
                f"turn_a{i}",
            )
        ctx = {"recent_dialogue": [{"role": "user", "text": "x"}, {"role": "assistant", "text": "y"}]}
        maybe_backfill_context_recent_dialogue(
            ctx,
            user_id=uid,
            group_id=None,
            user_text="continue",
            input_meta={},
        )
        rd = ctx.get("recent_dialogue") or []
        self.assertGreater(len(rd), 2)
        joined = json.dumps(rd, ensure_ascii=False)
        self.assertIn("turn_u", joined)
        self.assertNotIn('"text": "x"', joined)

    def test_short_ok_does_not_trigger_backfill(self):
        """Короткое «ок» не подменяет recent архивом при тонком окне."""
        os.environ["DIALOGUE_ARCHIVE_BACKFILL_ENABLED"] = "true"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        self.assertFalse(
            should_backfill_dialogue_from_archive(
                user_text="ок",
                recent_dialogue=[
                    {"role": "user", "text": "u"},
                    {"role": "assistant", "text": "a"},
                ],
                input_meta={},
            )
        )

    def test_should_backfill_false_when_env_off(self):
        os.environ["DIALOGUE_ARCHIVE_BACKFILL_ENABLED"] = "false"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        self.assertFalse(
            should_backfill_dialogue_from_archive(
                user_text="ok",
                recent_dialogue=[{"role": "user", "text": "long enough text here"}],
                input_meta={"telegram_has_forward": True},
            )
        )
        self.assertFalse(
            should_backfill_dialogue_from_archive(
                user_text="посмотри назад и проверь переписку",
                recent_dialogue=[{"role": "user", "text": f"x{i}"} for i in range(8)],
                input_meta={},
            )
        )

    def test_should_backfill_false_when_archive_disabled(self):
        os.environ["DIALOGUE_ARCHIVE_BACKFILL_ENABLED"] = "true"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "false"
        self.assertFalse(
            should_backfill_dialogue_from_archive(
                user_text="ok",
                recent_dialogue=[],
                input_meta={"telegram_has_forward": True},
            )
        )

    def test_should_backfill_on_forward_when_enabled(self):
        os.environ["DIALOGUE_ARCHIVE_BACKFILL_ENABLED"] = "true"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        self.assertTrue(
            should_backfill_dialogue_from_archive(
                user_text="ok",
                recent_dialogue=[{"role": "user", "text": "long enough text here"}],
                input_meta={"telegram_has_forward": True},
            )
        )

    def test_fifo_trim(self):
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_ENABLED"] = "true"
        os.environ["DIALOGUE_MESSAGE_ARCHIVE_MAX"] = "4"
        uid = "ufifo"
        for i in range(5):
            append_turn_to_message_archive(
                uid,
                None,
                {"role": "user", "text": f"f{i}"},
                f"a{i}",
            )
        items = load_message_archive_items(uid, None)
        self.assertEqual(len(items), 4)
        self.assertNotIn("f0", json.dumps(items))
        self.assertIn("f4", json.dumps(items))


if __name__ == "__main__":
    unittest.main()
