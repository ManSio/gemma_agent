"""
Мгновенные ЛС админам по критичным ops-событиям (квота/баланс OpenRouter и т.п.).

Fire-and-forget из sync/async кода; bot регистрируется при старте InputLayer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.report_timezone import format_operator_datetime
from core.telegram_ui import esc
from core.telegram_util import sanitize_html

logger = logging.getLogger(__name__)

_BOT: Any = None

_sync_lock = threading.Lock()


def register_admin_ops_bot(bot: Any) -> None:
    global _BOT
    _BOT = bot


def admin_notify_recipient_ids() -> List[str]:
    raw = os.getenv("ADMIN_NOTIFY_USER_IDS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    raw2 = os.getenv("ADMIN_USER_IDS", "").strip()
    return [x.strip() for x in raw2.split(",") if x.strip()]


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def quota_dm_enabled() -> bool:
    return _truthy("ADMIN_QUOTA_DM_ENABLED", True)


def _cooldown_sec(kind: str) -> int:
    if kind == "rate_limit":
        key = "ADMIN_QUOTA_DM_RATE_LIMIT_COOLDOWN_SEC"
        default = 900
    else:
        key = "ADMIN_QUOTA_DM_COOLDOWN_SEC"
        default = 3600
    try:
        return max(0, int((os.getenv(key) or str(default)).strip()))
    except ValueError:
        return default


def _checkpoint_path() -> Path:
    base = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
    return base / "admin_quota_dm_checkpoint.json"


def _read_checkpoint() -> Dict[str, Any]:
    p = _checkpoint_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_checkpoint(data: Dict[str, Any]) -> None:
    p = _checkpoint_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        logger.debug("admin_ops_notify checkpoint write failed: %s", e)


def _fingerprint(kind: str, status: Optional[int], error_text: str) -> str:
    err = (error_text or "").strip().lower()[:120]
    return f"{kind}|{int(status or 0)}|{err}"


def _cooldown_active(fingerprint: str, kind: str) -> bool:
    cd = _cooldown_sec(kind)
    if cd <= 0:
        return False
    with _sync_lock:
        ck = _read_checkpoint()
        sent = ck.get("sent") if isinstance(ck.get("sent"), dict) else {}
        last = float(sent.get(fingerprint) or 0)
        return (time.time() - last) < float(cd)


def _mark_sent(fingerprint: str) -> None:
    with _sync_lock:
        ck = _read_checkpoint()
        sent = ck.get("sent") if isinstance(ck.get("sent"), dict) else {}
        sent[fingerprint] = time.time()
        # prune old entries (>7d)
        cut = time.time() - 7 * 86400
        sent = {k: v for k, v in sent.items() if float(v) > cut}
        ck["sent"] = sent
        _write_checkpoint(ck)


def classify_openrouter_issue(
    http_status: Optional[int],
    error_text: str,
) -> Optional[str]:
    """
    Возвращает kind для алерта или None если не ops-критично.
    kind: billing_quota | rate_limit
    """
    from core.openrouter_provider import is_openrouter_quota_or_billing_error

    if is_openrouter_quota_or_billing_error(http_status, error_text):
        return "billing_quota"
    st = int(http_status or 0)
    txt = (error_text or "").lower()
    if st == 429 or "rate limit" in txt or "too many requests" in txt:
        return "rate_limit"
    return None


def _host_label() -> str:
    for key in ("BOT_INSTANCE_ID", "GEMMA_INSTANCE", "HOSTNAME"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return "gemma_bot"


def _build_quota_message(
    *,
    kind: str,
    http_status: Optional[int],
    error_text: str,
    model: str,
    fallback_model: Optional[str],
) -> str:
    when = format_operator_datetime(datetime.now(timezone.utc))
    host = esc(_host_label())
    model_e = esc(model or "—")
    err_e = esc((error_text or "—")[:500])
    st = int(http_status or 0)

    if kind == "billing_quota":
        title = "💳 <b>OpenRouter: закончились деньги или лимит ключа</b>"
        hint = (
            "Пополни баланс на openrouter.ai или смени <code>OPENROUTER_API_KEY</code> "
            "(и перезапуск). Пока может работать fallback на free-модель."
        )
    else:
        title = "⏱ <b>OpenRouter: rate limit</b>"
        hint = "Слишком много запросов; обычно проходит само. Если часто — снизь нагрузку или смени модель."

    lines = [
        title,
        "",
        f"🕐 <code>{esc(when)}</code> · хост <code>{host}</code>",
        f"HTTP: <code>{st or '—'}</code>",
        f"Модель: <code>{model_e}</code>",
        "",
        f"<b>Ответ API:</b>\n<blockquote>{err_e}</blockquote>",
    ]
    if fallback_model:
        lines.extend(
            [
                "",
                f"↪ Временный fallback: <code>{esc(fallback_model)}</code>",
            ]
        )
    lines.extend(
        [
            "",
            hint,
            "",
            "<code>/admin_llm_usage</code> · <code>/admin_logs</code>",
        ]
    )
    return sanitize_html("\n".join(lines))


async def _send_quota_dm(text: str) -> None:
    bot = _BOT
    ids = admin_notify_recipient_ids()
    if bot is None:
        logger.warning("[admin_ops_notify] bot not registered — quota DM skipped")
        return
    if not ids:
        logger.info("[admin_ops_notify] no admin ids — quota DM skipped")
        return
    per_send = float(os.getenv("TELEGRAM_ADMIN_OPS_DM_TIMEOUT_SEC", "30"))
    for uid in ids:
        try:
            await asyncio.wait_for(
                bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML"),
                timeout=per_send,
            )
            logger.info("[admin_ops_notify] quota DM sent to admin=%s", uid)
        except asyncio.TimeoutError:
            logger.warning("[admin_ops_notify] quota DM timeout admin=%s", uid)
        except Exception as e:
            logger.warning("[admin_ops_notify] quota DM failed admin=%s: %s", uid, e)


def maybe_notify_openrouter_quota(
    *,
    http_status: Optional[int],
    error_text: str,
    model: str,
    fallback_model: Optional[str] = None,
) -> bool:
    """
    Поставить в очередь ЛС админам при billing/quota/rate-limit.
    Возвращает True если уведомление запланировано.
    """
    if not quota_dm_enabled():
        return False
    kind = classify_openrouter_issue(http_status, error_text)
    if not kind:
        return False
    fp = _fingerprint(kind, http_status, error_text)
    if _cooldown_active(fp, kind):
        return False

    try:
        from core.error_analysis import record_error_event

        sev = "critical" if kind == "billing_quota" else "warn"
        record_error_event(
            "openrouter",
            f"{kind}: {(error_text or '')[:240]}",
            extra={
                "code": "OPENROUTER_QUOTA" if kind == "billing_quota" else "OPENROUTER_RATE_LIMIT",
                "http_status": http_status,
                "model": model,
                "fallback_model": fallback_model,
            },
            severity=sev,
        )
    except Exception as e:
        logger.debug("admin_ops_notify record_error_event: %s", e)

    text = _build_quota_message(
        kind=kind,
        http_status=http_status,
        error_text=error_text,
        model=model,
        fallback_model=fallback_model,
    )
    _mark_sent(fp)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[admin_ops_notify] no event loop — quota DM deferred skipped")
        return False

    from core.async_spawn import spawn_logged

    spawn_logged(_send_quota_dm(text), label="admin_quota_dm")
    return True


__all__ = [
    "register_admin_ops_bot",
    "admin_notify_recipient_ids",
    "quota_dm_enabled",
    "classify_openrouter_issue",
    "maybe_notify_openrouter_quota",
]
