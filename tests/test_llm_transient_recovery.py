import unittest

from core.brain.llm_transient_recovery import is_transient_llm_error
from core.brain.text_helpers import natural_fallback_response


class LlmTransientRecoveryTests(unittest.TestCase):
    def test_transient_errors(self):
        self.assertTrue(is_transient_llm_error("Event loop is closed"))
        self.assertTrue(is_transient_llm_error("Connection timeout"))
        self.assertFalse(is_transient_llm_error("invalid api key"))

    def test_llm_error_fallback_no_model_excuse(self):
        for i in range(3):
            msg = natural_fallback_response("llm_error", f"u{i}", "почему небо синее")
            low = msg.lower()
            self.assertNotIn("модель не ответила", low)
            self.assertNotIn("штатно", low)
            self.assertNotIn("openrouter", low)


if __name__ == "__main__":
    unittest.main()
