"""
Единый локальный реестр документов (НПА из кэша, книги BooksRAG): метаданные, путь к оригиналу,
краткие слепки по чанкам, полнотекстовый поиск (FTS5).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_PREVIEW_MAX = 1024
_CHUNK_MAX = 1600
_CHUNK_OVERLAP = 250
_TOKEN_LIMIT = 7

# Characters that must be stripped from FTS5 tokens: (, ), ^, ", *
_FTS5_SPECIAL_CHARS_RE = re.compile(r'[()^"*]')

# FTS5 reserved words that cannot be standalone query tokens (case-insensitive)
_FTS5_RESERVED = frozenset({"NEAR", "NOT", "AND", "OR"})


def corpus_enabled() -> bool:
    raw = os.getenv("DOCUMENT_CORPUS_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def corpus_db_path() -> Path:
    raw = (os.getenv("DOCUMENT_CORPUS_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    base = Path(os.getenv("DOCUMENT_CORPUS_DIR", "data/document_corpus"))
    return base.expanduser() / "corpus.sqlite"


def corpus_originals_dir() -> Path:
    """Каталог рядом с БД: копии оригиналов для выдачи пользователю и бэкапа."""
    return corpus_db_path().parent / "originals"


def corpus_files_dir() -> Path:
    """Каталог для хранения оригиналов загруженных файлов (data/corpus_files/)."""
    path = Path(os.getenv("CORPUS_FILES_DIR", str(corpus_db_path().parent.parent / "corpus_files")).strip())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _corpus_files_unique_path(filename: str) -> Path:
    """Resolve unique path in corpus_files_dir() if conflict exists, appending (1),(2),etc."""
    base = corpus_files_dir()
    safe = filename.strip() or "file"
    safe = re.sub(r'[\\/:*?"<>|]', "_", safe)[:200]
    dest = base / safe
    if not dest.exists():
        return dest
    stem, ext = (safe.rsplit(".", 1) if "." in safe else (safe, ""))
    for i in range(1, 5000):
        cand = base / f"{stem} ({i}){'.' + ext if ext else ''}"
        if not cand.exists():
            return cand
    import secrets
    return base / f"{stem}_{secrets.token_hex(4)}{'.' + ext if ext else ''}"


def _safe_original_filename(doc_id: str, kind: str, src: Path) -> str:
    safe = re.sub(r"[^\w\-]+", "_", doc_id.replace(":", "__"))[:100]
    ext = src.suffix if src.suffix else (".json" if kind == "law_act" else ".txt")
    return f"{safe}{ext}"


def mirror_original_file(doc_id: str, kind: str, src: Path) -> Optional[str]:
    """Копирует файл в corpus_originals_dir; возвращает абсолютный путь или None."""
    if not src.is_file():
        return None
    dest_dir = corpus_originals_dir()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _safe_original_filename(doc_id, kind, src)
        shutil.copy2(src, dest)
        return str(dest.resolve())
    except Exception as e:
        logger.warning("[document_corpus] mirror_original_file: %s", e)
        return None


def _allowed_original_send_roots() -> List[Path]:
    roots: List[Path] = []
    try:
        roots.append(corpus_originals_dir().resolve())
    except Exception as e:
        logger.debug('%s optional failed: %s', 'document_corpus_store', e, exc_info=True)
    try:
        from core.law_act_cache import law_cache_dir

        roots.append((law_cache_dir() / "entries").resolve())
    except Exception as e:
        logger.debug('%s optional failed: %s', 'document_corpus_store', e, exc_info=True)
    lib = (os.getenv("BOOKS_LIBRARY_PATH") or os.getenv("BOOKS_RAG_LIBRARY_PATH") or "data/library").strip()
    if lib:
        try:
            roots.append(Path(lib).expanduser().resolve())
        except Exception as e:
            logger.debug('%s optional failed: %s', 'document_corpus_store', e, exc_info=True)
    try:
        roots.append(corpus_files_dir().resolve())
    except Exception as e:
        logger.debug('%s optional failed: %s', 'document_corpus_store', e, exc_info=True)
    seen: set[str] = set()
    out: List[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def is_path_allowed_for_corpus_send(path: Path) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    if not rp.is_file():
        return False
    for root in _allowed_original_send_roots():
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def get_original_for_telegram(document_id: str) -> Dict[str, Any]:
    """Проверка пути и метаданные для отправки документа пользователю (Telegram)."""
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
    doc_id = (document_id or "").strip()
    if not doc_id:
        return {"ok": False, "error": "document_id required"}
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        row = conn.execute(
            "SELECT id, kind, title, original_path FROM corpus_documents WHERE id=?",
            (doc_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "document not in corpus"}
        raw_path = (row["original_path"] or "").strip()
        if not raw_path:
            return {"ok": False, "error": "original_path not set"}
        path = Path(raw_path)
        if not path.is_file():
            return {"ok": False, "error": "original file missing on disk"}
        if not is_path_allowed_for_corpus_send(path):
            return {"ok": False, "error": "path not in allowed corpus/library roots"}
        title = str(row["title"] or "").strip()
        suffix = path.suffix or ""
        if title:
            stem = re.sub(r"[^\w\-а-яёА-ЯЁ.]+", "_", title, flags=re.IGNORECASE)[:80].strip("._") or "document"
            filename = stem + suffix if suffix else path.name
        else:
            filename = path.name
        return {
            "ok": True,
            "path": str(path.resolve()),
            "filename": filename[-240:],
            "kind": row["kind"],
            "title": title or None,
        }
    finally:
        conn.close()


def get_path_for_corpus_file(document_id: str) -> Optional[str]:
    """Get the absolute path to the original file stored in corpus_files_dir for a document id.
    Returns the canonical path if the file exists, or None."""
    if not corpus_enabled():
        return None
    doc_id = (document_id or "").strip()
    if not doc_id:
        return None
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        row = conn.execute(
            "SELECT original_path FROM corpus_documents WHERE id=?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        raw_path = (row["original_path"] or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_file():
            return None
        if not is_path_allowed_for_corpus_send(path):
            return None
        return str(path.resolve())
    finally:
        conn.close()


def delete_document_from_corpus(document_id: str) -> Dict[str, Any]:
    """Удаляет документ из корпуса: FTS, чанки, метаданные и оригинальный файл (если есть)."""
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
    doc_id = (document_id or "").strip()
    if not doc_id:
        return {"ok": False, "error": "document_id required"}
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        row = conn.execute(
            "SELECT id, original_path FROM corpus_documents WHERE id=?",
            (doc_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "document not found"}
        file_path = (row["original_path"] or "").strip()
        # Cascade: delete from corpus_documents → ON DELETE CASCADE removes chunks → triggers remove FTS
        conn.execute("DELETE FROM corpus_documents WHERE id=?", (doc_id,))
        conn.commit()
        deleted_file = False
        if file_path:
            fp = Path(file_path)
            if fp.is_file() and is_path_allowed_for_corpus_send(fp):
                try:
                    fp.unlink()
                    deleted_file = True
                except OSError:
                    pass
        return {
            "ok": True,
            "document_id": doc_id,
            "file_deleted": deleted_file,
        }
    finally:
        conn.close()


def register_file_as_corpus_document(
    *,
    filename: str,
    mime_type: str,
    file_path_on_disk: str,
    text_content: str,
    user_id: str = "",
) -> Dict[str, Any]:
    """Зарегистрировать файл пользователя в корпусе.
    Копирует файл в corpus_files_dir, извлекает текст, индексирует в FTS5."""
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
    fname = (filename or "file").strip()
    src = Path(file_path_on_disk)
    if not src.is_file():
        return {"ok": False, "error": "source file not found on disk"}
    mime = (mime_type or "").strip()[:200]
    # Copy to corpus_files_dir with unique name
    dest = _corpus_files_unique_path(fname)
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        return {"ok": False, "error": f"failed to copy file: {e}"}
    # Generate stable doc_id
    import hashlib
    import time as _time
    h = hashlib.sha256(f"{user_id}:{fname}:{_time.time()}".encode()).hexdigest()[:16]
    doc_id = f"file:{h}"
    normalized_text = unicodedata.normalize("NFKC", text_content or "")
    n_ins = _upsert_document_and_chunks(
        doc_id=doc_id,
        kind="user_file",
        title=fname[:500],
        source_url=None,
        original_path=str(dest.resolve()),
        external_key=None,
        source_module="user_upload",
        full_text=normalized_text,
        meta={"filename": fname, "mime": mime, "user_id": str(user_id)},
    )
    return {
        "ok": True,
        "document_id": doc_id,
        "kind": "user_file",
        "filename": dest.name,
        "original_path": str(dest.resolve()),
        "chunks": n_ins,
    }


def get_original_path_for_document(document_id: str) -> Optional[str]:
    """Проверить, что оригинальный файл есть в corpus_files_dir, и вернуть путь."""
    if not corpus_enabled():
        return None
    doc_id = (document_id or "").strip()
    if not doc_id:
        return None
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        row = conn.execute(
            "SELECT original_path FROM corpus_documents WHERE id=?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        raw_path = (row["original_path"] or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_file():
            return None
        if not is_path_allowed_for_corpus_send(path):
            return None
        return str(path.resolve())
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    path = corpus_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS corpus_documents (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT,
            source_url TEXT,
            original_path TEXT,
            original_filename TEXT,
            original_mime TEXT,
            external_key TEXT,
            source_module TEXT NOT NULL,
            meta_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS corpus_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL REFERENCES corpus_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            heading TEXT,
            preview TEXT NOT NULL,
            char_start INTEGER,
            char_end INTEGER,
            UNIQUE(document_id, chunk_index)
        );

        CREATE INDEX IF NOT EXISTS idx_corpus_chunks_doc ON corpus_chunks(document_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS corpus_chunks_fts USING fts5(
            preview,
            heading,
            content='corpus_chunks',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS corpus_chunks_ai AFTER INSERT ON corpus_chunks BEGIN
            INSERT INTO corpus_chunks_fts(rowid, preview, heading)
            VALUES (new.id, new.preview, COALESCE(new.heading, ''));
        END;

        CREATE TRIGGER IF NOT EXISTS corpus_chunks_ad AFTER DELETE ON corpus_chunks BEGIN
            INSERT INTO corpus_chunks_fts(corpus_chunks_fts, rowid, preview, heading)
            VALUES('delete', old.id, old.preview, COALESCE(old.heading, ''));
        END;

        CREATE TRIGGER IF NOT EXISTS corpus_chunks_au AFTER UPDATE ON corpus_chunks BEGIN
            INSERT INTO corpus_chunks_fts(corpus_chunks_fts, rowid, preview, heading)
            VALUES('delete', old.id, old.preview, COALESCE(old.heading, ''));
            INSERT INTO corpus_chunks_fts(rowid, preview, heading)
            VALUES (new.id, new.preview, COALESCE(new.heading, ''));
        END;
        """
    )
    # Safe migration: add new columns if they don't exist yet
    for col, col_type in (
        ("original_filename", "TEXT"),
        ("original_mime", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE corpus_documents ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


_schema_done = False
_schema_lock = threading.Lock()
_FTS_SCHEMA_VERSION = 2  # increment when tokenizer or indexed columns change


def _ensure_schema() -> None:
    global _schema_done
    with _schema_lock:
        if _schema_done:
            return
        try:
            conn = _connect()
            _init_schema(conn)
            _ensure_fts_schema_version(conn)
            conn.close()
        except Exception as e:
            logger.warning("[document_corpus] schema init: %s", e)
        _schema_done = True


def _ensure_fts_schema_version(conn: sqlite3.Connection) -> None:
    """Rebuild FTS index if schema version is older than _FTS_SCHEMA_VERSION."""
    try:
        (cur_ver,) = conn.execute("PRAGMA user_version").fetchone()
    except Exception:
        cur_ver = 0
    if int(cur_ver) >= _FTS_SCHEMA_VERSION:
        return
    logger.info(
        "[document_corpus] rebuilding FTS index from version %s to %s (tokenizer changed)",
        cur_ver,
        _FTS_SCHEMA_VERSION,
    )
    # Drop old FTS table and triggers
    conn.execute("DROP TABLE IF EXISTS corpus_chunks_fts")
    # Recreate FTS table with new tokenizer
    conn.execute(
        """CREATE VIRTUAL TABLE corpus_chunks_fts USING fts5(
            preview, heading,
            content='corpus_chunks', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        )"""
    )
    # Repopulate FTS from all existing chunks
    conn.execute(
        """INSERT INTO corpus_chunks_fts(rowid, preview, heading)
           SELECT id, preview, COALESCE(heading, '') FROM corpus_chunks"""
    )
    # Recreate triggers (they were dropped with the old table)
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS corpus_chunks_ai AFTER INSERT ON corpus_chunks BEGIN
            INSERT INTO corpus_chunks_fts(rowid, preview, heading)
            VALUES (new.id, new.preview, COALESCE(new.heading, ''));
        END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS corpus_chunks_ad AFTER DELETE ON corpus_chunks BEGIN
            INSERT INTO corpus_chunks_fts(corpus_chunks_fts, rowid, preview, heading)
            VALUES('delete', old.id, old.preview, COALESCE(old.heading, ''));
        END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS corpus_chunks_au AFTER UPDATE ON corpus_chunks BEGIN
            INSERT INTO corpus_chunks_fts(corpus_chunks_fts, rowid, preview, heading)
            VALUES('delete', old.id, old.preview, COALESCE(old.heading, ''));
            INSERT INTO corpus_chunks_fts(rowid, preview, heading)
            VALUES (new.id, new.preview, COALESCE(new.heading, ''));
        END"""
    )
    conn.execute(f"PRAGMA user_version={_FTS_SCHEMA_VERSION}")
    conn.commit()
    logger.info("[document_corpus] FTS rebuild complete")


def _make_preview(text: str, max_len: int = _PREVIEW_MAX) -> str:
    t = (text or "").replace("\n", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _chunk_spans(text: str, max_chars: int = _CHUNK_MAX, overlap: int = _CHUNK_OVERLAP) -> List[Tuple[int, int, str]]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_chars:
        return [(0, len(t), t)]
    chunks: List[Tuple[int, int, str]] = []
    start = 0
    n = len(t)
    while start < n:
        end = min(start + max_chars, n)
        piece = t[start:end]
        if end < n:
            cut = max(piece.rfind("\n"), piece.rfind(". "), piece.rfind(" "))
            if cut > max_chars // 3:
                piece = piece[: cut + 1].strip()
                end = start + len(piece)
        if piece:
            chunks.append((start, end, piece))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks[:500]


def _heading_before_pos(text: str, pos: int) -> str:
    head = (text or "")[: max(0, pos)]
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    for ln in reversed(lines[-6:]):
        if re.match(
            r"(?i)^(статья\s+\d+|глава\s+[ivx\d]+|раздел\s+[ivx\d]+|глава\s+[а-яё]+|"
            r"раздел\s+[а-яё]+|chapter\s+\d+|part\s+\d+)",
            ln,
        ):
            return ln[:200]
        if len(ln) < 120 and ln.isupper():
            return ln[:200]
    return ""


def _upsert_document_and_chunks(
    *,
    doc_id: str,
    kind: str,
    title: str,
    source_url: Optional[str],
    original_path: Optional[str],
    external_key: Optional[str],
    source_module: str,
    full_text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    _ensure_schema()
    now = time.time()
    conn = _connect()
    try:
        _init_schema(conn)
        _ensure_fts_schema_version(conn)
        normalized_text = unicodedata.normalize("NFKC", full_text or "")
        meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
        conn.execute(
            """
            INSERT INTO corpus_documents (
                id, kind, title, source_url, original_path, external_key, source_module, meta_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind,
                title=excluded.title,
                source_url=excluded.source_url,
                original_path=excluded.original_path,
                external_key=excluded.external_key,
                source_module=excluded.source_module,
                meta_json=excluded.meta_json,
                updated_at=excluded.updated_at
            """,
            (
                doc_id,
                kind,
                (title or "")[:500],
                (source_url or "").strip() or None,
                (original_path or "").strip() or None,
                (external_key or "").strip() or None,
                source_module,
                meta_json,
                now,
                now,
            ),
        )
        conn.execute("DELETE FROM corpus_chunks WHERE document_id=?", (doc_id,))
        spans = _chunk_spans(normalized_text)
        n_ins = 0
        for idx, (cs, ce, piece) in enumerate(spans):
            hd = _heading_before_pos(normalized_text, cs)
            prev = _make_preview(piece)
            conn.execute(
                """
                INSERT INTO corpus_chunks (document_id, chunk_index, heading, preview, char_start, char_end)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, idx, hd or None, prev, cs, ce),
            )
            n_ins += 1
        conn.commit()
        return n_ins
    finally:
        conn.close()


def _fts_query_from_user(q: str) -> Optional[str]:
    normalized = unicodedata.normalize("NFKC", (q or "").lower())
    tokens = [
        t
        for t in re.findall(r"[\w\-]+", normalized, flags=re.UNICODE)
        if len(t) >= 2
    ][:_TOKEN_LIMIT]
    if not tokens:
        return None
    parts: List[str] = []
    for t in tokens:
        safe = _FTS5_SPECIAL_CHARS_RE.sub("", t)
        if not safe:
            continue
        # If the token is a reserved FTS5 keyword (case-insensitive), prefix it
        # so it's treated as a literal term, not as an operator.
        if safe.upper() in _FTS5_RESERVED:
            safe = f'"{safe}"'
        parts.append(f"{safe}*")
    if not parts:
        return None
    return " OR ".join(parts)


def unified_search(
    query: str,
    *,
    kinds: Optional[Sequence[str]] = None,
    limit: int = 16,
) -> Dict[str, Any]:
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false", "hits": []}
    fts = _fts_query_from_user(query)
    if not fts:
        return {"ok": True, "query": (query or "").strip(), "hits": [], "hint": "Запрос слишком короткий для поиска."}
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        _ensure_fts_schema_version(conn)
        kind_filter = ""
        params: List[Any] = [fts]
        if kinds:
            ks = [k.strip() for k in kinds if k and k.strip()]
            if ks:
                ph = ",".join("?" for _ in ks)
                kind_filter = f" AND d.kind IN ({ph})"
                params.extend(ks)
        lim = max(1, min(int(limit) or 16, 48))
        params.append(lim)
        sql = f"""
            SELECT d.id AS document_id, d.kind, d.title, d.source_url, d.original_path,
                   c.chunk_index, c.heading, c.preview, c.char_start, c.char_end,
                   bm25(corpus_chunks_fts) AS rk,
                   snippet(corpus_chunks_fts, '[', ']', '…', -1, 64) AS snippet
            FROM corpus_chunks_fts
            JOIN corpus_chunks c ON c.id = corpus_chunks_fts.rowid
            JOIN corpus_documents d ON d.id = c.document_id
            WHERE corpus_chunks_fts MATCH ? {kind_filter}
            ORDER BY rk
            LIMIT ?
        """
        try:
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("[document_corpus] fts search: %s", e)
            sql_fallback = f"""
                SELECT d.id AS document_id, d.kind, d.title, d.source_url, d.original_path,
                       c.chunk_index, c.heading, c.preview, c.char_start, c.char_end
                FROM corpus_chunks_fts
                JOIN corpus_chunks c ON c.id = corpus_chunks_fts.rowid
                JOIN corpus_documents d ON d.id = c.document_id
                WHERE corpus_chunks_fts MATCH ? {kind_filter}
                LIMIT ?
            """
            cur = conn.execute(sql_fallback, tuple(params))
            rows = cur.fetchall()
        hits: List[Dict[str, Any]] = []
        for r in rows:
            hit = {
                "document_id": r["document_id"],
                "kind": r["kind"],
                "title": r["title"],
                "source_url": r["source_url"],
                "original_path": r["original_path"],
                "chunk_index": r["chunk_index"],
                "heading": r["heading"],
                "preview": r["preview"],
                "char_start": r["char_start"],
                "char_end": r["char_end"],
            }
            if "rk" in r.keys():
                rk_val = r["rk"]
                hit["score"] = float(rk_val) if rk_val is not None else None
            if "snippet" in r.keys():
                hit["snippet"] = r["snippet"]
            hits.append(hit)
        return {
            "ok": True,
            "query": (query or "").strip(),
            "fts": fts,
            "hits": hits,
            "hint": "Слепки чанков; отдать файл пользователю: команда /corpus_doc с document_id из hit или инструмент DocumentCorpus.resolve_original; НПА полным текстом также LawSearch.fetch_act по source_url.",
        }
    finally:
        conn.close()


def get_document_outline(document_id: str, *, max_chunks: int = 60) -> Dict[str, Any]:
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
    doc_id = (document_id or "").strip()
    if not doc_id:
        return {"ok": False, "error": "document_id required"}
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        d = conn.execute("SELECT * FROM corpus_documents WHERE id=?", (doc_id,)).fetchone()
        if not d:
            return {"ok": False, "error": "document not found"}
        cap = max(1, min(int(max_chunks) or 60, 200))
        cur = conn.execute(
            """
            SELECT chunk_index, heading, preview, char_start, char_end
            FROM corpus_chunks WHERE document_id=? ORDER BY chunk_index LIMIT ?
            """,
            (doc_id, cap),
        )
        chunks = [dict(row) for row in cur.fetchall()]
        return {
            "ok": True,
            "document_id": d["id"],
            "kind": d["kind"],
            "title": d["title"],
            "source_url": d["source_url"],
            "original_path": d["original_path"],
            "source_module": d["source_module"],
            "chunks": chunks,
        }
    finally:
        conn.close()


def corpus_stats() -> Dict[str, Any]:
    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        nd = conn.execute("SELECT COUNT(*) FROM corpus_documents").fetchone()[0]
        nc = conn.execute("SELECT COUNT(*) FROM corpus_chunks").fetchone()[0]
        by_kind: Dict[str, int] = {}
        for row in conn.execute("SELECT kind, COUNT(*) FROM corpus_documents GROUP BY kind"):
            by_kind[row[0]] = row[1]
        return {
            "ok": True,
            "documents": nd,
            "chunks": nc,
            "by_kind": by_kind,
            "db": str(corpus_db_path()),
        }
    finally:
        conn.close()


def corpus_catalog(
    *,
    mode: str = "all",
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Список документов в корпусе (id, kind, title, число чанков) без полнотекстового поиска.
    mode: all | books | documents (или docs) — для books только kind=book, documents — всё кроме книг.
    """
    if not corpus_enabled():
        return {
            "ok": False,
            "error": "DOCUMENT_CORPUS_ENABLED=false",
            "items": [],
            "total": 0,
            "mode": mode,
            "page_command": "corpus_docs",
        }
    m = (mode or "all").strip().lower()
    if m in {"docs", "doc"}:
        m = "documents"
    if m not in {"all", "books", "documents"}:
        m = "all"
    lim = max(1, min(int(limit) or 200, 500))
    off = max(0, int(offset) or 0)

    if m == "books":
        where_clause = "WHERE d.kind = ?"
        params: List[Any] = ["book"]
        page_command = "corpus_books"
    elif m == "documents":
        where_clause = "WHERE d.kind != ?"
        params = ["book"]
        page_command = "corpus_docs"
    else:
        where_clause = ""
        params = []
        page_command = "corpus_docs"

    _ensure_schema()
    conn = _connect()
    try:
        _init_schema(conn)
        count_sql = f"SELECT COUNT(*) FROM corpus_documents d {where_clause}"
        total = int(conn.execute(count_sql, params).fetchone()[0])
        list_sql = f"""
            SELECT d.id, d.kind, d.title,
              (SELECT COUNT(*) FROM corpus_chunks c WHERE c.document_id = d.id) AS n_chunks
            FROM corpus_documents d
            {where_clause}
            ORDER BY d.kind, COALESCE(d.title, ''), d.id
            LIMIT ? OFFSET ?
        """
        cur = conn.execute(list_sql, params + [lim, off])
        items: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            items.append(
                {
                    "id": row[0],
                    "kind": row[1],
                    "title": row[2] or "",
                    "chunks": int(row[3] or 0),
                }
            )
        return {
            "ok": True,
            "mode": m,
            "page_command": page_command,
            "total": total,
            "offset": off,
            "limit": lim,
            "items": items,
            "truncated": off + len(items) < total,
            "db": str(corpus_db_path()),
        }
    finally:
        conn.close()


def register_law_act_from_cache(*, cache_key: str, url: str, title: str, text: str) -> None:
    if not corpus_enabled():
        return
    key = (cache_key or "").strip()
    if not key or not text:
        return
    doc_id = f"law:{key}"
    from core.law_act_cache import law_cache_dir

    entry_path = law_cache_dir() / "entries" / f"{key}.json"
    mirrored = mirror_original_file(doc_id, "law_act", entry_path)
    store_path = mirrored or str(entry_path)
    _upsert_document_and_chunks(
        doc_id=doc_id,
        kind="law_act",
        title=title or "",
        source_url=url,
        original_path=store_path,
        external_key=key,
        source_module="law_act_cache",
        full_text=text,
        meta={"url": url},
    )


def register_book_from_rag(*, book_id: str, title: str, file_path: str, content: str) -> None:
    if not corpus_enabled():
        return
    bid = (book_id or "").strip()
    if not bid or not content:
        return
    doc_id = f"book:{bid}"
    src_book = Path(file_path) if file_path else Path()
    mirrored_b = mirror_original_file(doc_id, "book", src_book) if file_path else None
    fp = mirrored_b or (str(src_book.resolve()) if file_path else "")
    _upsert_document_and_chunks(
        doc_id=doc_id,
        kind="book",
        title=title or "",
        source_url=None,
        original_path=fp or None,
        external_key=bid,
        source_module="books_rag",
        full_text=content,
        meta={"book_id": bid},
    )


def register_shared_knowledge_ingest(
    *,
    pending_id: str,
    user_id: str,
    original_name: str,
    body: str,
    saved_path: str,
) -> None:
    """
    После сохранения в data/shared_knowledge/ingest — проиндексировать в DocumentCorpus,
    чтобы unified_search / stats отражали «общую базу».
    """
    if not corpus_enabled():
        return
    pid = (pending_id or "").strip().lower()
    if not pid or not _valid_hex_pending_id(pid):
        return
    raw_body = (body or "").strip()
    if not raw_body:
        return
    doc_id = f"shared:{pid}"
    src = Path(saved_path)
    full_text = raw_body
    if src.is_file():
        try:
            full_text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            full_text = raw_body
    full_text = _strip_shared_ingest_file_header(full_text)
    if len(full_text.strip()) < 20:
        return
    mirrored: Optional[str] = None
    if src.is_file():
        mirrored = mirror_original_file(doc_id, "shared_ingest", src)
    fp = mirrored or (str(src.resolve()) if src.is_file() else None)
    title = (original_name or "").strip()[:500] or f"shared {pid[:8]}"
    _upsert_document_and_chunks(
        doc_id=doc_id,
        kind="shared_ingest",
        title=title,
        source_url=None,
        original_path=fp,
        external_key=pid[:24],
        source_module="shared_knowledge",
        full_text=full_text,
        meta={"user_id": str(user_id), "source_file": original_name, "pending_id": pid, "ingest_path": saved_path},
    )


def _valid_hex_pending_id(pid: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{16,32}", pid))


def _strip_shared_ingest_file_header(text: str) -> str:
    """Убрать служебные # строки из файла ingest для лучшего FTS."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    out = "\n".join(lines[i:]).strip()
    return out if out else text.strip()


def sync_shared_knowledge_ingest_dir() -> Dict[str, Any]:
    """Проиндексировать существующие .txt из shared_knowledge/ingest (миграция / догон после обновления)."""
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false"}
    base = Path((os.getenv("SHARED_KNOWLEDGE_DIR") or "data/shared_knowledge").strip() or "data/shared_knowledge")
    ingest = base.expanduser() / "ingest"
    if not ingest.is_dir():
        return {"ok": True, "indexed": 0, "hint": "no ingest directory"}
    n = 0
    errors = 0
    for p in sorted(ingest.glob("*.txt")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            errors += 1
            continue
        pid = ""
        uid = ""
        orig = ""
        for line in text.splitlines()[:24]:
            s = line.strip()
            if s.startswith("# pending_id:"):
                pid = s.split(":", 1)[1].strip().lower()
            elif s.startswith("# user_id:"):
                uid = s.split(":", 1)[1].strip()
            elif s.startswith("# source_file:"):
                orig = s.split(":", 1)[1].strip()
        if not pid or not _valid_hex_pending_id(pid):
            errors += 1
            continue
        body = _strip_shared_ingest_file_header(text)
        if len(body.strip()) < 20:
            continue
        try:
            register_shared_knowledge_ingest(
                pending_id=pid,
                user_id=uid or "unknown",
                original_name=orig or p.name,
                body=body,
                saved_path=str(p.resolve()),
            )
            n += 1
        except Exception as e:
            logger.warning("[document_corpus] sync_shared ingest %s: %s", p.name, e)
            errors += 1
    return {"ok": True, "indexed": n, "errors": errors, "dir": str(ingest)}


def sync_all_law_cache_entries() -> Dict[str, Any]:
    """Проиндексировать все JSON из law_act_cache/entries в корпус (миграция / догон)."""
    if not corpus_enabled():
        return {"ok": False, "error": "DOCUMENT_CORPUS_ENABLED=false", "indexed": 0}
    from core.law_act_cache import cache_key_for_url, law_cache_dir

    base = law_cache_dir() / "entries"
    if not base.is_dir():
        return {"ok": True, "indexed": 0, "hint": "no law cache entries dir"}
    n = 0
    errors = 0
    for p in sorted(base.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                row = json.load(f)
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "")
            title = str(row.get("title") or "")
            text = str(row.get("text") or "")
            if not url and not text:
                continue
            key = cache_key_for_url(url) if url else p.stem
            register_law_act_from_cache(cache_key=key, url=url, title=title, text=text)
            n += 1
        except Exception as e:
            errors += 1
            logger.debug("[document_corpus] sync law %s: %s", p, e)
    return {"ok": True, "indexed": n, "errors": errors}
