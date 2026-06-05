from __future__ import annotations

import logging

import os
import tempfile
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class FileContext:
    file_id: str
    file_type: str
    mime_type: str
    size: int
    original_name: str
    chat_id: str
    user_id: str
    local_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FileIntakeModule:
    def __init__(self) -> None:
        self.enabled = os.getenv("FILE_INTAKE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_image_mb = float(os.getenv("FILE_MAX_IMAGE_MB", "10"))
        self.max_doc_mb = float(os.getenv("FILE_MAX_DOC_MB", "15"))
        self.max_audio_mb = float(os.getenv("FILE_MAX_AUDIO_MB", "15"))
        self.temp_dir = os.getenv("FILE_TEMP_DIR", tempfile.gettempdir())

    def _limit_bytes(self, file_type: str) -> int:
        if file_type == "image":
            return int(self.max_image_mb * 1024 * 1024)
        if file_type in {"audio", "voice"}:
            return int(self.max_audio_mb * 1024 * 1024)
        return int(self.max_doc_mb * 1024 * 1024)

    def enforce_size_limit(self, file_type: str, size: int) -> bool:
        if size <= 0:
            return True
        return size <= self._limit_bytes(file_type)

    async def download_file(self, bot: Any, file_id: str, original_name: str = "") -> str:
        if not self.enabled:
            return ""
        ext = ""
        if "." in original_name:
            ext = "." + original_name.rsplit(".", 1)[-1]
        fd, target = tempfile.mkstemp(prefix="gemma_file_", suffix=ext, dir=self.temp_dir)
        os.close(fd)
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=target)
        return target

    def cleanup(self, path: Optional[str]) -> None:
        if not path:
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'file_intake', e, exc_info=True)