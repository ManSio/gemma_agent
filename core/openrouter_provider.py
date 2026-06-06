"""
OpenRouter Provider - AI Model Provider через OpenRouter API
Поддерживает множество моделей от различных поставщиков
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import aiohttp

from core.openrouter_completion_text import user_facing_completion_text
from core.openrouter_stream import merge_stream_finish_reason, parse_openrouter_sse_chunk
from core.llm_telemetry import record_openrouter_completion
from core.openrouter_prompt_cache import extra_completion_body_fields

logger = logging.getLogger(__name__)

_OPENROUTER_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})

_LOCAL_TRANSIENT_ERR_RE = re.compile(
    r"(?i)event loop is closed|attached to a different loop|"
    r"connector is closed|session is closed"
)


def _openrouter_http_retry_params() -> tuple[int, float]:
    try:
        n = int((os.getenv("OPENROUTER_HTTP_RETRY_ATTEMPTS") or "2").strip())
    except ValueError:
        n = 1
    n = max(1, min(n, 8))
    try:
        gap = float((os.getenv("OPENROUTER_HTTP_RETRY_GAP_SEC") or "10").strip())
    except ValueError:
        gap = 10.0
    gap = max(0.0, min(gap, 120.0))
    return n, gap


def _session_headers_enabled() -> bool:
    raw = (os.getenv("OPENROUTER_SESSION_HEADERS_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _normalize_tag_slug(tag: Optional[str]) -> str:
    raw = str(tag or "").strip().upper()
    if not raw:
        return ""
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _env_value_for_tag(tag: Optional[str], key: str) -> Optional[str]:
    slug = _normalize_tag_slug(tag)
    if slug:
        v = os.getenv(f"OPENROUTER_GEN_{slug}_{key}")
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    g = os.getenv(f"OPENROUTER_GEN_{key}")
    if g is None or str(g).strip() == "":
        return None
    return str(g).strip()


def _parse_float(v: Optional[str], *, low: float, high: float) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return max(low, min(high, x))


def _parse_int(v: Optional[str], *, low: int, high: int) -> Optional[int]:
    if v is None:
        return None
    try:
        x = int(v)
    except ValueError:
        return None
    return max(low, min(high, x))


def _parse_stop(v: Optional[str]) -> Optional[List[str]]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        if isinstance(obj, list):
            out = [str(x) for x in obj if str(x)]
            return out[:8] if out else None
    except json.JSONDecodeError:
        pass
    out = [p.strip() for p in s.split("||") if p.strip()]
    return out[:8] if out else None


def _apply_env_generation_overrides(payload: Dict[str, Any], *, tag: Optional[str]) -> Dict[str, Any]:
    temp = _parse_float(_env_value_for_tag(tag, "TEMPERATURE"), low=0.0, high=2.0)
    if temp is not None:
        payload["temperature"] = temp

    top_p = _parse_float(_env_value_for_tag(tag, "TOP_P"), low=0.0, high=1.0)
    if top_p is not None:
        payload["top_p"] = top_p

    max_tokens = _parse_int(_env_value_for_tag(tag, "MAX_TOKENS"), low=1, high=32768)
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    presence = _parse_float(_env_value_for_tag(tag, "PRESENCE_PENALTY"), low=-2.0, high=2.0)
    if presence is not None:
        payload["presence_penalty"] = presence

    frequency = _parse_float(_env_value_for_tag(tag, "FREQUENCY_PENALTY"), low=-2.0, high=2.0)
    if frequency is not None:
        payload["frequency_penalty"] = frequency

    repetition = _parse_float(_env_value_for_tag(tag, "REPETITION_PENALTY"), low=-2.0, high=2.0)
    if repetition is not None:
        payload["repetition_penalty"] = repetition

    top_k = _parse_int(_env_value_for_tag(tag, "TOP_K"), low=1, high=200)
    if top_k is not None:
        payload["top_k"] = top_k

    min_p = _parse_float(_env_value_for_tag(tag, "MIN_P"), low=0.0, high=1.0)
    if min_p is not None:
        payload["min_p"] = min_p

    seed = _parse_int(_env_value_for_tag(tag, "SEED"), low=0, high=2_147_483_647)
    if seed is not None:
        payload["seed"] = seed

    stop = _parse_stop(_env_value_for_tag(tag, "STOP"))
    if stop:
        payload["stop"] = stop

    from core.openrouter_reasoning import apply_openrouter_reasoning

    apply_openrouter_reasoning(payload, tag=tag)
    return payload


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _no_balance_fallback_enabled() -> bool:
    return _truthy_env("OPENROUTER_NO_BALANCE_FALLBACK_TO_FREE", True)


def _no_balance_fallback_model() -> str:
    return (os.getenv("OPENROUTER_NO_BALANCE_FALLBACK_MODEL") or "openrouter/free").strip() or "openrouter/free"


def _no_balance_cooldown_sec() -> int:
    raw = (os.getenv("OPENROUTER_NO_BALANCE_COOLDOWN_SEC") or "900").strip()
    try:
        val = int(raw)
    except ValueError:
        val = 900
    return max(0, min(val, 86400))


def is_openrouter_quota_or_billing_error(status: Optional[int], error_text: str) -> bool:
    """Публично: billing/quota/monthly-limit для admin DM и fallback."""
    return _looks_like_no_balance(status, error_text)


def _looks_like_no_balance(status: Optional[int], error_text: str) -> bool:
    txt = (error_text or "").lower()
    if int(status or 0) == 402:
        return True
    flags = (
        "insufficient",
        "insufficient credits",
        "insufficient balance",
        "not enough credits",
        "payment required",
        "billing",
        "quota exceeded",
        "key limit exceeded",
        "monthly limit",
    )
    if int(status or 0) == 403 and ("limit" in txt or "quota" in txt):
        return True
    return any(f in txt for f in flags)


# Один провайдер на процесс: иначе main.py, brain и site_recipe создавали бы разные экземпляры
# (двойной лог «initialized», разъезжающийся current_usage).
_provider_instance: Optional["OpenRouterProvider"] = None


def get_openrouter_provider() -> "OpenRouterProvider":
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = OpenRouterProvider()
    return _provider_instance


def reset_openrouter_provider_for_tests() -> None:
    """Только для тестов."""
    global _provider_instance
    if _provider_instance is not None:
        _provider_instance._http_session = None
        _provider_instance._http_lock = None
        _provider_instance._http_loop_id = None
    _provider_instance = None


class OpenRouterProvider:
    """Провайдер AI моделей через OpenRouter API"""
    
    def __init__(self, api_key: str = None, api_key_dev: str = None):
        """Инициализация провайдера"""
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.api_key_dev = api_key_dev or os.getenv("OPENROUTER_API_KEY_DEV")
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        
        # Модели по умолчанию
        self.free_model = os.getenv("OPENROUTER_MODEL_FREE", "openrouter/free")
        self.dev_model = os.getenv("OPENROUTER_MODEL_DEV", "deepseek/deepseek-v4-pro")
        self.qwen_model = os.getenv("OPENROUTER_MODEL_QWEN", "deepseek/deepseek-v4-flash")
        
        # Настройки переключения
        try:
            self.model_threshold = int(os.getenv("MODEL_SWITCH_THRESHOLD", "100"))
        except (ValueError, TypeError):
            self.model_threshold = 100
        self.current_usage = 0
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._http_lock: Optional[asyncio.Lock] = None
        self._http_loop_id: Optional[int] = None
        self._no_balance_freeze_until: float = 0.0

        logger.info(f"OpenRouter initialized with free model: {self.free_model}")

    async def _shared_http_session(self) -> aiohttp.ClientSession:
        """Один ClientSession на event loop — меньше TLS-handshake; без cross-loop Future."""
        loop_id = id(asyncio.get_running_loop())
        if self._http_loop_id != loop_id:
            self._http_session = None
            self._http_lock = asyncio.Lock()
            self._http_loop_id = loop_id
        elif self._http_lock is None:
            self._http_lock = asyncio.Lock()
        async with self._http_lock:
            if self._http_session is None or self._http_session.closed:
                try:
                    to_total = float(os.getenv("OPENROUTER_HTTP_TOTAL_TIMEOUT_SEC", "120"))
                except ValueError:
                    to_total = 120.0
                to_total = max(30.0, min(to_total, 600.0))
                self._http_session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=to_total),
                    connector=aiohttp.TCPConnector(
                        limit=32,
                        limit_per_host=16,
                        ttl_dns_cache=300,
                        enable_cleanup_closed=True,
                    ),
                )
        return self._http_session

    async def aclose(self) -> None:
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None
    
    def _get_current_api_key(self) -> str:
        """Always primary API key."""
        return self.api_key or ""
    
    def _get_current_model(self) -> str:
        """Always DeepSeek native."""
        return "deepseek/deepseek-v4-pro"

    def resolve_model_name(self, model_override: Optional[str] = None) -> str:
        """Фактическое имя модели для запроса (для профилей промпта и метрик)."""
        m = (model_override or "").strip()
        return m if m else self._get_current_model()
    
    async def generate(
        self, 
        prompt: str, 
        model: str = None, 
        system_prompt: str = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        vision_image_parts: Optional[List[Tuple[str, str]]] = None,
        api_key_override: Optional[str] = None,
        kv_cache_tail: str = "",
        **kwargs
    ) -> Dict[str, Any]:
        """Генерация текста через OpenRouter — always DeepSeek native.

        vision_image_parts: список пар (mime_type, base64_without_prefix) — для multimodal vision.
        kv_cache_tail: опциональный текст, вставляется system-сообщением ПОСЛЕ user-сообщения
                       для сохранения стабильного префикса в KV-кеше.
        """
        api_key = (api_key_override or "").strip() or self._get_current_api_key()

        if not api_key:
            logger.error("OpenRouter API key not provided")
            return {"error": "API key not configured", "content": ""}

        _telem_tag = (kwargs.pop("telemetry_tag", None) or "").strip() or None
        _telem_kind_in = (kwargs.pop("telemetry_kind", None) or "").strip() or None
        _telem_extra = kwargs.pop("telemetry_extra", None)
        _session_id = str(kwargs.pop("session_id", "") or "").strip()
        _conversation_id = str(kwargs.pop("conversation_id", "") or "").strip()
        kv_cache_tail = str(kwargs.pop("kv_cache_tail", "") or "").strip() or kv_cache_tail

        user_content: Union[str, List[Dict[str, Any]]] = prompt
        model_name = (model or "").strip() or "deepseek/deepseek-v4-pro"
        requested_model_name = model_name
        parts_in = vision_image_parts or []
        image_blocks: List[Dict[str, Any]] = []
        for item in parts_in:
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                continue
            mime, b64 = str(item[0] or "").strip(), str(item[1] or "").strip()
            if not b64:
                continue
            if not mime:
                mime = "image/jpeg"
            image_blocks.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )
        if image_blocks:
            user_content = [{"type": "text", "text": prompt}] + image_blocks
            if not model:
                vm = (os.getenv("OPENROUTER_MODEL_VISION") or "").strip()
                model_name = vm or "google/gemini-2.0-flash-exp:free"

        from core.llm_telemetry import build_openrouter_telemetry

        _telemetry = build_openrouter_telemetry(
            tag=_telem_tag,
            kind=_telem_kind_in,
            session_id=_session_id,
            extra=_telem_extra if isinstance(_telem_extra, dict) else None,
            vision=bool(image_blocks),
        )
        if _no_balance_fallback_enabled():
            freeze_left = self._no_balance_freeze_until - time.time()
            if freeze_left > 0:
                fb_model = _no_balance_fallback_model()
                if fb_model and model_name != fb_model:
                    model_name = fb_model
                    _telemetry["no_balance_frozen"] = True
                    _telemetry["no_balance_frozen_sec_left"] = int(freeze_left)

        # Prepare messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        if kv_cache_tail:
            messages.append({"role": "system", "content": kv_cache_tail})

        # ── KV-cache head verification: messages[1] (user content) head must be identical
        #     across consecutive requests in the same profile for cache hits.
        if len(messages) >= 2:
            _user_head = str(messages[1].get("content", ""))[:200]
            if _user_head:
                logger.info("messages[1] head_len=%s", len(_user_head))
        
        _no_payload = frozenset(
            {
                "api_key_override",
                "telemetry_kind",
                "telemetry_tag",
                "session_id",
                "conversation_id",
                "_no_balance_retry_done",
                "telemetry_extra",
            }
        )
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **{k: v for k, v in kwargs.items() if k not in _no_payload},
        }
        payload = _apply_env_generation_overrides(payload, tag=_telem_tag)
        for _k, _v in extra_completion_body_fields(model_name).items():
            if _k not in payload:
                payload[_k] = _v

        # KV Debug: dump actual request payload if enabled
        if os.getenv("GEMMA_KV_DEBUG_PROMPT_DUMP", "").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                from core.brain.kv_debug_logger import record_kv_trace as _kv_debug_dump
                _dump = dict(payload)
                _dump.pop("messages", None)
                _dump["messages"] = [
                    {"role": m.get("role"), "content_preview": str(m.get("content", ""))[:200]}
                    for m in (payload.get("messages") or [])
                ]
                _kv_debug_dump({
                    "event": "openrouter_payload",
                    "session_id": _session_id or "",
                    "payload": _dump,
                })
            except Exception as e:
                logger.debug('%s optional failed: %s', 'openrouter_provider', e, exc_info=True)
        # KV Debug: log the actual payload structure (session_id, provider, message count)
        if _telemetry.get("kv_debug_payload") or logger.isEnabledFor(logging.DEBUG):
            _msg_count = len(payload.get("messages", []))
            _sys_chars = (
                len(str(payload.get("messages", [{}])[0].get("content", "")))
                if payload.get("messages")
                else 0
            )
            logger.debug(
                "openrouter payload snapshot model=%s messages=%s system_chars=%s provider=%s session_id_len=%s",
                payload.get("model"),
                _msg_count,
                _sys_chars,
                payload.get("provider"),
                len(_session_id or ""),
            )

        t0 = time.perf_counter()
        http_retries, http_gap = _openrouter_http_retry_params()
        last_http_err: Dict[str, Any] = {"error": "openrouter_http_retry_exhausted", "content": ""}

        for http_try in range(http_retries):
            if http_try:
                await asyncio.sleep(http_gap)
            try:
                session = await self._shared_http_session()
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/ManSio/gemma_agent",
                    "X-Title": "Gemma Agent",
                    "X-OpenRouter-Title": "Gemma Agent",
                }
                if _session_id and _session_headers_enabled():
                    headers["X-Session-Id"] = _session_id

                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        upstream = data.get("model") if isinstance(data, dict) else None
                        raw_usage = data.get("usage") if isinstance(data, dict) else {}
                        try:
                            choices = data.get("choices") or []
                            ch0 = choices[0] if choices else None
                            finish = ""
                            if isinstance(ch0, dict):
                                finish = str(ch0.get("finish_reason") or "").strip().lower()
                            content = user_facing_completion_text(ch0, requested_model=model_name)
                            if finish == "length" and (content or "").strip():
                                raw_sfx = os.getenv("OPENROUTER_LENGTH_FINISH_SUFFIX")
                                if raw_sfx is None:
                                    suffix = (
                                        "[Ответ обрезан лимитом токенов модели. "
                                        "Напишите «продолжи» или сократите запрос.]"
                                    )
                                else:
                                    suffix = raw_sfx.strip()
                                if suffix:
                                    content = content.rstrip() + "\n\n" + suffix
                        except (IndexError, TypeError, AttributeError) as e:
                            logger.error("OpenRouter JSON parse error: %s data_keys=%s", e, list(data.keys()) if isinstance(data, dict) else type(data))
                            record_openrouter_completion(
                                ok=False,
                                requested_model=model_name,
                                upstream_model=upstream,
                                latency_ms=elapsed_ms,
                                usage=None,
                                http_status=200,
                                error=f"invalid_openrouter_response: {e}",
                                content_chars=0,
                                telemetry=_telemetry,
                            )
                            return {"error": "invalid_openrouter_response", "content": ""}

                        finish_fr = ""
                        try:
                            if isinstance(ch0, dict):
                                finish_fr = str(ch0.get("finish_reason") or "").strip().lower()
                        except Exception:
                            finish_fr = ""

                        if not content.strip():
                            logger.warning(
                                "OpenRouter returned empty content (requested_model=%s upstream_model=%s finish=%s usage=%s)",
                                model_name,
                                upstream,
                                finish_fr or "?",
                                raw_usage,
                            )
                            try:
                                from core.openrouter_silent_refusal import (
                                    completion_looks_like_silent_refusal,
                                    log_silent_refusal,
                                    silent_refusal_fallback_model,
                                    silent_refusal_retry_enabled,
                                )

                                if (
                                    silent_refusal_retry_enabled()
                                    and completion_looks_like_silent_refusal(ch0, content)
                                    and not kwargs.get("_silent_refusal_retry_done")
                                ):
                                    log_silent_refusal(
                                        requested_model=model_name,
                                        finish=finish_fr,
                                        tag=_telem_tag,
                                    )
                                    fb = silent_refusal_fallback_model(model_name)
                                    if fb and fb != model_name:
                                        kw_retry = dict(kwargs)
                                        kw_retry["_silent_refusal_retry_done"] = True
                                        kw_retry["telemetry_tag"] = (
                                            f"{_telem_tag}_silent_fb" if _telem_tag else "silent_fb"
                                        )
                                        return await self.generate(
                                            prompt,
                                            model=fb,
                                            system_prompt=system_prompt,
                                            max_tokens=max_tokens,
                                            temperature=temperature,
                                            api_key_override=api_key_override,
                                            kv_cache_tail=kv_cache_tail,
                                            **kw_retry,
                                        )
                            except Exception as e:
                                logger.debug("silent_refusal_retry: %s", e)

                        usage_detail = record_openrouter_completion(
                            ok=True,
                            requested_model=model_name,
                            upstream_model=upstream,
                            latency_ms=elapsed_ms,
                            usage=raw_usage,
                            http_status=200,
                            content_chars=len(content),
                            telemetry=_telemetry,
                        )

                        # Log detailed cache telemetry at INFO level
                        _cached_tok = usage_detail.get("cached_prompt_tokens") if isinstance(usage_detail, dict) else None
                        _cache_write = usage_detail.get("cache_write_tokens") if isinstance(usage_detail, dict) else None
                        _reasoning_tok = usage_detail.get("reasoning_tokens") if isinstance(usage_detail, dict) else None
                        logger.info(
                            "llm_cache_telemetry session_len=%s model=%s upstream=%s prompt_tok=%s cached_tok=%s cache_write=%s reasoning=%s latency_ms=%s",
                            len(_session_id or ""),
                            model_name,
                            upstream or "?",
                            usage_detail.get("prompt_tokens", "?") if isinstance(usage_detail, dict) else "?",
                            _cached_tok if _cached_tok is not None else "?",
                            _cache_write if _cache_write is not None else "?",
                            _reasoning_tok if _reasoning_tok is not None else "?",
                            int(elapsed_ms),
                        )

                        # Увеличить счетчик использования
                        self.current_usage += 1

                        tt = usage_detail.get("total_tokens")
                        if tt is None and isinstance(raw_usage, dict):
                            tt = raw_usage.get("total_tokens", 0)

                        return {
                            "success": True,
                            "content": content,
                            "model": model_name,
                            "upstream_model": upstream,
                            "tokens_used": int(tt or 0),
                            "usage_detail": usage_detail,
                            "latency_ms": round(elapsed_ms, 2),
                        }
                    error_text = await response.text()
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    transient = int(response.status) in _OPENROUTER_TRANSIENT_HTTP
                    if transient and (http_try + 1) < http_retries:
                        logger.warning(
                            "OpenRouter HTTP %s — повтор %s/%s через %.1fs",
                            response.status,
                            http_try + 1,
                            http_retries,
                            http_gap,
                        )
                        last_http_err = {"error": error_text, "content": ""}
                        continue
                    if _looks_like_no_balance(int(response.status), error_text):
                        fb_cand: Optional[str] = None
                        if _no_balance_fallback_enabled() and not nb_done:
                            fb_cand = _no_balance_fallback_model()
                        try:
                            from core.admin_ops_notify import maybe_notify_openrouter_quota

                            maybe_notify_openrouter_quota(
                                http_status=int(response.status),
                                error_text=error_text,
                                model=requested_model_name,
                                fallback_model=fb_cand if fb_cand and fb_cand != model_name else None,
                            )
                        except Exception as e:
                            logger.debug("admin_ops_notify quota hook: %s", e)
                    record_openrouter_completion(
                        ok=False,
                        requested_model=model_name,
                        upstream_model=None,
                        latency_ms=elapsed_ms,
                        usage=None,
                        http_status=response.status,
                        error=error_text[:500],
                        content_chars=0,
                        telemetry=_telemetry,
                    )
                    logger.error("OpenRouter API error: %s - %s", response.status, error_text)
                    nb_done = bool(kwargs.get("_no_balance_retry_done"))
                    if (
                        _no_balance_fallback_enabled()
                        and not nb_done
                        and _looks_like_no_balance(int(response.status), error_text)
                    ):
                        fb_model = _no_balance_fallback_model()
                        if fb_model and fb_model != model_name:
                            cooldown = _no_balance_cooldown_sec()
                            if cooldown > 0:
                                self._no_balance_freeze_until = time.time() + float(cooldown)
                            logger.warning(
                                "OpenRouter no-balance fallback: %s -> %s (status=%s, cooldown=%ss)",
                                requested_model_name,
                                fb_model,
                                response.status,
                                cooldown,
                            )
                            fb = await self.generate(
                                prompt=prompt,
                                model=fb_model,
                                system_prompt=system_prompt,
                                max_tokens=max_tokens,
                                temperature=temperature,
                                vision_image_parts=vision_image_parts,
                                api_key_override=api_key_override,
                                _no_balance_retry_done=True,
                                **kwargs,
                            )
                            if isinstance(fb, dict):
                                fb["balance_fallback_used"] = True
                                fb["balance_fallback_from"] = requested_model_name
                                fb["balance_fallback_cooldown_sec"] = cooldown
                            return fb
                    return {"error": error_text, "content": ""}

            except Exception as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                err_s = str(e)
                if _LOCAL_TRANSIENT_ERR_RE.search(err_s):
                    self._http_session = None
                    self._http_lock = None
                    self._http_loop_id = None
                if (http_try + 1) < http_retries:
                    logger.warning(
                        "OpenRouter request failed (%s) — повтор %s/%s через %.1fs",
                        e,
                        http_try + 1,
                        http_retries,
                        http_gap,
                    )
                    last_http_err = {"error": err_s, "content": ""}
                    continue
                record_openrouter_completion(
                    ok=False,
                    requested_model=model_name,
                    upstream_model=None,
                    latency_ms=elapsed_ms,
                    usage=None,
                    http_status=None,
                    error=str(e),
                    content_chars=0,
                    telemetry=_telemetry,
                )
                logger.error("Error calling OpenRouter: %s", e)
                return {"error": str(e), "content": ""}

        return last_http_err

    async def generate_stream(
        self,
        prompt: str,
        model: str = None,
        system_prompt: str = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        api_key_override: Optional[str] = None,
        kv_cache_tail: str = "",
        *,
        cancel_event: Optional[asyncio.Event] = None,
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        on_reasoning_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """SSE stream=true; on_delta — content; on_reasoning_delta — CoT (OpenRouter reasoning map)."""
        api_key = (api_key_override or "").strip() or self._get_current_api_key()
        if not api_key:
            return {"error": "API key not configured", "content": ""}

        _telem_tag = (kwargs.pop("telemetry_tag", None) or "").strip() or None
        _telem_kind_in = (kwargs.pop("telemetry_kind", None) or "").strip() or None
        _telem_extra = kwargs.pop("telemetry_extra", None)
        _session_id = str(kwargs.pop("session_id", "") or "").strip()
        kwargs.pop("conversation_id", None)
        kv_cache_tail = str(kwargs.pop("kv_cache_tail", "") or "").strip() or kv_cache_tail

        model_name = (model or "").strip() or "deepseek/deepseek-v4-pro"
        requested_model_name = model_name
        from core.llm_telemetry import build_openrouter_telemetry

        _telemetry = build_openrouter_telemetry(
            tag=_telem_tag,
            kind=_telem_kind_in,
            session_id=_session_id,
            extra=_telem_extra if isinstance(_telem_extra, dict) else None,
            stream=True,
        )

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        if kv_cache_tail:
            messages.append({"role": "system", "content": kv_cache_tail})

        _no_payload = frozenset(
            {
                "api_key_override",
                "telemetry_kind",
                "telemetry_tag",
                "session_id",
                "conversation_id",
                "telemetry_extra",
                "cancel_event",
                "on_delta",
                "on_reasoning_delta",
            }
        )
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **{k: v for k, v in kwargs.items() if k not in _no_payload},
        }
        payload = _apply_env_generation_overrides(payload, tag=_telem_tag)
        for _k, v in extra_completion_body_fields(model_name).items():
            if _k not in payload:
                payload[_k] = v

        t0 = time.perf_counter()
        parts: List[str] = []
        finish_reason = ""
        try:
            session = await self._shared_http_session()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ManSio/gemma_agent",
                "X-Title": "Gemma Agent",
                "X-OpenRouter-Title": "Gemma Agent",
            }
            if _session_id and _session_headers_enabled():
                headers["X-Session-Id"] = _session_id

            async with session.post(self.api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    try:
                        from core.admin_ops_notify import maybe_notify_openrouter_quota

                        maybe_notify_openrouter_quota(
                            http_status=int(response.status),
                            error_text=error_text,
                            model=model_name,
                            fallback_model=None,
                        )
                    except Exception as e:
                        logger.debug("admin_ops_notify stream quota hook: %s", e)
                    record_openrouter_completion(
                        ok=False,
                        requested_model=model_name,
                        upstream_model=None,
                        latency_ms=elapsed_ms,
                        usage=None,
                        http_status=response.status,
                        error=error_text[:500],
                        content_chars=0,
                        telemetry=_telemetry,
                    )
                    return {"error": error_text, "content": ""}

                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    line_b = await response.content.readline()
                    if not line_b:
                        break
                    line = line_b.decode("utf-8", errors="replace")
                    fr = merge_stream_finish_reason(line)
                    if fr:
                        finish_reason = fr
                    chunk = parse_openrouter_sse_chunk(line)
                    if chunk.reasoning and on_reasoning_delta is not None:
                        await on_reasoning_delta(chunk.reasoning)
                    if chunk.content:
                        parts.append(chunk.content)
                        if on_delta is not None:
                            await on_delta(chunk.content)

            content = "".join(parts)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            cancelled = bool(cancel_event is not None and cancel_event.is_set())
            if finish_reason == "length" and content.strip():
                raw_sfx = os.getenv("OPENROUTER_LENGTH_FINISH_SUFFIX")
                if raw_sfx is None:
                    suffix = (
                        "[Ответ обрезан лимитом токенов модели. "
                        "Напишите «продолжи» или сократите запрос.]"
                    )
                else:
                    suffix = raw_sfx.strip()
                if suffix:
                    content = content.rstrip() + "\n\n" + suffix

            record_openrouter_completion(
                ok=bool(content.strip()) and not cancelled,
                requested_model=model_name,
                upstream_model=None,
                latency_ms=elapsed_ms,
                usage=None,
                http_status=200,
                content_chars=len(content),
                telemetry={**_telemetry, "cancelled": cancelled},
            )
            return {
                "success": bool(content.strip()),
                "content": content,
                "model": model_name,
                "cancelled": cancelled,
                "latency_ms": round(elapsed_ms, 2),
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            record_openrouter_completion(
                ok=False,
                requested_model=model_name,
                upstream_model=None,
                latency_ms=elapsed_ms,
                usage=None,
                http_status=None,
                error=str(e),
                content_chars=len("".join(parts)),
                telemetry=_telemetry,
            )
            logger.error("OpenRouter stream error: %s", e)
            return {"error": str(e), "content": "".join(parts)}
    
    async def generate_with_fallback(
        self, 
        prompt: str,
        fallback_model: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Генерация с fallback на резервную модель"""
        try:
            result = await self.generate(prompt, **kwargs)
            if result.get("success"):
                return result
        except Exception as e:
            logger.warning(f"Primary model failed: {e}")
        
        # Fallback — remove model from kwargs to avoid duplicate kwarg error
        kw_copy = dict(kwargs)
        kw_copy.pop("model", None)
        fallback = fallback_model or self.qwen_model
        logger.info(f"Fallback to model: {fallback}")
        return await self.generate(prompt, model=fallback, **kw_copy)
    
    def list_models(self) -> List[Dict[str, str]]:
        """Список доступных моделей"""
        return [
            {"id": self.free_model, "name": "Free (Production)", "type": "production"},
            {"id": self.qwen_model, "name": "Qwen (Development)", "type": "development"},
            {"id": self.dev_model, "name": "Gemini Dev", "type": "development"},
        ]
    
    def get_current_model_info(self) -> Dict[str, Any]:
        """Информация о текущей модели"""
        return {
            "model": self._get_current_model(),
            "api_key_set": bool(self._get_current_api_key()),
            "usage": self.current_usage,
            "threshold": self.model_threshold
        }
    
    def reset_usage(self):
        """Сброс счетчика использования"""
        self.current_usage = 0
        logger.info("Usage counter reset")
