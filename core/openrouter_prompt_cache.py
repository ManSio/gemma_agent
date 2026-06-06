"""
Доп. поля тела запроса OpenRouter: prompt caching и provider routing.

Документация:
- https://openrouter.ai/docs/guides/best-practices/prompt-caching
- https://openrouter.ai/docs/guides/routing/provider-selection
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


def _mode() -> str:
    return (os.getenv("OPENROUTER_PROMPT_CACHE_MODE") or "off").strip().lower()


def _csv_list(name: str) -> List[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cache_first_providers_enabled() -> bool:
    """
    Приоритет провайдеров с рабочим prompt cache (DeepSeek/Baidu), без DeepInfra.
    Явно: OPENROUTER_CACHE_FIRST_PROVIDERS=true|false.
    Если не задано — включается при OPENROUTER_PROMPT_CACHE_MODE=auto (prod по умолчанию).
    """
    raw = os.getenv("OPENROUTER_CACHE_FIRST_PROVIDERS")
    if raw is not None:
        return _env_bool("OPENROUTER_CACHE_FIRST_PROVIDERS", False)
    return _mode() == "auto"


def _default_cache_first_order() -> List[str]:
    raw = (os.getenv("OPENROUTER_CACHE_FIRST_DEFAULT_ORDER") or "DeepSeek,baidu").strip()
    return [p.strip() for p in raw.split(",") if p.strip()]


def _default_cache_first_ignore() -> List[str]:
    raw = (os.getenv("OPENROUTER_CACHE_FIRST_DEFAULT_IGNORE") or "deepinfra").strip()
    return [p.strip() for p in raw.split(",") if p.strip()]


def _quantizations_for_model(model_name: str, quants: List[str]) -> List[str]:
    """
    fp8 pinning only for DeepSeek brain models.
    Free/small models (liquid/lfm, :free) → OpenRouter 404 «No endpoints with quantization: fp8».
    """
    if not quants:
        return []
    mid = (model_name or "").strip().lower()
    if "deepseek/" not in mid:
        return []
    return quants


def provider_routing_block(model_name: str) -> Dict[str, Any]:
    """
    Блок provider для OpenRouter (order / quantizations / ignore / only).
    Env:
      OPENROUTER_PROVIDER_ORDER=baidu,deepseek
      OPENROUTER_PROVIDER_QUANTIZATIONS=fp8
      OPENROUTER_PROVIDER_IGNORE=deepinfra
      OPENROUTER_PROVIDER_ONLY=
      OPENROUTER_PROVIDER_ALLOW_FALLBACKS=true
      OPENROUTER_CACHE_FIRST_PROVIDERS=true  (или PROMPT_CACHE_MODE=auto)
    """
    order = _csv_list("OPENROUTER_PROVIDER_ORDER")
    ignore = _csv_list("OPENROUTER_PROVIDER_IGNORE")
    only = _csv_list("OPENROUTER_PROVIDER_ONLY")
    quants = _csv_list("OPENROUTER_PROVIDER_QUANTIZATIONS")
    mid = (model_name or "").strip().lower()

    if _cache_first_providers_enabled() and "deepseek/" in mid:
        if not order:
            order = _default_cache_first_order()
        if not ignore:
            ignore = _default_cache_first_ignore()

    prov: Dict[str, Any] = {}
    if order:
        prov["order"] = order
    if ignore:
        prov["ignore"] = ignore
    if only:
        prov["only"] = only
    q_apply = _quantizations_for_model(model_name, quants)
    if q_apply:
        prov["quantizations"] = q_apply

    if os.getenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS") is not None:
        prov["allow_fallbacks"] = _env_bool("OPENROUTER_PROVIDER_ALLOW_FALLBACKS", True)
    elif "deepseek/" in mid:
        prov["allow_fallbacks"] = True

    return prov


def extra_completion_body_fields(model_name: str) -> Dict[str, Any]:
    """
    Поля верхнего уровня для POST /v1/chat/completions (дополняют payload).
    """
    mode = _mode()
    mid = (model_name or "").strip().lower()
    out: Dict[str, Any] = {}

    prov = provider_routing_block(model_name)
    if prov:
        out["provider"] = prov

    if mode in {"", "off", "false", "0", "none"}:
        return out

    if mode == "anthropic_auto":
        if "anthropic/" not in mid and "/claude" not in mid:
            return out
        ttl = (os.getenv("OPENROUTER_ANTHROPIC_CACHE_TTL") or "").strip().lower()
        cc: Dict[str, Any] = {"type": "ephemeral"}
        if ttl == "1h":
            cc["ttl"] = "1h"
        out["cache_control"] = cc

    return out
