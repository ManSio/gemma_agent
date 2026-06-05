"""
Многоступенчатые вызовы OpenRouter: несколько попыток на «дешёвой» модели,
затем (с паузой) запасной маршрут с OPENROUTER_MODEL_DEV / отдельным ключом.
Опционально гонка: вторая бесплатная попытка и отложенный premium (FIRST_COMPLETED).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from core.error_analysis import record_error_event
from core.llm_eta_learn import learn_from_llm_result
from core.llm_refusal import looks_model_refusal
from core.resilience import with_timeout

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def telemetry_kind_from_tag(tag: str) -> str:
    """Нормализованный kind для llm_usage.jsonl (вместо «?» в админке)."""
    base = (tag or "").split(":")[0].strip().lower()
    if not base:
        return "chat"
    if base.startswith("brain_") or base in (
        "brain_first",
        "brain_second",
        "brain_direct_dialog",
        "brain_fast_chitchat",
        "brain_translation",
    ):
        return "brain"
    if base.startswith("news_") or base in ("news_direct_search",):
        return "news_tools"
    if base in ("urlfetch", "news_direct_rss"):
        return "tools"
    if "router" in base or base in ("self_check", "meta_intent"):
        return "router_llm"
    if "reflection" in base:
        return "reflection_heavy"
    if base.startswith("mce"):
        return "mce"
    if "triage" in base:
        return "llm_triage"
    if base.startswith("goal"):
        return "goal_runner"
    if base.startswith("task_scout"):
        return "task_scout"
    return base


def _inject_telemetry_kw(kw: Dict[str, Any], tag: str) -> Dict[str, Any]:
    out = dict(kw)
    if not str(out.get("telemetry_tag") or "").strip():
        out["telemetry_tag"] = (tag or "").split(":")[0] or "llm"
    if not str(out.get("telemetry_kind") or "").strip():
        out["telemetry_kind"] = telemetry_kind_from_tag(tag)
    return out


def _result_usable(res: Optional[Dict[str, Any]]) -> bool:
    if not res or res.get("error"):
        return False
    body = str(res.get("content") or "").strip()
    if not body:
        return False
    if looks_model_refusal(body):
        return False
    return True


def _adaptive_timeout_sec(
    base_timeout: float,
    *,
    task_tier: Optional[str],
    prompt: str,
    max_tokens: int,
    tag: str,
    premium: bool,
) -> float:
    """
    Динамически увеличивает timeout для сложных задач.
    Ничего не уменьшает: только нейтрально или вверх.
    """
    if not _truthy("BRAIN_LLM_COMPLEX_TIMEOUT_ENABLED", True):
        return float(base_timeout)
    tier = str(task_tier or "").strip().lower()
    mult = 1.0

    if tier == "deep":
        mult = max(mult, _f("BRAIN_LLM_TIMEOUT_FACTOR_DEEP", 1.55))
    elif tier == "nested":
        mult = max(mult, _f("BRAIN_LLM_TIMEOUT_FACTOR_NESTED", 1.30))

    tag_low = (tag or "").lower()
    if "second_stage" in tag_low or "tool_chain" in tag_low:
        mult = max(mult, _f("BRAIN_LLM_TIMEOUT_FACTOR_SECOND_STAGE", 1.20))

    try:
        chars_thr = max(1000, int(os.getenv("BRAIN_LLM_TIMEOUT_PROMPT_CHARS_THRESHOLD", "7000")))
    except ValueError:
        chars_thr = 7000
    if len(prompt or "") >= chars_thr:
        mult = max(mult, _f("BRAIN_LLM_TIMEOUT_FACTOR_LONG_PROMPT", 1.25))

    try:
        tok_thr = max(256, int(os.getenv("BRAIN_LLM_TIMEOUT_MAX_TOKENS_THRESHOLD", "1800")))
    except ValueError:
        tok_thr = 1800
    if int(max_tokens or 0) >= tok_thr:
        mult = max(mult, _f("BRAIN_LLM_TIMEOUT_FACTOR_HIGH_TOKENS", 1.20))

    out = float(base_timeout) * max(1.0, float(mult))
    if premium:
        floor = _f("BRAIN_LLM_PREMIUM_TIMEOUT_MIN_SEC", 12.0)
        cap = _f("BRAIN_LLM_PREMIUM_TIMEOUT_MAX_SEC", 600.0)
    else:
        floor = _f("BRAIN_LLM_FREE_TIMEOUT_MIN_SEC", 6.0)
        cap = _f("BRAIN_LLM_FREE_TIMEOUT_MAX_SEC", 360.0)
    return max(floor, min(out, cap))


def estimate_tiered_timeouts(
    *,
    tag: str,
    prompt: str,
    max_tokens: int,
    base_timeout: Optional[float] = None,
    task_tier: Optional[str] = None,
) -> Dict[str, float]:
    """
    Оценка таймаут-окна tiered-цепочки для UI/логов.
    """
    t_free = float(base_timeout) if base_timeout is not None else _f("BRAIN_LLM_FREE_TIMEOUT_SEC", 55.0)
    t_free = max(5.0, min(t_free, 240.0))
    t_prem = _f("BRAIN_LLM_PREMIUM_TIMEOUT_SEC", 180.0)
    t_prem = max(10.0, min(t_prem, 360.0))
    t_free = _adaptive_timeout_sec(
        t_free,
        task_tier=task_tier,
        prompt=prompt,
        max_tokens=max_tokens,
        tag=tag,
        premium=False,
    )
    t_prem = _adaptive_timeout_sec(
        t_prem,
        task_tier=task_tier,
        prompt=prompt,
        max_tokens=max_tokens,
        tag=tag,
        premium=True,
    )
    free_attempts = _i("BRAIN_LLM_FREE_ATTEMPTS", 1)
    retry_gap = _f("BRAIN_LLM_FREE_RETRY_GAP_SEC", 0.8)
    retry_gap = max(0.0, min(retry_gap, 120.0))
    wait_pre = _f("BRAIN_LLM_WAIT_BEFORE_PREMIUM_SEC", 6.0)
    wait_pre = max(0.0, min(wait_pre, 120.0))
    timeout_upper_bound = free_attempts * t_free + max(0.0, free_attempts - 1) * retry_gap + wait_pre + t_prem
    return {
        "free_timeout_sec": float(t_free),
        "premium_timeout_sec": float(t_prem),
        "timeout_upper_bound_sec": float(timeout_upper_bound),
    }


def _pick_free_model(llm: Any, fixed: Optional[str]) -> str:
    if fixed and str(fixed).strip():
        return str(fixed).strip()
    env = (os.getenv("BRAIN_LLM_FREE_MODEL") or "").strip()
    if env:
        return env
    return str(getattr(llm, "free_model", "") or "openrouter/free").strip()


def _pick_premium_model(llm: Any, fixed: Optional[str]) -> str:
    if fixed and str(fixed).strip():
        return str(fixed).strip()
    env = (os.getenv("BRAIN_LLM_PREMIUM_MODEL") or "").strip()
    if env:
        return env
    dev = (os.getenv("OPENROUTER_MODEL_DEV") or "").strip()
    if dev:
        return dev
    return str(getattr(llm, "qwen_model", "") or getattr(llm, "dev_model", "") or _pick_free_model(llm, None)).strip()


def pick_tiered_free_model(llm: Any, fixed: Optional[str] = None) -> str:
    """Публичная обёртка: какая модель пойдёт в первую ступень tiered-цепочки."""
    return _pick_free_model(llm, fixed)


def pick_tiered_premium_model(llm: Any, fixed: Optional[str] = None) -> str:
    """Публичная обёртка: premium-ступень tiered-цепочки."""
    return _pick_premium_model(llm, fixed)


def _premium_api_key(llm: Any) -> str:
    if not _truthy("BRAIN_LLM_USE_DEV_KEY_FOR_PREMIUM", True):
        return str(getattr(llm, "api_key", "") or "")
    dev = str(getattr(llm, "api_key_dev", "") or "").strip()
    if dev:
        return dev
    return str(getattr(llm, "api_key", "") or "")


def _main_api_key(llm: Any) -> str:
    return str(getattr(llm, "api_key", "") or "")


async def llm_generate_tiered(
    llm: Any,
    *,
    tag: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    vision_image_parts: Optional[List[Tuple[str, str]]] = None,
    model: Optional[str] = None,
    base_timeout: Optional[float] = None,
    task_tier: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """
    Возвращает тот же формат, что OpenRouterProvider.generate().
    """
    # ── Semantic LLM Cache (token_efficiency.cache) ──
    _cacheable = True
    _tag_low = (tag or "").lower()
    if "batch_parallel" in _tag_low:
        _cacheable = False
    if not vision_image_parts and _cacheable:
        try:
            from core.llm_cache import llm_cache_lookup

            _cached = llm_cache_lookup(
                model=model or str(getattr(llm, "free_model", "") or ""),
                system_prompt=system_prompt or "",
                user_input=prompt or "",
            )
            if _cached is not None:
                _cached["cached"] = True
                return _cached
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_tiered', e, exc_info=True)
    else:
        _cacheable = False

    async def _maybe_cache(result: Dict[str, Any]) -> Dict[str, Any]:
        if _result_usable(result) and _cacheable:
            try:
                from core.llm_cache import llm_cache_store
                llm_cache_store(
                    model=model or str(getattr(llm, "free_model", "") or ""),
                    system_prompt=system_prompt or "",
                    user_input=prompt or "",
                    result=result,
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'llm_tiered', e, exc_info=True)
        return result

    if not _truthy("BRAIN_LLM_TIERED_RETRY", True):
        to = float(base_timeout or _f("OP_TIMEOUT_SEC", 90.0))
        r = await with_timeout(
            llm.generate(
                prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                vision_image_parts=vision_image_parts,
                model=model,
                **_inject_telemetry_kw(extra, tag),
            ),
            to,
            tag=tag,
        )
        try:
            learn_from_llm_result(
                r if isinstance(r, dict) else {},
                tag=tag,
                task_tier=task_tier,
                max_tokens=int(max_tokens),
                prompt=prompt or "",
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_tiered', e, exc_info=True)
        return await _maybe_cache(r)

    fixed = (model or "").strip() or None
    free_m = _pick_free_model(llm, fixed)
    prem_m = _pick_premium_model(llm, fixed)
    main_k = _main_api_key(llm)
    dev_k = _premium_api_key(llm)

    free_attempts = _i("BRAIN_LLM_FREE_ATTEMPTS", 1)
    t_free = float(base_timeout) if base_timeout is not None else _f("BRAIN_LLM_FREE_TIMEOUT_SEC", 55.0)
    t_free = max(5.0, min(t_free, 240.0))
    t_prem = _f("BRAIN_LLM_PREMIUM_TIMEOUT_SEC", 180.0)
    t_prem = max(10.0, min(t_prem, 360.0))
    t_free = _adaptive_timeout_sec(
        t_free,
        task_tier=task_tier,
        prompt=prompt,
        max_tokens=max_tokens,
        tag=tag,
        premium=False,
    )
    t_prem = _adaptive_timeout_sec(
        t_prem,
        task_tier=task_tier,
        prompt=prompt,
        max_tokens=max_tokens,
        tag=tag,
        premium=True,
    )
    wait_pre = _f("BRAIN_LLM_WAIT_BEFORE_PREMIUM_SEC", 6.0)
    wait_pre = max(0.0, min(wait_pre, 120.0))
    retry_gap = _f("BRAIN_LLM_FREE_RETRY_GAP_SEC", 0.8)
    retry_gap = max(0.0, min(retry_gap, 120.0))

    same_route = free_m == prem_m and (not dev_k or dev_k == main_k)

    async def _call(mname: str, key: Optional[str], timeout: float, attempt_tag: str) -> Dict[str, Any]:
        kw = _inject_telemetry_kw(dict(extra), f"{tag}:{attempt_tag}")
        try:
            return await with_timeout(
                llm.generate(
                    prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    vision_image_parts=vision_image_parts,
                    model=mname,
                    api_key_override=key,
                    **kw,
                ),
                timeout,
                tag=f"{tag}:{attempt_tag}",
            )
        except Exception as e:
            err_msg = (str(e).strip() or type(e).__name__ or "error")
            logger.warning("[%s] %s: %s", tag, attempt_tag, err_msg)
            record_error_event("llm_tiered", attempt_tag, exc=e, extra={"tag": tag, "model": mname})
            return {"error": err_msg, "content": ""}

    def _learn(res: Dict[str, Any]) -> None:
        try:
            learn_from_llm_result(
                res,
                tag=tag,
                task_tier=task_tier,
                max_tokens=int(max_tokens),
                prompt=prompt or "",
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_tiered', e, exc_info=True)
    last: Dict[str, Any] = {"error": "tiered_exhausted", "content": ""}

    # --- Попытки free (+ основной ключ) ---
    for i in range(free_attempts):
        if i:
            await asyncio.sleep(retry_gap)
        r = await _call(free_m, main_k or None, t_free, f"free_{i + 1}")
        last = r
        if _result_usable(r):
            _learn(r)
            return await _maybe_cache(r)

    # Одинаковый маршрут и ключ — дальше смысла нет
    if same_route:
        return await _maybe_cache(last)

    async def _premium_call() -> Dict[str, Any]:
        if wait_pre > 0:
            await asyncio.sleep(wait_pre)
        pk = dev_k if dev_k and dev_k != main_k else None
        return await _call(prem_m, pk, t_prem, "premium")

    if _truthy("BRAIN_LLM_RACE_PREMIUM", False):
        async def _free_extra() -> Dict[str, Any]:
            return await _call(free_m, main_k or None, t_free, "free_parallel")

        async def _delayed_premium() -> Dict[str, Any]:
            d = _f("BRAIN_LLM_RACE_PREMIUM_DELAY_SEC", 15.0)
            d = max(0.0, min(d, 120.0))
            if d > 0:
                await asyncio.sleep(d)
            pk = dev_k if dev_k and dev_k != main_k else None
            return await _call(prem_m, pk, t_prem, "premium_parallel")

        t_free_task = asyncio.create_task(_free_extra())
        t_prem_task = asyncio.create_task(_delayed_premium())
        done, pending = await asyncio.wait({t_free_task, t_prem_task}, return_when=asyncio.FIRST_COMPLETED)
        first_task = next(iter(done))
        try:
            winner = first_task.result()
        except Exception as e:
            winner = {"error": str(e), "content": ""}
        if _result_usable(winner):
            _learn(winner)
            for p in pending:
                p.cancel()
            return await _maybe_cache(winner)
        # ждём вторую задачу
        for p in pending:
            try:
                alt = await p
                if _result_usable(alt):
                    _learn(alt)
                    return await _maybe_cache(alt)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                last = {"error": str(e), "content": ""}
        if _result_usable(winner):
            _learn(winner)
            return await _maybe_cache(winner)
        return await _maybe_cache(last)

    # --- Последовательный premium ---
    pr = await _premium_call()
    if _result_usable(pr):
        _learn(pr)
        return await _maybe_cache(pr)
    return await _maybe_cache(pr if pr.get("content") or pr.get("error") else last)
