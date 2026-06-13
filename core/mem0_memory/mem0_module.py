"""
Mem0 Memory Module — in-process кэш или Mem0 HTTP API v3 (облако или self-hosted).
Облако: MEM0_API_KEY + опционально MEM0_API_URL.
Локальный сервер (uvicorn и т.п.): MEM0_LOCAL=true, MEM0_API_URL=http://127.0.0.1:8001,
при пустом MEM0_API_KEY подставляется MEM0_LOCAL_API_KEY (по умолчанию local) в заголовок Authorization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

from core.error_analysis import record_error_event
from core.sensitive_export import mem0_path_log_facets

logger = logging.getLogger(__name__)

DEFAULT_MEM0_BASE = "https://api.mem0.ai"


def _normalize_mem0_api_key(raw: Optional[str]) -> str:
    """
    Убираем типичный мусор из .env / копипасты: кавычки, уже вшитый префикс Token/Bearer
    (иначе получится «Token Token xxx» → 401).
    Также: BOM, \\r, вторая строка в значении, хвост # комментарий в той же строке.
    """
    s = (raw or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.strip()
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    sl = s.lower()
    if sl.startswith("token "):
        s = s[6:].strip()
    elif sl.startswith("bearer "):
        s = s[7:].strip()
    return s.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _mem0_key_fingerprint(norm: str) -> Dict[str, Any]:
    """Безопасно для чата: только факт наличия и длина (без хеша/фрагментов ключа)."""
    if not norm:
        return {"configured": False}
    return {
        "configured": True,
        "key_len": len(norm),
    }


def mem0_operator_diagnostics() -> Dict[str, Any]:
    """
    Сводка для /admin_operator: какие ключи реально видит процесс (после нормализации),
    без утечки полного секрета. Сверяйте key_sha256_12 до/после смены ключа в Portainer.
    """
    raw_p = os.getenv("MEM0_API_KEY") or ""
    raw_m = os.getenv("MEM0_MIRROR_API_KEY") or ""
    norm_p = _normalize_mem0_api_key(raw_p)
    norm_m = _normalize_mem0_api_key(raw_m)
    scheme = (os.getenv("MEM0_AUTH_SCHEME") or "token").strip().lower() or "token"
    base = ((os.getenv("MEM0_API_URL") or "").strip() or DEFAULT_MEM0_BASE).rstrip("/")
    mb = (os.getenv("MEM0_MIRROR_API_URL") or "").strip()
    mirror_base = (mb.rstrip("/") if mb else base)
    mirror_write = _env_bool("MEM0_MIRROR_WRITE", False)
    local_flag = _env_bool("MEM0_LOCAL", False) or _env_bool("MEM0_SELF_HOSTED", False)
    url_set = bool((os.getenv("MEM0_API_URL") or "").strip())
    local_standalone = bool(local_flag and url_set and not norm_p)
    key_for_http = norm_p
    if not key_for_http and local_flag and url_set:
        key_for_http = _normalize_mem0_api_key(os.getenv("MEM0_LOCAL_API_KEY") or "local")
    mem0_http_enabled = bool(norm_p) or local_standalone
    hints: List[str] = []

    if raw_p and ("\n" in raw_p or "\r" in raw_p):
        hints.append("В MEM0_API_KEY есть перенос строки — оставьте ключ в одну строку в .env / в переменных контейнера.")
    if raw_m and ("\n" in raw_m or "\r" in raw_m):
        hints.append("В MEM0_MIRROR_API_KEY есть перенос строки — одна строка, без \\n.")
    if raw_p and "\ufeff" in raw_p:
        hints.append("В MEM0_API_KEY был BOM; файл .env лучше UTF-8 без BOM.")
    if norm_p and norm_m and norm_p == norm_m:
        hints.append("Primary и mirror ключи совпадают после очистки — второй ключ не нужен или скопирован ошибочно.")
    if mirror_write and not norm_m:
        hints.append("MEM0_MIRROR_WRITE=true, но MEM0_MIRROR_API_KEY пуст — выключите запись в mirror или задайте ключ.")
    if scheme not in {"token", "bearer", "jwt"}:
        hints.append(f"MEM0_AUTH_SCHEME={scheme!r} необычен; для облака Mem0 обычно token или bearer.")
    if norm_m and mirror_write:
        hints.append(
            "При 401 на запись: смотрите лог key_role=mirror — тогда правьте MEM0_MIRROR_API_KEY или MEM0_MIRROR_WRITE=false."
        )
    if local_flag and not url_set:
        hints.append("MEM0_LOCAL=true, но MEM0_API_URL пуст — задайте URL self-hosted API (например http://127.0.0.1:8001).")
    if local_standalone:
        hints.append(
            "Режим локального Mem0: MEM0_API_KEY пуст, используется MEM0_LOCAL_API_KEY (или значение по умолчанию local) в Authorization; сервер должен принимать этот токен или не проверять его."
        )

    return {
        "mem0_cloud_enabled": bool(norm_p),
        "mem0_http_enabled": mem0_http_enabled,
        "mem0_local_standalone": local_standalone,
        "auth_scheme": scheme,
        "primary_api_url": base,
        "mirror_api_url": mirror_base if norm_m else None,
        "primary_key": _mem0_key_fingerprint(key_for_http),
        "mirror_key": _mem0_key_fingerprint(norm_m) if norm_m else {"configured": False},
        "mirror_write": mirror_write,
        "hints": hints,
        "fix": "Сверьте key_len после смены ключа; /admin_connectivity — живой запрос к API.",
    }


def load_mem0_config_from_env() -> Optional[Dict[str, str]]:
    """Собрать конфиг из окружения (как в main). Без ключа — только in-memory, кроме MEM0_LOCAL + MEM0_API_URL."""
    key = _normalize_mem0_api_key(os.getenv("MEM0_API_KEY"))
    url = (os.getenv("MEM0_API_URL") or "").strip()
    local = _env_bool("MEM0_LOCAL", False) or _env_bool("MEM0_SELF_HOSTED", False)
    if not key:
        if local and url:
            key = _normalize_mem0_api_key(os.getenv("MEM0_LOCAL_API_KEY") or "local")
        if not key:
            return None
    cfg: Dict[str, str] = {"mem0_api_key": key}
    if url:
        cfg["mem0_api_url"] = url.rstrip("/")
    tag = (os.getenv("MEM0_SOURCE_TAG") or "").strip()
    if tag:
        cfg["mem0_source_tag"] = tag
    mkey = _normalize_mem0_api_key(os.getenv("MEM0_MIRROR_API_KEY"))
    if mkey:
        cfg["mem0_mirror_api_key"] = mkey
    murl = (os.getenv("MEM0_MIRROR_API_URL") or "").strip()
    if murl:
        cfg["mem0_mirror_api_url"] = murl.rstrip("/")
    return cfg


def _add_payload_user_id(user_id: str) -> str:
    """Для записи в Mem0 всегда реальный пользователь (Telegram id и т.д.)."""
    return str(user_id).strip()


def _is_self_hosted_mem0_base(base: Optional[str]) -> bool:
    """Облако api.mem0.ai — формат messages/filters; свой mem0_server — text + user_id."""
    b = (base or "").strip().lower()
    if not b:
        return False
    return "api.mem0.ai" not in b


def _extract_text_from_messages_payload(payload: Dict[str, Any]) -> str:
    msgs = (payload or {}).get("messages")
    if not isinstance(msgs, list):
        return ""
    parts = [
        str(m.get("content") or "").strip()
        for m in msgs
        if isinstance(m, dict) and str(m.get("content") or "").strip()
    ]
    return "\n".join(parts).strip()


def _coerce_payload_for_self_hosted(path: str, payload: Dict[str, Any], base: Optional[str]) -> Dict[str, Any]:
    """
    mem0_server на deploy-host (/opt/mem0_local): add ждёт поле text, search — user_id в корне JSON.
    Бот шлёт облачный v3 (messages[], filters{}) → запись не сохранялась, поиск пустой.
    """
    if not _is_self_hosted_mem0_base(base):
        return payload
    p = dict(payload or {})
    low = str(path or "").lower()
    if "/memories/add" in low and not str(p.get("text") or "").strip():
        text = _extract_text_from_messages_payload(p)
        if text:
            p["text"] = text[:8000]
    if "/memories/search" in low and not str(p.get("user_id") or "").strip():
        flt = p.get("filters")
        if isinstance(flt, dict):
            uid = flt.get("user_id")
            if uid:
                p["user_id"] = str(uid).strip()
            else:
                or_list = flt.get("OR")
                if isinstance(or_list, list):
                    for item in or_list:
                        if isinstance(item, dict) and item.get("user_id"):
                            p["user_id"] = str(item["user_id"]).strip()
                            break
        if not str(p.get("limit") or "").strip() and p.get("top_k"):
            try:
                p["limit"] = max(1, min(100, int(p.get("top_k"))))
            except (TypeError, ValueError):
                pass
    return p


def _memory_text_from_search_row(row: Dict[str, Any]) -> str:
    """Облако v3: memory; self-hosted mem0_server: text."""
    return str(row.get("memory") or row.get("text") or "").strip()


def _merge_search_payloads(
    *responses: Any,
    top_k: int,
) -> List[Dict[str, Any]]:
    """Объединить результаты search из нескольких проектов Mem0 (разные API keys)."""
    best: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for resp in responses:
        if not isinstance(resp, dict):
            continue
        for r in resp.get("results") or []:
            if not isinstance(r, dict):
                continue
            mem = _memory_text_from_search_row(r)
            if not mem:
                continue
            norm = mem.lower()
            try:
                sc = float(r.get("score") or 0.0)
            except (TypeError, ValueError):
                sc = 0.0
            prev = best.get(norm)
            if prev is None or sc > prev[0]:
                best[norm] = (
                    sc,
                    {
                        "type": "mem0",
                        "content": mem,
                        "score": r.get("score"),
                        "id": r.get("id"),
                    },
                )
    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)
    return [item for _, item in ranked[:top_k]]


class Mem0MemoryModule:
    """Долговременная память: локально или Mem0 Cloud (add/search/list v3)."""

    def __init__(self, config: Optional[Dict[str, str]] = None):
        self.config = config or {}
        self.facts: Dict[str, List[Dict[str, Any]]] = {}
        self._user_system = None
        self._psychology_engine = None
        self._digital_twin = None
        self._api_key = _normalize_mem0_api_key(self.config.get("mem0_api_key"))
        self._base = (self.config.get("mem0_api_url") or DEFAULT_MEM0_BASE).rstrip("/")
        self._source_tag = (self.config.get("mem0_source_tag") or "").strip()
        self._mirror_key = _normalize_mem0_api_key(self.config.get("mem0_mirror_api_key"))
        self._mirror_base = (
            (self.config.get("mem0_mirror_api_url") or "").strip().rstrip("/")
            or self._base
        )
        self._mirror_write = _env_bool("MEM0_MIRROR_WRITE", False)
        self._session: Optional[aiohttp.ClientSession] = None
        self._pending_user: Dict[str, str] = {}
        try:
            self._top_k = max(1, min(100, int(os.getenv("MEM0_TOP_K", "10"))))
        except ValueError:
            self._top_k = 10
        try:
            self._threshold = float(os.getenv("MEM0_SEARCH_THRESHOLD", "0.1"))
        except ValueError:
            self._threshold = 0.1
        _raw_or = os.getenv("MEM0_SEARCH_OR_USER_IDS", "")
        self._search_or_user_ids = [x.strip() for x in _raw_or.split(",") if x.strip()]
        # Префикс путей API (Mem0 Platform: v3). Пустая строка в MEM0_API_PREFIX → /memories/... от корня base URL.
        _raw_pf = os.getenv("MEM0_API_PREFIX")
        if _raw_pf is None:
            self._mem_api_root = "v3"
        else:
            self._mem_api_root = _raw_pf.strip().strip("/")
        self._local_simple_compat = _env_bool("MEM0_LOCAL_SIMPLE_COMPAT", True)

    def _mem_path(self, tail: str) -> str:
        """Собрать путь вроде /v3/memories/add/; tail — memories/add/ или memories/search/."""
        t = tail.strip().lstrip("/")
        if not t.endswith("/"):
            t += "/"
        if self._mem_api_root:
            return f"/{self._mem_api_root}/{t}"
        return f"/{t}"

    def _search_filters(self, user_id: str) -> Dict[str, Any]:
        """Фильтр поиска/листа: свой user_id + опционально общие сущности (напр. genesis-project)."""
        uid = str(user_id).strip()
        extras = [x for x in self._search_or_user_ids if x != uid]
        if not extras:
            return {"user_id": uid}
        return {"OR": [{"user_id": uid}] + [{"user_id": e} for e in extras]}

    def _local_simple_retry_target(
        self, path: str, payload: Dict[str, Any]
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Совместимость с self-hosted Mem0-стабом, где API только /add /search /delete
        и простой JSON (без /v3/memories/*).
        """
        if not self._local_simple_compat:
            return None
        p = str(path or "").lower()
        if "/memories/search" in p:
            q = str((payload or {}).get("query") or "").strip()
            if not q:
                msgs = (payload or {}).get("messages")
                if isinstance(msgs, list):
                    for m in reversed(msgs):
                        if isinstance(m, dict) and str(m.get("content") or "").strip():
                            q = str(m.get("content")).strip()
                            break
            return "/search", {"query": q or "."}
        if "/memories/add" in p:
            text = str((payload or {}).get("text") or "").strip()
            if not text:
                msgs = (payload or {}).get("messages")
                if isinstance(msgs, list):
                    parts = [
                        str(m.get("content") or "").strip()
                        for m in msgs
                        if isinstance(m, dict) and str(m.get("content") or "").strip()
                    ]
                    text = "\n".join(parts).strip()
            return "/add", {"text": (text or ".")[:8000]}
        if "/memories/delete" in p:
            rid = (payload or {}).get("id")
            if rid is not None:
                try:
                    return "/delete", {"id": int(rid)}
                except (TypeError, ValueError):
                    return None
        return None

    @property
    def _cloud(self) -> bool:
        return bool(self._api_key)

    def _headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        key = _normalize_mem0_api_key(api_key or self._api_key)
        scheme = (os.getenv("MEM0_AUTH_SCHEME") or "token").strip().lower()
        if scheme in {"bearer", "jwt"}:
            auth = f"Bearer {key}"
        else:
            auth = f"Token {key}"
        return {
            "Authorization": auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _metadata(self) -> Optional[Dict[str, Any]]:
        if not self._source_tag:
            return None
        return {"source": self._source_tag}

    def _key_label(self, api_key: Optional[str], base: Optional[str]) -> str:
        """Для логов: какой ключ ходит в Mem0 (primary vs mirror)."""
        b = (base or self._base).rstrip("/")
        prim = self._base.rstrip("/")
        mk = _normalize_mem0_api_key(api_key) if api_key else ""
        if self._mirror_key and mk == self._mirror_key:
            return "mirror"
        if b != prim:
            return "mirror"
        return "primary"

    def disable_mirror_write_runtime(self, reason: str) -> None:
        """Отключить запись в mirror (оставить поиск, если ключи разные). Чтобы не спамить 401 при битом MEM0_MIRROR_API_KEY."""
        if not self._mirror_write:
            return
        self._mirror_write = False
        logger.warning(
            "MEM0_MIRROR_WRITE снят на лету: %s (поиск по mirror не отключается). "
            "Исправьте MEM0_MIRROR_API_KEY или выставьте MEM0_MIRROR_WRITE=false.",
            reason,
            extra={"gemma_event": "mem0_mirror_write_disabled", "reason": reason[:500]},
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        base: Optional[str] = None,
        api_key: Optional[str] = None,
        _allow_local_retry: bool = True,
    ) -> Any:
        session = await self._ensure_session()
        root = (base or self._base).rstrip("/")
        payload = _coerce_payload_for_self_hosted(path, payload, root)
        url = urljoin(root + "/", path.lstrip("/"))
        path_kind, path_len = mem0_path_log_facets(path)
        try:
            async with session.post(url, headers=self._headers(api_key), json=payload) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    if resp.status == 404 and _allow_local_retry:
                        alt = self._local_simple_retry_target(path, payload)
                        if alt:
                            alt_path, alt_payload = alt
                            alt_kind, _ = mem0_path_log_facets(alt_path)
                            logger.info(
                                "Mem0 404 path_kind=%s -> retry path_kind=%s (local simple compat)",
                                path_kind,
                                alt_kind,
                            )
                            return await self._post_json(
                                alt_path,
                                alt_payload,
                                base=base,
                                api_key=api_key,
                                _allow_local_retry=False,
                            )
                    if resp.status == 401:
                        k = _normalize_mem0_api_key(api_key or self._api_key)
                        role = self._key_label(api_key, base)
                        logger.warning(
                            "Mem0 HTTP 401 path_kind=%s path_len=%s key_role=%s body_len=%s",
                            path_kind,
                            path_len,
                            role,
                            len(text or ""),
                            extra={
                                "gemma_event": "mem0_http_401",
                                "key_role": role,
                                "path_kind": path_kind,
                                "path_len": path_len,
                            },
                        )
                        if "/memories/add/" in path and role == "mirror":
                            self.disable_mirror_write_runtime("HTTP 401 на memories/add (mirror)")
                        elif "/memories/add/" in path and role == "primary":
                            record_error_event(
                                "mem0",
                                "Mem0 HTTP 401 на запись (primary): проверьте MEM0_API_KEY и MEM0_AUTH_SCHEME",
                                extra={
                                    "path_kind": path_kind,
                                    "path_len": path_len,
                                    "body_len": len(text or ""),
                                },
                            )
                    else:
                        logger.warning(
                            "Mem0 HTTP %s path_kind=%s path_len=%s body_len=%s",
                            resp.status,
                            path_kind,
                            path_len,
                            len(text or ""),
                        )
                    return None
                if not text:
                    return {}
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Mem0 invalid JSON path_kind=%s path_len=%s", path_kind, path_len)
                    return None
                if (
                    _allow_local_retry
                    and isinstance(data, dict)
                    and data.get("success") is False
                    and "/memories/add" in str(path).lower()
                    and _is_self_hosted_mem0_base(root)
                    and _extract_text_from_messages_payload(payload)
                ):
                    retry_p = dict(payload)
                    retry_p["text"] = _extract_text_from_messages_payload(payload)[:8000]
                    logger.info("Mem0 add success=false -> retry with text (self-hosted compat)")
                    return await self._post_json(
                        path,
                        retry_p,
                        base=base,
                        api_key=api_key,
                        _allow_local_retry=False,
                    )
                return data
        except aiohttp.ClientError as e:
            logger.warning(
                "Mem0 request failed path_kind=%s err=%s",
                path_kind,
                type(e).__name__,
            )
            return None

    def _post_json_sync(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        params: Optional[Dict[str, Any]] = None,
        base: Optional[str] = None,
        api_key: Optional[str] = None,
        _allow_local_retry: bool = True,
    ) -> Any:
        import requests

        root = (base or self._base).rstrip("/")
        payload = _coerce_payload_for_self_hosted(path, payload, root)
        url = urljoin(root + "/", path.lstrip("/"))
        path_kind, path_len = mem0_path_log_facets(path)
        try:
            r = requests.post(
                url,
                headers=self._headers(api_key),
                json=payload,
                params=params or None,
                timeout=25,
            )
            if r.status_code >= 400:
                if r.status_code == 404 and _allow_local_retry:
                    alt = self._local_simple_retry_target(path, payload)
                    if alt:
                        alt_path, alt_payload = alt
                        alt_kind, _ = mem0_path_log_facets(alt_path)
                        logger.info(
                            "Mem0 HTTP 404 path_kind=%s -> retry path_kind=%s (local simple compat, sync)",
                            path_kind,
                            alt_kind,
                        )
                        return self._post_json_sync(
                            alt_path,
                            alt_payload,
                            params=None,
                            base=base,
                            api_key=api_key,
                            _allow_local_retry=False,
                        )
                if r.status_code == 401:
                    k = _normalize_mem0_api_key(api_key or self._api_key)
                    rb = (r.text or "")[:400]
                    role = self._key_label(api_key, base)
                    logger.warning(
                        "Mem0 HTTP 401 path_kind=%s path_len=%s key_role=%s body_len=%s",
                        path_kind,
                        path_len,
                        role,
                        len(rb or ""),
                        extra={
                            "gemma_event": "mem0_http_401",
                            "key_role": role,
                            "path_kind": path_kind,
                            "path_len": path_len,
                        },
                    )
                    if "/memories/add/" in path and role == "mirror":
                        self.disable_mirror_write_runtime("HTTP 401 на memories/add (mirror, sync)")
                    elif "/memories/add/" in path and role == "primary":
                        record_error_event(
                            "mem0",
                            "Mem0 HTTP 401 на запись (primary, sync): проверьте MEM0_API_KEY",
                            extra={
                                "path_kind": path_kind,
                                "path_len": path_len,
                                "body_len": len(rb or ""),
                            },
                        )
                else:
                    logger.warning(
                        "Mem0 HTTP %s path_kind=%s path_len=%s body_len=%s",
                        r.status_code,
                        path_kind,
                        path_len,
                        len(r.text or ""),
                    )
                return None
            if not (r.text or "").strip():
                return {}
            data = r.json()
            if (
                _allow_local_retry
                and isinstance(data, dict)
                and data.get("success") is False
                and "/memories/add" in str(path).lower()
                and _is_self_hosted_mem0_base(root)
                and _extract_text_from_messages_payload(payload)
            ):
                retry_p = dict(payload)
                retry_p["text"] = _extract_text_from_messages_payload(payload)[:8000]
                logger.info("Mem0 add success=false -> retry with text (self-hosted compat, sync)")
                return self._post_json_sync(
                    path,
                    retry_p,
                    params=params,
                    base=base,
                    api_key=api_key,
                    _allow_local_retry=False,
                )
            return data
        except requests.RequestException as e:
            logger.warning(
                "Mem0 sync request failed path_kind=%s err=%s",
                path_kind,
                type(e).__name__,
            )
            return None

    async def on_user_message(self, user_id: str, message: str) -> List[str]:
        from core.utils.llm_sanitize import sanitize_llm_value
        message = sanitize_llm_value(message)
        if self._cloud:
            if message and str(user_id).strip():
                self._pending_user[str(user_id)] = str(message)[:8000]
            return [message]
        if user_id not in self.facts:
            self.facts[user_id] = []
        self.facts[user_id].append(
            {
                "type": "message",
                "content": message,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return [message]

    async def on_before_response(self, user_id: str, query: str) -> List[Dict[str, Any]]:
        if self._cloud:
            q = (query or "").strip() or "."
            body = {
                "query": q,
                "filters": self._search_filters(user_id),
                "top_k": self._top_k,
                "threshold": self._threshold,
            }
            if self._mirror_key:
                prim, mir = await asyncio.gather(
                    self._post_json(self._mem_path("memories/search"), body),
                    self._post_json(
                        self._mem_path("memories/search"),
                        body,
                        base=self._mirror_base,
                        api_key=self._mirror_key,
                    ),
                )
                return _merge_search_payloads(prim, mir, top_k=self._top_k)
            data = await self._post_json(self._mem_path("memories/search"), body)
            if not isinstance(data, dict):
                return []
            return _merge_search_payloads(data, top_k=self._top_k)

        facts = self.facts.get(user_id, [])
        return facts[-10:] if facts else []

    async def on_after_response(self, user_id: str, response: str) -> List[str]:
        if self._cloud:
            uid = str(user_id)
            user_text = (self._pending_user.pop(uid, None) or "").strip()
            assistant = (response or "").strip()[:8000]
            if not assistant:
                return [response]
            messages: List[Dict[str, str]] = []
            if user_text:
                messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant})
            payload: Dict[str, Any] = {
                "user_id": _add_payload_user_id(uid),
                "messages": messages,
            }
            md = self._metadata()
            if md:
                payload["metadata"] = md
            await self._post_json(self._mem_path("memories/add"), payload)
            if self._mirror_write and self._mirror_key:
                await self._post_json(
                    self._mem_path("memories/add"),
                    payload,
                    base=self._mirror_base,
                    api_key=self._mirror_key,
                )
            return [response]

        if user_id not in self.facts:
            self.facts[user_id] = []
        self.facts[user_id].append(
            {
                "type": "response",
                "content": response,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return [response]

    def get_facts(self, user_id: str, query: str = None) -> List[Dict[str, Any]]:
        if self._cloud:
            if query:
                body = {
                    "query": str(query),
                    "filters": self._search_filters(user_id),
                    "top_k": self._top_k,
                    "threshold": self._threshold,
                }
                if self._mirror_key:
                    a = self._post_json_sync(self._mem_path("memories/search"), body)
                    b = self._post_json_sync(
                        self._mem_path("memories/search"),
                        body,
                        base=self._mirror_base,
                        api_key=self._mirror_key,
                    )
                    merged = _merge_search_payloads(a, b, top_k=self._top_k)
                    return [{"type": "mem0", "content": x["content"], "id": x.get("id")} for x in merged]
                data = self._post_json_sync(self._mem_path("memories/search"), body)
                if not isinstance(data, dict):
                    return []
                return [
                    {"type": "mem0", "content": _memory_text_from_search_row(r), "id": r.get("id")}
                    for r in (data.get("results") or [])
                    if isinstance(r, dict) and _memory_text_from_search_row(r)
                ]
            body = {"filters": self._search_filters(user_id)}
            params = {"page": 1, "page_size": 100}
            if self._mirror_key:
                a = self._post_json_sync(self._mem_path("memories"), body, params=params)
                b = self._post_json_sync(
                    self._mem_path("memories"),
                    body,
                    params=params,
                    base=self._mirror_base,
                    api_key=self._mirror_key,
                )
                merged = _merge_search_payloads(a, b, top_k=100)
                return [{"type": "mem0", "content": x["content"], "id": x.get("id")} for x in merged]
            data = self._post_json_sync(self._mem_path("memories"), body, params=params)
            if not isinstance(data, dict):
                return []
            return [
                {"type": "mem0", "content": _memory_text_from_search_row(r), "id": r.get("id")}
                for r in (data.get("results") or [])
                if isinstance(r, dict) and _memory_text_from_search_row(r)
            ]

        facts = self.facts.get(user_id, [])
        if query:
            return [f for f in facts if query.lower() in str(f.get("content", "")).lower()]
        return facts

    def summarize_user_memory(self, user_id: str) -> Dict[str, Any]:
        if self._cloud:
            facts = self.get_facts(user_id)
            return {
                "user_id": user_id,
                "fact_count": len(facts),
                "recent_facts": facts[-5:] if facts else [],
                "backend": "mem0_cloud",
            }
        facts = self.facts.get(user_id, [])
        return {
            "user_id": user_id,
            "fact_count": len(facts),
            "recent_facts": facts[-5:] if facts else [],
            "backend": "local",
        }

    def delete_facts_matching_text(self, user_id: str, text: str) -> int:
        """
        Удалить записи Mem0, где текст содержит substring (без учёта регистра).
        Возвращает число успешных delete-вызовов.
        """
        uid = str(user_id or "").strip()
        needle = (text or "").strip().lower()
        if not uid or not needle:
            return 0
        if not self._cloud:
            if uid not in self.facts:
                return 0
            before = len(self.facts[uid])
            self.facts[uid] = [
                f
                for f in self.facts[uid]
                if needle not in str(f.get("content", "")).lower()
            ]
            return before - len(self.facts[uid])

        ids_to_delete: Dict[str, Any] = {}
        for q in (text, None):
            rows = self.get_facts(uid, query=q if q else None)
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                content = str(row.get("content") or "").lower()
                if needle not in content:
                    continue
                rid = row.get("id")
                if rid is not None:
                    ids_to_delete[str(rid)] = rid

        deleted = 0
        for rid in ids_to_delete.values():
            if self._delete_memory_id(uid, rid):
                deleted += 1
        return deleted

    def _delete_memory_id(self, user_id: str, memory_id: Any) -> bool:
        if memory_id is None:
            return False
        uid = _add_payload_user_id(str(user_id))
        attempts = (
            {"memory_id": str(memory_id)},
            {"id": memory_id},
            {"memory_ids": [str(memory_id)]},
            {"user_id": uid, "id": memory_id},
        )
        for payload in attempts:
            data = self._post_json_sync(self._mem_path("memories/delete"), dict(payload))
            if isinstance(data, dict) and (
                data.get("success") is True
                or data.get("deleted") is True
                or data.get("message") == "Memory deleted successfully"
            ):
                return True
        return False

    def add_structured_facts(self, user_id: str, facts: List[Dict[str, Any]]) -> None:
        if self._cloud:
            lines = []
            for fact in facts or []:
                if not isinstance(fact, dict):
                    continue
                field = fact.get("field")
                content = fact.get("content")
                lines.append(f"{field}: {content}")
            if not lines:
                return
            payload: Dict[str, Any] = {
                "user_id": _add_payload_user_id(str(user_id)),
                "messages": [{"role": "user", "content": "\n".join(lines)}],
                "infer": False,
            }
            md = self._metadata()
            if md:
                payload["metadata"] = md
            self._post_json_sync(self._mem_path("memories/add"), payload)
            if self._mirror_write and self._mirror_key:
                self._post_json_sync(
                    self._mem_path("memories/add"),
                    payload,
                    base=self._mirror_base,
                    api_key=self._mirror_key,
                )
            return
        if user_id not in self.facts:
            self.facts[user_id] = []
        for fact in facts or []:
            if isinstance(fact, dict):
                self.facts[user_id].append(fact)

    def set_dependencies(self, user_system, psychology_engine, digital_twin):
        """Сохранить ссылки на зависимые модули для кросс-модульного контекста."""
        self._user_system = user_system
        self._psychology_engine = psychology_engine
        self._digital_twin = digital_twin
