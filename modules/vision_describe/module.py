"""
Vision Describe Module — описание изображений через OpenRouter vision.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

from core.models import Output
from core.brain.vision_io import vision_image_parts_for_brain, vision_mime_from_path
from core.brain.vision_llm import brain_run_vision_precaption, brain_default_vision_system_prompt

logger = logging.getLogger(__name__)


class VisionDescribeModule:
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input", {})
        payload = str(input_data.get("payload", "") or "").strip()
        file_context = args.get("file_context", input_data.get("file_context") or input_data.get("meta", {}).get("file_context"))

        vision_parts: List[Tuple[str, str]] = []

        if isinstance(file_context, dict) and file_context.get("file_type") == "image":
            parts = vision_image_parts_for_brain(file_context)
            if parts:
                vision_parts = parts

        if not vision_parts and payload:
            local = input_data.get("local_path") or (file_context or {}).get("local_path") if isinstance(file_context, dict) else None
            if local and os.path.isfile(str(local)):
                mime = vision_mime_from_path(str(local))
                try:
                    import base64
                    with open(str(local), "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    vision_parts = [(mime, b64)]
                except OSError as e:
                    logger.warning("[vision_describe] cannot read image: %s", e)

        if not vision_parts:
            return [Output(
                type="text",
                payload="Не удалось получить изображение для описания. Пришли фото как вложение.",
                meta={"module": "vision_describe", "error": "no_image"},
            )]

        user_prompt = payload or "Что на изображении? Опиши объекты и любой текст на кадре."
        try:
            description = await brain_run_vision_precaption(
                user_text=user_prompt,
                vision_parts=vision_parts,
            )
        except Exception as e:
            logger.warning("[vision_describe] vision call failed: %s", e)
            return [Output(
                type="text",
                payload="Не удалось обработать изображение. Попробуй позже.",
                meta={"module": "vision_describe", "error": str(e)},
            )]

        if not description or not description.strip():
            return [Output(
                type="text",
                payload="Модель не смогла описать изображение. Возможно, снимок слишком сложный или нечёткий.",
                meta={"module": "vision_describe"},
            )]

        return [Output(
            type="text",
            payload=description.strip(),
            meta={"module": "vision_describe"},
        )]
