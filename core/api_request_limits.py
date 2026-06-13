"""HTTP API request size limits from .env."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

from core.number_parse import parse_env_int

logger = logging.getLogger(__name__)

_DEFAULT_MESSAGE_MAX = 10000
_DEFAULT_RELAY_META_JSON_MAX = 4096
_DEFAULT_REQUEST_BODY_MAX = 65536


def api_message_max_chars() -> int:
    """Max characters for chat/relay/probe message fields."""
    v = parse_env_int("API_MESSAGE_MAX_CHARS", _DEFAULT_MESSAGE_MAX)
    return max(256, min(100_000, v))


def api_relay_meta_max_json_chars() -> int:
    """Max serialized JSON size for BotRelayRequest.meta."""
    v = parse_env_int("API_RELAY_META_MAX_JSON_CHARS", _DEFAULT_RELAY_META_JSON_MAX)
    return max(64, min(32_768, v))


def api_max_request_body_bytes() -> int:
    """Reject HTTP bodies larger than this before handler runs."""
    v = parse_env_int("API_MAX_REQUEST_BODY_BYTES", _DEFAULT_REQUEST_BODY_MAX)
    return max(4096, min(1_048_576, v))


API_MESSAGE_MAX_CHARS = api_message_max_chars()


def validate_relay_meta(meta: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pydantic validator: cap relay meta JSON size."""
    if meta is None:
        return meta
    if not isinstance(meta, dict):
        raise ValueError("meta must be an object")
    try:
        blob = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        raise ValueError("meta must be JSON-serializable") from e
    limit = api_relay_meta_max_json_chars()
    if len(blob) > limit:
        raise ValueError(f"meta JSON exceeds {limit} characters")
    return meta


class RequestBodySizeLimitMiddleware:
    """ASGI middleware rejecting oversized HTTP request bodies."""

    def __init__(self, app: Callable, max_bytes: Optional[int] = None) -> None:
        self.app = app
        self.max_bytes = max_bytes if max_bytes is not None else api_max_request_body_bytes()

    async def _send_413(self, send: Callable) -> None:
        payload = json.dumps(
            {"detail": f"Request body exceeds {self.max_bytes} bytes"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._send_413(send)
                    return
            except ValueError:
                pass

        received = 0
        rejected = False

        async def limited_receive() -> Dict[str, Any]:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                received += len(body)
                if received > self.max_bytes:
                    rejected = True
                    await self._send_413(send)
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        await self.app(scope, limited_receive, send)
