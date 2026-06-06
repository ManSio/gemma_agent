"""
Личный архив знаний: длинные вставки / текст с сайта с метаданными и опциональной сверкой через поиск.

Не гарантирует истинность: cross_check даёт независимые выдержки из поиска для сравнения, не «вердикт».
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

from core.universal_search_module import UniversalSearchModule

logger = logging.getLogger(__name__)

_ENTRY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_PERSONAL_LIB_NAME_RE = re.compile(r"^[\w.\- ]+\.txt$", re.IGNORECASE)


def _personal_library_dir(user_id: str) -> str:
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    lib = (os.getenv("USER_LIBRARY_DIR") or "").strip()
    if not lib:
        lib = os.path.join(root, "data", "user_library")
    uid = re.sub(r"[^\w\-.@]", "_", str(user_id).strip())[:120] or "unknown"
    return os.path.join(lib, uid)


def _safe_personal_library_filename(name: str) -> Optional[str]:
    raw = (name or "").strip().replace("\\", "/")
    base = os.path.basename(raw)
    if not base or not _PERSONAL_LIB_NAME_RE.match(base):
        return None
    return base


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def archive_enabled() -> bool:
    return _truthy("USER_KNOWLEDGE_ARCHIVE_ENABLED", True)


def index_path() -> str:
    p = (os.getenv("USER_KNOWLEDGE_ARCHIVE_INDEX") or "").strip()
    if p:
        return p
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, "data", "runtime", "user_knowledge_archive.jsonl")


def _user_root(user_id: str) -> str:
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    base = (os.getenv("USER_KNOWLEDGE_ARCHIVE_DIR") or "").strip()
    if not base:
        base = os.path.join(root, "data", "user_library", "knowledge_archive")
    uid = re.sub(r"[^\w\-.@]", "_", str(user_id).strip())[:120] or "unknown"
    return os.path.join(base, uid)


def _max_body_bytes() -> int:
    try:
        return max(10_000, int((os.getenv("USER_KNOWLEDGE_ARCHIVE_MAX_BYTES") or "2500000").strip()))
    except ValueError:
        return 2_500_000


def _preview(body: str, n: int = 280) -> str:
    t = (body or "").strip().replace("\n", " ")
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _snippet_window(text: str, pos: int, q_len: int, radius: int = 140) -> str:
    a = max(0, pos - radius)
    b = min(len(text), pos + q_len + radius)
    chunk = text[a:b].replace("\n", " ").strip()
    if a > 0:
        chunk = "…" + chunk
    if b < len(text):
        chunk = chunk + "…"
    return chunk


def _scan_file_for_query(path: str, q_cf: str, max_bytes: int) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes:
        data = data[:max_bytes]
    dl = data.casefold()
    if q_cf not in dl:
        return None
    pos = dl.index(q_cf)
    return _snippet_window(data, pos, len(q_cf))


def _trim_index(path: str, max_lines: int) -> None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[-max_lines:])
    except OSError as e:
        logger.debug("knowledge archive index trim: %s", e)


def _iter_index_reverse(path: str) -> Iterator[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            yield o


def count_archive_entries_for_user(user_id: str) -> int:
    """Число сохранённых текстов в личном архиве (файлы *.txt в каталоге пользователя)."""
    if not archive_enabled():
        return 0
    uroot = _user_root(user_id)
    if not uroot or not os.path.isdir(uroot):
        return 0
    n = 0
    try:
        for name in os.listdir(uroot):
            if str(name).lower().endswith(".txt"):
                n += 1
    except OSError:
        return 0
    return n


def _find_entry_meta(user_id: str, entry_id: str) -> Optional[Dict[str, Any]]:
    uid = str(user_id).strip()
    eid = entry_id.strip().lower()
    for rec in _iter_index_reverse(index_path()):
        if str(rec.get("user_id")) == uid and str(rec.get("entry_id", "")).lower() == eid:
            return rec
    return None


class UserKnowledgeArchiveModule:
    """Инструменты личного архива для brain (авто-регистрация в core.tools)."""

    async def archive_store(
        self,
        title: str = "",
        body: str = "",
        text: str = "",
        content: str = "",
        user_id: str = "unknown",
        source_type: str = "paste",
        source_url: str = "",
        tags: str = "",
    ) -> Dict[str, Any]:
        if not archive_enabled():
            return {"error": "archive disabled (USER_KNOWLEDGE_ARCHIVE_ENABLED=false)", "skipped": True}
        from core.utils.llm_sanitize import sanitize_llm_value

        title = (title or "").strip()[:500]
        raw = sanitize_llm_value(body or text or content)
        if not raw:
            return {"ok": False, "error": "empty_text"}
        body = raw
        if len(body.encode("utf-8")) > _max_body_bytes():
            return {
                "error": "body too large",
                "max_bytes": _max_body_bytes(),
                "hint": "Разбей на части или подними USER_KNOWLEDGE_ARCHIVE_MAX_BYTES.",
            }
        if not title:
            title = _preview(body, 80) or "без названия"

        st = (source_type or "paste").strip().lower()[:32]
        if st not in {"paste", "url", "document", "site_text", "other"}:
            st = "other"

        entry_id = secrets.token_hex(8)
        uroot = _user_root(user_id)
        try:
            os.makedirs(uroot, exist_ok=True)
        except OSError as e:
            return {"error": str(e)}
        fpath = os.path.join(uroot, f"{entry_id}.txt")
        try:
            with open(fpath, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
        except OSError as e:
            return {"error": str(e)}

        h = hashlib.sha256(body.encode("utf-8")).hexdigest()
        rec = {
            "entry_id": entry_id,
            "user_id": str(user_id).strip(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "title": title,
            "source_type": st,
            "source_url": (source_url or "").strip()[:2000],
            "tags": (tags or "").strip()[:500],
            "sha256": h,
            "char_count": len(body),
            "body_preview": _preview(body, 360),
            "body_path": fpath,
        }
        idx = index_path()
        try:
            os.makedirs(os.path.dirname(idx) or ".", exist_ok=True)
            with open(idx, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            try:
                os.unlink(fpath)
            except OSError:
                pass
            return {"error": str(e), "partial": "file removed after index failure"}

        try:
            ml = int((os.getenv("USER_KNOWLEDGE_ARCHIVE_INDEX_MAX_LINES") or "8000").strip() or "8000")
            if ml > 0 and os.path.isfile(idx) and os.path.getsize(idx) > 2_000_000:
                _trim_index(idx, ml)
        except (OSError, ValueError):
            pass

        return {
            "ok": True,
            "entry_id": entry_id,
            "title": title,
            "source_type": st,
            "char_count": len(body),
            "hint": "Для сверки с веб-источниками вызови UserKnowledgeArchive.archive_cross_check с этим entry_id.",
        }

    async def archive_list(
        self,
        user_id: str = "unknown",
        query: str = "",
        limit: int = 15,
    ) -> Dict[str, Any]:
        if not archive_enabled():
            return {"error": "archive disabled", "skipped": True}
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 15
        lim = max(1, min(lim, 50))
        q = (query or "").strip().lower()
        uid = str(user_id).strip()
        out: List[Dict[str, Any]] = []
        for rec in _iter_index_reverse(index_path()):
            if str(rec.get("user_id")) != uid:
                continue
            if q:
                blob = " ".join(
                    [
                        str(rec.get("title") or ""),
                        str(rec.get("tags") or ""),
                        str(rec.get("body_preview") or ""),
                        str(rec.get("source_url") or ""),
                    ]
                ).lower()
                if q not in blob:
                    continue
            out.append(
                {
                    "entry_id": rec.get("entry_id"),
                    "ts": rec.get("ts"),
                    "title": rec.get("title"),
                    "source_type": rec.get("source_type"),
                    "source_url": rec.get("source_url"),
                    "tags": rec.get("tags"),
                    "preview": rec.get("body_preview"),
                    "char_count": rec.get("char_count"),
                }
            )
            if len(out) >= lim:
                break
        return {"ok": True, "items": out, "count": len(out)}

    async def archive_read(
        self,
        entry_id: str,
        user_id: str = "unknown",
        max_chars: int = 12000,
    ) -> Dict[str, Any]:
        if not archive_enabled():
            return {"error": "archive disabled", "skipped": True}
        eid = (entry_id or "").strip().lower()
        if not _ENTRY_ID_RE.match(eid):
            return {"error": "invalid entry_id"}
        meta = _find_entry_meta(user_id, eid)
        if not meta:
            return {"error": "entry not found", "entry_id": eid}
        path = meta.get("body_path")
        if not isinstance(path, str) or not path:
            path = os.path.join(_user_root(user_id), f"{eid}.txt")
        try:
            mc = int(max_chars)
        except (TypeError, ValueError):
            mc = 12000
        mc = max(500, min(mc, 500_000))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(mc + 1)
        except OSError as e:
            return {"error": str(e), "meta": {k: meta[k] for k in ("title", "ts", "source_type", "source_url", "tags") if k in meta}}
        clipped = len(text) > mc
        body = text[:mc] if clipped else text
        return {
            "ok": True,
            "entry_id": eid,
            "meta": {k: meta[k] for k in ("title", "ts", "source_type", "source_url", "tags", "sha256", "char_count") if k in meta},
            "body": body,
            "clipped": clipped,
        }

    async def archive_search(
        self,
        query: str = "",
        user_id: str = "unknown",
        scope: str = "both",
        limit: int = 15,
    ) -> Dict[str, Any]:
        """
        Полнотекстовый поиск подстроки в заметках архива (тела .txt) и в файлах личной библиотеки.
        **archive_list** ищет только по превью/метаданным; для «всё про …» в текстах — этот инструмент.
        """
        q = (query or "").strip()
        if not q:
            return {
                "error": "query required",
                "hint": "Укажи args.query — подстроку для поиска (как в тексте документов).",
            }
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 15
        lim = max(1, min(lim, 40))

        sc = (scope or "both").strip().lower()
        if sc not in {"both", "archive", "library"}:
            sc = "both"

        try:
            mf = int((os.getenv("USER_KNOWLEDGE_ARCHIVE_SEARCH_MAX_FILES") or "120").strip() or "120")
        except ValueError:
            mf = 120
        mf = max(10, min(mf, 500))

        max_bytes = min(_max_body_bytes(), 2_500_000)
        q_cf = q.casefold()
        hits: List[Dict[str, Any]] = []
        scanned = 0
        uid = str(user_id).strip()

        def _add(hit: Dict[str, Any]) -> None:
            if len(hits) >= lim:
                return
            hits.append(hit)

        if sc in {"both", "archive"}:
            if not archive_enabled():
                if sc == "archive":
                    return {"error": "archive disabled", "skipped": True}
            else:
                for rec in _iter_index_reverse(index_path()):
                    if len(hits) >= lim or scanned >= mf:
                        break
                    if str(rec.get("user_id")) != uid:
                        continue
                    path = rec.get("body_path")
                    if not isinstance(path, str) or not path:
                        eid = str(rec.get("entry_id") or "").strip().lower()
                        if _ENTRY_ID_RE.match(eid):
                            path = os.path.join(_user_root(uid), f"{eid}.txt")
                        else:
                            continue
                    scanned += 1
                    snip = _scan_file_for_query(str(path), q_cf, max_bytes)
                    if snip:
                        _add(
                            {
                                "source": "archive",
                                "entry_id": rec.get("entry_id"),
                                "title": rec.get("title"),
                                "snippet": snip,
                            }
                        )

        if sc in {"both", "library"} and len(hits) < lim and scanned < mf:
            udir = _personal_library_dir(uid)
            scored: List[tuple[float, str]] = []
            if os.path.isdir(udir):
                try:
                    for name in os.listdir(udir):
                        if not str(name).lower().endswith(".txt"):
                            continue
                        p = os.path.join(udir, name)
                        try:
                            st = os.stat(p)
                        except OSError:
                            continue
                        scored.append((float(st.st_mtime), name))
                except OSError:
                    scored = []
            for _, name in sorted(scored, reverse=True):
                if len(hits) >= lim or scanned >= mf:
                    break
                path = os.path.join(udir, name)
                scanned += 1
                snip = _scan_file_for_query(path, q_cf, max_bytes)
                if snip:
                    _add({"source": "personal_library", "filename": name, "snippet": snip})

        return {
            "ok": True,
            "query": q,
            "scope": sc,
            "count": len(hits),
            "items": hits,
            "files_scanned": scanned,
        }

    async def archive_cross_check(
        self,
        user_id: str = "unknown",
        entry_id: str = "",
        claim: str = "",
        focus_query: str = "",
    ) -> Dict[str, Any]:
        """
        Независимые выдержки из UniversalSearch (1–2 запроса). Сравнение с оригиналом — задача модели/пользователя.
        """
        if not archive_enabled():
            return {"error": "archive disabled", "skipped": True}
        eid = (entry_id or "").strip().lower()
        body = ""
        title = ""
        source_url = ""
        if eid:
            if not _ENTRY_ID_RE.match(eid):
                return {"error": "invalid entry_id"}
            meta = _find_entry_meta(user_id, eid)
            if not meta:
                return {"error": "entry not found", "entry_id": eid}
            title = str(meta.get("title") or "")
            source_url = str(meta.get("source_url") or "")
            path = meta.get("body_path") or os.path.join(_user_root(user_id), f"{eid}.txt")
            try:
                with open(str(path), "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError as e:
                return {"error": str(e)}
        else:
            body = (claim or "").strip()
            if not body:
                return {
                    "error": "entry_id or claim required",
                    "hint": "Укажи entry_id из archive_store / archive_list или поле claim с текстом утверждения.",
                }
            title = _preview(body, 120)

        fq = (focus_query or "").strip()
        snippet = (body if not fq else body).replace("\n", " ").strip()
        if fq:
            base_q = fq[:420]
        else:
            base_q = f"{title} {snippet[:320]}".strip()[:450]

        queries: List[str] = [base_q]
        if source_url and "://" in source_url:
            try:
                host = urlparse(source_url).hostname
                if host:
                    q2 = f"site:{host} {title}".strip()[:450]
                    if q2 and q2 not in queries:
                        queries.append(q2)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'user_knowledge_archive_module', e, exc_info=True)
        try:
            nq = int((os.getenv("USER_KNOWLEDGE_ARCHIVE_VERIFY_MAX_QUERIES") or "2").strip() or "2")
        except ValueError:
            nq = 2
        nq = max(1, min(nq, 3))
        queries = queries[:nq]

        searcher = UniversalSearchModule()
        runs: List[Dict[str, Any]] = []
        for q in queries:
            try:
                r = await searcher.search(q, user_id=str(user_id))
            except Exception as e:
                r = {"ok": False, "error": str(e), "query": q}
            runs.append({"query": q, "search": r})

        if eid:
            side = os.path.join(_user_root(user_id), f"{eid}.verify.jsonl")
        else:
            ch = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            side = os.path.join(_user_root(user_id), f"_claim_{ch}.verify.jsonl")
        try:
            os.makedirs(os.path.dirname(side) or ".", exist_ok=True)
            with open(side, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "entry_id": eid or None,
                            "queries": queries,
                            "runs": runs,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError as e:
            logger.debug("verify log: %s", e)

        return {
            "ok": True,
            "entry_id": eid or None,
            "queries": queries,
            "runs": runs,
            "disclaimer": (
                "Автосверка не заменяет экспертизу: сопоставь даты, цифры и формулировки; при споре ищи первоисточник (официальный URL, первичная публикация)."
            ),
        }

    async def personal_library_list(
        self,
        user_id: str = "unknown",
        query: str = "",
        limit: int = 40,
    ) -> Dict[str, Any]:
        """
        Файлы личной библиотеки: тексты из вложений (Telegram «Личное»), каталог USER_LIBRARY_DIR/<user_id>/*.txt.
        Не путать с archive_list — там индексированные заметки UserKnowledgeArchive (knowledge_archive/…).
        """
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 40
        lim = max(1, min(lim, 100))
        q = (query or "").strip().lower()
        udir = _personal_library_dir(user_id)
        if not os.path.isdir(udir):
            return {
                "ok": True,
                "items": [],
                "count": 0,
                "dir": udir,
                "hint": "Каталог пуст или ещё не создан — вложений в личную библиотеку не сохраняли.",
            }
        items: List[Dict[str, Any]] = []
        try:
            names = sorted(os.listdir(udir), key=lambda s: s.lower())
        except OSError as e:
            return {"ok": False, "error": str(e), "dir": udir}
        for name in names:
            if not str(name).lower().endswith(".txt"):
                continue
            if q and q not in name.lower():
                continue
            path = os.path.join(udir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            items.append(
                {
                    "filename": name,
                    "path": path,
                    "size_bytes": int(st.st_size),
                    "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
            if len(items) >= lim:
                break
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "dir": udir,
            "hint": "Это файлы из вложений (кнопка «Личное»), не записи archive_store. Для заметок архива вызови archive_list.",
        }

    async def personal_library_read(
        self,
        user_id: str = "unknown",
        filename: str = "",
        max_chars: int = 12000,
    ) -> Dict[str, Any]:
        """Прочитать один .txt из личной библиотеки по имени файла (только basename)."""
        safe = _safe_personal_library_filename(filename)
        if not safe:
            return {
                "error": "invalid filename",
                "hint": "Укажи filename как имя файла с расширением .txt (как в personal_library_list), без путей.",
            }
        try:
            mc = int(max_chars)
        except (TypeError, ValueError):
            mc = 12000
        mc = max(500, min(mc, 500_000))
        path = os.path.join(_personal_library_dir(user_id), safe)
        if not os.path.isfile(path):
            return {"error": "file not found", "filename": safe}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(mc + 1)
        except OSError as e:
            return {"error": str(e), "filename": safe}
        clipped = len(text) > mc
        body = text[:mc] if clipped else text
        return {"ok": True, "filename": safe, "body": body, "clipped": clipped}
