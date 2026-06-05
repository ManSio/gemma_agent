"""
Локальный Mem0-compatible сервер для gemma_bot (POST /v3/memories/add|search).

По умолчанию **без LLM rewrite** — текст сохраняется как есть (исправляет мусор llama-8b).
Опционально: MEM0_LLM_REWRITE=true + deepseek flash + provider DeepSeek/baidu.

Env (файл /opt/mem0_local/.env или export):
  OPENROUTER_API_KEY          — обязателен для embeddings
  MEM0_DB_PATH                — default /opt/mem0_local/memory.db
  MEM0_EMBED_MODEL            — default openai/text-embedding-3-small
  MEM0_LLM_REWRITE            — false (default): сохранять req.text без сжатия LLM
  MEM0_LLM_REWRITE_MIN_CHARS  — rewrite только если текст длиннее (default 400)
  MEM0_LLM_MODEL              — default deepseek/deepseek-v4-flash
  MEM0_LLM_COMPRESS           — false (default): при переполнении удалять старые без LLM-сводки
  MEM0_OPENROUTER_PROVIDER_ORDER / MEM0_OPENROUTER_PROVIDER_IGNORE

Запуск:
  cd /opt/mem0_local && ./venv/bin/python -m uvicorn mem0_server:app --host 127.0.0.1 --port 8001
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Load optional .env next to this file when deployed as mem0_server.py
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.is_file():
    for line in _env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


OPENROUTER_KEY = (os.getenv("OPENROUTER_API_KEY") or os.getenv("MEM0_OPENROUTER_API_KEY") or "").strip()
EMBED_MODEL = (os.getenv("MEM0_EMBED_MODEL") or "openai/text-embedding-3-small").strip()
LLM_MODEL = (os.getenv("MEM0_LLM_MODEL") or "deepseek/deepseek-v4-flash").strip()
BASE_URL = (os.getenv("MEM0_OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").strip()
DB_PATH = (os.getenv("MEM0_DB_PATH") or "/opt/mem0_local/memory.db").strip()

MAX_MEMORIES_PER_USER = _env_int("MEM0_MAX_MEMORIES_PER_USER", 200)
MAX_EMBED_CACHE_ROWS = _env_int("MEM0_MAX_EMBED_CACHE_ROWS", 5000)
EMBED_CACHE_CLEAN_INTERVAL = _env_int("MEM0_EMBED_CACHE_CLEAN_INTERVAL", 3600)
LLM_REWRITE_ENABLED = _env_bool("MEM0_LLM_REWRITE", False)
LLM_REWRITE_MIN_CHARS = _env_int("MEM0_LLM_REWRITE_MIN_CHARS", 400)
LLM_COMPRESS_ENABLED = _env_bool("MEM0_LLM_COMPRESS", False)

app = FastAPI(title="Mem0 local (gemma_bot)")


class AddRequest(BaseModel):
    text: str
    user_id: Optional[str] = None
    metadata: Optional[dict] = None


class SearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    limit: int = 5


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            embedding TEXT,
            metadata TEXT,
            user_id TEXT,
            created_at REAL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_cache (
            text_hash TEXT PRIMARY KEY,
            embedding TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    columns = [c[1] for c in cur.execute("PRAGMA table_info(memory)").fetchall()]
    for col in ("embedding", "metadata", "user_id", "created_at"):
        if col not in columns:
            cur.execute(f"ALTER TABLE memory ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()


init_db()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _openrouter_headers() -> dict:
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}


def _provider_block() -> dict:
    order_raw = (os.getenv("MEM0_OPENROUTER_PROVIDER_ORDER") or "DeepSeek,baidu").strip()
    ignore_raw = (os.getenv("MEM0_OPENROUTER_PROVIDER_IGNORE") or "deepinfra").strip()
    prov: dict = {"allow_fallbacks": True}
    if order_raw:
        prov["order"] = [p.strip() for p in order_raw.split(",") if p.strip()]
    if ignore_raw:
        prov["ignore"] = [p.strip() for p in ignore_raw.split(",") if p.strip()]
    return prov


def embed_text(text: str) -> List[float]:
    payload = {"model": EMBED_MODEL, "input": text}
    r = requests.post(
        f"{BASE_URL}/embeddings",
        json=payload,
        headers=_openrouter_headers(),
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def llm_rewrite(text: str) -> str:
    """Опциональное сжатие. По умолчанию выключено (MEM0_LLM_REWRITE=false)."""
    raw = (text or "").strip()
    if not raw:
        return raw
    if not LLM_REWRITE_ENABLED:
        return raw
    if len(raw) < LLM_REWRITE_MIN_CHARS:
        return raw
    system = (
        "Ты сжимаешь заметки для памяти ассистента. "
        "Пиши только по-русски. Не добавляй английские слова и латиницу без необходимости. "
        "Сохрани факты и имена. Без markdown."
    )
    user = f"Сожми заметку в 1–3 коротких предложения:\n\n{raw[:6000]}"
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 220,
        "temperature": 0.2,
        "provider": _provider_block(),
    }
    r = requests.post(
        f"{BASE_URL}/chat/completions",
        json=payload,
        headers=_openrouter_headers(),
        timeout=60,
    )
    r.raise_for_status()
    out = str(r.json()["choices"][0]["message"]["content"] or "").strip()
    if not out or len(out) < 8:
        return raw
    return out[:2000]


def prepare_memory_text(text: str) -> str:
    try:
        return llm_rewrite(text)
    except Exception as e:
        logger.warning("mem0 rewrite failed, storing raw: %s", e)
        return (text or "").strip()


def get_cached_embedding(text: str) -> List[float]:
    h = text_hash(text)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    row = cur.execute("SELECT embedding FROM embedding_cache WHERE text_hash = ?", (h,)).fetchone()
    if row:
        try:
            emb = json.loads(row[0])
            conn.close()
            return emb
        except json.JSONDecodeError:
            logger.debug("bad embed cache row for hash %s", h[:12])
    emb = embed_text(text)
    cur.execute(
        "INSERT OR REPLACE INTO embedding_cache (text_hash, embedding) VALUES (?, ?)",
        (h, json.dumps(emb)),
    )
    conn.commit()
    conn.close()
    return emb


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def maybe_clean_embed_cache() -> None:
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM system_meta WHERE key='last_embed_cache_clean'").fetchone()
    last_clean = float(row[0]) if row else 0.0
    if now - last_clean < EMBED_CACHE_CLEAN_INTERVAL:
        conn.close()
        return
    total = cur.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
    if total > MAX_EMBED_CACHE_ROWS:
        cur.execute(
            """
            DELETE FROM embedding_cache
            WHERE rowid IN (
                SELECT rowid FROM embedding_cache
                ORDER BY rowid ASC
                LIMIT ?
            )
            """,
            (total - MAX_EMBED_CACHE_ROWS,),
        )
        conn.commit()
    cur.execute(
        "INSERT OR REPLACE INTO system_meta (key,value) VALUES ('last_embed_cache_clean',?)",
        (str(now),),
    )
    conn.commit()
    conn.close()


def maybe_compress_user_memories(user_id: Optional[str]) -> None:
    if not user_id:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, text FROM memory WHERE user_id=? ORDER BY created_at ASC",
        (user_id,),
    ).fetchall()
    if len(rows) <= MAX_MEMORIES_PER_USER:
        conn.close()
        return
    old = rows[: len(rows) // 2]
    ids = [r[0] for r in old]
    if LLM_COMPRESS_ENABLED and OPENROUTER_KEY:
        joined = "\n".join(f"- {r[1]}" for r in old)[:12000]
        try:
            summary = llm_rewrite(
                "Сводка старых заметок пользователя:\n" + joined
            )
            if summary:
                emb = get_cached_embedding(summary)
                cur.execute(
                    "INSERT INTO memory (text, embedding, metadata, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        summary,
                        json.dumps(emb),
                        json.dumps({"compressed": True}),
                        user_id,
                        time.time(),
                    ),
                )
        except Exception as e:
            logger.warning("mem0 compress llm failed: %s", e)
    cur.execute(f"DELETE FROM memory WHERE id IN ({','.join('?' for _ in ids)})", ids)
    conn.commit()
    conn.close()


@app.post("/v3/memories/add")
async def add_memory(request: Request):
    maybe_clean_embed_cache()
    body = await request.json()
    if "text" not in body:
        for alt in ("memory", "value", "content", "msg", "message"):
            if alt in body:
                body["text"] = body[alt]
                break
    if "text" not in body or not body["text"]:
        return {"success": False, "error": "Missing 'text' field"}
    req = AddRequest(**body)
    stored = prepare_memory_text(req.text)
    emb = get_cached_embedding(stored)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO memory (text, embedding, metadata, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (stored, json.dumps(emb), json.dumps(req.metadata or {}), req.user_id, time.time()),
    )
    conn.commit()
    mem_id = cur.lastrowid
    conn.close()
    maybe_compress_user_memories(req.user_id)
    return {"success": True, "memory": {"id": mem_id, "text": stored}}


@app.post("/v3/memories/search")
def search_memory(req: SearchRequest):
    maybe_clean_embed_cache()
    query_emb = get_cached_embedding(req.query)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if req.user_id:
        rows = cur.execute(
            "SELECT id, text, embedding, metadata, user_id FROM memory WHERE user_id = ?",
            (req.user_id,),
        ).fetchall()
    else:
        rows = cur.execute("SELECT id, text, embedding, metadata, user_id FROM memory").fetchall()
    conn.close()
    results = []
    for r in rows:
        if not r[2]:
            continue
        try:
            emb = json.loads(r[2])
        except json.JSONDecodeError:
            continue
        try:
            meta = json.loads(r[3]) if r[3] else {}
        except json.JSONDecodeError:
            meta = {}
        results.append(
            {
                "id": r[0],
                "text": r[1],
                "metadata": meta,
                "user_id": r[4],
                "score": cosine_similarity(query_emb, emb),
            }
        )
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"success": True, "results": results[: req.limit]}
