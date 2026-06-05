import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import operator_rules as orules


class TestOperatorRules(unittest.TestCase):
    def test_force_general_pattern(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rules.json"
            p.write_text(
                json.dumps(
                    {"force_general_when_text_matches": [r"t\.me/\+"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OPERATOR_RULES_PATH": str(p)}, clear=False):
                orules._cache.clear()  # noqa: SLF001
                orules._cache_mtime = 0.0  # noqa: SLF001
                self.assertTrue(orules.force_general_intent_by_operator_patterns("x https://t.me/+abc"))
                self.assertFalse(orules.force_general_intent_by_operator_patterns("2+2"))

    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rules.json"
            p.write_text('{"prefer_general_over_math_globally": true}', encoding="utf-8")
            with patch.dict(os.environ, {"OPERATOR_RULES_PATH": str(p)}, clear=False):
                orules._cache.clear()  # noqa: SLF001
                orules._cache_mtime = 0.0  # noqa: SLF001
                s = orules.snapshot_for_operator()
                self.assertTrue(s.get("exists"))
                self.assertTrue(s.get("prefer_general_over_math_globally"))


if __name__ == "__main__":
    unittest.main()
