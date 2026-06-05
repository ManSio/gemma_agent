"""
Обновляемое статус-сообщение в Telegram на время долгого ответа (как в v1: пользователь видит этапы).

Активируется из input_layer: telegram_progress_arm() перед execute_plan, disarm + delete в finally.
call_brain зовёт telegram_progress_pulse() — правки throttled, чтобы не ловить flood.
Пока этап не меняется, фоновая задача периодически обновляет только строку времени (ETC / лимит).
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_Tuple3 = Optional[Tuple[Any, int, int]]
_armed: contextvars.ContextVar[_Tuple3] = contextvars.ContextVar("telegram_progress_armed", default=None)
_last_pulse: contextvars.ContextVar[float] = contextvars.ContextVar("telegram_progress_last_pulse", default=0.0)
_started_at: contextvars.ContextVar[float] = contextvars.ContextVar("telegram_progress_started_at", default=0.0)
_eta_sec: contextvars.ContextVar[float] = contextvars.ContextVar("telegram_progress_eta_sec", default=0.0)
_timeout_sec: contextvars.ContextVar[float] = contextvars.ContextVar("telegram_progress_timeout_sec", default=0.0)
_last_stage_text: contextvars.ContextVar[str] = contextvars.ContextVar("telegram_progress_last_stage_text", default="")
# Текст этапа без суффикса времени — для throttle и фонового тика
_stage_base: contextvars.ContextVar[str] = contextvars.ContextVar("telegram_progress_stage_base", default="")
_tick_task: contextvars.ContextVar[Optional[asyncio.Task[None]]] = contextvars.ContextVar(
    "telegram_progress_tick_task", default=None
)


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _telegram_progress_cancel_tick_task() -> None:
    t = _tick_task.get()
    if t is not None and not t.done():
        t.cancel()
    _tick_task.set(None)


def _etc_refresh_gap_sec() -> float:
    """Интервал фонового обновления строки времени при неизменном тексте этапа."""
    raw = (os.getenv("TELEGRAM_PROGRESS_ETC_REFRESH_SEC") or "").strip()
    if raw:
        try:
            v = float(raw)
            return max(2.0, min(v, 60.0))
        except ValueError:
            pass
    try:
        v = float((os.getenv("TELEGRAM_PROGRESS_SAME_STAGE_INTERVAL_SEC") or "30").strip())
    except ValueError:
        v = 30.0
    return max(2.0, min(v, 60.0))


def telegram_progress_arm(bot: Any, chat_id: int, message_id: int) -> None:
    _telegram_progress_cancel_tick_task()
    _armed.set((bot, int(chat_id), int(message_id)))
    _last_pulse.set(0.0)
    _started_at.set(time.monotonic())
    _eta_sec.set(0.0)
    _timeout_sec.set(0.0)
    _last_stage_text.set("")
    _stage_base.set("")


def telegram_progress_disarm() -> None:
    _armed.set(None)
    _telegram_progress_cancel_tick_task()
    _started_at.set(0.0)
    _eta_sec.set(0.0)
    _timeout_sec.set(0.0)
    _last_stage_text.set("")
    _stage_base.set("")


def telegram_progress_set_timing(
    *,
    eta_sec: Optional[float] = None,
    timeout_sec: Optional[float] = None,
    from_now: bool = False,
    record_assembly: bool = False,
) -> None:
    """
    eta_sec: прогноз «до конца» от arm (from_now=False) или «ещё столько» от текущего момента (from_now=True).
    record_assembly: при первом LLM-этапе — записать elapsed как EMA сборки промпта.
    """
    if eta_sec is not None:
        try:
            v = max(0.0, float(eta_sec))
            if from_now:
                started = float(_started_at.get() or 0.0)
                if started > 0:
                    elapsed = max(0.0, time.monotonic() - started)
                    if record_assembly and elapsed > 0:
                        try:
                            from core.llm_eta_learn import learn_assembly_sec

                            learn_assembly_sec(elapsed)
                        except Exception as e:
                            logger.debug("telegram_progress assembly learn: %s", e)
                    v = elapsed + v
            _eta_sec.set(v)
        except (TypeError, ValueError):
            pass
    if timeout_sec is not None:
        try:
            _timeout_sec.set(max(0.0, float(timeout_sec)))
        except (TypeError, ValueError):
            pass


def telegram_progress_seed_from_user_text(user_text: str) -> None:
    """
    Первая строка прогресса («⏳ Думаю…») создаётся до мозга: без этого ETC пустой до ✍️.
    Грубая оценка по тексту пользователя + буфер на сборку промпта; позже pipeline перезапишет точнее.
    """
    if not _env_progress_enabled():
        return
    if not _armed.get():
        return
    from core.brain.eta_estimate import estimate_llm_eta_sec
    from core.brain.text_helpers import brain_first_stage_max_tokens

    ut = user_text or ""
    max_tok = brain_first_stage_max_tokens(ut)
    try:
        ctx_boost = float((os.getenv("TELEGRAM_PROGRESS_SEED_CONTEXT_CHARS") or "3800").strip() or "3800")
    except ValueError:
        ctx_boost = 3800.0
    ctx_boost = max(0.0, min(ctx_boost, 20000.0))
    prompt_len_approx = int(min(48000, max(len(ut), 1) + ctx_boost))
    eta_llm = estimate_llm_eta_sec(
        max_tokens=max_tok,
        task_tier="",
        prompt_len=prompt_len_approx,
        stage="first",
        user_text_len=len(ut),
    )
    try:
        assembly = float((os.getenv("TELEGRAM_PROGRESS_ASSEMBLY_BUFFER_SEC") or "10").strip() or "10")
    except ValueError:
        assembly = 10.0
    assembly = max(0.0, min(assembly, 120.0))
    try:
        from core.llm_eta_learn import blended_assembly_sec

        learned_asm = blended_assembly_sec()
        if learned_asm is not None and learned_asm > 0:
            assembly = max(assembly * 0.65, min(learned_asm, assembly * 1.35))
    except Exception as e:
        logger.debug("telegram_progress seed assembly blend: %s", e)
    eta_total = max(1.0, eta_llm + assembly)
    try:
        to_mult = float((os.getenv("TELEGRAM_PROGRESS_SEED_TIMEOUT_MULT") or "2.25").strip() or "2.25")
    except ValueError:
        to_mult = 2.25
    to_mult = max(1.2, min(to_mult, 4.0))
    try:
        to_min = float((os.getenv("TELEGRAM_PROGRESS_SEED_TIMEOUT_MIN_SEC") or "55").strip() or "55")
    except ValueError:
        to_min = 55.0
    to_min = max(15.0, min(to_min, 600.0))
    timeout = max(to_min, eta_total * to_mult)
    telegram_progress_set_timing(eta_sec=eta_total, timeout_sec=timeout)


def _fmt_short_sec(v: float) -> str:
    n = max(0, int(round(v)))
    if n < 60:
        return f"{n}с"
    mm, ss = divmod(n, 60)
    return f"{mm}м {ss:02d}с"


def _render_timing_suffix() -> str:
    """
    Строка времени: Прошло, при eta>0 — «Прогноз ≈», при превышении прогноза — «дольше обычного».
    Таймаут HTTP в текст не выводим (меньше шума); значение по-прежнему в _timeout_sec для ядра.
    """
    started = float(_started_at.get() or 0.0)
    if started <= 0:
        return ""
    now = time.monotonic()
    elapsed = max(0.0, now - started)
    eta = float(_eta_sec.get() or 0.0)
    lbl_elapsed = (os.getenv("TELEGRAM_PROGRESS_LABEL_ELAPSED") or "Прошло").strip() or "Прошло"
    lbl_forecast = (os.getenv("TELEGRAM_PROGRESS_LABEL_FORECAST") or "Прогноз ≈").strip() or "Прогноз ≈"
    parts = [f"{lbl_elapsed} {_fmt_short_sec(elapsed)}"]
    if eta > 0:
        parts.append(f"{lbl_forecast} {_fmt_short_sec(eta)}")
    if eta > 0 and elapsed > eta:
        parts.append("дольше обычного")
    return "\n" + " · ".join(parts)


def _env_progress_enabled() -> bool:
    return (os.getenv("TELEGRAM_PROGRESS_UI", "true") or "").strip().lower() in {"1", "true", "yes", "on"}


async def _telegram_progress_etc_refresh_loop() -> None:
    try:
        while _armed.get():
            await asyncio.sleep(_etc_refresh_gap_sec())
            if not _armed.get():
                break
            base = str(_stage_base.get() or "").strip()
            if not base:
                continue
            try:
                await telegram_progress_pulse(base, force=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug('%s optional failed: %s', 'telegram_progress', e, exc_info=True)
    except asyncio.CancelledError:
        pass


async def telegram_progress_start_etc_refresh() -> None:
    """Запустить фоновое обновление счётчиков времени (после arm и первого pulse)."""
    if not _env_progress_enabled() or not _truthy_env("TELEGRAM_PROGRESS_ETC_REFRESH_ENABLED", True):
        return
    if not _armed.get():
        return
    _telegram_progress_cancel_tick_task()
    t = asyncio.create_task(_telegram_progress_etc_refresh_loop())
    _tick_task.set(t)


async def telegram_progress_pulse(text: str, *, force: bool = False) -> None:
    if not _env_progress_enabled():
        return
    try:
        from core.telegram_stream_reply import telegram_stream_editing_active, telegram_stream_has_delivery

        if telegram_stream_editing_active() or telegram_stream_has_delivery():
            return
    except Exception as e:
        logger.debug("telegram_progress_pulse stream guard: %s", e)
    tup = _armed.get()
    if not tup:
        return
    base = (text or "").strip()
    if not base:
        return
    body = (base + _render_timing_suffix()).strip()
    try:
        min_gap = float((os.getenv("TELEGRAM_PROGRESS_MIN_INTERVAL_SEC") or "1.2").strip())
    except ValueError:
        min_gap = 1.2
    min_gap = max(0.4, min(min_gap, 10.0))
    refresh_gap = _etc_refresh_gap_sec()
    now = time.monotonic()
    last = float(_last_pulse.get() or 0.0)
    prev_base = str(_stage_base.get() or "")
    stage_changed = base != prev_base
    required_gap = min_gap if stage_changed else refresh_gap
    if not force and last > 0 and (now - last) < required_gap:
        return
    bot, chat_id, mid = tup
    try:
        await bot.edit_message_text(body[:4090], chat_id=chat_id, message_id=mid)
    except Exception as e:
        logger.debug("telegram_progress_pulse: %s", e)
        return
    _last_pulse.set(time.monotonic())
    _last_stage_text.set(body)
    _stage_base.set(base)
