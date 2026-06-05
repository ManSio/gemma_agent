import os
import unittest
from unittest.mock import patch

from core.env_flags import env_truthy, gemma_core_log_full


class EnvFlagsTests(unittest.TestCase):
    def test_truthy(self):
        with patch.dict(os.environ, {"X": "true"}, clear=False):
            self.assertTrue(env_truthy("X"))
        with patch.dict(os.environ, {"X": "0"}, clear=False):
            self.assertFalse(env_truthy("X"))

    def test_gemma_core_log_full(self):
        with patch.dict(os.environ, {"GEMMA_CORE_LOG_FULL": "1"}, clear=False):
            self.assertTrue(gemma_core_log_full())


if __name__ == "__main__":
    unittest.main()
