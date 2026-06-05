"""
Stable PDF Parser — извлечение текста через pypdf (как document_intake).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class StablePDFParser:
    """PDF text extraction; без pymupdf — только pypdf из requirements."""

    def __init__(self, max_pages: int = 50) -> None:
        self.max_pages = max(1, int(max_pages))

    def parse_pdf(self, file_path: str) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            return {"ok": False, "error": f"Файл не найден: {file_path}"}
        try:
            text = self.extract_text(file_path)
            pages = self._page_count(file_path)
            return {
                "ok": True,
                "pages": pages,
                "text": text,
                "metadata": {
                    "format": "PDF",
                    "path": str(path.name),
                    "backend": "pypdf",
                },
            }
        except Exception as e:
            logger.exception("[stable_pdf_parser] parse failed path=%s", file_path)
            return {"ok": False, "error": str(e)}

    def _page_count(self, file_path: str) -> int:
        import pypdf  # type: ignore

        with open(file_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            return len(reader.pages)

    def extract_text(self, file_path: str) -> str:
        import pypdf  # type: ignore

        parts: List[str] = []
        with open(file_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages[: self.max_pages]:
                parts.append((page.extract_text() or "").strip())
        return "\n".join(p for p in parts if p)

    async def test(self) -> bool:
        return True
