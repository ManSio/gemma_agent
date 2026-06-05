from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from core.error_analysis import record_error_event
from modules.skills.skill_interface import Skill, SkillResult
from modules.imaging.image_tools import (
    enhance_auto,
    safe_load_image,
    safe_save_image,
    to_grayscale,
    to_sepia,
)
from modules.imaging.colorization import ColorizationModule
from modules.imaging.ocr import OCRModule


def _extract_exif_summary(path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "items": {}}
    try:
        from PIL import Image, ExifTags

        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return out
            tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
            keep = ("Make", "Model", "DateTime", "DateTimeOriginal", "Orientation", "Software")
            out["available"] = True
            out["items"] = {k: str(tags.get(k)) for k in keep if tags.get(k) is not None}
            out["count"] = len(tags)
            return out
    except Exception:
        return out


def _detect_faces_and_landmarks(path: str) -> Dict[str, Any]:
    """
    Best-effort face metadata:
    - uses OpenCV haarcascade when available
    - landmarks unavailable without dedicated model
    """
    out: Dict[str, Any] = {
        "detector": "none",
        "faces": [],
        "count": 0,
        "landmarks": "unavailable",
    }
    try:
        import cv2  # type: ignore

        img = cv2.imread(path)
        if img is None:
            return out
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if cascade.empty():
            return out
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        out["detector"] = "opencv_haar_frontalface"
        out["faces"] = [
            {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
            for (x, y, w, h) in list(faces)
        ]
        out["count"] = len(out["faces"])
        return out
    except Exception:
        return out


def _region_hints_from_image(image: Any) -> Dict[str, Any]:
    """
    Lightweight region hints without heavy ML:
    - foreground bbox from difference to border average color
    - coarse tags for background/body/object areas
    """
    try:
        img = image.convert("RGB")
        w, h = img.size
        px = img.load()
        border_samples: List[Tuple[int, int, int]] = []
        step_x = max(1, w // 64)
        step_y = max(1, h // 64)
        for x in range(0, w, step_x):
            border_samples.append(px[x, 0])
            border_samples.append(px[x, h - 1])
        for y in range(0, h, step_y):
            border_samples.append(px[0, y])
            border_samples.append(px[w - 1, y])
        if not border_samples:
            return {"background": "unknown", "object_bbox": None}
        br = sum(c[0] for c in border_samples) / len(border_samples)
        bg = sum(c[1] for c in border_samples) / len(border_samples)
        bb = sum(c[2] for c in border_samples) / len(border_samples)
        thr = 42.0
        min_x, min_y, max_x, max_y = w, h, -1, -1
        for y in range(0, h, step_y):
            for x in range(0, w, step_x):
                r, g, b = px[x, y]
                d = abs(r - br) + abs(g - bg) + abs(b - bb)
                if d > thr:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
        bbox = None
        if max_x >= min_x and max_y >= min_y:
            bbox = {"x1": int(min_x), "y1": int(min_y), "x2": int(max_x), "y2": int(max_y)}
        return {
            "background": {"avg_rgb_border": [int(br), int(bg), int(bb)]},
            "body_region_hint": "center_object_area" if bbox else "unknown",
            "object_bbox": bbox,
            "segmenter": "heuristic_border_diff",
        }
    except Exception:
        return {"background": "unknown", "object_bbox": None}


def _intake_report(path: str, file_context: Dict[str, Any], loaded_meta: Dict[str, Any], image: Any) -> Dict[str, Any]:
    return {
        "task_type": "image_edit_or_analysis",
        "file_context": {
            "file_type": file_context.get("file_type"),
            "mime_type": file_context.get("mime_type"),
            "file_name": file_context.get("file_name"),
            "file_size": file_context.get("file_size"),
        },
        "image_meta": loaded_meta,
        "exif": _extract_exif_summary(path),
        "faces": _detect_faces_and_landmarks(path),
        "regions": _region_hints_from_image(image),
    }


class ImageSkillRouter:
    _INTENT_TOOLKIT: Dict[str, List[str]] = {
        "face_preserve_edit": ["face_preserve_edit", "inpainting"],
        "background_swap": ["background_swap", "segment_aware_transform", "inpainting"],
        "reposition_person": ["segment_aware_transform", "inpainting"],
        "redraw_object": ["inpainting", "object_removal"],
        "enhance_quality": ["super_resolution", "enhance_auto"],
        "colorize_bw": ["colorization"],
        "to_bw": ["grayscale"],
        "object_removal": ["object_removal", "inpainting"],
        "pose_transfer": ["pose_transfer", "segment_aware_transform", "inpainting"],
        "ocr": ["ocr"],
        "anime_style": ["stylize_anime", "enhance_auto"],
        "cartoon_style": ["stylize_anime", "sepia"],
        "realistic_style": ["enhance_auto", "super_resolution"],
        "describe": ["describe"],
    }

    @staticmethod
    def classify(user_text: str, file_context: Dict[str, Any]) -> Optional[str]:
        if not isinstance(file_context, dict) or file_context.get("file_type") != "image":
            return None
        t = (user_text or "").lower()
        if any(k in t for k in ("оставь лицо", "лицо не трогай", "face preserve", "preserve face")):
            return "face_preserve_edit"
        if any(k in t for k in ("замени фон", "смени фон", "background swap", "replace background")):
            return "background_swap"
        if any(k in t for k in ("переставь человека", "перемести человека", "reposition person")):
            return "reposition_person"
        if any(k in t for k in ("перерисуй объект", "redraw object")):
            return "redraw_object"
        if any(k in t for k in ("удали предмет", "remove object", "object removal")):
            return "object_removal"
        if any(k in t for k in ("поменяй позу", "замени позу", "pose transfer")):
            return "pose_transfer"
        if any(k in t for k in ("ocr", "прочитай текст", "что написано", "распознай текст")):
            return "ocr"
        if any(
            k in t
            for k in (
                "раскрась",
                "раскрасить",
                "сделай цветным",
                "в цвет",
                "colorize",
                "colourise",
                "bw to color",
                "ч/б в цвет",
                "черно-бел в цвет",
            )
        ):
            return "colorize_bw"
        if any(k in t for k in ("сделай ч/б", "черно-бел", "ч/б", "black and white", "grayscale")):
            return "to_bw"
        if any(k in t for k in ("улучши качество", "upscale", "super resolution", "enhance", "повышай качество")):
            return "enhance_quality"
        if any(k in t for k in ("реалист", "photoreal", "realistic")):
            return "realistic_style"
        if any(k in t for k in ("мульт", "cartoon", "комикс")):
            return "cartoon_style"
        if any(k in t for k in ("аниме", "anime", "раскраску", "рисунок", "stylize")):
            return "anime_style"
        return "describe"

    @staticmethod
    def tool_pipeline(intent: str) -> List[str]:
        return list(ImageSkillRouter._INTENT_TOOLKIT.get(intent, ["describe"]))


class ImageSkill(Skill):
    name = "image_skill"

    def __init__(self) -> None:
        self.enabled = os.getenv("IMAGE_TOOLS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_res = int(os.getenv("IMAGE_MAX_RESOLUTION", "2048"))
        self.colorization = ColorizationModule()
        self.ocr = OCRModule()
        self.edit_backend_url = (os.getenv("IMAGE_EDIT_BACKEND_URL") or "").strip()
        self.edit_backend_timeout_sec = max(8.0, float(os.getenv("IMAGE_EDIT_BACKEND_TIMEOUT_SEC", "45")))
        self.edit_backend_token = (os.getenv("IMAGE_EDIT_BACKEND_TOKEN") or "").strip()

    def _anime_stylize(self, image: Any) -> Dict[str, Any]:
        # Lightweight comic/anime-like stylization using PIL-only transforms.
        try:
            from PIL import ImageOps, ImageFilter

            base = image.convert("RGB")
            poster = ImageOps.posterize(base, bits=4)
            edges = base.convert("L").filter(ImageFilter.FIND_EDGES)
            edges = ImageOps.autocontrast(edges)
            edges = ImageOps.invert(edges).convert("RGB")
            mixed = poster.copy()
            mix_px = mixed.load()
            edge_px = edges.load()
            w, h = mixed.size
            for y in range(h):
                for x in range(w):
                    r, g, b = mix_px[x, y]
                    er, eg, eb = edge_px[x, y]
                    # Darken where edges are strong.
                    k = (er + eg + eb) / (3 * 255.0)
                    mul = 0.55 + 0.45 * k
                    mix_px[x, y] = (int(r * mul), int(g * mul), int(b * mul))
            return {"ok": True, "image": mixed}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _unsupported_tool_result(self, tool_name: str, intake: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ok": False,
            "tool": tool_name,
            "error": "tool_not_available_locally",
            "hint": (
                f"{tool_name} требует ML-модель сегментации/инпейнтинга/позы. "
                "Можно подключить backend через отдельный сервис."
            ),
            "intake_faces": intake.get("faces"),
            "intake_regions": intake.get("regions"),
        }

    @staticmethod
    def _human_fallback_hint(intent: str) -> str:
        _intent_ru = {
            "face_preserve_edit": "оставить лицо и изменить остальное",
            "background_swap": "заменить фон",
            "reposition_person": "переместить человека",
            "redraw_object": "перерисовать объект",
            "object_removal": "удалить предмет",
            "pose_transfer": "поменять позу",
        }
        goal = _intent_ru.get(intent, "сделать сложное редактирование фото")
        return (
            f"Сейчас не могу {goal} автоматически — внешний image-backend недоступен. "
            "Могу прямо сейчас: улучшить качество, сделать ч/б, стилизовать под аниме/рисунок, "
            "описать фото или распознать текст (OCR)."
        )

    async def _run_remote_image_edit(
        self,
        *,
        local_path: str,
        intent: str,
        pipeline: List[str],
        user_text: str,
        intake: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self.edit_backend_url:
            return {"ok": False, "error": "remote_backend_not_configured"}
        try:
            raw = Path(local_path).read_bytes()
            img_b64 = base64.b64encode(raw).decode("ascii")
        except Exception as e:
            return {"ok": False, "error": f"read_input_failed:{e}"}

        req: Dict[str, Any] = {
            "intent": intent,
            "pipeline": pipeline,
            "user_prompt": user_text,
            "intake": intake,
            "input_image_b64": img_b64,
            "input_mime": "image/png",
        }
        headers = {"Content-Type": "application/json"}
        if self.edit_backend_token:
            headers["Authorization"] = f"Bearer {self.edit_backend_token}"
        timeout = aiohttp.ClientTimeout(total=self.edit_backend_timeout_sec)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.edit_backend_url, headers=headers, json=req) as resp:
                    txt = await resp.text()
                    if resp.status != 200:
                        return {"ok": False, "error": f"backend_http_{resp.status}", "body": txt[:400]}
                    try:
                        data = json.loads(txt)
                    except Exception:
                        return {"ok": False, "error": "backend_invalid_json", "body": txt[:400]}
        except Exception as e:
            return {"ok": False, "error": f"backend_request_failed:{e}"}

        if not isinstance(data, dict):
            return {"ok": False, "error": "backend_invalid_payload"}
        if data.get("ok") is False:
            return {
                "ok": False,
                "error": str(data.get("error") or "backend_edit_failed"),
                "details": data.get("details"),
            }
        out_b64 = str(data.get("output_image_b64") or "").strip()
        out_url = str(data.get("output_image_url") or "").strip()
        if out_b64:
            try:
                raw_out = base64.b64decode(out_b64, validate=False)
                target = tempfile.mktemp(prefix=f"gemma_{intent}_", suffix=".png")
                Path(target).write_bytes(raw_out)
                return {"ok": True, "path": target, "backend_meta": data.get("meta")}
            except Exception as e:
                return {"ok": False, "error": f"backend_b64_decode_failed:{e}"}
        if out_url:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(out_url) as resp:
                        if resp.status != 200:
                            return {"ok": False, "error": f"backend_output_download_http_{resp.status}"}
                        raw_out = await resp.read()
                target = tempfile.mktemp(prefix=f"gemma_{intent}_", suffix=".png")
                Path(target).write_bytes(raw_out)
                return {"ok": True, "path": target, "backend_meta": data.get("meta")}
            except Exception as e:
                return {"ok": False, "error": f"backend_output_download_failed:{e}"}
        return {"ok": False, "error": "backend_no_output_image"}

    def _save_image_result(self, out_img: Any, op: str) -> Dict[str, Any]:
        target = tempfile.mktemp(prefix=f"gemma_{op}_", suffix=".png")
        saved = safe_save_image(out_img, target, "PNG")
        if not saved.get("ok"):
            return {"ok": False, "error": saved.get("error", "save_failed")}
        return {"ok": True, "path": target}

    def _run_local_image_tool(self, tool_name: str, image: Any, intake: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "enhance_auto":
            return enhance_auto(image)
        if tool_name == "grayscale":
            return to_grayscale(image)
        if tool_name == "sepia":
            return to_sepia(image)
        if tool_name == "stylize_anime":
            return self._anime_stylize(image)
        if tool_name in {"super_resolution"}:
            out = enhance_auto(image)
            if isinstance(out, dict) and out.get("ok"):
                out["method"] = "enhance_auto_fallback"
                out["note"] = (
                    "Super-resolution model not configured; applied auto-enhance instead. "
                    "Set IMAGE_SR_BACKEND for a dedicated upscaler."
                )
            return out
        if tool_name in {
            "face_preserve_edit",
            "segment_aware_transform",
            "background_swap",
            "object_removal",
            "pose_transfer",
            "inpainting",
        }:
            return self._unsupported_tool_result(tool_name, intake)
        if tool_name == "describe":
            return {"ok": True}
        return {"ok": False, "error": f"unknown_tool:{tool_name}"}

    async def run(
        self,
        *,
        intent: str,
        user_text: str,
        context: Dict[str, Any],
        user_facts: Dict[str, Any],
        digital_twin: Dict[str, Any],
    ) -> SkillResult:
        if not self.enabled:
            return SkillResult(result={"skill": self.name, "error": "image tools disabled"}, hint="Image tools disabled.")
        file_context = context.get("file_context") or {}
        intent = ImageSkillRouter.classify(user_text, file_context)
        if not intent:
            return SkillResult(result={"skill": self.name, "error": "no image context"}, hint="")
        local_path = file_context.get("local_path", "")
        if not local_path:
            return SkillResult(result={"skill": self.name, "error": "image local path unavailable"}, hint="Image file was not downloaded.")

        loaded = safe_load_image(local_path)
        if not loaded.get("ok"):
            record_error_event("image_tools", "safe_load_image failed in image_skill", extra={"error": loaded.get("error")})
            return SkillResult(
                result={"skill": self.name, "operation": intent, "ok": False, "error": loaded.get("error")},
                hint="Image processing unavailable.",
            )
        image = loaded["image"]
        intake = _intake_report(local_path, file_context, loaded.get("meta") or {}, image)
        pipeline = ImageSkillRouter.tool_pipeline(intent)
        route_payload: Dict[str, Any] = {
            "skill": self.name,
            "intent": intent,
            "pipeline": pipeline,
            "intake": intake,
        }
        if isinstance(file_context.get("secondary_images"), list):
            route_payload["secondary_images"] = [
                {
                    "file_type": str(x.get("file_type") or ""),
                    "mime_type": str(x.get("mime_type") or ""),
                    "original_name": str(x.get("original_name") or ""),
                }
                for x in file_context.get("secondary_images")
                if isinstance(x, dict)
            ]

        if intent == "ocr":
            res = await self.ocr.extract_text(local_path)
            route_payload.update({"operation": "ocr", **res})
            return SkillResult(
                result=route_payload,
                hint="OCR extraction completed." if res.get("ok") else "OCR unavailable.",
            )
        if intent == "colorize_bw":
            res = await self.colorization.colorize(local_path)
            route_payload.update({"operation": "colorize", **res})
            if res.get("ok"):
                hint = "Раскраска применена."
            else:
                hint = (
                    "Локальная раскраска недоступна (см. IMAGE_COLORIZATION_BACKEND в .env). "
                    "Можно: MyHeritage In Color, Palette.fm, Hotpot.ai; или opencv_caffe при настройке."
                )
            return SkillResult(result=route_payload, hint=hint)
        if intent in {
            "enhance_quality",
            "to_bw",
            "anime_style",
            "cartoon_style",
            "realistic_style",
            "face_preserve_edit",
            "background_swap",
            "reposition_person",
            "redraw_object",
            "object_removal",
            "pose_transfer",
            "describe",
        }:
            # Run first executable local tool in selected pipeline.
            selected_result: Dict[str, Any] = {}
            selected_tool = ""
            unsupported_count = 0
            for tool_name in pipeline:
                r = self._run_local_image_tool(tool_name, image, intake)
                selected_tool = tool_name
                selected_result = r
                if r.get("ok") and r.get("image") is not None:
                    break
                if r.get("ok") and "image" not in r:
                    break
                if r.get("error") == "tool_not_available_locally":
                    unsupported_count += 1
                    continue
            if unsupported_count > 0 and (not selected_result.get("ok")):
                remote = await self._run_remote_image_edit(
                    local_path=local_path,
                    intent=intent,
                    pipeline=pipeline,
                    user_text=user_text,
                    intake=intake,
                )
                if remote.get("ok") and remote.get("path"):
                    route_payload.update(
                        {
                            "operation": intent,
                            "ok": True,
                            "path": remote["path"],
                            "selected_tool": "remote_backend_pipeline",
                            "backend": {
                                "url": self.edit_backend_url,
                                "meta": remote.get("backend_meta"),
                            },
                        }
                    )
                    return SkillResult(
                        result=route_payload,
                        hint="Операция выполнена через внешний image backend.",
                    )
                route_payload["backend_error"] = remote.get("error")
            if selected_result.get("ok") and selected_result.get("image") is not None:
                save = self._save_image_result(selected_result["image"], selected_tool or intent)
                if not save.get("ok"):
                    route_payload.update(
                        {
                            "operation": intent,
                            "ok": False,
                            "error": save.get("error"),
                            "selected_tool": selected_tool,
                        }
                    )
                    return SkillResult(result=route_payload, hint="Image save failed.")
                route_payload.update(
                    {
                        "operation": intent,
                        "ok": True,
                        "path": save["path"],
                        "selected_tool": selected_tool,
                    }
                )
                hint_map = {
                    "enhance_quality": "Качество изображения улучшено.",
                    "to_bw": "Изображение переведено в ч/б.",
                    "anime_style": "Применена стилизация под рисунок/anime.",
                }
                return SkillResult(result=route_payload, hint=hint_map.get(intent, "Image processing completed."))
            if selected_result.get("ok") and "image" not in selected_result:
                route_payload.update(
                    {
                        "operation": intent,
                        "ok": True,
                        "selected_tool": selected_tool,
                        "note": "analysis_or_route_only",
                    }
                )
                return SkillResult(result=route_payload, hint="Маршрут для изображения построен.")
            route_payload.update(
                {
                    "operation": intent,
                    "ok": False,
                    "selected_tool": selected_tool,
                    "error": selected_result.get("error", "image_processing_failed"),
                    "tool_result": selected_result,
                }
            )
            return SkillResult(
                result=route_payload,
                hint=self._human_fallback_hint(intent),
            )
        route_payload.update({"operation": "describe", "ok": True})
        return SkillResult(result=route_payload, hint="Analyze and describe the image content.")
