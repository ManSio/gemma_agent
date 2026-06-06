"""
Короткий слой заземления для промпта: время, метка сообщения Telegram, профиль локации.
Без лишнего текста — снижает галлюцинации «нет доступа к времени» при экономии токенов.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def build_minimal_grounding(context: Dict[str, Any], user_facts: Dict[str, Any]) -> str:
    ctx = context if isinstance(context, dict) else {}
    facts = user_facts if isinstance(user_facts, dict) else {}
    parts: list[str] = []
    now = datetime.now(timezone.utc)
    parts.append(f"UTC_now={now.strftime('%Y-%m-%d %H:%M:%S')}")
    tu = ctx.get("telegram_message_date_unix")
    if tu is not None:
        try:
            mt = datetime.fromtimestamp(int(tu), tz=timezone.utc)
            parts.append(f"tg_msg_utc={mt.strftime('%Y-%m-%d %H:%M:%S')}")
        except (OSError, ValueError, TypeError):
            pass
    city = str(facts.get("city") or "").strip()
    country = str(facts.get("country") or "").strip()
    tz = str(facts.get("timezone") or "").strip()
    prof: list[str] = []
    if city:
        prof.append(f"city={city}")
    if country:
        prof.append(f"country={country}")
    if tz:
        prof.append(f"tz={tz}")
    if prof:
        parts.append("profile:" + ",".join(prof))
    if ctx.get("telegram_voice_transcription"):
        parts.append(
            "ввод=голос_STT(возможны обрывы и ошибки распознавания; отвечай только по явному смыслу текста, "
            "не додумывай заказ еды/товаров/услуг без прямых слов пользователя; при неясности — попроси повторить текстом)"
        )
    return "; ".join(parts)
