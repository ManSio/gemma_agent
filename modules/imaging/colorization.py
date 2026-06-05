from __future__ import annotations

import os
from typing import Any, Dict

from core.error_analysis import record_error_event


class ColorizationModule:
    def __init__(self) -> None:
        self.backend = os.getenv("IMAGE_COLORIZATION_BACKEND", "none").strip().lower()
        self.external_endpoint = os.getenv("IMAGE_COLORIZATION_API_ENDPOINT", "")
        self.max_res = int(os.getenv("IMAGE_MAX_RESOLUTION", "2048"))
        self._opencv_loaded = False

    def enabled(self) -> bool:
        return self.backend in {"opencv_caffe", "external_api"}

    async def colorize(self, file_path: str) -> Dict[str, Any]:
        if self.backend == "none":
            return {"ok": False, "error": "colorization disabled"}
        if self.backend == "external_api":
            if not self.external_endpoint:
                return {"ok": False, "error": "external colorization api not configured"}
            from modules.imaging.http_util import post_image_file

            resp = await post_image_file(self.external_endpoint, file_path, field_name="file")
            if not resp.get("ok"):
                return {"ok": False, "error": resp.get("error") or "colorization external failed"}
            out_path = str(resp.get("path") or resp.get("output_path") or "").strip()
            if not out_path and isinstance(resp.get("data"), dict):
                out_path = str(resp["data"].get("path") or "").strip()
            if out_path:
                return {"ok": True, "path": out_path, "operation": "colorize"}
            return {"ok": False, "error": "colorization external: no output path in response"}
        if self.backend == "opencv_caffe":
            try:
                # Lazy import for low footprint
                import cv2  # type: ignore
                _ = cv2
                self._opencv_loaded = True
            except Exception as e:
                record_error_event("colorization", "opencv backend unavailable", exc=e)
                return {"ok": False, "error": "opencv backend unavailable"}
            record_error_event(
                "colorization",
                "opencv_caffe pipeline not implemented",
                extra={"backend": self.backend},
            )
            return {
                "ok": False,
                "error": "opencv_caffe colorization not available; set IMAGE_COLORIZATION_BACKEND=external_api",
            }
        return {"ok": False, "error": "unknown colorization backend"}
