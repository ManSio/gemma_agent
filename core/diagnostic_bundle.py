"""
Полный диагностический архив для расследования задержек и деградации.
Собирается в один JSON (+ ZIP с пояснением для Telegram).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile

from core.admin_zip_copy import copy_admin_zip_to_data_tools
from core.boot_timeline import boot_timeline_snapshot
from core.code_cartography import build_bundle_slice
from core.connectivity_check import run_connectivity_checks
from core.diagnostics import build_diagnostic_snapshot
from core.error_analysis import read_recent_events
from core.performance_probe import collect_performance_snapshot

logger = logging.getLogger(__name__)

_DIAG_ENV_KEYS = (
    "OP_TIMEOUT_SEC",
    "OP_RETRIES",
    "LATENCY_TRACE_LOG",
    "LATENCY_TRACE_SLOW_MS",
    "TELEGRAM_HTTP_TIMEOUT",
    "TELEGRAM_PROXY",
    "HTTPS_PROXY",
    "TELEGRAM_GET_ME_TIMEOUT_SEC",
    "TELEGRAM_STARTUP_DM_TIMEOUT_SEC",
    "OPENROUTER_MODEL_FREE",
    "OPENROUTER_MODEL_DEV",
    "OPENROUTER_MODEL_QWEN",
    "OPENROUTER_PROMPT_CACHE_MODE",
    "OPENROUTER_ANTHROPIC_CACHE_TTL",
    "MODEL_SWITCH_THRESHOLD",
    "OPERATOR_RULES_PATH",
    "EPHEMERAL_LESSONS_PATH",
    "EPHEMERAL_PENDING_PATH",
    "EPHEMERAL_PENDING_AUTO_PROMOTE_USERS",
    "EPHEMERAL_AUTOLEARN",
    "EPHEMERAL_AUTOLEARN_STRIKES_TRUSTED",
    "EPHEMERAL_AUTOLEARN_STRIKES_UNTRUSTED",
    "EPHEMERAL_AUTOLEARN_TRUST_USER_IDS",
    "EPHEMERAL_AUTOLEARN_MAX_PROMOTIONS_PER_DAY",
    "DIALOGUE_MEMORY_MAX",
    "DIALOGUE_SUMMARY_MAX_CHARS",
    "DIALOGUE_COMPACT_SNIPPET_CHARS",
    "DIALOGUE_COMPACT_LLM",
    "DIALOGUE_COMPACT_LLM_MODEL",
    "DIALOGUE_COMPACT_MAX_TOKENS",
    "DIALOGUE_COMPACT_TIMEOUT_SEC",
    "BRAIN_LOG_PROMPT_METRICS",
    "MODEL_PROFILES_PATH",
    "MODEL_PROFILE_LOG",
    "BRAIN_HOT_PATH_SLIM",
    "BRAIN_HOT_PATH_SLIM_MAX_USER_CHARS",
    "BRAIN_HOT_PATH_SLIM_IN_GROUPS",
    "BRAIN_LLM_FREE_TIMEOUT_SHORT_SEC",
    "BRAIN_LLM_TIERED_RETRY",
    "BRAIN_LLM_FREE_ATTEMPTS",
    "BRAIN_LLM_FREE_TIMEOUT_SEC",
    "BRAIN_LLM_PREMIUM_TIMEOUT_SEC",
    "BRAIN_LLM_WAIT_BEFORE_PREMIUM_SEC",
    "BRAIN_LLM_PREMIUM_MODEL",
    "BRAIN_LLM_FREE_MODEL",
    "BRAIN_FAST_CHITCHAT_FORCE_FREE_MODEL",
    "BRAIN_FAST_CHITCHAT_MODEL",
    "BRAIN_FAST_CHITCHAT_MAX_TOKENS",
    "BRAIN_PRIVATE_DM_CHITCHAT_CONTINUITY_GUARD",
    "BRAIN_LLM_USE_DEV_KEY_FOR_PREMIUM",
    "BRAIN_LLM_RACE_PREMIUM",
    "MODULES_PATH",
    "SELF_MAINTENANCE_ENABLED",
    "SELF_MAINTENANCE_INTERVAL_SEC",
    "RESILIENCE_RUNTIME_DIR",
    "BOOT_DIAGNOSTIC_FILE_DELAY_SEC",
    "BOOT_DIAGNOSTIC_INCLUDE_CONNECTIVITY",
    "ADMIN_STARTUP_NOTIFY",
    "DIAG_IO_PROBE_BYTES",
    "RESOURCE_METRICS_TTL_SEC",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "LOG_USE_COLOR",
    "GEMMA_AUTOPILOT_MODE",
    "AUTOPILOT_CYCLE_INTERVAL_SEC",
    "AUTOPILOT_INNER_TICK_SEC",
    "AUTOPILOT_DIGEST_ENABLED",
    "AUTOPILOT_DIGEST_HOURS_UTC",
    "AUTOPILOT_DIGEST_QUIET_ONLY",
    "AUTOPILOT_IDLE_LLM_PROBE",
    "AUTOPILOT_IDLE_MIN_SEC",
    "AUTOPILOT_QUIET_HOURS_UTC",
    "AUTOPILOT_LLM_PROBE_MIN_INTERVAL_SEC",
    "AUTOPILOT_LLM_PROBE_NOTIFY_ON_FAIL",
    "USAGE_LEARNING_SAVE_EVERY",
    "USER_ACCESS_APPROVAL_REQUIRED",
    "USER_ACCESS_GUEST_REPLY_QUOTA",
    "BRAIN_RESPONSE_CACHE_ENABLED",
    "BRAIN_RESPONSE_CACHE_MODULES",
    "ACCESS_GATE_STATE_PATH",
    "GEMMA_VERBOSE_CORE",
    "GEMMA_LLM_AUDIT_LOG",
    "GEMMA_CORE_LOG_FULL",
    "GEMMA_LOG_FILE",
    "GEMMA_LOG_FILE_OFF",
    "GEMMA_LOG_FILE_MAX_MB",
    "DIAG_LOG_TAIL_MAX_BYTES",
    "CODE_CARTO_ROOT",
    "CODE_CARTO_DIRS",
    "CODE_CARTO_FULL_HASH",
    "CODE_CARTO_PERSIST_ON_DIAGNOSTIC_ZIP",
)


def _env_for_diagnostics() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in _DIAG_ENV_KEYS:
        v = os.getenv(k)
        if v is None:
            out[k] = None
        elif any(x in k for x in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            out[k] = bool(v.strip())
        else:
            out[k] = v
    out["SECRETS_CONFIGURED"] = "redacted"  # true/false per key omitted to avoid leaking even key presence
    return out


def _tools_status() -> Dict[str, Any]:
    from core import tools as tools_mod

    scan_done = getattr(tools_mod, "_tools_scan_done", False)
    reg = getattr(tools_mod, "TOOLS", {})
    return {
        "scan_done": bool(scan_done),
        "registered_tools": len(reg) if isinstance(reg, dict) else 0,
    }


def _logging_diagnostics() -> Dict[str, Any]:
    """Снимок конфигурации root-логгера (дубли строк в docker logs → часто handler_count > 1)."""
    root = logging.getLogger()
    handlers_out: list[Dict[str, Any]] = []
    for i, h in enumerate(root.handlers):
        fmt = getattr(h, "formatter", None)
        fmt_cls = type(fmt).__name__ if fmt is not None else None
        fmt_str = None
        if fmt is not None and hasattr(fmt, "_fmt"):
            fmt_str = getattr(fmt, "_fmt", None)
        handlers_out.append(
            {
                "index": i,
                "class": type(h).__name__,
                "level": logging.getLevelName(h.level),
                "formatter_class": fmt_cls,
                "formatter_fmt": fmt_str,
            }
        )
    return {
        "root_level": logging.getLevelName(root.level),
        "root_propagate": root.propagate,
        "root_handler_count": len(root.handlers),
        "root_handlers": handlers_out,
        "note": "Ожидается root_handler_count=1 после setup_logging(); несколько — возможные дубли одной строки в логах.",
    }


def _process_log_file_tail_for_bundle() -> Dict[str, Any]:
    """
    Хвост лог-файла процесса (python3 напрямую): тот же путь, что у FileHandler в setup_logging.
    Docker-контейнеры часто пишут только в stdout — тогда exists=false или пустой tail до первой ротации.
    """
    from core.log_paths import resolved_process_log_file_path

    path = resolved_process_log_file_path()
    if not path:
        return {
            "enabled": False,
            "reason": "GEMMA_LOG_FILE_OFF=1 — файл лога процесса не используется",
        }
    p = Path(path)
    try:
        resolved = str(p.resolve())
    except OSError:
        resolved = path
    if not p.is_file():
        return {
            "enabled": True,
            "path": resolved,
            "exists": False,
            "note": "Файл ещё не создан: первый запуск, нет прав на каталог, или лог только в консоли.",
        }
    try:
        max_bytes = 120_000
        raw_lim = (os.getenv("DIAG_LOG_TAIL_MAX_BYTES") or "").strip()
        if raw_lim:
            try:
                max_bytes = max(4096, min(2_000_000, int(raw_lim)))
            except ValueError:
                pass
        size = p.stat().st_size
        with p.open("rb") as f:
            if size <= max_bytes:
                raw_b = f.read()
            else:
                f.seek(-max_bytes, os.SEEK_END)
                raw_b = f.read()
        text = raw_b.decode("utf-8", errors="replace")
        if size > max_bytes:
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1 :]
        return {
            "enabled": True,
            "path": resolved,
            "exists": True,
            "size_bytes": size,
            "tail_max_bytes": max_bytes,
            "tail_truncated": size > max_bytes,
            "tail_line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "tail": text,
            "note": "Сырые строки лога процесса (не docker logs). Увеличить хвост: DIAG_LOG_TAIL_MAX_BYTES.",
        }
    except OSError as e:
        return {"enabled": True, "path": resolved, "exists": True, "error": str(e)}


def voice_probe_snapshot() -> Dict[str, Any]:
    """Снимок STT/TTS для bundle.json: видно, импортируется ли vosk без копания в runtime_errors."""
    from core.voice_module import VoiceModule

    vm = VoiceModule()
    st = vm.stt_status()
    vosk_ok = False
    vosk_err = ""
    try:
        import vosk  # noqa: F401

        vosk_ok = True
    except Exception as e:
        vosk_err = f"{type(e).__name__}: {e}"
    backend = str(st.get("stt_backend") or "")
    hint: str | None = None
    if backend == "vosk" and not vosk_ok:
        hint = (
            "В образе нет пакета vosk (или сломан импорт). Добавьте vosk в requirements.txt, "
            "пересоберите Docker-образ; либо переключите VOICE_STT_BACKEND=openrouter."
        )
    return {
        "stt_status": st,
        "vosk_import_ok": vosk_ok,
        "vosk_import_error": vosk_err or None,
        "hint": hint,
    }


def _openrouter_public() -> Dict[str, Any]:
    try:
        from core.openrouter_provider import get_openrouter_provider

        p = get_openrouter_provider()
        if hasattr(p, "get_current_model_info"):
            return dict(p.get_current_model_info())
    except Exception as e:
        return {"error": str(e)}
    return {}


README_RU = """gemma_bot — как читать диагностический архив
============================================

Файл bundle.json — машинный снимок; не публикуйте его целиком в открытый чат
(там могут быть пути, счётчики, фрагменты конфигурации).

1) boot_timeline
   Поле \"marks\" — фазы запуска с delta_ms от старта процесса.
   Большой разрыв между соседними метками = узкое место при старте.

2) pipeline сообщения (упрощённо)
   Telegram → InputLayer._process_message → Orchestrator.plan (синхронно:
   может вызваться self_maintenance / resilience) → Orchestrator.execute_plan
   → модуль (часто chat-orchestrator) → core.brain → OpenRouterProvider.generate.

3) Типичные причины «тупит»
   - Первый тяжёлый plan(): обслуживание, журнал ошибок, safe mode, первый
     скан инструментов (см. tools.scan_done и registered_tools).
   - OP_TIMEOUT_SEC: обрыв HTTP к OpenRouter при медленном free-маршруте;
     в логах brain — TimeoutError; в кабинете OpenRouter генерация могла всё же завершиться.
   - Пустой content у части free-моделей: см. core/openrouter_completion_text.py и логи OpenRouter.

4) Что включить для детальной трассировки
   LATENCY_TRACE_LOG=all — разбор latency trace по сообщениям (input_layer).
   GEMMA_VERBOSE_CORE=true — DEBUG для всего процесса, httpx/aiohttp приглушены.
   GEMMA_LLM_AUDIT_LOG=true — INFO-строка на каждый вызов OpenRouter из ядра (токены, cost, ms).
   LOG_FORMAT=json|console|plain — формат строки в stdout (json удобен для docker logs / jq).
   /admin_stats — счётчики openrouter_* и p95 openrouter_completion_ms в diagnostic_snapshot.observability.

4a) logging (в bundle.json)
   Секция logging: root_handler_count и root_handlers — сколько обработчиков на корневом логгере.

4b) process_log_file — хвост файла лога процесса (запуск python3 на хосте)
   Путь как у FileHandler: по умолчанию data/logs/gemma_bot.log или GEMMA_LOG_FILE.
   Поле tail — последние строки (размер ограничен DIAG_LOG_TAIL_MAX_BYTES, по умолч. ~120 КБ).
   Если exists=false — бот ещё не писал в файл или лог только в консоли (смотрите вывод терминала).
   В Docker без тома на этот файл хвост может быть пустым — тогда смотрите docker logs.
   Если handler_count > 1, одно событие может печататься несколько раз (дубли INFO в логах).

5) performance (в bundle.json)
   host_resources — CPU/RAM/заполнение диска (см. diagnostic_snapshot тоже).
   cpu_percent_sample_250ms — замер psutil с интервалом 0,25 с (точнее мгновенного тика).
   storage_io_probe — запись+fsync+чтение файла (размер DIAG_IO_PROBE_BYTES, по умолчанию 2 МБ).
   Оценка «HDD медленный»: write_fsync_ms / read_ms и поле hints.

6) Команды в боте
   /admin_xray_json — аномалии, узкие места и расширенный «рентген».
   /admin_pulse_json — пульс процесса, p95, воркер, хвост решений планировщика (в памяти).
   /admin_connectivity — Telegram, OpenRouter, Mem0 (может занять ~20 с). /admin_health — последние сбои API без повторных запросов.
   /admin_resilience_json — safe mode и пороги.
   /admin_system_json — сводный отчёт.
   /admin_code_map — карта .py, дифф к прошлому снимку и к эталону; JSON: /admin_code_map_json
   Поле bundle.json code_cartography — недавно менявшиеся пути, хвост истории, дрифт baseline.

7) voice (в bundle.json)
   Секция voice: vosk_import_ok и stt_status — сразу видно, установлен ли pip-пакет vosk при VOICE_STT_BACKEND=vosk.
   runtime_errors_recent может содержать вчерашние строки; ориентируйтесь на ts последних записей и на voice.vosk_import_ok.

Собрано автоматически diagnostic_bundle.py.
"""


async def build_diagnostic_bundle(
    orchestrator: Any,
    admin_module: Any,
    *,
    include_connectivity: bool = False,
) -> Dict[str, Any]:
    """Полный снимок для расследования (без секретов в явном виде)."""
    connectivity: Optional[Dict[str, Any]] = None
    if include_connectivity:
        try:
            probes = (os.getenv("CONNECTIVITY_INCLUDE_HTTP_PROBES") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            connectivity = await run_connectivity_checks(include_http_probes=probes)
        except Exception as e:
            connectivity = {"error": str(e)}

    admin_report: Dict[str, Any] = {}
    if admin_module is not None and hasattr(admin_module, "full_system_report"):
        try:
            admin_report = admin_module.full_system_report()
        except Exception as e:
            admin_report = {"error": str(e)}

    plugins: Dict[str, Any] = {}
    try:
        reg = orchestrator.plugin_registry
        plugins = {
            "loaded": sorted(list(reg.loaded_modules.keys())),
            "count": len(reg.loaded_modules),
        }
    except Exception as e:
        plugins = {"error": str(e)}

    perf: Dict[str, Any] = {}
    try:
        perf = collect_performance_snapshot()
    except Exception as e:
        perf = {"error": str(e)}

    carto: Dict[str, Any] = {}
    try:
        persist = (os.getenv("CODE_CARTO_PERSIST_ON_DIAGNOSTIC_ZIP") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        carto = build_bundle_slice(persist=persist)
    except Exception as e:
        carto = {"error": str(e)}

    voice: Dict[str, Any] = {}
    try:
        voice = voice_probe_snapshot()
    except Exception as e:
        voice = {"error": str(e)}

    mem0_op: Dict[str, Any] = {}
    try:
        from core.mem0_memory.mem0_module import mem0_operator_diagnostics

        mem0_op = mem0_operator_diagnostics()
    except Exception as e:
        mem0_op = {"error": str(e)}

    return {
        "bundle_version": 2,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "boot_timeline": boot_timeline_snapshot(),
        "env": _env_for_diagnostics(),
        "logging": _logging_diagnostics(),
        "process_log_file": _process_log_file_tail_for_bundle(),
        "tools": _tools_status(),
        "openrouter": _openrouter_public(),
        "plugins": plugins,
        "performance": perf,
        "diagnostic_snapshot": build_diagnostic_snapshot(orchestrator),
        "admin_full_system_report": admin_report,
        "runtime_errors_recent": read_recent_events(limit=250),
        "connectivity": connectivity,
        "code_cartography": carto,
        "voice": voice,
        "mem0_operator": mem0_op,
    }


def diagnostic_bundle_zip_bytes(bundle: Dict[str, Any]) -> bytes:
    raw = json.dumps(bundle, ensure_ascii=False, indent=2)
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", raw)
        zf.writestr("КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt", README_RU)
    return bio.getvalue()


BUG_REPORT_README_RU = """\
Архив багрепорта (/admin_bug)
=============================

Файлы:
- bundle_summary.json — компактная сводка состояния (по умолчанию, быстро читать).
- bundle.json — полный диагностический снимок (только в режиме /admin_bug full).
- incident_context.json — короткий контекст инцидента: последние 5 сообщений + таймлайн.
- bug_report.json — контекст Telegram: реплай, родительское сообщение, заметка.
- logs_snapshot.json — снимок журнала ошибок (метаданные + те же строки, что у /admin_logs).
- logs_snapshot.txt — хвост лога текстом.
- КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt — расшифровка bundle.json.

Использование: ответьте реплаем на проблемное сообщение и отправьте:
  /admin_bug
  /admin_bug net
  /admin_bug 60
  /admin_bug comp=voice
  /admin_bug full
  /admin_bug net 50 comp=brain краткая заметка

Копия архива сохраняется на сервере в data/diagnostics/bug_reports/
(переменная ADMIN_BUG_LOG_LINES задаёт число строк лога по умолчанию, 1–100).
"""


def _compact_bundle_for_bug(bundle: Dict[str, Any]) -> Dict[str, Any]:
    bt = bundle.get("boot_timeline") if isinstance(bundle.get("boot_timeline"), dict) else {}
    marks = bt.get("marks") if isinstance(bt, dict) else []
    marks_tail = marks[-10:] if isinstance(marks, list) else []
    perf = bundle.get("performance") if isinstance(bundle.get("performance"), dict) else {}
    host = perf.get("host_resources") if isinstance(perf.get("host_resources"), dict) else {}
    openrouter = bundle.get("openrouter") if isinstance(bundle.get("openrouter"), dict) else {}
    tools = bundle.get("tools") if isinstance(bundle.get("tools"), dict) else {}
    conn = bundle.get("connectivity")
    runtime_errors = bundle.get("runtime_errors_recent")
    runtime_tail = runtime_errors[-12:] if isinstance(runtime_errors, list) else []
    return {
        "bundle_version": bundle.get("bundle_version"),
        "generated_utc": bundle.get("generated_utc"),
        "boot_timeline": {
            "started_at_utc": bt.get("started_at_utc") if isinstance(bt, dict) else None,
            "mark_count": len(marks) if isinstance(marks, list) else 0,
            "marks_tail": marks_tail,
        },
        "tools": {
            "scan_done": tools.get("scan_done"),
            "registered_tools": tools.get("registered_tools"),
        },
        "openrouter": {
            "model": openrouter.get("model"),
            "provider": openrouter.get("provider"),
            "api_key_mode": openrouter.get("api_key_mode"),
        },
        "host_resources": {
            "cpu_percent": host.get("cpu_percent"),
            "memory_percent": host.get("memory_percent"),
            "disk_percent": host.get("disk_percent"),
            "pressure": host.get("pressure"),
            "hints": host.get("hints"),
        },
        "connectivity": conn,
        "runtime_errors_recent_tail": runtime_tail,
    }


def admin_bug_report_zip_bytes(
    bundle: Dict[str, Any],
    *,
    bug_report: Dict[str, Any],
    logs_snapshot: Dict[str, Any],
    include_full_bundle: bool = False,
) -> bytes:
    """ZIP: compact summary + bug_report + logs; full bundle — по флагу."""
    raw_bundle = json.dumps(bundle, ensure_ascii=False, indent=2)
    raw_bundle_summary = json.dumps(_compact_bundle_for_bug(bundle), ensure_ascii=False, indent=2)
    raw_bug = json.dumps(bug_report, ensure_ascii=False, indent=2)
    incident_context = {
        "created_utc": bug_report.get("created_utc"),
        "capture_source": bug_report.get("capture_source"),
        "reply_missing": bug_report.get("reply_missing"),
        "human_note": bug_report.get("human_note"),
        "recent_chat_tail": bug_report.get("recent_chat_tail") or [],
        "event_timeline": bug_report.get("event_timeline") or [],
    }
    raw_incident_context = json.dumps(incident_context, ensure_ascii=False, indent=2)
    raw_logs = json.dumps(logs_snapshot, ensure_ascii=False, default=str, indent=2)
    body = str(logs_snapshot.get("body") or "")
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle_summary.json", raw_bundle_summary)
        if include_full_bundle:
            zf.writestr("bundle.json", raw_bundle)
        zf.writestr("incident_context.json", raw_incident_context)
        zf.writestr("bug_report.json", raw_bug)
        zf.writestr("logs_snapshot.json", raw_logs)
        zf.writestr("logs_snapshot.txt", body)
        zf.writestr("КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt", README_RU)
        zf.writestr("КАК_ЧИТАТЬ_БАГРЕПОРТ.txt", BUG_REPORT_README_RU)
    return bio.getvalue()


def _notify_recipient_ids() -> list[str]:
    raw = os.getenv("ADMIN_NOTIFY_USER_IDS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    raw2 = os.getenv("ADMIN_USER_IDS", "").strip()
    return [x.strip() for x in raw2.split(",") if x.strip()]


async def schedule_boot_diagnostic_if_configured(
    bot: Bot,
    orchestrator: Any,
    admin_module: Any,
) -> None:
    """Если BOOT_DIAGNOSTIC_FILE_DELAY_SEC > 0 — через N секунд отправить ZIP админам."""
    try:
        delay = float(os.getenv("BOOT_DIAGNOSTIC_FILE_DELAY_SEC", "0") or "0")
    except ValueError:
        delay = 0.0
    if delay <= 0:
        return
    include_net = (os.getenv("BOOT_DIAGNOSTIC_INCLUDE_CONNECTIVITY", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ids = _notify_recipient_ids()
    if not ids:
        logger.info("BOOT_DIAGNOSTIC: нет ADMIN_NOTIFY_USER_IDS / ADMIN_USER_IDS — файл не отправляем.")
        return
    await asyncio.sleep(delay)
    try:
        bundle = await build_diagnostic_bundle(
            orchestrator,
            admin_module,
            include_connectivity=include_net,
        )
    except Exception as e:
        logger.exception("BOOT_DIAGNOSTIC: сбор bundle failed: %s", e)
        return
    zbytes = diagnostic_bundle_zip_bytes(bundle)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"gemma_diagnostic_{ts}.zip"
    copy_admin_zip_to_data_tools(zbytes, fname)
    cap = (
        "🔬 Автодиагностика (boot)\n"
        f"connectivity внутри: {'да' if include_net else 'нет'}\n"
        "Распакуйте ZIP: bundle.json + КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt"
    )
    per_send = float(os.getenv("BOOT_DIAGNOSTIC_SEND_TIMEOUT_SEC", "120"))
    for uid in ids:
        doc = BufferedInputFile(zbytes, filename=fname)
        try:
            await asyncio.wait_for(
                bot.send_document(chat_id=int(uid), document=doc, caption=cap),
                timeout=per_send,
            )
        except asyncio.TimeoutError:
            logger.warning("BOOT_DIAGNOSTIC: таймаут отправки user_id=%s", uid)
        except (TelegramForbiddenError, TelegramBadRequest, ValueError) as e:
            logger.warning("BOOT_DIAGNOSTIC: не доставлено user_id=%s: %s", uid, e)
        except Exception as e:
            logger.warning("BOOT_DIAGNOSTIC: ошибка user_id=%s: %s", uid, e)
