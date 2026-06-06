"""
Vision Layer — анализ изображений через эвристики и вызов LLM.
"""
from typing import Dict, Any, Optional, List
import base64
import io
import logging
import os
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Детекция формата по magic bytes ──────────────────────────────────

_IMAGE_MAGIC: Dict[bytes, str] = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",   # RIFF + WEBP
    b"BM": "bmp",
}


def _detect_format(raw: bytes) -> str:
    for magic, fmt in _IMAGE_MAGIC.items():
        if raw[:len(magic)] == magic:
            # Уточняем webp
            if magic == b"RIFF" and raw[8:12] == b"WEBP":
                return "webp"
            return fmt
    return "unknown"


def _estimate_dimensions(raw: bytes, fmt: str) -> Dict[str, int]:
    """Приблизительно определить размеры изображения по заголовку."""
    try:
        if fmt == "jpeg" and len(raw) > 10:
            # JPEG: ищем маркер SOF0/SOF2 (0xFF 0xC0 / 0xC2)
            i = 2
            while i < len(raw) - 10:
                if raw[i] == 0xFF and raw[i + 1] in (0xC0, 0xC2):
                    height = (raw[i + 5] << 8) | raw[i + 6]
                    width = (raw[i + 7] << 8) | raw[i + 8]
                    return {"width": width, "height": height}
                i += 1
        elif fmt == "png" and len(raw) > 24:
            # PNG: IHDR начиная с offset 16
            w = struct.unpack(">I", raw[16:20])[0]
            h = struct.unpack(">I", raw[20:24])[0]
            return {"width": w, "height": h}
        elif fmt == "gif" and len(raw) > 10:
            w = struct.unpack("<H", raw[6:8])[0]
            h = struct.unpack("<H", raw[8:10])[0]
            return {"width": w, "height": h}
        elif fmt == "bmp" and len(raw) > 24:
            w = struct.unpack("<I", raw[18:22])[0]
            h = struct.unpack("<I", raw[22:26])[0]
            return {"width": w, "height": h}
        elif fmt == "webp" and len(raw) > 30:
            # VP8/VP8L
            if raw[12:16] == b"VP8 " and len(raw) > 30:
                w = struct.unpack("<H", raw[26:28])[0] & 0x3FFF
                h = struct.unpack("<H", raw[28:30])[0] & 0x3FFF
                return {"width": w, "height": h}
    except Exception as e:
        logger.debug('%s optional failed: %s', 'vision_layer', e, exc_info=True)
    return {"width": 0, "height": 0}


def _detect_aspect_region(width: int, height: int) -> str:
    """Определить тип сцены по пропорциям."""
    if width <= 0 or height <= 0:
        return "unknown"
    ratio = width / height
    if ratio > 2.5:
        return "panorama / wide shot"
    if ratio > 1.3:
        return "landscape"
    if 0.75 <= ratio <= 1.3:
        return "square / close crop"
    if ratio < 0.5:
        return "portrait / tall"
    return "portrait"


def _categorize_heuristic(raw: bytes, fmt: str, dims: Dict[str, int]) -> Dict[str, Any]:
    """Собрать все детерминированные признаки об изображении."""
    size_kb = len(raw) / 1024
    info: Dict[str, Any] = {
        "size_kb": round(size_kb, 1),
        "format": fmt,
    }
    if dims.get("width"):
        info["width"] = dims["width"]
        info["height"] = dims["height"]
        info["megapixels"] = round(dims["width"] * dims["height"] / 1_000_000, 2)
        info["scene_type"] = _detect_aspect_region(dims["width"], dims["height"])

    # Признак: фото или графика
    if fmt == "jpeg":
        info["type_guess"] = "photo (JPEG, likely camera/screenshot)"
    elif fmt == "png":
        info["type_guess"] = "graphics / screenshot (PNG, lossless)"
    elif fmt == "gif":
        info["type_guess"] = "animated graphic (GIF)"
    elif fmt == "webp":
        info["type_guess"] = "web image (WebP)"
    else:
        info["type_guess"] = f"unknown format ({fmt})"

    # Размерный класс
    if size_kb < 10:
        info["quality_guess"] = "very small — icon or thumbnail"
    elif size_kb < 100:
        info["quality_guess"] = "small — likely low resolution"
    elif size_kb < 500:
        info["quality_guess"] = "medium — typical web image"
    elif size_kb < 2000:
        info["quality_guess"] = "large — high resolution"
    else:
        info["quality_guess"] = "very large — raw / high quality"

    return info


# ── VisionLayer ────────────────────────────────────────────────────────

class VisionLayer:
    """Слой анализа изображений с эвристиками и вызовом LLM."""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._llm_available = self._check_llm()

    @staticmethod
    def _check_llm() -> bool:
        """Проверить, доступна ли LLM для vision."""
        try:
            from core.openrouter_provider import get_openrouter_provider
            provider = get_openrouter_provider()
            api_key = provider._get_current_api_key()
            return bool(api_key)
        except Exception:
            return False

    async def describe(self, image_data: Any) -> str:
        """Описание изображения: эвристики + LLM (если доступен)."""
        raw = self._to_bytes(image_data)
        if not raw:
            return "Изображение не получено (пустые данные)."

        fmt = _detect_format(raw)
        dims = _estimate_dimensions(raw, fmt)
        info = _categorize_heuristic(raw, fmt, dims)

        lines = [
            f"📷 Анализ изображения:",
        ]
        if info.get('width'):
            lines.append(
                f"  • Размер: {info.get('width', '?')}×{info.get('height', '?')} px"
                f" ({info.get('megapixels', '?')} Мп)"
            )
        lines.extend([
            f"  • Формат: {info['format']}",
            f"  • Объём: {info['size_kb']} КБ",
            f"  • Тип: {info['type_guess']}",
        ])
        if info.get('quality_guess'):
            lines.append(f"  • Качество: {info['quality_guess']}")
        if info.get('scene_type'):
            lines.append(f"  • Сцена: {info['scene_type']}")

        description = "\n".join(lines)

        # LLM enhancement if available
        if self._llm_available:
            llm_desc = await self._llm_describe(raw)
            if llm_desc:
                description += f"\n\n🧠 LLM: {llm_desc}"

        return description

    async def ocr(self, image_data: Any) -> str:
        """Распознавание текста на изображении.

        Пытается вызвать LLM vision для OCR. Если LLM недоступен —
        сообщает, что OCR не поддерживается без LLM.
        """
        raw = self._to_bytes(image_data)
        if not raw:
            return "Изображение не получено (пустые данные)."

        fmt = _detect_format(raw)
        dims = _estimate_dimensions(raw, fmt)
        size_kb = len(raw) / 1024

        if self._llm_available:
            text = await self._llm_ocr(raw)
            if text:
                return f"📝 Распознанный текст:\n{text}"
            return "Текст на изображении не обнаружен."

        return (
            f"OCR недоступен — LLM vision не настроен.\n"
            f"Технические параметры: {fmt}, {dims.get('width','?')}×{dims.get('height','?')}, "
            f"{size_kb:.1f} КБ"
        )

    async def colorize(self, image_data: Any) -> str:
        """Раскрашивание ч/б изображения — пробует LLM vision с инструкцией."""
        raw = self._to_bytes(image_data)
        if not raw:
            return "Изображение не получено."

        fmt = _detect_format(raw)
        dims = _estimate_dimensions(raw, fmt)
        size_kb = len(raw) / 1024

        # Если есть LLM — просим описать цвета (единственное, что можем без внешней модели)
        if self._llm_available:
            llm_result = await self._llm_describe(raw)
            if llm_result:
                return (
                    f"🎨 Раскрашивание запрошено.\n"
                    f"Параметры: {fmt}, {dims.get('width','?')}×{dims.get('height','?')} px, "
                    f"{size_kb:.1f} КБ.\n"
                    f"🧠 LLM-описание для восстановления цветов:\n{llm_result}"
                )

        return (
            f"🎨 Раскрашивание: {fmt}, "
            f"{dims.get('width','?')}×{dims.get('height','?')} px, "
            f"{size_kb:.1f} КБ.\n"
            f"Для реальной колоризации требуется внешняя модель "
            f"(deOldify / DDColor — не установлена)."
        )

    async def upscale(self, image_data: Any) -> str:
        """Улучшение качества — анализ + LLM-описание."""
        raw = self._to_bytes(image_data)
        if not raw:
            return "Изображение не получено."

        fmt = _detect_format(raw)
        dims = _estimate_dimensions(raw, fmt)
        size_kb = len(raw) / 1024

        if self._llm_available:
            llm_result = await self._llm_describe(raw)
            if llm_result:
                return (
                    f"🔍 Upscale запрошен.\n"
                    f"Параметры: {fmt}, {dims.get('width','?')}×{dims.get('height','?')} px"
                    f" ({size_kb:.1f} КБ).\n"
                    f"🧠 LLM-описание (для восстановления деталей):\n{llm_result}"
                )

        return (
            f"🔍 Upscale: {fmt}, "
            f"{dims.get('width','?')}×{dims.get('height','?')} px"
            f" ({size_kb:.1f} КБ).\n"
            f"Для upscale требуется модель супер-резолюции (ESRGAN / SwinIR — не установлена)."
        )

    async def remove_bg(self, image_data: Any) -> str:
        """Удаление фона — анализ + LLM с инструкцией."""
        raw = self._to_bytes(image_data)
        if not raw:
            return "Изображение не получено."

        fmt = _detect_format(raw)
        dims = _estimate_dimensions(raw, fmt)
        size_kb = len(raw) / 1024

        # PNG/GIF с прозрачностью
        if fmt in ("png", "gif") and size_kb < 10:
            return (
                f"✂️ Удаление фона: {fmt}, {dims.get('width','?')}×{dims.get('height','?')} px, "
                f"{size_kb:.1f} КБ.\n"
                f"Изображение уже имеет прозрачность (PNG/GIF)."
            )

        if self._llm_available:
            llm_result = await self._llm_describe(raw)
            if llm_result:
                return (
                    f"✂️ Удаление фона запрошено.\n"
                    f"Параметры: {fmt}, {dims.get('width','?')}×{dims.get('height','?')} px"
                    f" ({size_kb:.1f} КБ).\n"
                    f"🧠 LLM-описание основного объекта:\n{llm_result}"
                )

        return (
            f"✂️ Удаление фона: {fmt}, "
            f"{dims.get('width','?')}×{dims.get('height','?')} px"
            f" ({size_kb:.1f} КБ).\n"
            f"Для удаления фона требуется модель (rembg / u2net — не установлена)."
        )

    async def process_image(self, image_data: Any, operations: List[str]) -> Dict[str, str]:
        """Применить несколько операций к изображению."""
        results: Dict[str, str] = {}
        for operation in operations:
            op = operation.strip().lower()
            if op == "describe":
                results["describe"] = await self.describe(image_data)
            elif op == "ocr":
                results["ocr"] = await self.ocr(image_data)
            elif op == "colorize":
                results["colorize"] = await self.colorize(image_data)
            elif op == "upscale":
                results["upscale"] = await self.upscale(image_data)
            elif op == "remove_bg":
                results["remove_bg"] = await self.remove_bg(image_data)
            else:
                results[operation] = f"Неизвестная операция: {operation}"
        return results

    # ── Хелперы ──────────────────────────────────────────────────────

    @staticmethod
    def _to_bytes(data: Any) -> bytes:
        """Привести входные данные к bytes независимо от типа."""
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, str):
            try:
                return base64.b64decode(data)
            except Exception:
                return data.encode("utf-8")
        if isinstance(data, Path):
            try:
                return data.read_bytes()
            except Exception as e:
                logger.debug('%s optional failed: %s', 'vision_layer', e, exc_info=True)
        if hasattr(data, "read"):
            try:
                return data.read()
            except Exception as e:
                logger.debug('%s optional failed: %s', 'vision_layer', e, exc_info=True)
        return b""

    @staticmethod
    async def _llm_describe(raw: bytes) -> Optional[str]:
        """Вызвать LLM vision для описания через OpenRouterProvider."""
        try:
            from core.openrouter_provider import get_openrouter_provider
            import base64
            b64 = base64.b64encode(raw).decode("utf-8")
            # Определяем mime по magic bytes
            fmt = _detect_format(raw)
            mime = f"image/{fmt}" if fmt in ("jpeg", "png", "gif", "webp", "bmp") else "image/jpeg"
            provider = get_openrouter_provider()
            resp = await provider.generate(
                prompt="Опиши подробно, что изображено на этой картинке.",
                vision_image_parts=[(mime, b64)],
                temperature=0.3,
                max_tokens=300,
            )
            return resp.get("content", "")
        except Exception as e:
            logger.debug("[vision] LLM describe error: %s", e)
            return None

    @staticmethod
    async def _llm_ocr(raw: bytes) -> Optional[str]:
        """Вызвать LLM vision для OCR через OpenRouterProvider."""
        try:
            from core.openrouter_provider import get_openrouter_provider
            import base64
            b64 = base64.b64encode(raw).decode("utf-8")
            fmt = _detect_format(raw)
            mime = f"image/{fmt}" if fmt in ("jpeg", "png", "gif", "webp", "bmp") else "image/jpeg"
            provider = get_openrouter_provider()
            resp = await provider.generate(
                prompt=(
                    "Прочитай и выведи весь текст, который видишь "
                    "на этом изображении. Только текст, без комментариев."
                ),
                vision_image_parts=[(mime, b64)],
                temperature=0.1,
                max_tokens=500,
            )
            return resp.get("content", "")
        except Exception as e:
            logger.debug("[vision] LLM OCR error: %s", e)
            return None


# ── Упрощённый API ────────────────────────────────────────────────────

async def describe(image):
    return await VisionLayer().describe(image)

async def ocr(image):
    return await VisionLayer().ocr(image)

async def colorize(image):
    return await VisionLayer().colorize(image)

async def upscale(image):
    return await VisionLayer().upscale(image)

async def remove_bg(image):
    return await VisionLayer().remove_bg(image)
