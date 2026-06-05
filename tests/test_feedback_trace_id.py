"""trace_id в session_task и ответе на 👎."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.user_correction_bus import format_learning_ack_from_rating, format_trace_id_for_feedback
from core.user_response_feedback import apply_user_rating


class FeedbackTraceIdTests(unittest.TestCase):
    def test_trace_hint_format(self) -> None:
        hint = format_trace_id_for_feedback("abc123def456")
        self.assertIn("abc123def456"[:12], hint)
        self.assertIn("turns_search", hint)

    def test_rating_includes_trace_from_session_task(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data" / "users" / "behavior"
            data.mkdir(parents=True)
            bf = data / "1__dm.json"
            bf.write_text(
                '{"session_task": {"last_trace_id": "trace-xyz-99", "last_user_excerpt": "hi"}}',
                encoding="utf-8",
            )

            class _BS:
                def _path(self, uid, gid):
                    return str(bf)

                def load(self, uid, gid):
                    import json

                    return json.loads(bf.read_text(encoding="utf-8"))

                def save(self, uid, gid, rec):
                    import json

                    bf.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

            with mock.patch.dict("os.environ", {"GEMMA_PROJECT_ROOT": str(root)}):
                rep = apply_user_rating(
                    user_id="1",
                    score=-1,
                    behavior_store=_BS(),
                    source="test",
                )
            self.assertEqual(rep.get("trace_id"), "trace-xyz-99")
            ack = format_learning_ack_from_rating(rep)
            self.assertIn("trace-xyz"[:12], ack)

    def test_turn_observer_writes_trace_id(self) -> None:
        from core.turn_observer import record_from_turn_outcome

        with mock.patch("core.turn_observer.append_turn_record") as ar:
            record_from_turn_outcome(
                {
                    "user_id": "1",
                    "outcome": "ok",
                    "trace_id": "tid-full-abc",
                    "user_excerpt": "test",
                    "assistant_excerpt": "ok",
                }
            )
            row = ar.call_args[0][0]
            self.assertEqual(row.get("trace_id"), "tid-full-abc")


if __name__ == "__main__":
    unittest.main()
