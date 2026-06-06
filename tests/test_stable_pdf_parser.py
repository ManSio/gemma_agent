"""stable_pdf_parser: pypdf на реальном минимальном PDF."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_libraries.stable_pdf_parser.module import StablePDFParser


def _blank_pdf_bytes() -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter  # type: ignore

    buf = BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(buf)
    return buf.getvalue()


class StablePdfParserTests(unittest.TestCase):
    def test_missing_file(self) -> None:
        r = StablePDFParser().parse_pdf("/nonexistent/x.pdf")
        self.assertFalse(r.get("ok"))

    def test_parse_real_pdf_via_pypdf(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(_blank_pdf_bytes())
            path = tf.name
        try:
            r = StablePDFParser().parse_pdf(path)
            self.assertTrue(r.get("ok"))
            self.assertGreaterEqual(int(r.get("pages") or 0), 1)
            self.assertEqual(r.get("metadata", {}).get("backend"), "pypdf")
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
