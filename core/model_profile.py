"""
Профили под конкретные семейства моделей OpenRouter: системные дополнения, плотность «каркаса
рассуждений» в user-промпте, лёгкая подстройка temperature и доп. маркеры утечек CoT.

Порядок сопоставления: JSON-оверлей (MODEL_PROFILES_PATH) → встроенные правила (специфичнее раньше) → default.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModelProfile:
    """Набор подсказок под id модели (по подстроке slug)."""

    match_label: str
    system_addon_first: str = ""
    system_addon_second: str = ""
    # full — длинный каркас в user-промпте; short — одна строка; omit — без блока
    reasoning_scaffold: str = "full"
    temperature_first_delta: float = 0.0
    temperature_second_delta: float = 0.0
    cot_extra_markers_en: Tuple[str, ...] = ()
    cot_extra_markers_ru: Tuple[str, ...] = ()


_DEFAULT = ModelProfile(match_label="default")

_BUILTIN: List[Tuple[str, ModelProfile]] = [
    (
        "deepseek-r1",
        ModelProfile(
            match_label="deepseek-r1",
            reasoning_scaffold="short",
            system_addon_first=(
                "Ты модель с выделенным рассуждением: пользователю не показывай цепочку мыслей и англ. self-talk. "
                "Только финальный ответ на языке пользователя или один блок TOOL_CALL как в инструкции."
            ),
            system_addon_second=(
                "Не дублируй технические поля из результата инструмента; сформулируй короткий человеческий итог."
            ),
            cot_extra_markers_en=("reasoning:", "analysis:", "<think>", "</think>"),
            cot_extra_markers_ru=("рассуждение:", "цепочка мыслей"),
        ),
    ),
    (
        "deepseek",
        ModelProfile(
            match_label="deepseek",
            # v4 Flash и родственные: полный каркас в user-промпте + краткий финальный ответ.
            reasoning_scaffold="full",
            system_addon_first=(
                "Следуй системным инструкциям и внутреннему каркасу рассуждения; пользователю не выводи план, чек-листы и метки вроде «рассуждение:». "
                "Если модель отдаёт скрытые reasoning-токены — итог для пользователя всё равно обычный текст или один TOOL_CALL. "
                "По умолчанию ответ плотный; развёрнутый сценарный разбор (ветки, риски, хронология) — при task_outline / явной просьбе / теме из operator_rules. "
                "При противоречии между длинными операторскими дополнениями и явной репликой пользователя в этом сообщении — приоритет у реплики (кроме безопасности и формата TOOL_CALL). "
                "Инструмент: строка TOOL_CALL: и один валидный JSON без markdown-ограждений."
            ),
            temperature_first_delta=-0.15,
            cot_extra_markers_en=("reasoning:", "analysis:", "<think>", "</think>"),
            cot_extra_markers_ru=("рассуждение:", "цепочка мыслей"),
        ),
    ),
    (
        "qwen",
        ModelProfile(
            match_label="qwen",
            reasoning_scaffold="short",
            system_addon_first=(
                "Держи ответ плотным. TOOL_CALL: ровно один JSON-объект после строки TOOL_CALL: без markdown-ограждений."
            ),
            temperature_first_delta=-0.05,
        ),
    ),
    (
        "gemini",
        ModelProfile(
            match_label="gemini",
            reasoning_scaffold="short",
            system_addon_first=(
                "Не выводи разделы «размышление», «план», «analysis». Не пересказывай длинно поля контекста — сразу ответ или TOOL_CALL."
            ),
            cot_extra_markers_en=("i'm considering", "let me think", "step 1:"),
        ),
    ),
    (
        "meta-llama",
        ModelProfile(
            match_label="meta-llama",
            reasoning_scaffold="short",
            system_addon_first=("Не используй преамбулы вроде «As an AI». Сразу по существу."),
        ),
    ),
    (
        "mistralai",
        ModelProfile(
            match_label="mistralai",
            reasoning_scaffold="short",
            system_addon_first=("Формулируй кратко; для инструментов строго формат TOOL_CALL из системной инструкции."),
        ),
    ),
    (
        "anthropic/",
        ModelProfile(
            match_label="anthropic",
            reasoning_scaffold="short",
            system_addon_first=(
                "TOOL_CALL: один JSON без обёртки ```; не добавляй пояснений до или после JSON при вызове инструмента."
            ),
        ),
    ),
    (
        "claude",
        ModelProfile(
            match_label="claude",
            reasoning_scaffold="short",
            system_addon_first=(
                "При вызове инструмента выводи только строку TOOL_CALL: и JSON; без преамбулы «Хорошо, я…»."
            ),
        ),
    ),
    (
        "openai/gpt",
        ModelProfile(
            match_label="openai",
            reasoning_scaffold="short",
            system_addon_first=("Соблюдай формат TOOL_CALL; избегай внутренних чек-листов в ответе пользователю."),
        ),
    ),
    (
        "openrouter/",
        ModelProfile(
            match_label="openrouter",
            reasoning_scaffold="short",
            system_addon_first=(
                "Ты можешь быть слабее крупных моделей: при сомнении в факте вызови инструмент или честно скажи, что не знаешь."
            ),
        ),
    ),
]

_OVERLAY_MTIME: Optional[float] = None
_OVERLAY_PAIRS: List[Tuple[str, ModelProfile]] = []


def _normalize_lists_to_tuples(d: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(d)
    for k in ("cot_extra_markers_en", "cot_extra_markers_ru"):
        v = out.get(k)
        if isinstance(v, list):
            out[k] = tuple(str(x) for x in v)
    return out


def _load_json_overlay() -> List[Tuple[str, ModelProfile]]:
    global _OVERLAY_MTIME, _OVERLAY_PAIRS
    path = (os.getenv("MODEL_PROFILES_PATH") or "").strip()
    if not path:
        _OVERLAY_MTIME, _OVERLAY_PAIRS = None, []
        return []
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        logger.warning("MODEL_PROFILES_PATH не читается: %s", path)
        _OVERLAY_MTIME, _OVERLAY_PAIRS = None, []
        return []
    if _OVERLAY_MTIME == mtime and _OVERLAY_PAIRS:
        return _OVERLAY_PAIRS
    pairs: List[Tuple[str, ModelProfile]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("model profiles JSON: %s", e)
        _OVERLAY_MTIME, _OVERLAY_PAIRS = mtime, []
        return []
    entries: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        entries = [x for x in raw if isinstance(x, dict)]
    elif isinstance(raw, dict) and isinstance(raw.get("patterns"), list):
        entries = [x for x in raw["patterns"] if isinstance(x, dict)]
    fields = set(ModelProfile.__dataclass_fields__.keys()) - {"match_label"}
    for item in entries:
        needle = str(item.get("match") or "").strip().lower()
        if not needle:
            continue
        label = str(item.get("match_label") or f"file:{needle[:40]}")
        patch: Dict[str, Any] = {"match_label": label}
        for key in fields:
            if key in item and item[key] is not None:
                patch[key] = item[key]
        patch = _normalize_lists_to_tuples(patch)
        try:
            prof = replace(_DEFAULT, **patch)
        except TypeError as e:
            logger.warning("model profile entry skip %s: %s", needle, e)
            continue
        pairs.append((needle, prof))
    _OVERLAY_MTIME, _OVERLAY_PAIRS = mtime, pairs
    if pairs and _truthy("MODEL_PROFILE_LOG", False):
        logger.info("model profiles: loaded %s custom pattern(s) from %s", len(pairs), path)
    return pairs


def resolve_model_profile(model_id: str) -> ModelProfile:
    mid = (model_id or "").strip().lower()
    if not mid:
        return _DEFAULT
    for needle, prof in _load_json_overlay():
        if needle in mid:
            if _truthy("MODEL_PROFILE_LOG", False):
                logger.info("[model_profile] %s -> %s (overlay)", model_id, prof.match_label)
            return prof
    for needle, prof in _BUILTIN:
        if needle in mid:
            if _truthy("MODEL_PROFILE_LOG", False):
                logger.info("[model_profile] %s -> %s (builtin)", model_id, prof.match_label)
            return prof
    return _DEFAULT


def resolve_brain_primary_model(llm: Any) -> str:
    return "deepseek/deepseek-v4-pro"


def resolve_brain_secondary_model(llm: Any) -> str:
    return "deepseek/deepseek-v4-pro"


def clamp_temperature(base: float, delta: float) -> float:
    try:
        t = float(base) + float(delta)
    except (TypeError, ValueError):
        t = float(base)
    return max(0.05, min(0.95, t))


def merge_system(*parts: str) -> str:
    return " ".join((p or "").strip() for p in parts if (p or "").strip())
