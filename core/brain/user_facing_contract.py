"""
Контракт текста для пользователя: нормализация ответа LLM и классификация короткого хода.

Не словари триггеров — контекст (последняя реплика ассистента, длина, форма реплики).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Sequence

from core.brain.cot_strip import strip_provider_think_tags
from core.brain.text_helpers import safe_text

logger = logging.getLogger(__name__)

ShortTurnKind = Literal[
    "normal",
    "continuation",
    "agreement",
    "chitchat",
    "substantive",
    "noise",
]

NormalizeStatus = Literal["ok", "empty", "leak", "garbage_json"]

_ASSISTANT_INVITE_RE = re.compile(
    r"(?i)(могу\s+подсказ|если\s+хочешь|скажи\s*,\s*что|с\s+чего\s+начн|"
    r"продолжим|уточни|напиши\s+ещ|могу\s+помочь|хотите\s+сгенерир|"
    r"давайте\b|чем\s+могу\s+помочь|начнём|начнем|готов\s+подсказать)"
)
_CONT_EXPLICIT_RE = re.compile(
    r"(?i)^\s*(продолж\w*|дальше|ещ[её]|continue|go\s+on)\s*[\.\!\?…]*\s*$"
)
_CHITCHAT_RE = re.compile(
    r"(?i)^\s*(привет|здравств|добрый|как\s+дела|hi|hello|hey|спасибо|пока)\b"
)
_SUBSTANTIVE_Q_RE = re.compile(
    r"(?i)\b(почему|зачем|отчего|как\s+работает|что\s+такое|объясни|расскажи\s+почему)\b"
)
_SHORT_FOLLOWUP_RE = re.compile(
    r"(?i)(ну\s+и\s+что|а\s+где|где\s|когда\s|сколько\s|почему\s|а\s+как|и\s+что|"
    r"в\s+сельсовете|соответствует|точно|правда\s+ли)"
)
_NEGATION_START_RE = re.compile(r"(?i)^\s*не\s+")
_LOCAL_MODELS_RE = re.compile(
    r"(?i)(huggingface|hf\.co|lm\s*studio|gguf|ollama|comfyui|stable\s*diffusion|"
    r"automatic1111|локальн\w*\s+модел|midjourney\s*prompt)"
)
_SCENARIO_FALLBACK_RE = re.compile(
    r"не\s+удалось\s+сформировать\s+нормальный\s+ответ",
    re.IGNORECASE,
)
_THINK_LEAK_RE = re.compile(r"(?i)redacted_thinking|</?think>")


def _short_turn_max_chars() -> int:
    try:
        v = int((os.getenv("USER_FACING_SHORT_TURN_MAX_CHARS") or "56").strip())
    except ValueError:
        v = 56
    return max(24, min(96, v))


@dataclass(frozen=True)
class UserFacingNormalizeResult:
    text: str
    status: NormalizeStatus
    stripped_think_tags: bool = False


def _dialogue_rows(rows: Optional[Sequence[Any]]) -> List[dict]:
    out: List[dict] = []
    if not rows:
        return out
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def last_assistant_text(
    recent_dialogue: Optional[Sequence[Any]] = None,
    *,
    last_assistant: str = "",
) -> str:
    la = (last_assistant or "").strip()
    if la:
        return la
    for row in reversed(_dialogue_rows(recent_dialogue)):
        role = str(row.get("role") or "").strip().lower()
        if role not in ("assistant", "bot"):
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if text:
            return text
    return ""


def assistant_invites_continuation(assistant_text: str) -> bool:
    """Бот предложил продолжить / ждёт короткого согласия или уточнения."""
    t = (assistant_text or "").strip()
    if not t:
        return False
    tail = t[-320:]
    if tail.rstrip().endswith("?"):
        return True
    if _ASSISTANT_INVITE_RE.search(tail):
        return True
    return False


def classify_short_user_turn(
    user_text: str,
    recent_dialogue: Optional[Sequence[Any]] = None,
    *,
    last_assistant: str = "",
) -> ShortTurnKind:
    ut = (user_text or "").strip()
    if not ut:
        return "noise"
    try:
        from core.brain.code_empty_recovery import thread_awaits_code_body

        ctx = {"recent_dialogue": recent_dialogue, "dialogue_state": {}}
        if last_assistant:
            ctx["dialogue_state"] = {"last_assistant_excerpt": last_assistant}
        if thread_awaits_code_body(ut, ctx):
            return "continuation"
    except Exception:
        pass
    if len(ut) > _short_turn_max_chars():
        return "normal"

    low = ut.lower()
    if _NEGATION_START_RE.match(ut) and len(ut) > 12:
        return "normal"

    last = last_assistant_text(recent_dialogue, last_assistant=last_assistant)
    invited = assistant_invites_continuation(last)

    if _CHITCHAT_RE.match(ut):
        return "chitchat"
    if _CONT_EXPLICIT_RE.match(ut):
        return "continuation"
    if _SUBSTANTIVE_Q_RE.search(ut):
        return "substantive"

    if "?" in ut and len(ut.split()) <= 8:
        if _SHORT_FOLLOWUP_RE.search(low) or (last and len(last) > 40):
            return "continuation"

    if invited and len(ut) <= 32:
        if re.fullmatch(r"(?i)[а-яёa-z]{2,12}", ut.replace(" ", "")):
            return "agreement"
        if re.fullmatch(r"(?i)(да|ок|ага|угу|ладно|yes|ok|yep|yeah)", low.rstrip(".!?…")):
            return "agreement"
        if low.startswith("дав") and len(ut) <= 8:
            return "agreement"

    if len(ut) <= 2:
        return "noise"
    return "normal"


def is_short_turn_continuing_dialogue(kind: ShortTurnKind) -> bool:
    return kind in ("continuation", "agreement")


def is_continuation_turn_from_context(
    user_text: str,
    context: Optional[dict] = None,
) -> bool:
    ctx = context if isinstance(context, dict) else {}
    rd = ctx.get("recent_dialogue") or ctx.get("recent_messages")
    ds = ctx.get("dialogue_state")
    last = ""
    if isinstance(ds, dict):
        last = str(ds.get("last_assistant_excerpt") or "").strip()
    kind = classify_short_user_turn(user_text, rd, last_assistant=last)
    if is_short_turn_continuing_dialogue(kind):
        return True
    if kind == "continuation":
        return True
    return False


def normalize_user_facing_text(
    text: str,
    *,
    user_text: str = "",
    extra_markers_en: Optional[tuple] = None,
    extra_markers_ru: Optional[tuple] = None,
) -> UserFacingNormalizeResult:
    """Единая нормализация перед Telegram: think-теги → finalize (CoT, leak, JSON)."""
    raw = safe_text(text)
    if not raw.strip():
        return UserFacingNormalizeResult("", "empty")

    stripped = strip_provider_think_tags(raw)
    think_removed = stripped != raw.strip()

    from core.brain.response_finalize import (
        _finalize_user_reply_legacy,
        _looks_like_garbage_json,
        looks_like_prompt_instruction_leak,
    )

    before_leak = stripped
    out = _finalize_user_reply_legacy(
        stripped,
        user_text=user_text,
        extra_markers_en=extra_markers_en or (),
        extra_markers_ru=extra_markers_ru or (),
    )
    if not (out or "").strip():
        if looks_like_prompt_instruction_leak(before_leak):
            return UserFacingNormalizeResult("", "leak", stripped_think_tags=think_removed)
        if _looks_like_garbage_json(before_leak):
            return UserFacingNormalizeResult("", "garbage_json", stripped_think_tags=think_removed)
        return UserFacingNormalizeResult("", "empty", stripped_think_tags=think_removed)

    try:
        from core.brain.code_empty_recovery import apply_code_delivery_if_needed

        out = apply_code_delivery_if_needed(user_text, out)
    except Exception as e:
        logger.debug("apply_code_delivery_if_needed: %s", e)

    return UserFacingNormalizeResult(
        out.strip(),
        "ok",
        stripped_think_tags=think_removed,
    )


def thread_mentions_local_models(
    user_text: str,
    recent_dialogue: Optional[Sequence[Any]] = None,
) -> bool:
    parts = [(user_text or "").strip()]
    for row in _dialogue_rows(recent_dialogue)[-8:]:
        parts.append(str(row.get("text") or row.get("content") or ""))
    blob = "\n".join(p for p in parts if p)
    return bool(_LOCAL_MODELS_RE.search(blob))


def build_local_models_scope_hint(
    user_text: str,
    recent_dialogue: Optional[Sequence[Any]] = None,
) -> str:
    if not thread_mentions_local_models(user_text, recent_dialogue):
        return ""
    return (
        "DOMAIN_LOCAL_MODELS: речь о внешних локальных моделях и софте пользователя "
        "(Hugging Face, LM Studio, ComfyUI, GGUF/Ollama). "
        "Не отвечай шаблоном «я сам не создаю изображения» — обсуждай выбор и запуск у пользователя. "
        "Команда /imagine в этом боте — отдельно, только если пользователь просит картинку здесь."
    )


def build_continuation_dialogue_hint(
    user_text: str,
    recent_dialogue: Optional[Sequence[Any]] = None,
    *,
    last_assistant: str = "",
) -> str:
    kind = classify_short_user_turn(
        user_text, recent_dialogue, last_assistant=last_assistant
    )
    if not is_short_turn_continuing_dialogue(kind):
        return ""
    return (
        "DIALOGUE_CONTINUATION: короткая реплика продолжает текущую нить из recent_dialogue. "
        "Разверни прошлую тему (шаги, выбор модели, настройка), не начинай с нуля и не уходи в chitchat."
    )


def detect_delivery_issues(
    assistant_text: str,
    *,
    detail: str = "",
    normalize_status: str = "",
) -> List[str]:
    issues: List[str] = []
    at = (assistant_text or "").strip()
    d = (detail or "").strip().lower()
    ns = (normalize_status or "").strip().lower()
    if _THINK_LEAK_RE.search(at):
        issues.append("delivery_think_leak")
    if _SCENARIO_FALLBACK_RE.search(at):
        issues.append("delivery_scenario_fallback")
    if ns and ns != "ok":
        issues.append(f"delivery_normalize_{ns}")
    if "delivery_normalize" in d or "short_turn_kind=" in d:
        if "delivery_normalize_empty" in d or "normalize_status=empty" in d:
            issues.append("delivery_normalize_empty")
    if len(at) < 12 and at and not _SCENARIO_FALLBACK_RE.search(at):
        if "empty_output" in d or "pre_send_empty" in d:
            issues.append("delivery_empty_gate")
    return issues


def delivery_detail_suffix(
    *,
    normalize_status: str = "",
    short_turn_kind: str = "",
    scenario_recovered: bool = False,
) -> str:
    parts: List[str] = []
    if normalize_status:
        parts.append(f"normalize_status={normalize_status}")
    if short_turn_kind:
        parts.append(f"short_turn_kind={short_turn_kind}")
    if scenario_recovered:
        parts.append("scenario_recovered=1")
    return " ".join(parts)


def recover_delivery_fallback(
    user_text: str,
    recent_dialogue: Optional[Sequence[Any]] = None,
    *,
    last_assistant: str = "",
    reason: str = "empty",
) -> str:
    """Единый текст при срыве доставки (scenario / pre_send), с учётом контекста нити."""
    kind = classify_short_user_turn(
        user_text, recent_dialogue, last_assistant=last_assistant
    )
    if is_short_turn_continuing_dialogue(kind):
        return (
            "Продолжаем с прошлого шага. Напиши одним сообщением, с чего начать "
            "(ОС, LM Studio / ComfyUI, или конкретная модель с Hugging Face) — дам шаги."
        )
    if thread_mentions_local_models(user_text, recent_dialogue):
        return (
            "По локальным моделям и LM Studio: уточни ОС (Windows/Linux) и цель "
            "(только чат / генерация картинок / обе). Подберу порядок установки без путаницы с /imagine."
        )
    try:
        from core.brain.text_helpers import natural_fallback_response

        return natural_fallback_response(
            "empty_llm" if reason == "empty" else reason,
            "unknown",
            user_text,
        )
    except Exception:
        return (
            "Не получилось собрать ответ. Сформулируй задачу одной фразой — попробую снова."
        )


def short_reply_acceptable_for_turn(
    reply_body: str,
    user_text: str,
    recent_dialogue: Optional[Sequence[Any]] = None,
    *,
    last_assistant: str = "",
) -> bool:
    """Короткий ответ бота не считать «пустым» для scenario pre_send."""
    b = (reply_body or "").strip()
    if not b:
        return False
    kind = classify_short_user_turn(
        user_text, recent_dialogue, last_assistant=last_assistant
    )
    if is_short_turn_continuing_dialogue(kind) and len(b) <= 400:
        return True
    return False
