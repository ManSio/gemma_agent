"""Обрезка контекста диалога (/session_trim)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.session_trim import trim_user_session


class SessionTrimTests(unittest.TestCase):
    def test_trims_recent_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data" / "users" / "behavior"
            data.mkdir(parents=True)
            bf = data / "99__dm.json"
            msgs = [{"role": "user", "text": f"m{i}"} for i in range(12)]
            bf.write_text(
                '{"recent_messages": '
                + __import__("json").dumps(msgs)
                + ', "dialogue_summary": "long summary text"}',
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"GEMMA_PROJECT_ROOT": str(root)}):
                with mock.patch("core.behavior_store.BehaviorStore._path", return_value=str(bf)):
                    rep = trim_user_session("99", None, keep_recent=4, bump_kv=False)
            self.assertTrue(rep.get("ok"))
            raw = __import__("json").loads(bf.read_text(encoding="utf-8"))
            self.assertEqual(len(raw.get("recent_messages") or []), 4)
            self.assertEqual(raw.get("dialogue_summary"), "")


if __name__ == "__main__":
    unittest.main()
