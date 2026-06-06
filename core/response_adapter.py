from __future__ import annotations

from typing import Any, Dict

from core.models import Output
from core.response_model import ResponseEnvelope


class UnifiedResponseAdapter:
    """Normalizes heterogeneous internal responses into one envelope."""

    def from_output(self, output: Output) -> ResponseEnvelope:
        meta = output.meta or {}
        env = ResponseEnvelope(
            kind=str(output.type or "text"),
            text=str(output.payload or ""),
            skill=str(meta.get("skill") or meta.get("module") or ""),
            data={
                "module": meta.get("module"),
                "image_operation": meta.get("image_operation"),
                "ocr_text": meta.get("ocr_text"),
                "code_intake": meta.get("code_intake"),
                "document_intake": meta.get("document_intake"),
            },
            trace={
                "planner_reason": meta.get("planner_reason"),
                "dialogue_state": meta.get("dialogue_state"),
            },
        )
        image_path = meta.get("image_output_path")
        if isinstance(image_path, str) and image_path:
            env.attachments.append({"type": "image", "path": image_path})
        image_url = meta.get("image_url")
        if isinstance(image_url, str) and image_url.startswith("http"):
            env.attachments.append(
                {
                    "type": "image",
                    "url": image_url,
                    "caption": meta.get("caption"),
                }
            )
        file_path = meta.get("file_output_path")
        if isinstance(file_path, str) and file_path:
            env.attachments.append({"type": "file", "path": file_path})
        tlr = meta.get("telegram_location_reply")
        if isinstance(tlr, dict):
            try:
                lat = float(tlr["latitude"])
                lon = float(tlr["longitude"])
                env.attachments.append(
                    {
                        "type": "location",
                        "latitude": lat,
                        "longitude": lon,
                        "live_period": tlr.get("live_period"),
                        "horizontal_accuracy": tlr.get("horizontal_accuracy"),
                    }
                )
            except (KeyError, TypeError, ValueError):
                pass
        if meta.get("warning"):
            env.warnings.append(str(meta.get("warning")))
        if meta.get("error"):
            env.errors.append({"message": str(meta.get("error"))})
        return env

    def to_telegram_payload(self, env: ResponseEnvelope) -> Dict[str, Any]:
        return {
            "text": env.text,
            "attachments": env.attachments,
            "warnings": env.warnings,
            "errors": env.errors,
            "meta": env.to_dict(),
        }
