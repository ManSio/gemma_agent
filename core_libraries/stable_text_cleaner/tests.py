"""
Tests for stable text cleaner library
"""
import unittest

from core_libraries.stable_text_cleaner.module import StableTextCleaner

class TestStableTextCleaner(unittest.TestCase):
    
    def setUp(self):
        self.cleaner = StableTextCleaner()
    
    def test_clean_text(self):
        """Test text cleaning"""
        result = self.cleaner.clean_text("  hello    world  ")
        self.assertEqual(result, "hello world")
    
    def test_normalize_spaces(self):
        """Test space normalization"""
        result = self.cleaner.normalize_spaces("hello   world")
        self.assertEqual(result, "hello world")

if __name__ == "__main__":
    unittest.main()