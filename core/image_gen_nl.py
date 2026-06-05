"""
Эвристики маршрутизации запросов на генерацию изображений без slash-команды /imagine.
"""
from __future__ import annotations

import os
import re
from typing import Final, Optional

# Объект запроса: картинка / изображение / иконка… (не «модуль» / «плагин»).
_IMG_OBJECT: Final[str] = (
    r"(?:картинк\w*|изображен\w*|рисунк\w*|иллюстрац\w*|график\w*|"
    r"иконк\w*|аватар\w*|лого\b|баннер\w*|мем\w*|"
    r"(?:png|jpeg|jpg|webp)\b|"
    r"\b(?:images?|pictures?|illustrations?|artwork|banner|logo|avatar|icon)s?\b)"
)

# Глаголы «сделай визуал» (в т.ч. опечатки вроде «сгенирируешь»).
_RU_IMG_VERB: Final[str] = (
    r"(?:сгенерир\w*|с генерир\w*|сгенериру[а-яё]*|с генериру[а-яё]*|сгенириру[а-яё]*|с генириру[а-яё]*|сгенерировать(?:те)?|с генерировать(?:те)?|"
    r"нагенерируй(?:те)?|создай(?:те)?|нарисуй(?:те)?|покажи(?:те)?|сделай(?:те)?)"
)

# Русские и английские формулировки «сделай визуал».
_IMG_GEN_PROSE: Final[re.Pattern[str]] = re.compile(
    rf"(?is)"
    rf"(?:"
    rf"\b{_RU_IMG_VERB}\b"
    rf".{{0,120}}?\b{_IMG_OBJECT}"
    rf"|"
    rf"\b(?:сгенерир\w*|сгенериру[а-яё]*|сгенириру[а-яё]*|нарисуй(?:те)?|создай(?:те)?)\b\s+"
    rf"(?:мне\s+|для\s+меня\s+)?(?:ещё\s+)?{_IMG_OBJECT}\b"
    rf"|"
    rf"\b(?:хочу|надо|можешь|можно)\b.{{0,80}}?\b(?:сгенерир\w*|сгенериру[а-яё]*|сгенириру[а-яё]*|"
    rf"сгенерировать|нарисовать|создать)\b.{{0,80}}?\b{_IMG_OBJECT}"
    rf"|"
    rf"\bgenerate\b.{{0,120}}?\b(?:an?\s+)?{_IMG_OBJECT}"
    rf"|"
    rf"\b(?:create|make)\b.{{0,80}}?\b(?:an?\s+)?{_IMG_OBJECT}"
    rf"|"
    rf"\bdraw(?:ing)?\b.{{0,80}}?\b(?:me\s+)?(?:a\s+)?(?:{_IMG_OBJECT}|"
    rf"picture|image|portrait|landscape|scene|character|sketch)\b"
    rf")",
)


def image_gen_nl_route_enabled() -> bool:
    raw = os.getenv("IMAGE_GEN_NL_ROUTE")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Нормализация подписи к фото (эмодзи «🖼» и т.п.).
_IMG_CAPTION_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^[\s\U0001F300-\U0001FAFF🖼🎨📷]+",
    re.UNICODE,
)


def normalize_image_request_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    prev = None
    while prev != s:
        prev = s
        s = _IMG_CAPTION_PREFIX_RE.sub("", s).strip()
    return s


# Стилизация по фото без слова «картинка»: «сделай как мультик», «в стиле аниме».
_IMG_STYLE_TRANSFORM: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:сделай|сделать|сделайте)\b\s+(?:как|в\s+виде|в\s+стиле)\s+\S+"
    r"|"
    r"\b(?:как|в\s+стиле|в\s+виде)\s+"
    r"(?:мультик\w*|аниме\w*|мультяш\w*|cartoon|comic|pixar\w*|disney\w*|анимац\w*|"
    r"акварел\w*|киберпанк\w*|реализм\w*|фото\w*)"
    r"|"
    r"\b(?:cartoon|anime|comic|pixar|disney)\s+style\b"
    r")"
)


# «Сделай мальчика на фото…», «сгенерируй … на фото что он…» — без слова «картинка».
_IMG_SCENE_ON_PHOTO: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:сгенерир\w*|сгенериру[а-яё]*|сгенириру[а-яё]*|сгенерировать(?:те)?|"
    r"нагенерируй(?:те)?|создай(?:те)?|нарисуй(?:те)?|сделай(?:те)?|"
    r"добавь(?:те)?|помести(?:те)?|вставь(?:те)?)\b"
    r".{0,220}?\b(?:на\s+фото|по\s+фото|с\s+этого\s+фото|на\s+снимк\w*|с\s+фото|из\s+фото|"
    r"из\s+фотограф\w*|с\s+фотограф\w*|из\s+этой\s+фотограф\w*|к\s+фото)\b"
    r"|"
    r"\b(?:на\s+фото|по\s+фото|на\s+снимк\w*)\b.{0,100}?\b(?:"
    r"сделай(?:те)?|сгенериру[а-яё]*|сгенириру[а-яё]*|нарисуй(?:те)?|"
    r"чтобы\s+он|что\s+он|чтоб\s+он"
    r")\b"
    r")"
)

_IMG_QA_ON_PHOTO: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:^|\b)(?:что|кто|где|когда|сколько|какой|какая|какие|опиши|расскажи|объясни)\b"
    r".{0,60}?\b(?:на\s+фото|на\s+снимк\w*|по\s+фото)\b"
)


def prose_wants_image_scene_on_photo(text: str) -> bool:
    """Генерация/композиция по приложенному фото без слова «картинка»."""
    t = normalize_image_request_text(text)
    if not t or t.startswith("/"):
        return False
    if _IMG_QA_ON_PHOTO.search(t):
        return False
    return bool(_IMG_SCENE_ON_PHOTO.search(t))


# Короткий follow-up после фото без повторного «на фото»: «сделай его SWAT», «как в Форсаже».
_IMG_PENDING_FOLLOWUP: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:сделай|сделать|сделайте|преврати|превратить|поставь|помести|вставь)\b"
    r".{0,100}?\b(?:его|её|ее|их|чтоб\s+он|чтобы\s+он|что\s+он|он\s+стал|она\s+стала)\b"
    r"|"
    r"\b(?:сделай|сделать|сделайте|поставь|помести)\b"
    r".{0,120}?\b(?:"
    r"спец\s*подраздел|swat\b|экипировк|переодел|костюм\b|форма\b|"
    r"форсаж|гонщик|чемпионк|боевик|сталлон|sylvester|movie\s+poster|film\s+still|"
    r"черт\w*\s+лиц|форм\w*\s+тел|ракурс|узнава|внешност|пропорц"
    r")\b"
    r")"
)


def prose_wants_image_pending_followup(text: str) -> bool:
    """Текст после недавнего фото — подтянуть pending, даже без «на фото»."""
    t = normalize_image_request_text(text)
    if not t or t.startswith("/"):
        return False
    try:
        max_c = int((os.getenv("IMAGE_GEN_NL_MAX_CHARS") or "3000").strip())
    except ValueError:
        max_c = 3000
    if len(t) > max_c:
        return False
    if _IMG_QA_ON_PHOTO.search(t):
        return False
    if prose_wants_image_generation(t) or prose_wants_image_edit(t):
        return False
    return bool(_IMG_PENDING_FOLLOWUP.search(t))


def text_eligible_for_pending_image_attach(text: str) -> bool:
    return prose_wants_image_gen_or_edit(text) or prose_wants_image_pending_followup(text)


_NEW_IMAGE_PROJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\bновый\s+проект\b"
    r"|"
    r"\bновая\s+планировк\w*\b"
    r"|"
    r"\bдругой\s+план\b"
    r"|"
    r"\bс\s+чистого\s+листа\b"
    r"|"
    r"\bначн(?:ём|ем)\s+заново\b"
    r"|"
    r"\bnew\s+project\b"
    r"|"
    r"\breset\s+(?:image|project|session)\b"
    r")"
)


def prose_wants_new_image_project(text: str) -> bool:
    """Явный сброс очереди фото и сессии правки."""
    t = normalize_image_request_text(text)
    if not t:
        return False
    return bool(_NEW_IMAGE_PROJECT_RE.search(t))


# Композит / два референса: фон, перенос субъекта, склейка.
_IMG_COMPOSITE_PROSE: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:замени|заменить|смени|сменить)\b.{0,40}?\b(?:фон|background)\b"
    r"|"
    r"\b(?:фон|background)\b.{0,40}?\b(?:с|из|со)\b.{0,30}?\b(?:втор\w*|2-?й|2-?го|перв\w*|1-?й|друг\w*)\s+(?:фото|снимк\w*|картинк\w*)\b"
    r"|"
    r"\b(?:перенес\w*|перенести|вставь|вставить|помести|поместить|совмести|совместить|"
    r"склей|склеить|объедини|объединить|наложи|наложить)\b"
    r"|"
    r"\b(?:с|из|со)\b.{0,25}?\b(?:перв\w*|втор\w*|одн\w*|друг\w*)\s+(?:фото|снимк\w*)\b.{0,60}?\b(?:на|в|к)\b"
    r"|"
    r"\b(?:combine|merge|composite|transfer)\b.{0,60}?\b(?:photo|image|background)\b"
    r"|"
    r"\b(?:use|take)\b.{0,30}?\bbackground\b.{0,40}?\bfrom\b"
    r")"
)


def prose_wants_image_composite(text: str) -> bool:
    t = normalize_image_request_text(text)
    if not t or t.startswith("/"):
        return False
    return bool(_IMG_COMPOSITE_PROSE.search(t))


# Редактирование / перерисовка приложенного фото (Nano Banana 2 и др.).
_IMG_EDIT_PROSE: Final[re.Pattern[str]] = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:перерисуй|перерисовать|переделай|переделать|измени|изменить|отредактируй|отредактировать|"
    r"дорисуй|дорисовать|стилизуй|стилизовать|преобразуй|преобразовать|обработай|обработать|"
    r"замени|заменить|вставь|вставить)\b"
    r"|"
    r"\b(?:edit|redraw|repaint|restyle|transform|remix)\b"
    r"(?:\s+(?:this|the|my))?\s+(?:image|photo|picture)\b"
    r"|"
    r"\b(?:make\s+it|turn\s+it\s+into)\b"
    r")"
)


def prose_wants_image_edit(text: str) -> bool:
    t = normalize_image_request_text(text)
    if not t or t.startswith("/"):
        return False
    return bool(_IMG_EDIT_PROSE.search(t))


def prose_wants_image_style_transform(text: str) -> bool:
    """«Сделай как мультик» / «в стиле аниме» при приложенном фото."""
    t = normalize_image_request_text(text)
    if not t or t.startswith("/"):
        return False
    if any(x in t.lower() for x in ("module.json", "entrypoint", "capabilities", "tool_call")):
        return False
    return bool(_IMG_STYLE_TRANSFORM.search(t))


def _file_context_is_user_image(file_context: Optional[dict]) -> bool:
    if not isinstance(file_context, dict):
        return False
    if str(file_context.get("file_type") or "").strip().lower() != "image":
        return False
    path = str(file_context.get("local_path") or "").strip()
    return bool(path)


def prose_wants_image_gen_or_edit(text: str) -> bool:
    return (
        prose_wants_image_generation(text)
        or prose_wants_image_edit(text)
        or prose_wants_image_style_transform(text)
        or prose_wants_image_scene_on_photo(text)
        or prose_wants_image_composite(text)
        or prose_wants_image_pending_followup(text)
    )


def attachment_wants_image_generation(
    file_context: Optional[dict],
    text: str,
) -> bool:
    """Фото пользователя + текст с генерацией или редактированием → image_generator."""
    if not image_gen_nl_route_enabled() or not _truthy_env("IMAGE_GEN_REFERENCE_ENABLED", True):
        return False
    if not _file_context_is_user_image(file_context):
        return False
    t = normalize_image_request_text(text)
    if not t:
        return False
    try:
        from core.spatial_design.classifier import classify_spatial_turn

        if classify_spatial_turn(text, file_context=file_context):
            return False
    except Exception:
        pass
    return (
        prose_wants_image_generation(t)
        or prose_wants_image_edit(t)
        or prose_wants_image_style_transform(t)
        or prose_wants_image_scene_on_photo(t)
        or prose_wants_image_composite(t)
    )


def prose_wants_image_generation(text: str) -> bool:
    t = normalize_image_request_text(text)
    if not t or t.startswith("/"):
        return False
    try:
        max_c = int((os.getenv("IMAGE_GEN_NL_MAX_CHARS") or "3000").strip())
    except ValueError:
        max_c = 3000
    if len(t) > max_c:
        return False
    low = t.lower()
    _dev = (
        "module.json",
        "entrypoint",
        "manifest",
        "hot_install",
        "selfprogramming",
        "generate_module",
        "pip_requirements",
        "async def execute",
        "capabilities",
        "tool_call",
    )
    if any(x in low for x in _dev):
        return False
    return bool(_IMG_GEN_PROSE.search(t))


_STRIP_NL_PREFIX: Final[re.Pattern[str]] = re.compile(
    r"(?is)^\s*"
    r"(?:пожалуйста[,]?\s*)?"
    r"(?:бот[,]?\s*)?"
    r"(?:"
    r"(?:хочу|надо|можешь|можно)\b[\s,]*"
    r"(?:ли\s+)?"
    r"(?:чтобы\s+ты\s+)?"
    r")?"
    r"(?:"
    r"(?:сгенерир\w*|сгенериру[а-яё]*|сгенириру[а-яё]*|сгенерировать(?:те)?|"
    r"нагенерируй(?:те)?|создай(?:те)?|нарисуй(?:те)?|покажи(?:те)?|сделай(?:те)?|"
    r"generate|creating|create|make|draw(?:ing)?)\b[\s,]*"
    r"(?:me\s+|us\s+)?"
    r"(?:please\s+)?"
    r")+"
    r"(?:мне\s+|для\s+меня\s+|для\s+нас\s+)?"
    r"(?:an?\s+|the\s+)?"
    r"(?:ещё\s+|еще\s+)?"
    r"(?:как\s+(?:нибудь|то)\s+)?"
    r"(?:"
    r"(?:картинк\w*|изображен\w*|рисунк\w*|иллюстрац\w*|график\w*|иконк\w*|аватар\w*|"
    r"лого\b|баннер\w*|мем\w*|images?|pictures?|illustrations?|artwork|banners?|logos?|avatars?|icons?)\b"
    r"[\s,]*)?"
    r"(?:для\s+\S+\s+)?"
    r"(?:в\s+телеграм\w*[\s,]*)?"
    r"[\s—:–-]*"
)


def strip_nl_imagine_boilerplate(text: str) -> str:
    """Убирает типичное начало «сгенерируй картинку …», оставляя описание сцены."""
    s = (text or "").strip()
    if not s or s.lower().startswith("/imagine"):
        return s
    prev = None
    while prev != s:
        prev = s
        s = _STRIP_NL_PREFIX.sub("", s).strip()
    return s.strip(" \t—:-–,.;")
