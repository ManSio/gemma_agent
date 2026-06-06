"""
Autopilot cycle: periodic xray report, recommendations, optional threshold actions,
usage digest (scheduled UTC hours), idle OpenRouter probe in quiet windows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from core.env_flags import gemma_core_log_full
from core.live_pulse import build_xray_snapshot, xray_anomalies_for_display
from core.telegram_util import sanitize_html

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _notify_recipient_ids() -> List[str]:
    raw = os.getenv("ADMIN_NOTIFY_USER_IDS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    raw2 = os.getenv("ADMIN_USER_IDS", "").strip()
    return [x.strip() for x in raw2.split(",") if x.strip()]


def _recommendations(xray: Dict[str, Any]) -> List[str]:
    rec: List[str] = []
    anomalies = xray_anomalies_for_display(xray, include_warn=False)
    codes = {str(a.get("code")) for a in anomalies if isinstance(a, dict)}
    if "telegram_p95_very_high" in codes or "telegram_p95_high" in codes:
        rec.append("Поднять OP_TIMEOUT_SEC/CONNECTIVITY_CHECK_TIMEOUT_SEC и проверить прокси/канал.")
    if "openrouter_p95_very_high" in codes or "openrouter_fail_ratio_high" in codes:
        rec.append("Снизить нагрузку на free-маршрут: рассмотреть MODEL_SWITCH_THRESHOLD ниже и альтернативную free-модель.")
    if "worker_queue_near_limit" in codes:
        rec.append("Увеличить HEAVY_WORKER_CONCURRENCY или снизить тяжёлые задачи под нагрузкой.")
    if "host_pressure_critical" in codes or "host_pressure_warn" in codes:
        rec.append("Проверить ресурсы хоста: CPU/RAM/диск и лимиты контейнера.")
    if "safe_mode_active" in codes:
        rec.append("Проверить /admin_resilience_json и причины входа в safe mode.")
    if "slow_boot_path" in codes:
        rec.append("Сверить boot_timeline: участок plugins_ready → after_post_boot_hooks.")
    if not rec:
        rec.append("Критичных аномалий не обнаружено; продолжайте наблюдение через /admin_xray.")
    return rec


def _autopilot_enabled() -> bool:
    return _truthy("GEMMA_AUTOPILOT_MODE", False)


def cycle_enabled() -> bool:
    if _truthy("AUTOPILOT_CYCLE_ENABLED", False):
        return True
    return _autopilot_enabled()


def digest_enabled() -> bool:
    if os.getenv("AUTOPILOT_DIGEST_ENABLED") is None:
        return cycle_enabled()
    return _truthy("AUTOPILOT_DIGEST_ENABLED", False)


def _runtime_dir() -> Path:
    return Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))


def _daily_journal_checkpoint_path() -> Path:
    return _runtime_dir() / "autopilot_daily_journal_checkpoint.json"


def _read_daily_journal_checkpoint() -> Dict[str, Any]:
    p = _daily_journal_checkpoint_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_daily_journal_checkpoint(payload: Dict[str, Any]) -> None:
    p = _daily_journal_checkpoint_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _human_duration(sec: float) -> str:
    s = max(0, int(sec))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d > 0:
        return f"{d}d {h:02d}h {m:02d}m"
    return f"{h:02d}h {m:02d}m"


def _autopilot_dm_reports_enabled() -> bool:
    """Отчёты цикла в ЛС админам — только при явном AUTOPILOT_REPORT_TO_ADMINS=true."""
    if os.getenv("AUTOPILOT_REPORT_TO_ADMINS") is None:
        return False
    return _truthy("AUTOPILOT_REPORT_TO_ADMINS", False)


def _autopilot_report_cooldown_active() -> bool:
    try:
        cd = int(os.getenv("AUTOPILOT_REPORT_COOLDOWN_SEC", "14400"))
    except ValueError:
        cd = 14400
    if cd <= 0:
        return False
    p = _runtime_dir() / "autopilot_last_admin_report.sent"
    try:
        if not p.exists():
            return False
        last = float(p.read_text(encoding="utf-8").strip())
        return (time.time() - last) < float(cd)
    except Exception:
        return False


def _autopilot_report_mark_sent() -> None:
    p = _runtime_dir() / "autopilot_last_admin_report.sent"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def _read_dedup_checkpoint() -> Dict[str, Any]:
    p = _runtime_dir() / "autopilot_dedup_checkpoint.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_dedup_checkpoint(payload: Dict[str, Any]) -> None:
    p = _runtime_dir() / "autopilot_dedup_checkpoint.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _llm_triage_cooldown_active() -> bool:
    try:
        cd = int(os.getenv("AUTOPILOT_LLM_TRIAGE_COOLDOWN_SEC", "3600"))
    except ValueError:
        cd = 3600
    if cd <= 0:
        return False
    p = _runtime_dir() / "autopilot_last_llm_triage.sent"
    try:
        if not p.exists():
            return False
        last = float(p.read_text(encoding="utf-8").strip())
        return (time.time() - last) < float(cd)
    except Exception:
        return False


def _llm_triage_mark_sent() -> None:
    p = _runtime_dir() / "autopilot_last_llm_triage.sent"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


async def _maybe_llm_triage_anomalies(
    orchestrator: Any,
    bot: Any,
    xray: Dict[str, Any],
    recommendations: List[str],
    actions: List[str],
) -> None:
    """
    Опционально: короткий разбор аномалий через call_brain (без TOOL_CALL), в ЛС админам.
    Вкл: AUTOPILOT_LLM_TRIAGE_ENABLED=true
    """
    if not _truthy("AUTOPILOT_LLM_TRIAGE_ENABLED", False):
        return
    if bot is None:
        return
    anomalies = xray.get("anomalies") if isinstance(xray.get("anomalies"), list) else []
    try:
        min_n = int((os.getenv("AUTOPILOT_LLM_TRIAGE_MIN_ANOMALIES") or "1").strip() or "1")
    except ValueError:
        min_n = 1
    if len(anomalies) < max(0, min_n):
        return
    if _llm_triage_cooldown_active():
        logger.debug("autopilot llm triage skipped: cooldown")
        return
    if _truthy("AUTOPILOT_LLM_TRIAGE_REQUIRE_DM_REPORT", False) and not _autopilot_dm_reports_enabled():
        return

    try:
        max_chars = max(2000, int((os.getenv("AUTOPILOT_LLM_TRIAGE_MAX_PAYLOAD_CHARS") or "12000").strip()))
    except ValueError:
        max_chars = 12000

    payload: Dict[str, Any] = {
        "anomalies": anomalies[:15],
        "recommendations": recommendations[:10],
        "actions": actions[:8],
    }
    pulse = xray.get("pulse")
    if isinstance(pulse, dict):
        payload["pulse_excerpt"] = pulse
    try:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        raw = str(payload)
    if len(raw) > max_chars:
        raw = raw[: max_chars - 20] + "…(обрезано)"

    user_text = (
        "Внутренний триаж автопилота (сообщение для администратора). "
        "Ниже JSON с аномалиями и шаблонными рекомендациями. "
        "Дай кратко: что это значит, приоритет, 2–5 конкретных шагов (переменные env, команды /admin_*), без выдуманных фактов.\n\n"
        + raw
    )

    system_prompt = (
        "Ты помощник по эксплуатации сервера gemma_bot. "
        "Ответь кратко по-русски (примерно до 1500 символов). "
        "Запрещено: TOOL_CALL, XML, теги <tool>. Только связный текст. "
        "Если данных мало — укажи, что открыть: /admin_xray_json, /admin_diagnostic."
    )

    ctx: Dict[str, Any] = {
        "user_id": "autopilot-llm-triage",
        "brain_disable_tools": True,
        "brain_skip_memory_fetch": True,
        "memory_managed": True,
        "telegram_is_admin": True,
    }

    try:
        from core.brain import call_brain

        reply = await call_brain(user_text, ctx, system_prompt)
    except Exception as e:
        logger.warning("autopilot llm triage call_brain failed: %s", e)
        return

    reply = (reply or "").strip()
    if not reply:
        return
    try:
        cap = max(500, int((os.getenv("AUTOPILOT_LLM_TRIAGE_MAX_REPLY_CHARS") or "3500").strip()))
    except ValueError:
        cap = 3500
    if len(reply) > cap:
        reply = reply[: cap - 1] + "…"

    header = (
        "🧠 Триаж автопилота (LLM)\n"
        "Режим: AUTOPILOT_LLM_TRIAGE_ENABLED\n"
        "───\n"
    )
    msg = header + reply
    for uid in _notify_recipient_ids():
        try:
            await bot.send_message(chat_id=int(uid), text=msg)
        except Exception as e:
            logger.debug("autopilot llm triage notify %s: %s", uid, e)
    _llm_triage_mark_sent()
    try:
        from core.monitoring import MONITOR

        MONITOR.inc("autopilot_llm_triage_sent_total")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'autopilot_cycle', e, exc_info=True)
    logger.info(
        "autopilot llm triage sent anomalies=%s",
        len(anomalies),
        extra={"gemma_event": "autopilot_llm_triage", "anomaly_count": len(anomalies)},
    )


def _llm_probe_state_path() -> Path:
    return _runtime_dir() / "autopilot_llm_probe_state.json"


def _read_probe_state() -> Dict[str, Any]:
    p = _llm_probe_state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_probe_state(data: Dict[str, Any]) -> None:
    p = _llm_probe_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.warning("autopilot llm probe state write: %s", e)


def _report_text(xray: Dict[str, Any], recommendations: List[str], actions: List[str]) -> str:
    from core.report_timezone import format_operator_datetime

    anomalies = xray_anomalies_for_display(xray, include_warn=False)
    now = datetime.now(timezone.utc)
    wall = format_operator_datetime(now)
    lines = [
        "🛰️ <b>Цикл автопилота</b>",
        f"Время: <code>{wall}</code>",
        f"Аномалий: <b>{len(anomalies)}</b>",
    ]
    for a in anomalies[:6]:
        if isinstance(a, dict):
            detail = a.get("detail") or a.get("label")
            if detail:
                lines.append(f"• <code>{a.get('code')}</code>: {detail}")
    lines.append("")
    lines.append("<b>Рекомендации</b>")
    for r in recommendations[:6]:
        lines.append(f"• {r}")
    if actions:
        lines.append("")
        lines.append("<b>Авто-действия</b>")
        for a in actions:
            lines.append(f"• <code>{a}</code>")
    lines.append("")
    lines.append("<i>Подробно: /admin_xray · /admin_xray_json</i>")
    return "\n".join(lines)


_HOST_PRESSURE_ANOMALY_CODES = frozenset(
    {"host_pressure", "host_pressure_critical", "host_pressure_warn"}
)


def _host_pressure_level_from_xray(xray: Dict[str, Any]) -> str:
    pulse = xray.get("pulse") if isinstance(xray.get("pulse"), dict) else {}
    hr = pulse.get("host_resources") if isinstance(pulse.get("host_resources"), dict) else {}
    pr = hr.get("pressure") if isinstance(hr.get("pressure"), dict) else {}
    return str(pr.get("level") or "ok")


def _anomalies_to_emit(xray: Dict[str, Any], *, skip_safe_mode_echo: bool) -> List[Dict[str, Any]]:
    """Не эмитим в EventBus эхо и ложные host_pressure — иначе safe mode снимается и сразу включается снова."""
    raw = xray.get("anomalies") if isinstance(xray.get("anomalies"), list) else []
    host_level = _host_pressure_level_from_xray(xray)
    out: List[Dict[str, Any]] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        if a.get("type") == "event_bus":
            continue
        code = str(a.get("code") or a.get("label") or "xray_anomaly").strip()
        if skip_safe_mode_echo and code == "safe_mode_active":
            continue
        if host_level == "ok" and code in _HOST_PRESSURE_ANOMALY_CODES:
            continue
        out.append(a)
    return out


def _emit_cycle_anomalies(xray: Dict[str, Any], *, skip_safe_mode_echo: bool) -> int:
    try:
        from core.event_bus import bus as _eb

        n = 0
        for a in _anomalies_to_emit(xray, skip_safe_mode_echo=skip_safe_mode_echo):
            code = a.get("code", a.get("label", "xray_anomaly"))
            _eb.emit(
                "anomaly.detected",
                {
                    "code": code,
                    "severity": a.get("severity", "warn"),
                    "details": a,
                    "source": "autopilot_cycle",
                },
            )
            n += 1
        return n
    except Exception:
        return 0


async def _apply_actions(orchestrator: Any, xray: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    if not _truthy("AUTOPILOT_ACTIONS_ENABLED", False):
        return actions
    try:
        rc = getattr(orchestrator, "_resilience", None)
        if rc is None or not rc.is_enabled() or not rc.is_safe_mode():
            return actions
        ev = rc.evaluate(orchestrator)
        if isinstance(ev, dict) and not ev.get("error") and not ev.get("degraded") and not ev.get("critical"):
            rc.exit_safe_mode("autopilot_cycle: stable metrics")
            actions.append("safe_mode_cleared")
    except Exception as e:
        logger.warning("autopilot actions failed: %s", e)
    return actions


async def run_cycle_once(orchestrator: Any, bot: Any = None) -> Dict[str, Any]:
    xray = build_xray_snapshot(orchestrator)
    rec = _recommendations(xray)

    try:
        from core.event_bus import bus as _eb

        _eb.emit(
            "maintenance.tick",
            {
                "interval_sec": int(os.getenv("AUTOPILOT_CYCLE_INTERVAL_SEC", "300")),
                "cycle_id": id(xray),
            },
        )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'autopilot_cycle', e, exc_info=True)
    emitted = _emit_cycle_anomalies(xray, skip_safe_mode_echo=False)
    actions = await _apply_actions(orchestrator, xray)
    if "safe_mode_cleared" in actions:
        emitted += _emit_cycle_anomalies(xray, skip_safe_mode_echo=True)

    out = {
        "xray": xray,
        "recommendations": rec,
        "actions": actions,
        "anomalies_emitted": emitted,
    }
    logger.info(
        "autopilot cycle anomalies=%s actions=%s",
        len(xray.get("anomalies") or []),
        actions,
        extra={"gemma_event": "autopilot_cycle", "anomaly_count": len(xray.get("anomalies") or []), "actions": actions},
    )

    notify = _autopilot_dm_reports_enabled()
    if bot is not None and notify:
        if _autopilot_report_cooldown_active():
            logger.debug("autopilot report skipped: cooldown (AUTOPILOT_REPORT_COOLDOWN_SEC)")
        else:
            txt = _report_text(xray, rec, actions)
            txt = sanitize_html(txt)
            sent_any = False
            for uid in _notify_recipient_ids():
                try:
                    await bot.send_message(chat_id=int(uid), text=txt, parse_mode="HTML")
                    sent_any = True
                except Exception as e:
                    logger.debug("autopilot notify %s: %s", uid, e)
            if sent_any:
                _autopilot_report_mark_sent()
    try:
        await _maybe_llm_triage_anomalies(orchestrator, bot, xray, rec, actions)
    except Exception as e:
        logger.warning("autopilot llm triage: %s", e)
    # Task profiles: dedup and cleanup (раз в сутки)
    try:
        _dedup_ck = _read_dedup_checkpoint()
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _dedup_ck.get("last_dedup_date") != _today:
            from core.brain.classifier import dedup_and_cleanup
            removed = dedup_and_cleanup()
            if removed:
                logger.info("[classifier] dedup_and_cleanup: %d points removed", removed)
            _write_dedup_checkpoint({"last_dedup_date": _today})
    except Exception as e:
        logger.debug("task_profiles dedup: %s", e)
    # ── Time-series snapshot: persist metrics every cycle ──
    try:
        from core.monitoring import MONITOR
        from pathlib import Path
        _ts_path = str(Path("data/runtime/metrics_timeseries.jsonl"))
        MONITOR.persist_snapshot(_ts_path)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'autopilot_cycle', e, exc_info=True)
    return out


async def maybe_run_usage_digest(bot: Any, orchestrator: Any = None) -> None:
    if bot is None or not digest_enabled():
        return
    from core.telegram_ui import format_usage_digest_html
    from core.usage_learning import (
        build_digest_payload,
        commit_digest_checkpoint,
        parse_int_list,
        seconds_since_activity,
        should_emit_digest_this_hour,
    )

    hours = parse_int_list(os.getenv("AUTOPILOT_DIGEST_HOURS_UTC", "8,20"), default=[8, 20])
    now = datetime.now(timezone.utc)
    emit, slot = should_emit_digest_this_hour(now=now, digest_hours=hours)
    if not emit:
        return
    if _truthy("AUTOPILOT_DIGEST_QUIET_ONLY", False):
        try:
            idle_min = float(os.getenv("AUTOPILOT_IDLE_MIN_SEC", "600"))
        except ValueError:
            idle_min = 600.0
        if seconds_since_activity() < idle_min:
            logger.debug("autopilot digest skipped: not quiet enough")
            return
    payload = build_digest_payload(slot_label=slot, orchestrator=orchestrator)
    txt = sanitize_html(format_usage_digest_html(payload))
    sent_any = False
    for uid in _notify_recipient_ids():
        try:
            await bot.send_message(chat_id=int(uid), text=txt, parse_mode="HTML")
            sent_any = True
        except Exception as e:
            logger.debug("autopilot digest notify %s: %s", uid, e)
    if sent_any:
        commit_digest_checkpoint(slot)
        logger.info("autopilot digest sent slot=%s", slot, extra={"gemma_event": "autopilot_digest", "slot": slot})


def _daily_journal_enabled() -> bool:
    if os.getenv("AUTOPILOT_DAILY_JOURNAL_ENABLED") is None:
        return digest_enabled()
    return _truthy("AUTOPILOT_DAILY_JOURNAL_ENABLED", False)


def _should_emit_daily_journal(now: datetime) -> tuple[bool, str]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    try:
        hour = int((os.getenv("AUTOPILOT_DAILY_JOURNAL_HOUR_UTC") or "0").strip())
    except ValueError:
        hour = 0
    hour = max(0, min(23, hour))
    slot = now.strftime("%Y-%m-%d")
    if now.hour != hour:
        return False, slot
    cp = _read_daily_journal_checkpoint()
    if str(cp.get("last_daily_journal_slot") or "") == slot:
        return False, slot
    return True, slot


def _daily_journal_text(slot: str, orchestrator: Any) -> str:
    from core.boot_timeline import process_uptime_seconds
    from core.live_pulse import build_xray_snapshot
    from core.usage_learning import snapshot as usage_snapshot, insights as usage_insights
    from core.report_timezone import format_operator_datetime
    from core.daily_highlights import save_daily_highlights

    now = datetime.now(timezone.utc)
    wall = format_operator_datetime(now)
    xray = build_xray_snapshot(orchestrator)
    usage = usage_snapshot()
    insights = usage_insights()
    anomalies = xray_anomalies_for_display(xray)
    errors = xray.get("errors") if isinstance(xray.get("errors"), dict) else {}
    pulse = xray.get("pulse") if isinstance(xray.get("pulse"), dict) else {}
    mon = pulse.get("monitoring") if isinstance(pulse.get("monitoring"), dict) else {}
    top_int = (usage.get("top_intents") or [])[:3]
    top_mod = (usage.get("top_modules") or [])[:3]

    lines: List[str] = [
        "📓 <b>Ежедневный дневник бота</b>",
        f"Дата (UTC): <code>{slot}</code>",
        f"Сформировано: <code>{wall}</code>",
        "",
        "<b>Uptime процесса</b>",
        f"• Текущий uptime: <code>{_human_duration(process_uptime_seconds())}</code>",
        "",
        "<b>Что происходило</b>",
        f"• Событий (usage): <b>{int(usage.get('total_events') or 0)}</b>",
        f"• Выполнений плана: <b>{int(mon.get('execute_plan_calls') or 0)}</b>",
        f"• Fallback планировщика: <b>{int(mon.get('planner_fallback_total') or 0)}</b>",
        f"• Подозрений обрыва ответа: <b>{int(mon.get('telegram_reply_suspect_incomplete_total') or 0)}</b>",
    ]
    if isinstance(top_int, list) and top_int:
        lines.append(
            "• Топ intent: "
            + ", ".join(f"{str(x.get('intent') or '?')}({int(x.get('count') or 0)})" for x in top_int if isinstance(x, dict))
        )
    if isinstance(top_mod, list) and top_mod:
        lines.append(
            "• Топ модули: "
            + ", ".join(f"{str(x.get('module') or '?')}({int(x.get('count') or 0)})" for x in top_mod if isinstance(x, dict))
        )
    lines.extend(
        [
            "",
            "<b>Что понял (авто-выводы)</b>",
        ]
    )
    if insights:
        for row in insights[:4]:
            lines.append(f"• {row}")
    else:
        lines.append("• Существенных трендов за день не выявлено.")
    lines.extend(
        [
            "",
            "<b>Риски и аномалии</b>",
            f"• Аномалий в xray: <b>{len(anomalies)}</b>",
            f"• Ошибок в журнале (агрегат): <b>{int(errors.get('total') or 0)}</b>",
        ]
    )
    if anomalies:
        for a in anomalies[:5]:
            if isinstance(a, dict):
                lines.append(f"• <code>{a.get('code')}</code>: {a.get('detail')}")
    try:
        dh = save_daily_highlights(slot=slot, xray=xray, usage=usage, insights=insights)
        notes = dh.get("notes") if isinstance(dh, dict) else []
    except Exception:
        notes = []
    if isinstance(notes, list) and notes:
        lines.extend(["", "<b>Яркие моменты (сохранено в память дня)</b>"])
        for note in notes[:4]:
            if isinstance(note, str) and note.strip():
                lines.append(f"• {note.strip()}")
    lines.extend(
        [
            "",
            "<i>Детали: /admin_xray_json · /admin_health_json · /admin_usage_digest_json</i>",
        ]
    )
    return "\n".join(lines)


async def maybe_run_daily_journal(bot: Any, orchestrator: Any = None) -> None:
    if bot is None or orchestrator is None or not _daily_journal_enabled():
        return
    now = datetime.now(timezone.utc)
    emit, slot = _should_emit_daily_journal(now)
    if not emit:
        return
    msg = sanitize_html(_daily_journal_text(slot, orchestrator))
    sent_any = False
    for uid in _notify_recipient_ids():
        try:
            await bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
            sent_any = True
        except Exception as e:
            logger.debug("autopilot daily journal notify %s: %s", uid, e)
    if sent_any:
        _write_daily_journal_checkpoint(
            {
                "last_daily_journal_slot": slot,
                "sent_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info(
            "autopilot daily journal sent slot=%s",
            slot,
            extra={"gemma_event": "autopilot_daily_journal", "slot": slot},
        )


async def maybe_run_idle_llm_probe(bot: Any = None) -> None:
    if not _truthy("AUTOPILOT_IDLE_LLM_PROBE", False):
        return
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return
    from core.usage_learning import parse_int_list, seconds_since_activity
    from core.connectivity_check import check_openrouter_api

    quiet = parse_int_list(os.getenv("AUTOPILOT_QUIET_HOURS_UTC", ""), default=[])
    if quiet:
        now = datetime.now(timezone.utc)
        if now.hour not in quiet:
            return
    try:
        idle_min = float(os.getenv("AUTOPILOT_IDLE_MIN_SEC", "600"))
    except ValueError:
        idle_min = 600.0
    if seconds_since_activity() < idle_min:
        return
    try:
        min_gap = float(os.getenv("AUTOPILOT_LLM_PROBE_MIN_INTERVAL_SEC", "7200"))
    except ValueError:
        min_gap = 7200.0
    st = _read_probe_state()
    last = float(st.get("last_probe_unix") or 0)
    if last and (time.time() - last) < min_gap:
        return
    probe_model = (os.getenv("AUTOPILOT_IDLE_LLM_PROBE_MODEL") or os.getenv("OPENROUTER_CONNECTIVITY_MODEL") or "").strip() or None
    if probe_model is None:
        ff = (os.getenv("OPENROUTER_CONNECTIVITY_FORCE_FREE") or "true").strip().lower()
        if ff in {"1", "true", "yes", "on"}:
            probe_model = "openrouter/free"
    res = await check_openrouter_api(key, model=probe_model)
    _write_probe_state({"last_probe_unix": time.time(), "last_ok": bool(res.get("ok")), "last_model": res.get("model")})
    if res.get("ok"):
        logger.info(
            "autopilot idle llm probe ok model=%s",
            res.get("model"),
            extra={"gemma_event": "autopilot_llm_probe", "ok": True},
        )
        return
    logger.warning(
        "autopilot idle llm probe FAIL: %s",
        res.get("error_code"),
        extra={"gemma_event": "autopilot_llm_probe", "ok": False, "error_code": res.get("error_code")},
    )
    if not _truthy("AUTOPILOT_LLM_PROBE_NOTIFY_ON_FAIL", True) or bot is None:
        return
    msg = sanitize_html(
        "⚠️ <b>Idle LLM probe</b>\n"
        f"OpenRouter: <code>{res.get('error_code')}</code>\n"
        f"<i>{res.get('user_message', '')[:400]}</i>\n"
        "<code>/admin_connectivity</code>"
    )
    for uid in _notify_recipient_ids():
        try:
            await bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
        except Exception as e:
            logger.debug("autopilot probe notify %s: %s", uid, e)


async def start_autopilot_cycle(orchestrator: Any, bot: Any = None) -> None:
    if not cycle_enabled():
        logger.info("autopilot cycle disabled")
        return
    try:
        from core.usage_learning import ensure_loaded

        ensure_loaded()
    except Exception as e:
        logger.warning("usage_learning ensure_loaded: %s", e)

    # Task profiles: ensure collection exists + cold start
    try:
        from core.brain.classifier import cold_start, ensure_collection
        await ensure_collection()
        added = await cold_start()
        if added:
            logger.info("[classifier] cold_start: %d samples loaded", added)
    except Exception as e:
        logger.debug("task_profiles setup: %s", e)

    interval = max(120, int(float(os.getenv("AUTOPILOT_CYCLE_INTERVAL_SEC", "900"))))
    tick = max(15, min(180, int(float(os.getenv("AUTOPILOT_INNER_TICK_SEC", "60")))))
    proactive_enabled = _truthy("PROACTIVE_ASSISTANCE_ENABLED", False)
    logger.info("autopilot cycle started interval=%ss tick=%ss digest=%s probe=%s proactive=%s", interval, tick, digest_enabled(), _truthy("AUTOPILOT_IDLE_LLM_PROBE", False), proactive_enabled)
    if bot is not None:
        try:
            from core.reminder_dispatch import register_reminder_bot

            register_reminder_bot(bot)
        except Exception as e:
            logger.debug("reminder bot register: %s", e)
    next_full = time.monotonic()
    while True:
        loop_start = time.monotonic()
        try:
            if loop_start >= next_full:
                await run_cycle_once(orchestrator, bot=bot)
                next_full = loop_start + interval
            # EventBus: maintenance.tick на каждый внутренний цикл (MCE, healers)
            try:
                from core.event_bus import bus as _eb
                _eb.emit("maintenance.tick", {"cycle_type": "inner"})
            except Exception as e:
                logger.debug('%s optional failed: %s', 'autopilot_cycle', e, exc_info=True)
            await maybe_run_usage_digest(bot, orchestrator=orchestrator)
            await maybe_run_daily_journal(bot, orchestrator=orchestrator)
            await maybe_run_idle_llm_probe(bot)
            # Self-Learning: maintenance tasks (forgetting curve + consolidation)
            try:
                from core.self_learning import LessonManager, consolidate_and_retire

                _self_learning_mgr = LessonManager.get_instance()
                _self_learning_mgr.apply_forgetting_curve()
            except Exception as e:
                logger.debug("self_learning maintenance: %s", e)
            try:
                await consolidate_and_retire()
            except Exception as e:
                logger.debug("self_learning consolidation: %s", e)
            try:
                from core.learning_maintenance import maybe_run_learning_maintenance

                lm_rep = maybe_run_learning_maintenance()
                if not lm_rep.get("skipped"):
                    logger.info("autopilot learning_maintenance: %s", lm_rep.get("steps", {}).keys())
            except Exception as e:
                logger.debug("learning_maintenance: %s", e)
            # Напоминания (light_reminders.json + Schedule.add_event)
            if bot is not None:
                try:
                    from core.reminder_dispatch import tick_due_reminders

                    n_rem = await tick_due_reminders(bot)
                    if n_rem:
                        logger.info("autopilot reminders delivered=%s", n_rem)
                except Exception as e:
                    logger.debug("reminder tick: %s", e)
            # Autonomy 3.0: proactive assistance
            if proactive_enabled:
                await maybe_proactive_assistance(orchestrator, bot)
        except Exception as e:
            logger.warning("autopilot tick failed: %s", e)
        elapsed = time.monotonic() - loop_start
        sleep_for = max(1.0, tick - elapsed)
        await asyncio.sleep(sleep_for)


# ── Proactive Assistance 1.0 (Autonomy 3.0) ──

_LAST_USER_ACTIVITY: Dict[str, float] = {}
PROACTIVE_IDLE_SEC = 10


def record_user_activity(user_id: str) -> None:
    """Called from input_layer when user sends a message."""
    _LAST_USER_ACTIVITY[str(user_id)] = time.time()


async def maybe_proactive_assistance(orchestrator: Any, bot: Any) -> None:
    """Periodically check if agent should proactively offer help."""
    if bot is None:
        return
    now = time.time()
    candidates: List[str] = []
    for uid, last_ts in list(_LAST_USER_ACTIVITY.items()):
        if now - last_ts >= PROACTIVE_IDLE_SEC:
            candidates.append(uid)
    if not candidates:
        return

    # Find users with active objects or recent tool use
    for uid in candidates[:3]:
        try:
            if not orchestrator or not orchestrator.behavior_store:
                continue
            rec = orchestrator.behavior_store.load(uid, None)
            st = rec.get("session_task") if isinstance(rec, dict) else None
            if not isinstance(st, dict):
                continue
            last_tool = str(st.get("last_tool") or "").strip()
            has_object = bool(st.get("last_tool") or st.get("goal_runner"))

            if has_object and last_tool:
                hint = _proactive_hint(last_tool)
                if hint:
                    try:
                        await bot.send_message(
                            chat_id=int(uid),
                            text=sanitize_html(hint),
                            parse_mode="HTML",
                        )
                        logger.info("proactive_assistance: sent hint to %s for %s", uid[:8], last_tool)
                        del _LAST_USER_ACTIVITY[uid]
                    except Exception as e:
                        logger.debug("proactive send to %s: %s", uid[:8], e)
        except Exception as e:
            logger.debug("proactive check %s: %s", uid[:8], e)


def _proactive_hint(tool_name: str) -> Optional[str]:
    hints = {
        "document_reader": "📄 Хочешь, я проверю этот документ ещё раз или найду что-то в нём?",
        "vision_ocr": "🖼️ Хочешь, я распознаю текст с изображения или опишу, что на нём?",
        "download": "📥 Хочешь, я скачаю файл или проверю содержимое?",
        "corpus_search": "🔍 Хочешь, я поищу в корпусе документов?",
        "url_check": "🔗 Хочешь, я проверю ссылку или сайт?",
        "digital_twin": "👤 Хочешь, я обновлю цифровой портрет?",
    }
    return hints.get(tool_name.split(".")[0])
