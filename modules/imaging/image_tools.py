from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from core.error_analysis import record_error_event


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


IMAGE_TOOLS_ENABLED = _env_bool("IMAGE_TOOLS_ENABLED", True)
IMAGE_MAX_MB = float(os.getenv("IMAGE_MAX_MB", "10"))
IMAGE_MAX_RESOLUTION = int(os.getenv("IMAGE_MAX_RESOLUTION", "2048"))
IMAGE_JPEG_QUALITY = int(os.getenv("IMAGE_JPEG_QUALITY", "85"))
IMAGE_STRIP_EXIF = _env_bool("IMAGE_STRIP_EXIF", True)
IMAGE_DOWNSCALE_BEFORE_PROCESSING = _env_bool("IMAGE_DOWNSCALE_BEFORE_PROCESSING", True)
IMAGE_SECURITY_STRICT = _env_bool("IMAGE_SECURITY_STRICT", True)
ALLOW_UNSAFE_FORMATS = _env_bool("IMAGE_ALLOW_UNSAFE_FORMATS", False)
ABS_MAX_DIMENSION = 20000
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_EXIF_BYTES = 512 * 1024
MAX_ICC_BYTES = 1024 * 1024


def _lazy_pil():
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageFile, ImageStat
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    return Image, ImageEnhance, ImageFilter, ImageOps, ImageStat


def _err(stage: str, error: str, **extra: Any) -> Dict[str, Any]:
    payload = {"ok": False, "error": error, "stage": stage}
    if extra:
        payload["meta"] = extra
    record_error_event("image_tools", f"{stage} failed", extra={"error": error, **extra})
    return payload


def detect_image_type(path: str) -> str:
    try:
        Image, _, _, _, _ = _lazy_pil()
        with Image.open(path) as img:
            fmt = (img.format or "").lower()
        return fmt
    except Exception:
        return ""


def validate_image_dimensions(image) -> Dict[str, Any]:
    try:
        w, h = image.size
        if w <= 0 or h <= 0:
            return _err("validate_dimensions", "invalid_image_dimensions", width=w, height=h)
        if w > ABS_MAX_DIMENSION or h > ABS_MAX_DIMENSION:
            return _err("validate_dimensions", "dimension_hard_limit_exceeded", width=w, height=h)
        if max(w, h) > IMAGE_MAX_RESOLUTION and IMAGE_SECURITY_STRICT:
            return _err("validate_dimensions", "max_resolution_exceeded", width=w, height=h, max_resolution=IMAGE_MAX_RESOLUTION)
        if (w * h * 4) > MAX_DECOMPRESSED_BYTES:
            return _err("validate_dimensions", "decompressed_size_too_large", width=w, height=h)
        return {"ok": True}
    except Exception as e:
        return _err("validate_dimensions", str(e))


def validate_image_format(image) -> Dict[str, Any]:
    try:
        mode = (image.mode or "").upper()
        if mode not in {"RGB", "RGBA", "L", "P", "CMYK", "LA"}:
            return _err("validate_format", "unsupported_mode", mode=mode)
        return {"ok": True, "mode": mode}
    except Exception as e:
        return _err("validate_format", str(e))


def validate_image_metadata(image) -> Dict[str, Any]:
    try:
        exif = b""
        try:
            exif = image.info.get("exif", b"") or b""
        except Exception:
            exif = b""
        icc = image.info.get("icc_profile", b"") or b""
        if len(exif) > MAX_EXIF_BYTES and IMAGE_SECURITY_STRICT:
            return _err("validate_metadata", "exif_too_large", exif_bytes=len(exif))
        if len(icc) > MAX_ICC_BYTES and IMAGE_SECURITY_STRICT:
            return _err("validate_metadata", "icc_profile_too_large", icc_bytes=len(icc))
        return {"ok": True, "exif_bytes": len(exif), "icc_bytes": len(icc)}
    except Exception as e:
        return _err("validate_metadata", str(e))


def validate_image_safety(path: str) -> Dict[str, Any]:
    if not IMAGE_TOOLS_ENABLED:
        return _err("validate_safety", "image_tools_disabled")
    try:
        if not os.path.isfile(path):
            return _err("validate_safety", "file_not_found", path=path)
        size = os.path.getsize(path)
        if size > int(IMAGE_MAX_MB * 1024 * 1024):
            return _err("validate_safety", "file_too_large", size=size, max_mb=IMAGE_MAX_MB)
        t = detect_image_type(path)
        if not t:
            return _err("validate_safety", "mime_not_image", path=path)
        if t in {"tiff", "bmp", "ico"} and not ALLOW_UNSAFE_FORMATS:
            return _err("validate_safety", "format_not_allowed", detected_type=t)
        Image, _, _, _, _ = _lazy_pil()
        with Image.open(path) as probe:
            probe.verify()
        return {"ok": True, "detected_type": t, "size": size}
    except Exception as e:
        return _err("validate_safety", str(e), path=path)


def fix_exif_orientation(image):
    try:
        _, _, _, ImageOps, _ = _lazy_pil()
        return ImageOps.exif_transpose(image)
    except Exception:
        return image


def safe_load_image(path: str) -> Dict[str, Any]:
    safe = validate_image_safety(path)
    if not safe.get("ok"):
        return safe
    try:
        Image, _, _, _, _ = _lazy_pil()
        with Image.open(path) as src:
            src.load()
            img = fix_exif_orientation(src)
            if IMAGE_DOWNSCALE_BEFORE_PROCESSING and max(img.size) > IMAGE_MAX_RESOLUTION:
                img.thumbnail((IMAGE_MAX_RESOLUTION, IMAGE_MAX_RESOLUTION))
            vd = validate_image_dimensions(img)
            if not vd.get("ok"):
                return vd
            vf = validate_image_format(img)
            if not vf.get("ok"):
                return vf
            vm = validate_image_metadata(img)
            if not vm.get("ok"):
                return vm
            if IMAGE_STRIP_EXIF:
                clean = img.copy()
                clean.info.pop("exif", None)
                clean.info.pop("icc_profile", None)
                img = clean
            if img.mode in {"CMYK", "P", "LA"}:
                img = img.convert("RGB")
            meta = {
                "size_bytes": safe.get("size"),
                "mime": safe.get("detected_type"),
                "resolution": img.size,
                "mode": img.mode,
            }
            return {"ok": True, "image": img, "meta": meta}
    except Exception as e:
        return _err("safe_load_image", str(e), path=path)


def resize_max(image, max_side_px: int):
    try:
        max_side_px = max(256, int(max_side_px))
        w, h = image.size
        if max(w, h) <= max_side_px:
            return {"ok": True, "image": image}
        out = image.copy()
        out.thumbnail((max_side_px, max_side_px))
        return {"ok": True, "image": out}
    except Exception as e:
        return _err("resize_max", str(e))


def safe_save_image(image, target_path: str, format_name: str = "PNG") -> Dict[str, Any]:
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if image.mode in {"CMYK", "LA", "P"}:
            image = image.convert("RGB")
        if max(image.size) > IMAGE_MAX_RESOLUTION:
            rr = resize_max(image, IMAGE_MAX_RESOLUTION)
            if not rr.get("ok"):
                return rr
            image = rr["image"]
        vd = validate_image_dimensions(image)
        if not vd.get("ok"):
            return vd
        save_kwargs: Dict[str, Any] = {}
        fmt = (format_name or "PNG").upper()
        if fmt == "JPEG":
            save_kwargs["quality"] = max(60, min(95, int(IMAGE_JPEG_QUALITY)))
            save_kwargs["optimize"] = True
            save_kwargs["progressive"] = False
        image.save(target_path, format=fmt, exif=b"", icc_profile=None, **save_kwargs)
        return {"ok": True, "path": target_path}
    except Exception as e:
        return _err("safe_save_image", str(e), path=target_path)


def to_grayscale(image):
    try:
        _, _, _, ImageOps, _ = _lazy_pil()
        return {"ok": True, "image": ImageOps.grayscale(image)}
    except Exception as e:
        return _err("to_grayscale", str(e))


def to_sepia(image):
    try:
        _, _, _, _, _ = _lazy_pil()
        gray_res = to_grayscale(image)
        if not gray_res.get("ok"):
            return gray_res
        gray = gray_res["image"].convert("RGB")
        px = gray.load()
        w, h = gray.size
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                tr = min(255, int(r * 1.07 + 20))
                tg = min(255, int(g * 0.95 + 10))
                tb = min(255, int(b * 0.82))
                px[x, y] = (tr, tg, tb)
        return {"ok": True, "image": gray}
    except Exception as e:
        return _err("to_sepia", str(e))


def blur(image, radius: float):
    try:
        _, _, ImageFilter, _, _ = _lazy_pil()
        return {"ok": True, "image": image.filter(ImageFilter.GaussianBlur(radius=max(0.1, float(radius))))}
    except Exception as e:
        return _err("blur", str(e))


def rotate_safe(image, angle: float):
    try:
        out = image.rotate(float(angle), expand=True)
        if max(out.size) > IMAGE_MAX_RESOLUTION:
            rr = resize_max(out, IMAGE_MAX_RESOLUTION)
            if not rr.get("ok"):
                return rr
            out = rr["image"]
        return {"ok": True, "image": out}
    except Exception as e:
        return _err("rotate_safe", str(e))


def crop_safe(image, box: Tuple[int, int, int, int]):
    try:
        w, h = image.size
        x1, y1, x2, y2 = box
        x1 = max(0, min(w, int(x1)))
        y1 = max(0, min(h, int(y1)))
        x2 = max(x1 + 1, min(w, int(x2)))
        y2 = max(y1 + 1, min(h, int(y2)))
        out = image.crop((x1, y1, x2, y2))
        return {"ok": True, "image": out}
    except Exception as e:
        return _err("crop_safe", str(e), box=box)


def enhance_sharp(image):
    try:
        _, _, ImageFilter, _, _ = _lazy_pil()
        out = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
        return {"ok": True, "image": out}
    except Exception as e:
        return _err("enhance_sharp", str(e))


def enhance_denoise(image):
    try:
        _, _, ImageFilter, _, _ = _lazy_pil()
        out = image.filter(ImageFilter.MedianFilter(size=3))
        return {"ok": True, "image": out}
    except Exception as e:
        return _err("enhance_denoise", str(e))


def enhance_brightness(image, factor: float):
    try:
        _, ImageEnhance, _, _, _ = _lazy_pil()
        return {"ok": True, "image": ImageEnhance.Brightness(image).enhance(float(factor))}
    except Exception as e:
        return _err("enhance_brightness", str(e), factor=factor)


def enhance_contrast(image, factor: float):
    try:
        _, ImageEnhance, _, _, _ = _lazy_pil()
        return {"ok": True, "image": ImageEnhance.Contrast(image).enhance(float(factor))}
    except Exception as e:
        return _err("enhance_contrast", str(e), factor=factor)


def enhance_saturation(image, factor: float):
    try:
        _, ImageEnhance, _, _, _ = _lazy_pil()
        return {"ok": True, "image": ImageEnhance.Color(image).enhance(float(factor))}
    except Exception as e:
        return _err("enhance_saturation", str(e), factor=factor)


def _simple_color_balance(image):
    try:
        _, _, _, _, ImageStat = _lazy_pil()
        img = image if image.mode == "RGB" else image.convert("RGB")
        stat = ImageStat.Stat(img)
        means = stat.mean[:3]
        avg = sum(means) / 3.0 if means else 128.0
        gains = [avg / (m if m > 1 else 1) for m in means]
        px = img.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                px[x, y] = (
                    max(0, min(255, int(r * gains[0]))),
                    max(0, min(255, int(g * gains[1]))),
                    max(0, min(255, int(b * gains[2]))),
                )
        return {"ok": True, "image": img}
    except Exception as e:
        return _err("color_balance", str(e))


def enhance_auto(image):
    try:
        _, _, _, ImageOps, _ = _lazy_pil()
        img = image
        img = ImageOps.autocontrast(img)
        r1 = enhance_denoise(img)
        if not r1.get("ok"):
            return r1
        r2 = enhance_sharp(r1["image"])
        if not r2.get("ok"):
            return r2
        r3 = _simple_color_balance(r2["image"])
        if not r3.get("ok"):
            return r3
        return {"ok": True, "image": r3["image"]}
    except Exception as e:
        return _err("enhance_auto", str(e))
