"""
Сборка промпта для LLM — именованные «режимы», без дублирования оркестратора.

Идея (как в зрелых агентских системах, но без лишних фреймворков):
- **Маршрутизатор** уже у вас: UnifiedPlanner + intent; он решает *куда* отдать ход.
- **Сборка промпта** — отдельный слой: *сколько* контекста положить в один вызов мозга.

Режимы (tiers) в `core/brain/pipeline.call_brain`:
- FULL — полный блок «Контекст» (persona, twin, goals, knowledge, …).
- HOT_SLIM — сжатый блок (короткие реплики, усечённые каталоги/правила).
- IMAGE_SLIM — после vision-precaption: ещё меньше полей, фокус на картинке.

Служебные вызовы (connectivity, короткий ping OpenRouter) не используют этот модуль —
они идут напрямую в HTTP с минимальным `messages` (см. `core/connectivity_check.py`).

Дальнейшее развитие: явный ключ `context["prompt_assembly"]` для принудительного tier
(отладка) и отдельные «пакеты» контекста из Mem0/фактов по политике retention.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class PromptAssemblyTier(str, Enum):
    """Какой каркас user-промпта собрался для первого прохода мозга."""

    FULL = "full"
    HOT_SLIM = "hot_slim"
    IMAGE_SLIM = "image_slim"


def brain_prompt_tier(*, use_slim_image: bool, hot_path_slim: bool) -> PromptAssemblyTier:
    """Определить tier по уже вычисленным флагам ветвления (pipeline)."""
    if use_slim_image:
        return PromptAssemblyTier.IMAGE_SLIM
    if hot_path_slim:
        return PromptAssemblyTier.HOT_SLIM
    return PromptAssemblyTier.FULL


def tier_label_for_metrics(tier: PromptAssemblyTier) -> str:
    return tier.value


def describe_tier_ru(tier: PromptAssemblyTier) -> str:
    return {
        PromptAssemblyTier.FULL: "полный контекст",
        PromptAssemblyTier.HOT_SLIM: "сжатый контекст (hot path)",
        PromptAssemblyTier.IMAGE_SLIM: "контекст после vision (slim)",
    }.get(tier, tier.value)


def snapshot_context_policy(context: Dict[str, Any]) -> Dict[str, Any]:
    """Компактный снимок для логов: что подсказало политику сборки (без больших полей)."""
    ctx = context if isinstance(context, dict) else {}
    ph = ctx.get("predictive_hint") if isinstance(ctx.get("predictive_hint"), dict) else {}
    return {
        "terse_mode": bool(ph.get("terse_mode")),
        "group_id_set": bool(ctx.get("group_id")),
        "override": (ctx.get("prompt_assembly_override") or "").strip() or None,
    }
