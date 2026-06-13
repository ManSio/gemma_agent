"""
Проверка сети и ключей (Telegram Bot API, OpenRouter) с единым таймаутом.

Таймаут по умолчанию 20 с — CONNECTIVITY_CHECK_TIMEOUT_SEC в .env.
Ответы для пользователя/логов — стабильные строки (см. MESSAGES).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from core.number_parse import parse_env_float
from core.openrouter_completion_text import text_from_completion_choice
from core.mem0_memory.mem0_module import (
    DEFAULT_MEM0_BASE,
    Mem0MemoryModule,
    _normalize_mem0_api_key,
    load_mem0_config_from_env,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 20.0


def _utc_iso_seconds() -> str:
    """Метка времени для операторских снимков без дробной части секунд."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# Последние результаты проверок внешних API — для /admin_health, /api/v1/health (без повторных запросов).
_last_external_by_key: Dict[str, Dict[str, Any]] = {}
_last_full_connectivity: Optional[Dict[str, Any]] = None


def _external_row_key(result: Dict[str, Any]) -> str:
    svc = str(result.get("service") or "?")
    role = result.get("role")
    return f"{svc}:{role}" if role else svc


def record_external_service_check(result: Dict[str, Any], *, source: str) -> None:
    """Обновляет снимок по сервису (telegram / openrouter / mem0 + role)."""
    if not isinstance(result, dict):
        return
    _last_external_by_key[_external_row_key(result)] = {
        "ts": _utc_iso_seconds(),
        "source": source,
        "service": result.get("service"),
        "role": result.get("role"),
        "ok": result.get("ok"),
        "skipped": bool(result.get("skipped")),
        "error_code": result.get("error_code"),
        "user_message": (result.get("user_message") or "")[:800],
    }


def get_external_connectivity_hints_for_health() -> Dict[str, Any]:
    """Сводка для оператора: что именно не работает, по последним проверкам."""
    rows = list(_last_external_by_key.values())
    failures = [r for r in rows if not r.get("skipped") and r.get("ok") is False]
    failures.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    lr = _last_full_connectivity
    return {
        "by_service": dict(sorted(_last_external_by_key.items())),
        "failures": failures,
        "failure_messages": [
            f"{r.get('service') or '?'}: {r.get('user_message') or r.get('error_code')}"
            for r in failures[:16]
        ],
        "last_full_ok": lr.get("ok") if isinstance(lr, dict) else None,
        "last_full_at": lr.get("at") if isinstance(lr, dict) else None,
        "last_full_summary": (lr or {}).get("summary") if isinstance(lr, dict) else None,
        "last_full_lines": (lr or {}).get("lines") if isinstance(lr, dict) else None,
    }

MESSAGES = {
    "telegram_missing": "TELEGRAM_TOKEN не задан — бот не подключится к Telegram.",
    "telegram_ok": "Telegram: токен действителен, бот @{username}.",
    "telegram_http_error": "Telegram API вернул ошибку (HTTP {status}). Проверьте токен.",
    "telegram_not_ok": "Telegram API: ok=false в ответе — токен недействителен или отозван.",
    "telegram_timeout": "Telegram API не ответил за {timeout} с — проверьте сеть, DNS или файрвол.",
    "telegram_network": "Не удалось связаться с Telegram: {detail}",
    "openrouter_missing": "OPENROUTER_API_KEY не задан — ответы нейросети работать не будут.",
    "openrouter_ok": "OpenRouter: ключ принят, тестовый ответ получен (модель {model}).",
    "openrouter_empty": "OpenRouter ответил без текста — смените OPENROUTER_MODEL_FREE на другую модель.",
    "openrouter_http": "OpenRouter HTTP {status}: {detail}",
    "openrouter_timeout": "OpenRouter не ответил за {timeout} с — сеть, блокировка или перегрузка API.",
    "openrouter_network": "Ошибка запроса к OpenRouter: {detail}",
    "summary_all_ok": "Сеть и ключи: Telegram, OpenRouter и Mem0 (если задан) в порядке.",
    "summary_issues": "Есть проблемы: {issues}",
    "mem0_skipped": (
        "Mem0 HTTP не настроен: нет MEM0_API_KEY и нет пары MEM0_LOCAL=true + MEM0_API_URL "
        "(или MEM0_SELF_HOSTED) — модуль Mem0 работает только с in-process кэшем, без запросов к серверу."
    ),
    "mem0_ok": "Mem0 ({role}): ключ принят, поиск API отвечает.",
    "mem0_ok_self_hosted": "Mem0 ({role}): self-hosted / MEM0_LOCAL — HTTP поиск отвечает.",
    "mem0_ok_simple_api": "Mem0 ({role}): отвечает упрощённый API (/search). Для путей Platform задайте MEM0_API_PREFIX или сервер с /v3/memories/search/.",
    "mem0_ok_self_hosted_simple": (
        "Mem0 ({role}): self-hosted отвечает на /search (упрощённый API; полный Platform — /v3/memories/search/ или MEM0_API_PREFIX)."
    ),
    "mem0_http": "Mem0 ({role}): HTTP {status} — {detail}",
    "mem0_timeout": "Mem0 ({role}): нет ответа за {timeout} с.",
    "mem0_network": "Mem0 ({role}): сеть — {detail}",
}


def _timeout() -> aiohttp.ClientTimeout:
    sec = parse_env_float("CONNECTIVITY_CHECK_TIMEOUT_SEC", float(DEFAULT_TIMEOUT_SEC))
    return aiohttp.ClientTimeout(total=max(5.0, min(sec, 120.0)))


def _telegram_get_me_url(token: str) -> str:
    return f"https://api.telegram.org/bot{token}/getMe"


def _connectivity_openrouter_model() -> str:
    """Модель для служебных проверок OpenRouter (можно принудить free-маршрут)."""
    explicit = (os.getenv("OPENROUTER_CONNECTIVITY_MODEL") or "").strip()
    if explicit:
        return explicit
    force_free = (os.getenv("OPENROUTER_CONNECTIVITY_FORCE_FREE") or "true").strip().lower()
    if force_free in {"1", "true", "yes", "on"}:
        return "openrouter/free"
    return (os.getenv("OPENROUTER_MODEL_FREE") or "openrouter/auto").strip()


async def check_telegram_bot_token(token: Optional[str]) -> Dict[str, Any]:
    """getMe с таймаутом; заранее заданные user_message."""
    t = (token or "").strip()
    if not t:
        return {
            "ok": False,
            "service": "telegram",
            "error_code": "missing_token",
            "user_message": MESSAGES["telegram_missing"],
            "http_status": None,
            "roundtrip_ms": None,
        }
    url = _telegram_get_me_url(t)
    to = _timeout()
    try:
        t0 = time.perf_counter()
        async with aiohttp.ClientSession(timeout=to) as session:
            async with session.get(url) as resp:
                status = resp.status
                roundtrip_ms = (time.perf_counter() - t0) * 1000.0
                try:
                    data = await resp.json(content_type=None)
                except (json.JSONDecodeError, aiohttp.ContentTypeError) as e:
                    txt = (await resp.text())[:500]
                    return {
                        "ok": False,
                        "service": "telegram",
                        "error_code": "bad_json",
                        "user_message": MESSAGES["telegram_network"].format(detail=str(e)),
                        "http_status": status,
                        "raw": txt,
                        "roundtrip_ms": round(roundtrip_ms, 2),
                    }
                if status != 200:
                    return {
                        "ok": False,
                        "service": "telegram",
                        "error_code": "http_error",
                        "user_message": MESSAGES["telegram_http_error"].format(status=status),
                        "http_status": status,
                        "body": data if isinstance(data, dict) else str(data)[:500],
                        "roundtrip_ms": round(roundtrip_ms, 2),
                    }
                if not (isinstance(data, dict) and data.get("ok")):
                    return {
                        "ok": False,
                        "service": "telegram",
                        "error_code": "api_not_ok",
                        "user_message": MESSAGES["telegram_not_ok"],
                        "http_status": status,
                        "body": data,
                        "roundtrip_ms": round(roundtrip_ms, 2),
                    }
                result = data.get("result") or {}
                username = result.get("username") or "unknown"
                return {
                    "ok": True,
                    "service": "telegram",
                    "error_code": None,
                    "user_message": MESSAGES["telegram_ok"].format(username=username),
                    "http_status": status,
                    "bot_id": result.get("id"),
                    "username": username,
                    "roundtrip_ms": round(roundtrip_ms, 2),
                }
    except asyncio.TimeoutError:
        sec = to.total or DEFAULT_TIMEOUT_SEC
        return {
            "ok": False,
            "service": "telegram",
            "error_code": "timeout",
            "user_message": MESSAGES["telegram_timeout"].format(timeout=int(sec)),
            "http_status": None,
            "roundtrip_ms": None,
        }
    except aiohttp.ClientError as e:
        return {
            "ok": False,
            "service": "telegram",
            "error_code": "network",
            "user_message": MESSAGES["telegram_network"].format(detail=str(e)),
            "http_status": None,
            "roundtrip_ms": None,
        }


async def check_openrouter_api(
    api_key: Optional[str],
    *,
    model: Optional[str] = None,
    api_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Минимальный запрос chat/completions (несколько токенов) для проверки ключа и сети.
    """
    key = (api_key or "").strip()
    if not key:
        return {
            "ok": False,
            "service": "openrouter",
            "error_code": "missing_key",
            "user_message": MESSAGES["openrouter_missing"],
            "http_status": None,
            "roundtrip_ms": None,
        }
    model_name = (model or _connectivity_openrouter_model() or "openrouter/auto").strip()
    url = (api_url or os.getenv("OPENROUTER_API_URL") or "https://openrouter.ai/api/v1/chat/completions").strip()
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": 'Reply with exactly: OK'}],
        "max_tokens": 16,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ManSio/gemma_agent",
        "X-Title": "Gemma Agent Connectivity Check",
        "X-OpenRouter-Title": "Gemma Agent Connectivity Check",
    }
    to = _timeout()
    try:
        t0 = time.perf_counter()
        async with aiohttp.ClientSession(timeout=to) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                status = resp.status
                raw_text = await resp.text()
                roundtrip_ms = (time.perf_counter() - t0) * 1000.0
                try:
                    data = json.loads(raw_text) if raw_text else {}
                except json.JSONDecodeError:
                    data = {"_raw": raw_text[:800]}
                if status != 200:
                    detail = raw_text[:400] if raw_text else str(data)[:400]
                    return {
                        "ok": False,
                        "service": "openrouter",
                        "error_code": "http_error",
                        "user_message": MESSAGES["openrouter_http"].format(status=status, detail=detail),
                        "http_status": status,
                        "model": model_name,
                        "roundtrip_ms": round(roundtrip_ms, 2),
                    }
                choices = data.get("choices") if isinstance(data, dict) else None
                ch0 = choices[0] if isinstance(choices, list) and choices else None
                content = text_from_completion_choice(ch0, include_reasoning=True)
                if not content:
                    return {
                        "ok": False,
                        "service": "openrouter",
                        "error_code": "empty_content",
                        "user_message": MESSAGES["openrouter_empty"],
                        "http_status": status,
                        "model": model_name,
                        "roundtrip_ms": round(roundtrip_ms, 2),
                    }
                routed = data.get("model") if isinstance(data, dict) else None
                usage = data.get("usage") if isinstance(data, dict) else {}
                out = {
                    "ok": True,
                    "service": "openrouter",
                    "error_code": None,
                    "user_message": MESSAGES["openrouter_ok"].format(model=model_name),
                    "http_status": status,
                    "model": model_name,
                    "routed_model": routed,
                    "reply_preview": content[:200],
                    "roundtrip_ms": round(roundtrip_ms, 2),
                    "usage": usage if isinstance(usage, dict) else {},
                }
                pt = out["usage"].get("prompt_tokens")
                ct = out["usage"].get("completion_tokens")
                try:
                    pt_i = int(pt) if pt is not None else None
                    ct_i = int(ct) if ct is not None else None
                    tot = (pt_i or 0) + (ct_i or 0)
                    if tot > 0 and roundtrip_ms > 0:
                        out["total_tokens_per_sec"] = round(tot / (roundtrip_ms / 1000.0), 3)
                    if ct_i and ct_i > 0 and roundtrip_ms > 0:
                        out["completion_tokens_per_sec"] = round(ct_i / (roundtrip_ms / 1000.0), 3)
                except (TypeError, ValueError):
                    pass
                return out
    except asyncio.TimeoutError:
        sec = to.total or DEFAULT_TIMEOUT_SEC
        return {
            "ok": False,
            "service": "openrouter",
            "error_code": "timeout",
            "user_message": MESSAGES["openrouter_timeout"].format(timeout=int(sec)),
            "http_status": None,
            "model": model_name,
            "roundtrip_ms": None,
        }
    except aiohttp.ClientError as e:
        return {
            "ok": False,
            "service": "openrouter",
            "error_code": "network",
            "user_message": MESSAGES["openrouter_network"].format(detail=str(e)),
            "http_status": None,
            "model": model_name,
            "roundtrip_ms": None,
        }


def _mem0_memories_search_url(base: str) -> str:
    """Как Mem0MemoryModule._mem_path: /{MEM0_API_PREFIX}/memories/search/ (по умолч. v3)."""
    b = (base or "").strip().rstrip("/")
    raw_pf = os.getenv("MEM0_API_PREFIX")
    if raw_pf is None:
        root = "v3"
    else:
        root = raw_pf.strip().strip("/")
    if root:
        return f"{b}/{root}/memories/search/"
    return f"{b}/memories/search/"


def _mem0_simple_compat_enabled() -> bool:
    """Совпадает с Mem0MemoryModule: при 404 пробовать POST /search с {\"query\"}."""
    v = (os.getenv("MEM0_LOCAL_SIMPLE_COMPAT") or "true").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _mem0_auth_header(key: str) -> str:
    k = _normalize_mem0_api_key(key)
    scheme = (os.getenv("MEM0_AUTH_SCHEME") or "token").strip().lower()
    if scheme in {"bearer", "jwt"}:
        return f"Bearer {k}"
    return f"Token {k}"


async def check_mem0_platform(
    api_key: Optional[str],
    *,
    api_url: Optional[str] = None,
    role: str = "primary",
    self_hosted: bool = False,
) -> Dict[str, Any]:
    """
    Лёгкий POST …/memories/search/ — проверяет ключ и доступность Mem0 Platform / self-hosted.
    """
    key = _normalize_mem0_api_key(api_key)
    if not key:
        return {
            "ok": True,
            "skipped": True,
            "service": "mem0",
            "role": role,
            "error_code": "no_key",
            "user_message": MESSAGES["mem0_skipped"],
            "http_status": None,
            "roundtrip_ms": None,
        }
    root = (api_url or os.getenv("MEM0_API_URL") or DEFAULT_MEM0_BASE).strip().rstrip("/")
    url = _mem0_memories_search_url(root)
    body = {
        "query": ".",
        "filters": {"user_id": "__gemma_connectivity__"},
        "top_k": 1,
        "threshold": 0.0,
    }
    headers = {
        "Authorization": _mem0_auth_header(key),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    to = _timeout()
    simple_url = f"{root}/search"
    simple_body = {"query": str(body.get("query") or ".")}
    try:
        t0 = time.perf_counter()
        async with aiohttp.ClientSession(timeout=to) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                status = resp.status
                raw_text = await resp.text()
                roundtrip_ms = (time.perf_counter() - t0) * 1000.0
                if status == 200:
                    msg = (
                        MESSAGES["mem0_ok_self_hosted"].format(role=role)
                        if self_hosted
                        else MESSAGES["mem0_ok"].format(role=role)
                    )
                    return {
                        "ok": True,
                        "skipped": False,
                        "service": "mem0",
                        "role": role,
                        "error_code": None,
                        "user_message": msg,
                        "http_status": status,
                        "roundtrip_ms": round(roundtrip_ms, 2),
                    }
                if (
                    status == 404
                    and _mem0_simple_compat_enabled()
                    and simple_url != url.rstrip("/")
                ):
                    t1 = time.perf_counter()
                    async with session.post(
                        simple_url, headers=headers, json=simple_body
                    ) as resp2:
                        st2 = resp2.status
                        raw2 = await resp2.text()
                        roundtrip_ms = (time.perf_counter() - t1) * 1000.0
                        if st2 == 200:
                            msg = (
                                MESSAGES["mem0_ok_self_hosted_simple"].format(role=role)
                                if self_hosted
                                else MESSAGES["mem0_ok_simple_api"].format(role=role)
                            )
                            return {
                                "ok": True,
                                "skipped": False,
                                "service": "mem0",
                                "role": role,
                                "error_code": None,
                                "user_message": msg,
                                "http_status": st2,
                                "roundtrip_ms": round(roundtrip_ms, 2),
                                "mem0_connectivity_path": "simple_search",
                            }
                        detail = (
                            f"platform_status={status} body_len={len(raw_text or '')}; "
                            f"fallback_status={st2} body_len={len(raw2 or '')}"
                        )
                        return {
                            "ok": False,
                            "skipped": False,
                            "service": "mem0",
                            "role": role,
                            "error_code": "http_error" if st2 != 401 else "invalid_key",
                            "user_message": MESSAGES["mem0_http"].format(
                                role=role, status=st2, detail=detail[:480]
                            ),
                            "http_status": st2,
                            "roundtrip_ms": round(roundtrip_ms, 2),
                        }
                detail = f"http_status={status} body_len={len(raw_text or '')}"
                return {
                    "ok": False,
                    "skipped": False,
                    "service": "mem0",
                    "role": role,
                    "error_code": "http_error" if status != 401 else "invalid_key",
                    "user_message": MESSAGES["mem0_http"].format(role=role, status=status, detail=detail),
                    "http_status": status,
                    "roundtrip_ms": round(roundtrip_ms, 2),
                }
    except asyncio.TimeoutError:
        sec = to.total or DEFAULT_TIMEOUT_SEC
        return {
            "ok": False,
            "skipped": False,
            "service": "mem0",
            "role": role,
            "error_code": "timeout",
            "user_message": MESSAGES["mem0_timeout"].format(role=role, timeout=int(sec)),
            "http_status": None,
            "roundtrip_ms": None,
        }
    except aiohttp.ClientError as e:
        return {
            "ok": False,
            "skipped": False,
            "service": "mem0",
            "role": role,
            "error_code": "network",
            "user_message": MESSAGES["mem0_network"].format(role=role, detail=type(e).__name__),
            "http_status": None,
            "roundtrip_ms": None,
        }


async def log_mem0_startup_status(mem0_memory: Mem0MemoryModule, log: logging.Logger) -> None:
    """Один запрос к Mem0 при старте процесса: явный INFO/WARNING в логах оператора."""
    from core.sensitive_export import mem0_check_public_view, mem0_log_facets

    if not mem0_memory._cloud:
        return
    _raw_p = await check_mem0_platform(mem0_memory._api_key, api_url=mem0_memory._base, role="primary")
    _mp = mem0_check_public_view(_raw_p)
    record_external_service_check(_mp, source="startup")
    _ok, _http_status, _error_code = mem0_log_facets(_raw_p)
    if _ok:
        log.info(
            "Mem0 primary check ok http_status=%s",
            _http_status,
            extra={"gemma_event": "mem0_primary_ok"},
        )
    else:
        log.warning(
            "Mem0 primary check failed code=%s http_status=%s — см. /admin_connectivity",
            _error_code,
            _http_status,
            extra={"gemma_event": "mem0_primary_check_failed"},
        )
    if mem0_memory._mirror_key:
        _raw_m = await check_mem0_platform(
            mem0_memory._mirror_key,
            api_url=mem0_memory._mirror_base,
            role="mirror",
        )
        _mm = mem0_check_public_view(_raw_m)
        record_external_service_check(_mm, source="startup")
        _ok_m, _http_status_m, _error_code_m = mem0_log_facets(_raw_m)
        if _ok_m:
            log.info(
                "Mem0 mirror check ok http_status=%s",
                _http_status_m,
                extra={"gemma_event": "mem0_mirror_ok"},
            )
        else:
            log.warning(
                "Mem0 mirror check failed code=%s http_status=%s — см. /admin_connectivity",
                _error_code_m,
                _http_status_m,
                extra={"gemma_event": "mem0_mirror_check_failed"},
            )
            if getattr(mem0_memory, "_mirror_write", False):
                mem0_memory.disable_mirror_write_runtime(
                    "mirror не прошёл проверку при старте — иначе каждый ответ давал бы 401 на MEM0_MIRROR_WRITE",
                )


async def run_connectivity_checks(
    *,
    telegram_token: Optional[str] = None,
    openrouter_key: Optional[str] = None,
    include_http_probes: bool = False,
) -> Dict[str, Any]:
    """
    Проверка Telegram, OpenRouter, Mem0 (primary + mirror при наличии ключей).
    Возвращает сводку и отдельные блоки; обновляет снимок для /admin_health.
    """
    tg_tok = telegram_token if telegram_token is not None else os.getenv("TELEGRAM_TOKEN")
    or_key = openrouter_key if openrouter_key is not None else os.getenv("OPENROUTER_API_KEY")

    tg = await check_telegram_bot_token(tg_tok)
    or_res = await check_openrouter_api(or_key, model=_connectivity_openrouter_model())

    mem0_key = _normalize_mem0_api_key(os.getenv("MEM0_API_KEY"))
    mem0_cfg = load_mem0_config_from_env()
    if mem0_key:
        mem0_prim = await check_mem0_platform(mem0_key, role="primary", self_hosted=False)
    elif mem0_cfg and (mem0_cfg.get("mem0_api_url") or "").strip():
        mem0_prim = await check_mem0_platform(
            mem0_cfg.get("mem0_api_key"),
            api_url=mem0_cfg.get("mem0_api_url"),
            role="primary",
            self_hosted=True,
        )
    else:
        mem0_prim = {
            "ok": True,
            "skipped": True,
            "service": "mem0",
            "role": "primary",
            "error_code": "no_key",
            "user_message": MESSAGES["mem0_skipped"],
        }
    mem0_mirror_key = _normalize_mem0_api_key(os.getenv("MEM0_MIRROR_API_KEY"))
    mirror_url = (os.getenv("MEM0_MIRROR_API_URL") or "").strip() or None
    mem0_mir = (
        await check_mem0_platform(mem0_mirror_key, api_url=mirror_url, role="mirror")
        if mem0_mirror_key
        else {
            "ok": True,
            "skipped": True,
            "service": "mem0",
            "role": "mirror",
            "error_code": "no_key",
            "user_message": "Mem0 mirror: не задан MEM0_MIRROR_API_KEY.",
        }
    )

    issues: List[str] = []
    if not tg.get("ok"):
        issues.append(f"telegram:{tg.get('error_code')}")
    if not or_res.get("ok"):
        issues.append(f"openrouter:{or_res.get('error_code')}")
    if (not mem0_prim.get("skipped")) and (not mem0_prim.get("ok")):
        issues.append(f"mem0_primary:{mem0_prim.get('error_code')}")
    if (not mem0_mir.get("skipped")) and (not mem0_mir.get("ok")):
        issues.append(f"mem0_mirror:{mem0_mir.get('error_code')}")

    all_ok = (
        bool(tg.get("ok"))
        and bool(or_res.get("ok"))
        and (bool(mem0_prim.get("skipped")) or bool(mem0_prim.get("ok")))
        and (not mem0_mirror_key or bool(mem0_mir.get("ok")))
    )
    summary = (
        MESSAGES["summary_all_ok"]
        if all_ok
        else MESSAGES["summary_issues"].format(issues=", ".join(issues) if issues else "неизвестно")
    )

    line_mem0_p = mem0_prim.get("user_message", "")
    line_mem0_m = mem0_mir.get("user_message", "") if mem0_mirror_key else ""

    out: Dict[str, Any] = {
        "ok": all_ok,
        "timeout_sec": parse_env_float("CONNECTIVITY_CHECK_TIMEOUT_SEC", float(DEFAULT_TIMEOUT_SEC)),
        "summary": summary,
        "telegram": tg,
        "openrouter": or_res,
        "mem0": mem0_prim,
        "mem0_mirror": mem0_mir,
        "lines": [
            tg.get("user_message", ""),
            or_res.get("user_message", ""),
            line_mem0_p,
            *([line_mem0_m] if mem0_mirror_key else []),
        ],
    }
    skip_plugin_http = (os.getenv("CONNECTIVITY_SKIP_PLUGIN_HTTP_PROBES") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    plugin_merged_into_http_only = skip_plugin_http
    if not skip_plugin_http:
        try:
            from core.network_probe import run_plugin_http_probes

            out["plugin_http_probes"] = await run_plugin_http_probes()
        except Exception as e:
            logger.debug("plugin_http_probes: %s", e)
            out["plugin_http_probes"] = {"label": "plugin_http_probes", "ok": False, "error": str(e)}
    else:
        out["plugin_http_probes"] = {
            "label": "plugin_http_probes",
            "skipped": True,
            "results": [],
        }

    if include_http_probes:
        try:
            from core.network_probe import run_http_latency_probes

            out["http_probes"] = await run_http_latency_probes(
                include_plugin_endpoints=plugin_merged_into_http_only,
            )
        except Exception as e:
            logger.debug("http_probes: %s", e)
            out["http_probes"] = {"ok": False, "error": str(e)}

    global _last_full_connectivity
    _last_full_connectivity = {
        "at": _utc_iso_seconds(),
        "ok": all_ok,
        "summary": summary,
        "lines": list(out.get("lines") or []),
    }
    record_external_service_check(tg, source="connectivity")
    record_external_service_check(or_res, source="connectivity")
    record_external_service_check(mem0_prim, source="connectivity")
    if mem0_mirror_key:
        record_external_service_check(mem0_mir, source="connectivity")
    return out
