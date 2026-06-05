"""
Минимальная заглушка HTTP API Mem0 Platform **v3** для локальной отладки с gemma_bot.

Реализует те же пути, что ждёт core/mem0_memory/mem0_module.py:
  POST /v3/memories/search/
  POST /v3/memories/add/
  POST /v3/memories/   (список с query page, page_size)

По умолчанию данные **сохраняются на диск** (JSON) и поднимаются после перезапуска процесса.
Поиск — простое вхождение подстроки без эмбеддингов. Для продакшена используйте облако Mem0
или полноценный self-hosted стек (см. https://docs.mem0.ai/).

Переменные окружения:
  MEM0_STUB_PERSIST — true/false (по умолчанию true). false — только RAM, как раньше.
  MEM0_STUB_DATA_PATH — путь к JSON (по умолчанию ../data/mem0_stub_store.json от этого файла).

Зависимости:
  pip install fastapi uvicorn

Запуск (из каталога scripts/ репозитория):
  cd /path/to/gemma_bot/scripts
  ../venv/bin/python -m uvicorn mem0_platform_stub:app --host 0.0.0.0 --port 8001

Заголовок Authorization: Token <любой_текст> — принимается без проверки ключа.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header
from pydantic import BaseModel

# user_id -> list of memories
_store: Dict[str, List[Dict[str, Any]]] = {}
_store_lock = threading.Lock()
_persist_enabled = True
_data_file: Path


def _default_data_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "mem0_stub_store.json"


def _env_bool(name: str, default: bool = True) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _init_paths() -> Path:
    global _persist_enabled, _data_file
    _persist_enabled = _env_bool("MEM0_STUB_PERSIST", True)
    raw = (os.getenv("MEM0_STUB_DATA_PATH") or "").strip()
    _data_file = Path(raw).expanduser() if raw else _default_data_path()
    return _data_file


def _load_store() -> None:
    global _store
    if not _persist_enabled:
        return
    path = _data_file
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        _store = {}
        return
    try:
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        _store = {}
        return
    if not isinstance(raw, dict):
        _store = {}
        return
    out: Dict[str, List[Dict[str, Any]]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = [x for x in v if isinstance(x, dict)]
    _store = out


def _persist_store() -> None:
    if not _persist_enabled:
        return
    path = _data_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(_store, ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8") as f:
        f.write(payload)
    tmp.replace(path)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _init_paths()
    with _store_lock:
        _load_store()
    yield
    # optional flush on shutdown (add уже пишет после каждой записи)
    if _persist_enabled:
        with _store_lock:
            try:
                _persist_store()
            except OSError:
                pass


_init_paths()

app = FastAPI(title="Mem0 Platform v3 stub", version="0.2.0", lifespan=_lifespan)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_user_id_from_filters(filters: Any) -> Optional[str]:
    if not isinstance(filters, dict):
        return None
    if "user_id" in filters and isinstance(filters["user_id"], str):
        return filters["user_id"].strip()
    or_list = filters.get("OR")
    if isinstance(or_list, list):
        for item in or_list:
            if isinstance(item, dict) and isinstance(item.get("user_id"), str):
                return str(item["user_id"]).strip()
    return None


@app.get("/docs")
async def docs_redirect():
    return {"message": "OpenAPI at /openapi.json"}


class SearchBody(BaseModel):
    query: str
    filters: Dict[str, Any]
    top_k: int = 10
    threshold: float = 0.1


@app.post("/v3/memories/search/")
async def memories_search(body: SearchBody, authorization: Optional[str] = Header(None)):
    uid = _extract_user_id_from_filters(body.filters)
    with _store_lock:
        rows = list(_store.get(uid or "", []))
    q = (body.query or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for r in rows:
        mem = str(r.get("memory") or "")
        if not q or q in mem.lower():
            score = 0.9 if q and q in mem.lower() else 0.5
            if score >= body.threshold:
                out.append({**r, "score": score})
    out.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return {"results": out[: max(1, min(100, body.top_k))]}


class AddBody(BaseModel):
    user_id: str
    messages: List[Dict[str, str]]
    metadata: Optional[Dict[str, Any]] = None
    infer: bool = True


@app.post("/v3/memories/add/")
async def memories_add(body: AddBody, authorization: Optional[str] = Header(None)):
    uid = str(body.user_id).strip()
    parts: List[str] = []
    for m in body.messages or []:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}" if role else content)
    text = "\n".join(parts).strip()[:16000]
    if not text:
        return {"status": "OK", "results": []}
    mid = str(uuid.uuid4())
    rec = {
        "id": mid,
        "memory": text,
        "metadata": body.metadata or {},
        "score": 1.0,
        "created_at": _now(),
        "updated_at": _now(),
        "categories": [],
    }
    with _store_lock:
        if uid not in _store:
            _store[uid] = []
        _store[uid].append(rec)
        try:
            _persist_store()
        except OSError:
            pass
    return {"status": "OK", "results": [{"id": mid, "memory": text}]}


class MemoriesListBody(BaseModel):
    filters: Dict[str, Any]


@app.post("/v3/memories/")
async def memories_list(
    body: MemoriesListBody,
    page: int = 1,
    page_size: int = 100,
    authorization: Optional[str] = Header(None),
):
    uid = _extract_user_id_from_filters(body.filters)
    with _store_lock:
        rows = list(_store.get(uid or "", []))
    start = max(0, (page - 1) * page_size)
    end = start + max(1, min(1000, page_size))
    slice_rows = rows[start:end]
    return {"results": slice_rows}

