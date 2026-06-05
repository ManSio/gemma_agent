"""
Стиль общения в группах: «свой в доску», поддержка беседы, завязать разговор.

Не добавляет авто-спам: только текст в system prompt при обращении к боту.
"""
from __future__ import annotations

import os
from typing import Optional

_PRESETS = {
    "friend": (
        "В групповом чате общайся как близкий друг на «ты»: тепло, без официоза, уместный юмор. "
        "Поддерживай нить разговора: отвечай по делу, можно коротко подыграть теме. "
        "Если просят завести разговон или «скучно» — предложи одну нейтральную тему или один лёгкий вопрос всем, без нотаций и лекций. "
        "Не монолог: обычно 1–3 коротких блока текста, если не просят развёрнуто."
    ),
    "sibling": (
        "В группе — тон близкого брата/сестры: поддержка, лёгкий юмор, без поучений и менторства. "
        "Можешь мягко подхватить шутку или предложить тему, если просят разрядить обстановку."
    ),
    "buddy": "",  # alias
}


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    # Включает «свой в доску» в группах; по умолчанию выкл., чтобы не менять тон существующих инсталляций.
    return _truthy("GROUP_SOCIAL_ENABLED", False)


def augment_system_prompt_for_group(base: str, *, group_id: Optional[str]) -> str:
    if not group_id or not enabled():
        return base
    mode = (os.getenv("GROUP_SOCIAL_MODE") or "friend").strip().lower()
    if mode == "off":
        return base
    custom = (os.getenv("GROUP_SOCIAL_PROMPT_ADDON") or "").strip()
    if custom:
        addon = custom
    else:
        if mode == "buddy":
            mode = "friend"
        addon = _PRESETS.get(mode) or _PRESETS["friend"]
    if not addon:
        return base
    sep = "" if (base or "").strip().endswith("\n") else "\n\n"
    return f"{base}{sep}{addon}"
