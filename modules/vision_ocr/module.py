"""Vision OCR module: real text extraction from local image path."""
from __future__ import annotations

from typing import Any, Dict, List

from core.models import Output
from modules.imaging.ocr import OCRModule


class VisionOCRModule:
    """Модуль OCR без тестовых заглушек."""

    def __init__(self, config: Dict[str, Any] | None = None):
        self.config = config or {}
        self.ocr = OCRModule()

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input", {}) if isinstance(args, dict) else {}
        context = args.get("context", {}) if isinstance(args, dict) else {}
        file_context = context.get("file_context", {}) if isinstance(context, dict) else {}
        local_path = str(file_context.get("local_path") or "").strip()
        payload = input_data.get("payload", "")

        if not local_path:
            return [
                Output(
                    type="text",
                    payload=(
                        "Не вижу локального файла изображения для OCR. "
                        "Отправь фото с подписью `/ocr` или реплаем на фото."
                    ),
                    meta={"module": "vision_ocr", "error": "missing_local_path", "input": payload},
                )
            ]

        res = await self.ocr.extract_text(local_path)
        if not res.get("ok"):
            err = str(res.get("error") or "ocr_failed")
            hint = (
                "OCR сейчас недоступен. Проверь `IMAGE_OCR_BACKEND` "
                "(например `tesseract`) и наличие зависимостей OCR на сервере."
            )
            return [
                Output(
                    type="text",
                    payload=f"Не удалось распознать текст: {err}.\n{hint}",
                    meta={"module": "vision_ocr", "error": err, "local_path": local_path},
                )
            ]

        text = str(res.get("text") or "").strip()
        if not text:
            return [
                Output(
                    type="text",
                    payload="Текст на изображении не найден или распознан пусто.",
                    meta={"module": "vision_ocr", "ok": True, "text_empty": True, "local_path": local_path},
                )
            ]
        return [
            Output(
                type="text",
                payload=f"Распознанный текст:\n{text}",
                meta={"module": "vision_ocr", "ok": True, "local_path": local_path},
            )
        ]