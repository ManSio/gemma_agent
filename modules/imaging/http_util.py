"""HTTP upload helper for external imaging APIs."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def post_image_file(
    endpoint: str,
    file_path: str,
    *,
    field_name: str = "file",
    timeout_sec: float = 60.0,
    extra_form: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    try:
        import aiohttp
    except ImportError as e:
        return {"ok": False, "error": f"aiohttp unavailable: {e}"}
    url = (endpoint or "").strip()
    if not url:
        return {"ok": False, "error": "endpoint empty"}
    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": "file not found"}
    timeout = aiohttp.ClientTimeout(total=max(10.0, min(timeout_sec, 180.0)))
    form = aiohttp.FormData()
    form.add_field(field_name, path.read_bytes(), filename=path.name, content_type="application/octet-stream")
    for k, v in (extra_form or {}).items():
        form.add_field(k, str(v))
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    return {"ok": False, "error": f"http {resp.status}", "body": body[:500]}
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    data = {"text": body}
                if isinstance(data, dict):
                    data.setdefault("ok", True)
                    return data
                return {"ok": True, "data": data}
    except Exception as e:
        logger.warning("post_image_file %s: %s", url, e)
        return {"ok": False, "error": str(e)}
