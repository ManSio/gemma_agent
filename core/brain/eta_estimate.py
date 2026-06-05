"""Грубая оценка ETA для LLM (прогресс-UI Telegram)."""
from __future__ import annotations

import os


def _f_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        v = default
    return max(lo, min(v, hi))


def _expected_gen_tokens(
    *,
    max_tokens: int,
    user_text_len: int,
    stage: str,
    task_tier: str,
) -> float:
    """
    Ожидаемые gen-токены (не лимит max_tokens).

    Аудит VPS 05/2026: brain p50 latency ~3.4 с при типичном completion << max_tokens;
    полный max_tokens завышает ETC в 3–5 раз на коротких репликах.
    """
    try:
        from core.llm_eta_learn import lookup_learned_completion

        learned = lookup_learned_completion(stage=stage, task_tier=task_tier, max_tokens=max_tokens)
        if learned is not None:
            return learned
    except Exception:
        pass

    frac = _f_env("BRAIN_LLM_ETA_MAX_TOK_FRAC", 0.28, 0.08, 0.85)
    base = float(max(1, max_tokens)) * frac
    short_chars = int(_f_env("BRAIN_LLM_ETA_SHORT_USER_CHARS", 80, 16, 400))
    short_tok = _f_env("BRAIN_LLM_ETA_SHORT_GEN_TOKENS", 220, 64, 1200)
    if user_text_len <= short_chars:
        base = min(base, short_tok)
    elif user_text_len > short_chars:
        chars_per_tok = _f_env("BRAIN_LLM_ETA_USER_CHARS_PER_TOK", 5.5, 2.0, 24.0)
        from_user_floor = min(6000.0, float(user_text_len) / chars_per_tok)
        base = max(base, from_user_floor * 0.45)
    floor_tok = _f_env("BRAIN_LLM_ETA_MIN_GEN_TOKENS", 140, 32, 800)
    cap = _f_env("BRAIN_LLM_ETA_GEN_TOKENS_CAP", 8000, 512, 16000)
    return min(cap, max(floor_tok, base))


def estimate_llm_eta_sec(
    *,
    max_tokens: int,
    task_tier: str = "",
    prompt_len: int = 0,
    stage: str = "first",
    user_text_len: int = 0,
) -> float:
    """
    Оценка секунд до готовности ответа (без сборки промпта и без ретраев).

    user_text_len: длиннее реплика пользователя → выше пол gen-токенов (эвристика),
    т.к. BRAIN_FIRST_MAX_TOKENS часто фиксирован и не отражает ожидаемую длину ответа.
    """
    tier = (task_tier or "").strip().lower()
    try:
        from core.llm_eta_learn import lookup_learned_tps

        learned_tps = lookup_learned_tps(stage=stage, task_tier=tier, max_tokens=max_tokens)
    except Exception:
        learned_tps = None
    tps = learned_tps if learned_tps is not None else _f_env("BRAIN_LLM_TOKENS_PER_SEC_EST", 52, 5.0, 500.0)
    overhead = _f_env("BRAIN_LLM_ETA_OVERHEAD_SEC", 1.8, 0.0, 20.0)

    mult = 1.0
    if tier == "deep":
        mult = max(mult, 1.35)
    elif tier == "nested":
        mult = max(mult, 1.15)
    if stage in {"second", "tool_chain"}:
        mult = max(mult, 1.15)

    gen_tokens = _expected_gen_tokens(
        max_tokens=max_tokens,
        user_text_len=user_text_len,
        stage=stage,
        task_tier=tier,
    )

    prompt_chars_per_tok = _f_env("BRAIN_LLM_ETA_PROMPT_CHARS_PER_TOK", 4.2, 2.5, 8.0)
    prompt_tps = _f_env("BRAIN_LLM_ETA_PROMPT_TPS", 1400, 200, 8000)
    prompt_tokens_est = max(0.0, float(prompt_len) / prompt_chars_per_tok)
    prompt_sec = prompt_tokens_est / prompt_tps

    gen_sec = (gen_tokens / tps) * mult
    heuristic = max(1.0, overhead + prompt_sec + gen_sec)
    try:
        from core.llm_eta_learn import blended_eta_sec

        return blended_eta_sec(
            stage=stage,
            task_tier=tier,
            max_tokens=int(max_tokens),
            heuristic_sec=heuristic,
        )
    except Exception:
        return heuristic
