"""
Tests for stable vision library
"""
import unittest

from core_libraries.stable_vision.module import StableVision

class TestStableVision(unittest.TestCase):
    
    def setUp(self):
        self.vision = StableVision()
    
    def test_analyze_image(self):
        """Test image analysis"""
        result = self.vision.analyze_image("test.png")
        self.assertIsInstance(result, dict)
        self.assertIn("width", result)
    
    def test_extract_text_from_image(self):
        """Test text extraction from image"""
        text = self.vision.extract_text_from_image("test.png")
        self.assertIsInstance(text, str)

if __name__ == "__main__":
    unittest.main()