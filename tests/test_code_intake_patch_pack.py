import unittest

from core.code_intake import CodeIntakeLayer


class CodeIntakePatchPackTests(unittest.TestCase):
    def test_patch_pack_sorted(self):
        ci = CodeIntakeLayer()
        pack = ci.build_patch_pack_multi(
            "a.py",
            ["lint warning", "security issue", "refactor readability"],
        )
        self.assertTrue(pack["ok"])
        self.assertGreaterEqual(pack["count"], 3)
        priorities = [it["priority"] for it in pack["items"]]
        # high should appear before medium/low if present
        if "high" in priorities and "low" in priorities:
            self.assertLess(priorities.index("high"), priorities.index("low"))


if __name__ == "__main__":
    unittest.main()
