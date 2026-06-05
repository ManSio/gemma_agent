import unittest

from core.autonomy_module import AutonomyModule


class AutonomyKwargsTests(unittest.TestCase):
    def test_auto_diagnostics_ignores_user_id(self):
        m = AutonomyModule()
        out = m.auto_diagnostics(user_id="123")
        self.assertIn("events_total", out)

    def test_auto_suggestions_ignores_extra_kwargs(self):
        m = AutonomyModule()
        out = m.auto_suggestions(user_id="x", foo=1)
        self.assertIsInstance(out, list)
