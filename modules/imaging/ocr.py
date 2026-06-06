from __future__ import annotations

import os
from typing import Any, Dict

from core.error_analysis import record_error_event
from modules.imaging.image_tools import safe_load_image


class OCRModule:
    def __init__(self) -> None:
        self.backend = os.getenv("IMAGE_OCR_BACKEND", "none").strip().lower()
        self.external_endpoint = os.getenv("OCR_API_ENDPOINT", "")
        self.langs = os.getenv("OCR_LANGS", "eng+rus")
        self.max_res = int(os.getenv("IMAGE_MAX_RESOLUTION", "2048"))

    async def extract_text(self, file_path: str) -> Dict[str, Any]:
        if self.backend == "none":
            return {"ok": False, "error": "ocr disabled"}
        if self.backend == "external_api":
            if not self.external_endpoint:
                return {"ok": False, "error": "ocr external api not configured"}
            from modules.imaging.http_util import post_image_file

            resp = await post_image_file(self.external_endpoint, file_path, field_name="file")
            if not resp.get("ok"):
                return {"ok": False, "error": resp.get("error") or "ocr external failed"}
            text = str(resp.get("text") or resp.get("result") or resp.get("data") or "").strip()
            if not text and isinstance(resp.get("data"), dict):
                text = str(resp["data"].get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "ocr external empty response"}
            return {"ok": True, "text": text}
        if self.backend == "tesseract":
            try:
                import pytesseract  # type: ignore
            except Exception as e:
                record_error_event("ocr", "pytesseract unavailable", exc=e)
                return {"ok": False, "error": "pytesseract unavailable"}
            loaded = safe_load_image(file_path)
            if not loaded.get("ok"):
                record_error_event("ocr", "safe_load_image failed", extra={"error": loaded.get("error")})
                return {"ok": False, "error": loaded.get("error", "safe_load_failed")}
            img = loaded["image"]
            try:
                text = pytesseract.image_to_string(img, lang=self.langs)
            except Exception as e:
                record_error_event("ocr", "tesseract extraction failed", exc=e)
                return {"ok": False, "error": str(e)}
            return {"ok": True, "text": (text or "").strip()}
        return {"ok": False, "error": "unknown ocr backend"}
