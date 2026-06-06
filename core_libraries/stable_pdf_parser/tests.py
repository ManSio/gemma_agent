"""
Tests for stable PDF parser library
"""
import unittest

from core_libraries.stable_pdf_parser.module import StablePDFParser

class TestStablePDFParser(unittest.TestCase):
    
    def setUp(self):
        self.parser = StablePDFParser()
    
    def test_parse_pdf(self):
        """Test PDF parsing"""
        result = self.parser.parse_pdf("test.pdf")
        self.assertIsInstance(result, dict)
        self.assertIn("text", result)
    
    def test_extract_text(self):
        """Test text extraction"""
        text = self.parser.extract_text("test.pdf")
        self.assertIsInstance(text, str)

if __name__ == "__main__":
    unittest.main()