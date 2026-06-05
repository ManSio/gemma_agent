import unittest

from core.security_manager import SecurityManager


class SecurityManagerTests(unittest.TestCase):
    def setUp(self):
        self.sm = SecurityManager()

    def test_ok_empty(self):
        r = self.sm.evaluate(flood={}, link_safety={}, file_context={})
        self.assertEqual(r["level"], "ok")
        self.assertEqual(r["issues"], [])

    def test_high_risk_dangerous_link(self):
        r = self.sm.evaluate(
            flood={"blocked": False},
            link_safety={"worst": "dangerous"},
            file_context={},
        )
        self.assertEqual(r["level"], "high_risk")

    def test_warning_suspicious_link(self):
        r = self.sm.evaluate(
            flood={},
            link_safety={"worst": "suspicious"},
            file_context={},
        )
        self.assertEqual(r["level"], "warning")

    def test_file_context_error_warning(self):
        r = self.sm.evaluate(flood={}, link_safety={}, file_context={"error": "size_limit_exceeded"})
        self.assertEqual(r["level"], "warning")


if __name__ == "__main__":
    unittest.main()
