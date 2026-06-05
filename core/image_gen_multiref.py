"""
Промпт и порядок референсов для мультифото (композит, фон, перенос субъекта).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.image_gen_nl import (
    normalize_image_request_text,
    prose_wants_image_composite,
    prose_wants_image_edit,
    prose_wants_image_gen_or_edit,
    prose_wants_image_pending_followup,
    prose_wants_image_scene_on_photo,
    prose_wants_image_style_transform,
)


def pending_max_photos() -> int:
    import os

    raw = (os.getenv("IMAGE_PENDING_MAX_PHOTOS") or "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(n, 4))


def reference_max_count() -> int:
    import os

    raw = (os.getenv("IMAGE_GEN_REFERENCE_MAX_COUNT") or "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(n, 4))


def collect_reference_paths_chronological(file_context: Dict[str, Any]) -> List[str]:
    """Пути к референсам: фото 1 = первое отправленное, последнее = самое новое."""
    paths: List[str] = []
    seen: set[str] = set()
    secondary = file_context.get("secondary_images")
    if isinstance(secondary, list):
        for row in secondary:
            if not isinstance(row, dict):
                continue
            p = str(row.get("local_path") or "").strip()
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
    primary = str(file_context.get("local_path") or "").strip()
    if primary and primary not in seen:
        paths.append(primary)
    return paths[:reference_max_count()]


def merge_pending_file_contexts(pending_fcs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    pending_fcs: newest-first (как pop_pending_images).
    Возвращает file_context: primary = последнее фото, secondary = старые по порядку.
    """
    valid = [dict(fc) for fc in pending_fcs if isinstance(fc, dict) and fc.get("local_path")]
    if not valid:
        return None
    chronological = list(reversed(valid))
    main = dict(chronological[-1])
    if len(chronological) > 1:
        main["secondary_images"] = chronological[:-1]
    return main


# Явные роли в тексте пользователя («первое фото — лицо»).
_ROLE_HINT_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:перв\w*|1-?й|1-?е|1-?го)\s+(?:фото|снимок|картинк\w*|image)"
    r"|"
    r"(?:втор\w*|2-?й|2-?е|2-?го)\s+(?:фото|снимок|картинк\w*|image)"
    r"|"
    r"(?:трет\w*|3-?й|3-?е)\s+(?:фото|снимок|картинк\w*)"
    r"|"
    r"(?:first|second|third)\s+(?:photo|image|picture)"
    r")"
)


def user_prompt_has_explicit_image_roles(text: str) -> bool:
    return bool(_ROLE_HINT_RE.search(normalize_image_request_text(text)))


_MULTI_REF_COUNT_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"\b(?:два|две|три|нескольк\w*)\s+(?:фото|снимк\w*|картинк\w*)"
    r"|"
    r"\b(?:2|3)\s+фото"
    r"|"
    r"(?:втор\w*|2-?й|2-?е|2-?го|трет\w*|3-?й|3-?е|друг\w*)\s+(?:фото|снимк\w*|картинк\w*)"
    r"|"
    r"\b(?:first|second|third)\s+(?:photo|image|picture)"
    r")"
)


def prose_wants_multiref_pending_merge(text: str) -> bool:
    """
    Склеивать pending-фото с новым фото+подпись только если пользователь явно просит
    несколько референсов. Иначе новый план/картинка = только текущее вложение.
    """
    body = normalize_image_request_text(text)
    if not body:
        return False
    if user_prompt_has_explicit_image_roles(body):
        return True
    if prose_wants_image_composite(body):
        return True
    if prose_wants_identity_preservation(body):
        return True
    if prose_wants_image_pending_followup(body):
        return True
    return bool(_MULTI_REF_COUNT_RE.search(body))


_IDENTITY_MARKERS_RE = re.compile(
    r"(?i)(?:"
    r"черт\w*\s+лиц|лиц\w*\s+и\s+тел|форм\w*\s+тел|пропорц\w*|внешност\w*|узнава\w*|"
    r"похож\w*\s+на|тот\s+же\s+человек|та\s+же\s+человек|сохран\w*\s+лиц|"
    r"ракурс\w*|разных\s+угл|с\s+разных|нескольк\w*\s+фото|"
    r"\b3\s+фото|\bтри\s+фото|"
    r"facial\s+features?|face\s+likeness|body\s+shape|same\s+person|identity|"
    r"multi[- ]?angle|different\s+angles?"
    r")"
)


def prose_wants_identity_preservation(text: str) -> bool:
    """Пользователь просит сохранить узнаваемость человека по референсам."""
    t = normalize_image_request_text(text)
    if not t:
        return False
    return bool(_IDENTITY_MARKERS_RE.search(t))


def multiref_identity_mode(text: str, ref_count: int) -> bool:
    """
    Усиленный промпт для 2–3 фото одного человека (лицо + силуэт).
    3 референса → по умолчанию identity; 2 — при явных маркерах или сцене/стиле на фото.
    """
    if ref_count < 2:
        return False
    body = normalize_image_request_text(text)
    if ref_count >= 3:
        return True
    if prose_wants_identity_preservation(body):
        return True
    low = body.lower()
    if prose_wants_image_composite(body) and re.search(r"(?i)(?:фон|background)", low):
        if not prose_wants_identity_preservation(body):
            return False
    return bool(
        prose_wants_image_edit(body)
        or prose_wants_image_scene_on_photo(body)
        or prose_wants_image_style_transform(body)
    )


def build_reference_user_prompt(prompt: str, *, ref_count: int) -> str:
    """Текст для multimodal user message (Nano Banana / Gemini image)."""
    body = (prompt or "").strip()
    if ref_count <= 0:
        return body
    if ref_count == 1:
        if prose_wants_image_edit(body) or prose_wants_image_gen_or_edit(body):
            return (
                "Edit or redraw the attached reference image according to the instruction. "
                "Keep faces and main subject unless the user asks to change them.\n\n"
                f"{body}"
            )
        return body

    role_note = ""
    if user_prompt_has_explicit_image_roles(body):
        role_note = (
            "The user named which reference is which (e.g. first/second photo) — follow that mapping.\n"
        )
    numbering = "\n".join(
        f"- Reference image {i + 1} (attached below in this order): "
        f"{'first photo the user sent in this chat' if i == 0 else f'{i + 1}-th photo they sent'}."
        for i in range(ref_count)
    )

    if multiref_identity_mode(body, ref_count):
        angle_hint = (
            "Treat the references as multiple camera angles of the SAME person: use all of them to infer "
            "consistent facial structure (eyes, nose, mouth, jaw), hair, skin tone, age, and body proportions "
            "(height, build, posture). Do not blend faces from different people.\n"
        )
        if ref_count >= 3:
            angle_hint += (
                "With three references, cross-check each angle before drawing: front/profile/three-quarter "
                "should match one identity in the output.\n"
            )
        composite = prose_wants_image_composite(body)
        scene_ops = (
            "You may change outfit, background, or scene per the user — but the person must remain clearly "
            "the same individual as in the references."
        )
        if composite:
            scene_ops = (
                "Composite or transfer elements as requested, but preserve the subject's face and body "
                "identity from the references (do not replace with a generic model face)."
            )
        return (
            f"The user attached {ref_count} reference photos of one subject (identity preservation).\n"
            f"{numbering}\n"
            f"{role_note}"
            f"{angle_hint}"
            f"{scene_ops}\n"
            "Output a single image. Prioritize likeness over artistic exaggeration unless the user asks otherwise.\n\n"
            f"User instruction:\n{body}"
        )

    composite = prose_wants_image_composite(body)
    ops = (
        "Combine the reference images: transfer subject/person/object from one photo onto the scene or "
        "background of another, replace background, merge elements, or apply the user's edit across refs."
        if composite
        else "Use all reference images together according to the instruction (edit, restyle, or composite)."
    )
    return (
        f"The user attached {ref_count} reference images.\n"
        f"{numbering}\n"
        f"{role_note}"
        f"{ops}\n"
        "Produce one output image that fulfills the instruction.\n\n"
        f"User instruction:\n{body}"
    )
