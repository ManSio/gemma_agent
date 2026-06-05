"""Image generator module via OpenRouter.

OpenRouter не предоставляет POST /api/v1/images/generations (404): генерация идёт через
/api/v1/chat/completions с полем modalities. См. https://openrouter.ai/docs/guides/overview/multimodal/image-generation
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from core.image_gen_multiref import build_reference_user_prompt, collect_reference_paths_chronological
from core.image_gen_nl import normalize_image_request_text, strip_nl_imagine_boilerplate
from core.models import Output

logger = logging.getLogger(__name__)

_OPENROUTER_CHAT = "https://openrouter.ai/api/v1/chat/completions"

# Частая опечатка в .env: bytedance/… — на OpenRouter провайдер bytedance-seed (см. страницу модели).
_IMAGE_MODEL_ALIASES: Dict[str, str] = {
    "bytedance/seedream-4.5": "bytedance-seed/seedream-4.5",
    "bytedance/seedream-4": "bytedance-seed/seedream-4.5",
    "nano-banana-2": "google/gemini-3.1-flash-image-preview",
    "nano-banana2": "google/gemini-3.1-flash-image-preview",
    "banana2": "google/gemini-3.1-flash-image-preview",
    "gemini-3.1-flash-image-preview": "google/gemini-3.1-flash-image-preview",
}


def _normalize_image_model_id(model: str) -> str:
    m = (model or "").strip()
    return _IMAGE_MODEL_ALIASES.get(m, m)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_name(stem: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "_", stem).strip("._")
    return s[:80] or "image"


def _normalize_openrouter_image_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return _OPENROUTER_CHAT
    if "openrouter.ai" in u.lower() and "/api/v1/images/generations" in u:
        logger.warning(
            "[image_generator] IMAGE_GEN_API_URL points at deprecated /images/generations (404 on OpenRouter); "
            "using %s",
            _OPENROUTER_CHAT,
        )
        return _OPENROUTER_CHAT
    return u


def _aspect_ratio_from_size(size: str) -> Optional[str]:
    s = (size or "").strip().lower().replace(" ", "")
    m = re.match(r"^(\d+)x(\d+)$", s)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return None
    g = math.gcd(w, h)
    rw, rh = w // g, h // g
    known: Dict[Tuple[int, int], str] = {
        (1, 1): "1:1",
        (2, 3): "2:3",
        (3, 2): "3:2",
        (3, 4): "3:4",
        (4, 3): "4:3",
        (4, 5): "4:5",
        (5, 4): "5:4",
        (9, 16): "9:16",
        (16, 9): "16:9",
        (21, 9): "21:9",
    }
    return known.get((rw, rh), f"{rw}:{rh}")


def _file_ext_from_data_url(data_url: str) -> str:
    head = (data_url or "").split(",", 1)[0].lower()
    if "image/jpeg" in head or "image/jpg" in head:
        return ".jpg"
    if "image/webp" in head:
        return ".webp"
    if "image/gif" in head:
        return ".gif"
    return ".png"


def _decode_data_url(data_url: str) -> Tuple[Optional[bytes], str]:
    u = (data_url or "").strip()
    if not u.startswith("data:") or "," not in u:
        return None, ".png"
    try:
        header, b64 = u.split(",", 1)
        if ";base64" not in header:
            return None, ".png"
        raw = base64.b64decode(b64, validate=False)
        return raw, _file_ext_from_data_url(u)
    except Exception:
        return None, ".png"


def _message_content_excerpt(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        bits: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = str(part.get("text") or "").strip()
                if t:
                    bits.append(t)
        return " ".join(bits).strip()
    return ""


def diagnose_chat_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Поля ответа OpenRouter для логов и подписи при пустой картинке."""
    diag: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return diag
    for key in ("native_finish_reason", "service_tier"):
        val = data.get(key)
        if val is not None and str(val).strip():
            diag[key] = str(val).strip()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return diag
    first = choices[0] if isinstance(choices[0], dict) else {}
    for key in ("finish_reason", "native_finish_reason"):
        val = first.get(key)
        if val is not None and str(val).strip():
            diag[key] = str(val).strip()
    msg = first.get("message") if isinstance(first.get("message"), dict) else {}
    excerpt = _message_content_excerpt(msg)
    if excerpt:
        diag["message_excerpt"] = excerpt[:400]
    return diag


def image_finish_label(diag: Dict[str, Any]) -> str:
    for key in ("native_finish_reason", "finish_reason"):
        val = diag.get(key)
        if val:
            return str(val)
    return "нет картинки"


def short_model_label(model: str) -> str:
    m = (model or "").strip()
    if "/" in m:
        return m.split("/", 1)[-1]
    return m or "unknown"


def format_fallback_notice(
    *,
    primary_model: str,
    fallback_model: str,
    reason: str,
    message_excerpt: str = "",
) -> str:
    line = (
        f"Основная модель ({short_model_label(primary_model)}) не вернула изображение "
        f"({reason}). Использована запасная: {short_model_label(fallback_model)}."
    )
    if message_excerpt.strip():
        line += f"\nОтвет модели: {message_excerpt.strip()[:280]}"
    return line


def format_api_fallback_notice(*, primary_model: str, fallback_model: str, api_error: str) -> str:
    err = (api_error or "ошибка API").strip()[:120]
    return (
        f"Основная модель ({short_model_label(primary_model)}): {err}. "
        f"Использована запасная: {short_model_label(fallback_model)}."
    )


class ImageGeneratorModule:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.api_url = _normalize_openrouter_image_url(os.getenv("IMAGE_GEN_API_URL") or _OPENROUTER_CHAT)
        _raw_model = (os.getenv("IMAGE_GEN_MODEL") or "google/gemini-3.1-flash-image-preview").strip()
        self.model = _normalize_image_model_id(_raw_model)
        if self.model != _raw_model:
            logger.warning(
                "[image_generator] IMAGE_GEN_MODEL %s → %s (slug OpenRouter)",
                _raw_model,
                self.model,
            )
        _raw_fb = (os.getenv("IMAGE_GEN_MODEL_FALLBACK") or "").strip()
        self.fallback_model = _normalize_image_model_id(_raw_fb) if _raw_fb else ""
        if _raw_fb and self.fallback_model != _raw_fb:
            logger.warning(
                "[image_generator] IMAGE_GEN_MODEL_FALLBACK %s → %s",
                _raw_fb,
                self.fallback_model,
            )
        self.size = (os.getenv("IMAGE_GEN_SIZE") or "1024x1024").strip()
        self.quality = (os.getenv("IMAGE_GEN_QUALITY") or "medium").strip()
        self.style = (os.getenv("IMAGE_GEN_STYLE") or "").strip()
        self.timeout_sec = max(15.0, float(os.getenv("IMAGE_GEN_TIMEOUT_SEC", "90")))
        self.save_dir = Path(os.getenv("IMAGE_GEN_OUTPUT_DIR", str(_repo_root() / "data" / "generated_images")))
        self.enabled = _truthy("IMAGE_GEN_ENABLED", True)
        self.daily_limit_per_user = max(1, int(os.getenv("IMAGE_GEN_DAILY_LIMIT_PER_USER", "15")))
        self._quota_path = Path(
            os.getenv("IMAGE_GEN_QUOTA_PATH", str(_repo_root() / "data" / "runtime" / "image_gen_quota.json"))
        )

    def _extract_prompt(self, payload: str) -> str:
        raw = normalize_image_request_text(payload)
        if not raw:
            return ""
        low = raw.lower()
        if low.startswith("/imagine"):
            return raw[len("/imagine") :].strip()
        cleaned = strip_nl_imagine_boilerplate(raw).strip()
        return cleaned if cleaned else raw

    def _quota_slot(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _quota_load(self) -> Dict[str, Any]:
        p = self._quota_path
        if not p.is_file():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _quota_save(self, data: Dict[str, Any]) -> None:
        p = self._quota_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def _quota_allow(self, user_id: str) -> tuple[bool, int, int]:
        slot = self._quota_slot()
        data = self._quota_load()
        days = data.get("days") if isinstance(data.get("days"), dict) else {}
        row = days.get(slot) if isinstance(days.get(slot), dict) else {}
        users = row.get("users") if isinstance(row.get("users"), dict) else {}
        used = int(users.get(user_id) or 0)
        if used >= self.daily_limit_per_user:
            return False, used, self.daily_limit_per_user
        return True, used, self.daily_limit_per_user

    def _quota_inc(self, user_id: str) -> None:
        slot = self._quota_slot()
        data = self._quota_load()
        days = data.get("days") if isinstance(data.get("days"), dict) else {}
        row = days.get(slot) if isinstance(days.get(slot), dict) else {}
        users = row.get("users") if isinstance(row.get("users"), dict) else {}
        users[user_id] = int(users.get(user_id) or 0) + 1
        row["users"] = users
        days[slot] = row
        keys = sorted(days.keys())[-7:]
        data["days"] = {k: days[k] for k in keys}
        self._quota_save(data)

    def _modalities_list(self) -> List[str]:
        # Как в доке OpenRouter для Seedream и др.: по умолчанию только image.
        # Для моделей «картинка+текст» (Gemini): IMAGE_GEN_MODALITIES=image,text
        raw = (os.getenv("IMAGE_GEN_MODALITIES") or "image").strip()
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        return parts if parts else ["image"]

    def _image_config_payload(self) -> Optional[Dict[str, Any]]:
        if not _truthy("IMAGE_GEN_IMAGE_CONFIG", True):
            return None
        cfg: Dict[str, Any] = {}
        ar = (os.getenv("IMAGE_GEN_ASPECT_RATIO") or "").strip()
        if not ar:
            ar = _aspect_ratio_from_size(self.size) or "1:1"
        cfg["aspect_ratio"] = ar
        q = (self.quality or "").strip().lower()
        size_map = {
            "low": "0.5K",
            "draft": "0.5K",
            "medium": "1K",
            "standard": "1K",
            "high": "2K",
            "hd": "2K",
            "2k": "2K",
            "4k": "4K",
        }
        if q in size_map:
            cfg["image_size"] = size_map[q]
        return cfg if cfg else None

    def _user_content_for_chat(self, prompt: str, *, ref_count: int = 0) -> str:
        body = build_reference_user_prompt(prompt, ref_count=ref_count)
        if self.style:
            return f"{body}\n\n(style hint: {self.style})"
        return body

    def _reference_max_bytes(self) -> int:
        try:
            return max(64_000, int((os.getenv("IMAGE_GEN_REFERENCE_MAX_BYTES") or "4194304").strip()))
        except ValueError:
            return 4_194_304

    def _reference_enabled(self) -> bool:
        return _truthy("IMAGE_GEN_REFERENCE_ENABLED", True)

    @staticmethod
    def _guess_mime(path: Path) -> str:
        ext = path.suffix.lower()
        if ext in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if ext == ".webp":
            return "image/webp"
        if ext == ".gif":
            return "image/gif"
        return "image/png"

    def _encode_image_file(self, path: Path) -> Optional[Tuple[str, str]]:
        try:
            if not path.is_file():
                return None
            raw = path.read_bytes()
            if len(raw) > self._reference_max_bytes():
                logger.warning("[image_generator] reference too large: %s", path)
                return None
            mime = self._guess_mime(path)
            b64 = base64.b64encode(raw).decode("ascii")
            return mime, b64
        except Exception as e:
            logger.debug("[image_generator] reference read %s: %s", path, e)
            return None

    def _collect_reference_paths(self, input_data: Dict[str, Any]) -> List[Path]:
        if not self._reference_enabled():
            return []
        meta = input_data.get("meta") if isinstance(input_data.get("meta"), dict) else {}
        fc = meta.get("file_context") if isinstance(meta.get("file_context"), dict) else {}
        if not fc:
            return []
        ordered = collect_reference_paths_chronological(fc)
        out: List[Path] = []
        seen: set[str] = set()
        for p_str in ordered:
            p = Path(p_str)
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    def _build_chat_user_content(
        self,
        prompt: str,
        reference_paths: List[Path],
    ) -> Any:
        text = self._user_content_for_chat(prompt, ref_count=len(reference_paths))
        if not reference_paths:
            return text
        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for path in reference_paths:
            enc = self._encode_image_file(path)
            if not enc:
                continue
            mime, b64 = enc
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        if len(parts) == 1:
            return text
        return parts

    def _is_legacy_images_endpoint(self) -> bool:
        return "/images/generations" in self.api_url.lower()

    @staticmethod
    def _openrouter_error_hint(status: int, txt: str) -> str:
        try:
            j = json.loads(txt)
            err = j.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or err.get("code") or "").strip()
                if msg:
                    return f"http_{status}: {msg[:420]}"
            if isinstance(err, str) and err.strip():
                return f"http_{status}: {err.strip()[:420]}"
        except Exception:
            pass
        tail = (txt or "").strip().replace("\n", " ")[:320]
        return f"http_{status}" + (f": {tail}" if tail else "")

    async def _call_legacy_images_api(self, *, prompt: str, model: str) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ManSio/gemma_agent",
            "X-Title": "Gemma Agent Image Generator",
        }
        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": self.size,
            "quality": self.quality,
        }
        if self.style:
            body["style"] = self.style
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout_sec)) as session:
            async with session.post(self.api_url, headers=headers, json=body) as resp:
                txt = await resp.text()
                if resp.status != 200:
                    return {
                        "ok": False,
                        "error": ImageGeneratorModule._openrouter_error_hint(resp.status, txt),
                        "body": txt[:800],
                    }
                try:
                    data = await resp.json()
                except Exception:
                    return {"ok": False, "error": "invalid_json", "body": txt[:800]}
                return {"ok": True, "data": data}

    async def _call_chat_completions_image(
        self,
        *,
        prompt: str,
        model: str,
        reference_paths: Optional[List[Path]] = None,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ManSio/gemma_agent",
            "X-Title": "Gemma Agent Image Generator",
        }
        refs = reference_paths or []
        user_content = self._build_chat_user_content(prompt, refs)
        base: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
        }
        modalities_full = self._modalities_list()
        ic = self._image_config_payload()

        variants: List[Dict[str, Any]] = []
        # 1) Минимальный payload (пример OpenRouter / Seedream): только modalities image
        variants.append({**base, "modalities": ["image"]})
        # 2) Как задано в IMAGE_GEN_MODALITIES (например image,text для Gemini)
        variants.append({**base, "modalities": list(modalities_full)})
        # 3) С image_config (соотношение сторон / размер — не все модели принимают)
        if ic:
            variants.append({**base, "modalities": list(modalities_full), "image_config": dict(ic)})

        seen: set[str] = set()
        bodies: List[Dict[str, Any]] = []
        for v in variants:
            key = json.dumps(v, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                bodies.append(v)

        last_status = 0
        last_txt = ""
        retry_400 = _truthy("IMAGE_GEN_RETRY_ON_400", True)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout_sec)) as session:
            for i, body in enumerate(bodies):
                async with session.post(self.api_url, headers=headers, json=body) as resp:
                    txt = await resp.text()
                    if resp.status == 200:
                        try:
                            data = json.loads(txt)
                        except Exception:
                            return {"ok": False, "error": "invalid_json", "body": txt[:800]}
                        if isinstance(data, dict):
                            return {"ok": True, "data": data}
                        return {"ok": False, "error": "invalid_json", "body": txt[:800]}
                    last_status, last_txt = resp.status, txt
                    if not retry_400 or last_status != 400:
                        break
                    if i + 1 >= len(bodies):
                        break
                    logger.debug(
                        "[image_generator] chat completions http_%s, retry variant %s/%s",
                        last_status,
                        i + 2,
                        len(bodies),
                    )
        return {
            "ok": False,
            "error": self._openrouter_error_hint(last_status, last_txt),
            "body": last_txt[:800],
        }

    async def _call_openrouter(
        self,
        *,
        prompt: str,
        model: str,
        reference_paths: Optional[List[Path]] = None,
    ) -> Dict[str, Any]:
        if self._is_legacy_images_endpoint():
            return await self._call_legacy_images_api(prompt=prompt, model=model)
        return await self._call_chat_completions_image(
            prompt=prompt,
            model=model, reference_paths=reference_paths
        )

    @staticmethod
    def _first_image_url_from_chat_response(data: Dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") if isinstance(first.get("message"), dict) else {}
        images = msg.get("images")
        if not isinstance(images, list) or not images:
            return ""
        img0 = images[0] if isinstance(images[0], dict) else {}
        iu = img0.get("image_url")
        if iu is None:
            iu = img0.get("imageUrl")
        if isinstance(iu, dict):
            return str(iu.get("url") or "").strip()
        if isinstance(iu, str):
            return iu.strip()
        return ""

    async def _resolve_image(self, data: Dict[str, Any]) -> Dict[str, Any]:
        chat_url = self._first_image_url_from_chat_response(data)
        if chat_url:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            if chat_url.startswith("data:"):
                raw, ext = _decode_data_url(chat_url)
                if not raw:
                    return {"ok": False, "error": "data_url_decode_failed"}
                fname = _safe_name(f"img_{ts}") + ext
                out = self.save_dir / fname
                out.write_bytes(raw)
                return {"ok": True, "path": str(out)}
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                    async with session.get(chat_url) as resp:
                        if resp.status != 200:
                            return {"ok": False, "error": f"download_http_{resp.status}"}
                        body = await resp.read()
                        ext = ".png"
                        ct = (resp.headers.get("Content-Type") or "").lower()
                        if "jpeg" in ct or "jpg" in ct:
                            ext = ".jpg"
                        elif "webp" in ct:
                            ext = ".webp"
                fname = _safe_name(f"img_{ts}") + ext
                out = self.save_dir / fname
                out.write_bytes(body)
                return {"ok": True, "path": str(out)}
            except Exception:
                return {"ok": False, "error": "download_failed"}

        arr = data.get("data")
        if not isinstance(arr, list) or not arr:
            return {"ok": False, "error": "no_images_in_response"}
        row = arr[0] if isinstance(arr[0], dict) else {}
        b64 = str(row.get("b64_json") or "").strip()
        url = str(row.get("url") or "").strip()

        self.save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = _safe_name(f"img_{ts}") + ".png"
        out = self.save_dir / fname

        if b64:
            try:
                out.write_bytes(base64.b64decode(b64))
                return {"ok": True, "path": str(out)}
            except Exception:
                return {"ok": False, "error": "b64_decode_failed"}
        if url:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            return {"ok": False, "error": f"download_http_{resp.status}"}
                        out.write_bytes(await resp.read())
                return {"ok": True, "path": str(out)}
            except Exception:
                return {"ok": False, "error": "download_failed"}
        return {"ok": False, "error": "no_image_fields"}

    @staticmethod
    def _fallback_user_notice_enabled() -> bool:
        return _truthy("IMAGE_GEN_FALLBACK_USER_NOTICE", True)

    def _log_empty_image_response(
        self,
        model: str,
        diag: Dict[str, Any],
        *,
        ref_count: int,
    ) -> None:
        logger.warning(
            "[image_generator] no image in response model=%s finish=%s native=%s tier=%s refs=%s excerpt=%r",
            model,
            diag.get("finish_reason"),
            diag.get("native_finish_reason"),
            diag.get("service_tier"),
            ref_count,
            (diag.get("message_excerpt") or "")[:160],
        )

    def _log_primary_api_failure(
        self,
        model: str,
        error: str,
        *,
        ref_count: int,
        body_excerpt: str = "",
    ) -> None:
        logger.warning(
            "[image_generator] primary api failed model=%s error=%s refs=%s body=%r",
            model,
            (error or "")[:200],
            ref_count,
            (body_excerpt or "")[:160],
        )

    def _build_success_hint(
        self,
        selected_model: str,
        ref_paths: List[Path],
        fallback_notices: List[str],
    ) -> str:
        parts: List[str] = []
        if fallback_notices and self._fallback_user_notice_enabled():
            parts.extend(fallback_notices)
        hint = f"Готово. Модель: {selected_model}"
        if ref_paths:
            hint += f" (по вашему фото: {len(ref_paths)})"
        parts.append(hint)
        return "\n\n".join(parts)

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        if not self.enabled:
            return [Output(type="text", payload="Генерация изображений отключена (`IMAGE_GEN_ENABLED=false`).", meta={"module": "image_generator"})]
        if not self.api_key:
            return [Output(type="text", payload="Не задан OPENROUTER_API_KEY для генерации изображений.", meta={"module": "image_generator", "error": "missing_api_key"})]

        input_data = args.get("input", {}) if isinstance(args, dict) else {}
        payload = str(input_data.get("payload") or "")
        meta = input_data.get("meta") if isinstance(input_data.get("meta"), dict) else {}
        user_id = str(meta.get("user_id") or "unknown")
        prompt = self._extract_prompt(payload)
        if not prompt:
            return [
                Output(
                    type="text",
                    payload=(
                        "Использование: /imagine <описание> или «сгенерируй картинку …».\n"
                        "Перерисовка: фото + «перерисуй …» или сначала фото, потом текст.\n"
                        "Подробнее: /help → 🖼 Картинки"
                    ),
                    meta={"module": "image_generator", "error": "empty_prompt"},
                )
            ]
        allowed, used, limit = self._quota_allow(user_id)
        if not allowed:
            return [
                Output(
                    type="text",
                    payload=(
                        f"Лимит генерации изображений исчерпан: {used}/{limit} за текущие UTC-сутки. "
                        "Попробуй снова после 00:00 UTC."
                    ),
                    meta={"module": "image_generator", "error": "daily_limit_reached", "used": used, "limit": limit},
                )
            ]

        ref_paths = self._collect_reference_paths(input_data if isinstance(input_data, dict) else {})
        primary_model = self.model
        selected_model = primary_model
        fallback_notices: List[str] = []
        result = await self._call_openrouter(
            prompt=prompt, model=selected_model, reference_paths=ref_paths
        )
        if (
            not result.get("ok")
            and self.fallback_model
            and self.fallback_model != selected_model
        ):
            self._log_primary_api_failure(
                selected_model,
                str(result.get("error") or "api_error"),
                ref_count=len(ref_paths),
                body_excerpt=str(result.get("body") or ""),
            )
            fallback_notices.append(
                format_api_fallback_notice(
                    primary_model=primary_model,
                    fallback_model=self.fallback_model,
                    api_error=str(result.get("error") or "api_error"),
                )
            )
            selected_model = self.fallback_model
            result = await self._call_openrouter(
                prompt=prompt, model=selected_model, reference_paths=ref_paths
            )
        if not result.get("ok"):
            return [
                Output(
                    type="text",
                    payload=f"Не удалось сгенерировать изображение: {result.get('error')}.",
                    meta={"module": "image_generator", "error": str(result.get("error") or "image_gen_failed")},
                )
            ]

        response_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        resolved = await self._resolve_image(response_data)
        if not resolved.get("ok") and str(resolved.get("error") or "") == "no_images_in_response":
            diag = diagnose_chat_response(response_data)
            self._log_empty_image_response(selected_model, diag, ref_count=len(ref_paths))
            retry_model = (
                self.fallback_model
                if self.fallback_model and self.fallback_model != selected_model
                else selected_model
            )
            if self.fallback_model and retry_model != selected_model:
                fallback_notices.append(
                    format_fallback_notice(
                        primary_model=primary_model,
                        fallback_model=retry_model,
                        reason=image_finish_label(diag),
                        message_excerpt=str(diag.get("message_excerpt") or ""),
                    )
                )
            retry_prompt = (
                f"{prompt}\n\n"
                "Верни только одно итоговое изображение (image output). Без текста, если модель позволяет."
            )
            retry_result = await self._call_openrouter(
                prompt=retry_prompt,
                model=retry_model,
                reference_paths=ref_paths,
            )
            if retry_result.get("ok"):
                retry_data = retry_result.get("data") if isinstance(retry_result.get("data"), dict) else {}
                resolved = await self._resolve_image(retry_data)
                if resolved.get("ok"):
                    selected_model = retry_model
                elif str(resolved.get("error") or "") == "no_images_in_response":
                    retry_diag = diagnose_chat_response(retry_data)
                    self._log_empty_image_response(retry_model, retry_diag, ref_count=len(ref_paths))
        if not resolved.get("ok"):
            err = str(resolved.get("error") or "save_failed")
            last_diag = diagnose_chat_response(response_data)
            if err == "no_images_in_response":
                reason = image_finish_label(last_diag)
                msg = (
                    f"Модель вернула ответ без картинки ({reason}). "
                    "Проверьте modalities image на OpenRouter; при необходимости IMAGE_GEN_MODALITIES=image или image,text."
                )
                excerpt = str(last_diag.get("message_excerpt") or "").strip()
                if excerpt:
                    msg += f"\nОтвет модели: {excerpt[:320]}"
            else:
                msg = f"Изображение получено, но не удалось сохранить: {err}."
            return [
                Output(
                    type="text",
                    payload=msg,
                    meta={
                        "module": "image_generator",
                        "error": err,
                        "image_finish_reason": image_finish_label(last_diag),
                    },
                )
            ]
        self._quota_inc(user_id)

        meta_out: Dict[str, Any] = {
            "module": "image_generator",
            "image_output_path": str(resolved["path"]),
            "prompt": prompt[:240],
            "model": selected_model,
        }
        if ref_paths:
            meta_out["reference_images"] = len(ref_paths)
        if fallback_notices:
            meta_out["image_gen_fallback_notices"] = len(fallback_notices)
        hint = self._build_success_hint(selected_model, ref_paths, fallback_notices)
        return [Output(type="text", payload=hint, meta=meta_out)]
