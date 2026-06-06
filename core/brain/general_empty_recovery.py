"""
Восстановление пустого ответа на обычные Q&A (не code_generation).

Retry одним коротким вызовом LLM; при сбое — детерминированные подсказки по узким темам (DWG/CAD).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from core.brain.cot_strip import strip_leaked_cot
from core.brain.text_helpers import safe_text
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_DWG_TOPIC_RE = re.compile(
    r"(?i)(dwg|autocad|черт[её]ж|cad\b|\.dwg|freecad|фрикад|draftsight|"
    r"trueview|librecad|шрифт.*черт|надпис.*съез|съезжают|pdf.*dwg|dwg.*pdf)"
)
_NO_FREECAD_RE = re.compile(
    r"(?i)(нету?\s+фрикад|нет\s+freecad|без\s+freecad|не\s+установлен\s+freecad)"
)
_SETUP_RE = re.compile(r"(?i)(настро|как\s+сделать|что\s+делать|помоги|исправ)")
_WHY_MESS_RE = re.compile(
    r"(?i)(почему|съезжа|куч[аеу]|не\s+чита|криво|буквы|надпис|тире|шрифт)"
)

_RETRY_SKIP_PROFILES = frozenset(
    {"code_generation", "code_debug", "batch", "task_executor"}
)

_GENERAL_RETRY_SYSTEM = (
    "Ты помощник в Telegram. Ответь по-русски на последний вопрос пользователя, "
    "учитывая короткий контекст диалога выше. "
    "3–8 предложений, по делу, без TOOL_CALL, без рассуждений вслух, без заголовков ##."
)


def is_dwg_cad_topic(text: str) -> bool:
    return bool(_DWG_TOPIC_RE.search(text or ""))


def _dialogue_excerpt(rows: Optional[Sequence[Any]], *, max_chars: int = 900) -> str:
    if not rows:
        return ""
    lines: List[str] = []
    for row in list(rows)[-6:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if not text:
            continue
        tag = "Пользователь" if role == "user" else "Бот"
        lines.append(f"{tag}: {text[:280]}")
    blob = "\n".join(lines).strip()
    if len(blob) > max_chars:
        return blob[-max_chars:]
    return blob


def dwg_cad_domain_fallback(user_text: str, *, recent_dialogue: Optional[Sequence[Any]] = None) -> str:
    """Детерминированный ответ, если LLM пустой на тему DWG/PDF/шрифтов."""
    ut = (user_text or "").strip()
    ctx = _dialogue_excerpt(recent_dialogue)
    combined = f"{ctx}\n{ut}".strip()
    if not is_dwg_cad_topic(combined):
        return ""

    if _NO_FREECAD_RE.search(ut) or (_NO_FREECAD_RE.search(ctx) and is_dwg_cad_topic(combined)):
        return (
            "FreeCAD не обязателен. Бесплатные варианты:\n"
            "• Autodesk DWG TrueView (Windows) — только просмотр/печать DWG, часто хватает проверить надписи.\n"
            "• LibreCAD — проще, но с DWG бывают ограничения; иногда лучше открыть DXF.\n"
            "• Онлайн: Autodesk Viewer (загрузка DWG в браузер) — без установки.\n"
            "• Если PDF «красивый», а DWG кривой — попроси у того, кто выдал файлы, PDF с вшитыми шрифтами "
            "или комплект шрифтов (папка fonts) для AutoCAD.\n"
            "Напиши, чем открываешь DWG сейчас (программа/телефон/ПК) — подскажу точнее."
        )

    if _SETUP_RE.search(ut) or (_SETUP_RE.search(ctx) and len(ut) < 40):
        return (
            "Чаще всего «съезжают» буквы из‑за шрифтов, а не «поломки» файла:\n"
            "1) В CAD: Параметры → Файлы → путь Substitute Font File (FONTALT) — укажи папку со шрифтами "
            "(часто нужны SHX: gost, txt, romans и т.п.).\n"
            "2) Стили текста (STYLE): для каждого стиля задай шрифт, который реально установлен.\n"
            "3) Команда TXTEXP (экспорт текста в линии) перед выдачей PDF — если файл уходит заказчику.\n"
            "4) Для просмотра без AutoCAD — DWG TrueView или Autodesk Viewer.\n"
            "Какой просмотрщик/версия AutoCAD — подскажу по шагам."
        )

    if _WHY_MESS_RE.search(ut) or _WHY_MESS_RE.search(ctx):
        return (
            "PDF и DWG устроены по‑разному. В PDF текст часто уже «нарисован» или вшиты шрифты — "
            "поэтому в любом просмотрщике выглядит ровно. В DWG надписи — объекты со ссылкой на шрифт (SHX/TTF); "
            "если на этом ПК нет того же шрифта, программа подставляет другой — буквы наезжают и «слипаются».\n"
            "Это не баг PDF: тот же чертёж в DWG без нужных шрифтов так и будет кривым. "
            "Решение: открыть в CAD с комплектом шрифтов, настроить FONTALT/STYLE или получить PDF/DXF от автора чертежа."
        )

    return (
        "DWG — формат чертежей AutoCAD и совместимых CAD. Для просмотра без полной AutoCAD: "
        "DWG TrueView (бесплатно), Autodesk Viewer в браузере или LibreCAD. "
        "Если надписи «съезжают» — почти всегда не хватает шрифтов на этом компьютере; уточни, чем открываешь файл."
    )


def _build_retry_prompt(
    user_text: str,
    *,
    recent_dialogue: Optional[Sequence[Any]] = None,
) -> str:
    ut = (user_text or "").strip()
    excerpt = _dialogue_excerpt(recent_dialogue)
    if excerpt:
        return f"Контекст диалога:\n{excerpt}\n\nТекущий вопрос: {ut}"
    return ut


async def try_recover_empty_general_reply(
    *,
    llm: Any,
    user_text: str,
    brain_profile: str,
    first_result: Dict[str, Any],
    task_tier: str,
    telemetry_extra: Optional[Dict[str, Any]] = None,
    llm_session_id: str = "",
    recent_dialogue: Optional[Sequence[Any]] = None,
) -> str:
    prof = (brain_profile or "standard").strip().lower()
    if prof in _RETRY_SKIP_PROFILES:
        return ""
    if not effective_bool("BRAIN_GENERAL_EMPTY_RETRY", default=True):
        return dwg_cad_domain_fallback(user_text, recent_dialogue=recent_dialogue)

    domain_fb = dwg_cad_domain_fallback(user_text, recent_dialogue=recent_dialogue)

    usage = first_result.get("usage_detail") or {}
    comp = int(usage.get("completion_tokens") or 0)
    raw0 = safe_text(first_result.get("content", ""))
    if raw0.strip():
        try:
            from core.openrouter_completion_text import _strip_think_noise

            light = _strip_think_noise(raw0)
            if light.strip():
                return light.strip()
        except Exception as e:
            logger.debug("general_recovery light strip: %s", e)
        try:
            from core.brain.cot_strip import strip_leaked_cot

            mild = strip_leaked_cot(raw0)
            if mild.strip() and len(mild.strip()) >= 2:
                return mild.strip()
        except Exception as e:
            logger.debug("general_recovery cot strip: %s", e)
    if comp > 0 and not raw0.strip():
        logger.info(
            "[general_recovery] empty content with completion_tokens=%s profile=%s",
            comp,
            prof,
        )
    elif comp > 0 and raw0.strip():
        logger.info(
            "[general_recovery] retry after strip emptied reply profile=%s completion_tokens=%s",
            prof,
            comp,
        )

    prompt = _build_retry_prompt(user_text, recent_dialogue=recent_dialogue)
    if not prompt.strip():
        return domain_fb

    try:
        from core.llm_tiered import llm_generate_tiered
        from core.resilience import with_timeout

        tiered = effective_bool("BRAIN_LLM_TIERED_RETRY", default=True)
        if tiered:
            retry = await llm_generate_tiered(
                llm,
                tag="llm_general_empty_retry",
                prompt=prompt,
                system_prompt=_GENERAL_RETRY_SYSTEM,
                max_tokens=900,
                temperature=0.2,
                base_timeout=None,
                task_tier=task_tier or "fast",
                telemetry_tag="brain_general_retry",
                telemetry_extra=telemetry_extra,
                session_id=llm_session_id,
                conversation_id=llm_session_id,
            )
        else:
            retry = await with_timeout(
                llm.generate(
                    prompt=prompt,
                    system_prompt=_GENERAL_RETRY_SYSTEM,
                    max_tokens=900,
                    temperature=0.2,
                    telemetry_tag="brain_general_retry",
                    telemetry_extra=telemetry_extra,
                    session_id=llm_session_id,
                    conversation_id=llm_session_id,
                ),
                timeout_sec=75.0,
                tag="llm_general_empty_retry",
            )
    except Exception as e:
        logger.warning("[general_recovery] retry failed: %s", e)
        return domain_fb

    if retry.get("error"):
        return domain_fb

    raw = safe_text(retry.get("content", ""))
    try:
        from core.brain.user_facing_contract import normalize_user_facing_text

        norm = normalize_user_facing_text(raw, user_text=user_text)
        if (norm.text or "").strip() and norm.status == "ok":
            return norm.text.strip()
    except Exception as e:
        logger.debug('%s optional failed: %s', 'general_empty_recovery', e, exc_info=True)
    out = strip_leaked_cot(raw)
    if (out or "").strip():
        return out.strip()
    return domain_fb
