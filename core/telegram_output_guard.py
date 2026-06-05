"""Сжатие и фильтрация исходящих сообщений Telegram перед отправкой."""
from __future__ import annotations

import os
import re
import time
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple

from core.models import Output

_PHOTO_DEDUP_LOCK = Lock()
_RECENT_PHOTO_TURNS: Dict[str, float] = {}


def _env_flag(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _token_set(text: str) -> Set[str]:
    return set(re.findall(r"[а-яёa-z0-9]{3,}", (text or "").lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa or not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


_QUERY_STOP = frozenset(
    {
        "какой",
        "какая",
        "какое",
        "какие",
        "что",
        "где",
        "когда",
        "почему",
        "зачем",
        "это",
        "тот",
        "та",
        "те",
        "мне",
        "меня",
        "тебе",
        "комнате",
        "комната",
        "цвет",
    }
)


def _keyword_hits_in_reply(user_text: str, reply: str) -> int:
    """Сколько предметных слов запроса (не стоп-слов) есть в ответе."""
    u = [w for w in _token_set(user_text) if w not in _QUERY_STOP]
    if not u:
        u = list(_token_set(user_text))
    blob = (reply or "").lower()
    hits = 0
    for w in u:
        if w in blob or (len(w) >= 5 and w[:5] in blob):
            hits += 1
    return hits


def _overlap_with_user_query(user_text: str, reply: str) -> float:
    """Доля значимых слов запроса, встречающихся в ответе."""
    u = _token_set(user_text) - _QUERY_STOP
    r = _token_set(reply)
    if not u:
        u = _token_set(user_text)
    if not u:
        return 0.5
    if not r:
        return 0.0
    return len(u & r) / len(u)


def _is_substantive_text_output(out: Output) -> bool:
    if out.type != "text":
        return False
    meta = out.meta or {}
    if bool(meta.get("confirmation")) or str(meta.get("reason") or "") == "math_ambiguous":
        return False
    return len(str(out.payload or "").strip()) >= 80


def keep_single_best_text_output(outputs: List[Output], user_text: str) -> List[Output]:
    """
    Один содержательный текст на ход — с максимальной релевантностью user_text.
    Иначе в Telegram уходят два ответа (старый топик + новый).
    """
    if not outputs or not _env_flag("TELEGRAM_SINGLE_BEST_OUTPUT", True):
        return outputs
    substantive = [o for o in outputs if _is_substantive_text_output(o)]
    if len(substantive) <= 1:
        return outputs
    scored: List[Tuple[Output, float, int]] = []
    for o in substantive:
        body = str(o.payload or "")
        scored.append(
            (
                o,
                _overlap_with_user_query(user_text, body),
                _keyword_hits_in_reply(user_text, body),
            )
        )
    scored.sort(key=lambda x: (x[2], x[1]), reverse=True)
    best = scored[0][0]
    keep_ids: Set[int] = {id(best)}
    kept: List[Output] = []
    for o in outputs:
        if _is_substantive_text_output(o) and id(o) not in keep_ids:
            continue
        kept.append(o)
    return kept if kept else [best]


def dedupe_telegram_outputs(outputs: List[Output], user_text: str) -> List[Output]:
    """
    Убирает дубли и явно нерелевантный второй ответ (два содержательных текста на один вопрос).
  """
    if not outputs or not _env_flag("TELEGRAM_OUTPUT_DEDUPE_ENABLED", True):
        return outputs

    substantive = [o for o in outputs if _is_substantive_text_output(o)]
    if len(substantive) < 2:
        return outputs

    scored: List[Tuple[Output, float, int]] = []
    for o in substantive:
        body = str(o.payload or "")
        scored.append(
            (
                o,
                _overlap_with_user_query(user_text, body),
                _keyword_hits_in_reply(user_text, body),
            )
        )
    scored.sort(key=lambda x: (x[2], x[1]), reverse=True)
    best_out, best_score, best_hits = scored[0]
    keep_ids: Set[int] = {id(best_out)}

    for o, score, hits in scored[1:]:
        body_b = str(o.payload or "")
        if _jaccard(str(best_out.payload or ""), body_b) >= 0.72:
            continue
        if hits < best_hits and best_hits >= 1:
            continue
        if best_score >= 0.2 and score < 0.35 and (best_score - score) >= 0.15:
            continue
        keep_ids.add(id(o))

    if len(keep_ids) == len(substantive):
        return outputs

    kept: List[Output] = []
    for o in outputs:
        if _is_substantive_text_output(o) and id(o) not in keep_ids:
            continue
        kept.append(o)
    return kept if kept else [best_out]


_URL_RE = re.compile(r"\(?(https?://[^\s)]+)\)?", re.I)
_RUBRIC_JUNK = (
    "последние новости",
    "лента новостей",
    "новости сегодня",
    "новости россии и мира",
    "международные новости и срочные",
    "все последние новости",
    "/rubric/",
    "/rubrics/",
)

_SEARCH_PORTAL_TITLE_RE = re.compile(
    r"(?i)(?:sign[- ]?in|google\s+slides|google\s+workspace|"
    r"новости\s+mail\s*:|риа\s+новости\s*[-—]|"
    r"события\s+в\s+.+?\s+и\s+мире\s+сегодня|"
    r"темы\s+дня,\s*фото|картина\s+дня|"
    r"режиме\s+реального\s+времени|"
    r"новости\s*-\s*hi-tech|"
    r"новости\s+о\s+последних\s+законодательных|"
    r"аналитика\s+и\s+комментарии\s+экспертов|"
    r"все\s+актуальные\s+новости\s+россии|"
    r"news\.ru\s*-\s*главные)"
)

_PORTAL_URL_HOSTS = frozenset(
    {
        "docs.google.com",
        "accounts.google.com",
        "consent.google.com",
        "slides.google.com",
        "news.google.com",
        "otvet.mail.ru",
    }
)

_NON_NEWS_HOST_PARTS = frozenset(
    {
        "reddit.com",
        "meetup.com",
        "poki.com",
        "zhihu.com",
        "arrse.co.uk",
        "play.google.com",
        "apps.apple.com",
        "instagram.com",
        "facebook.com",
        "tiktok.com",
        "vk.com",
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
    }
)

_PORTAL_BRANDING_TITLE_RE = re.compile(
    r"(?i)(?:"
    r"новости\s+по\s+теме\s*:|"
    r"instagram|followers|\(\s*@\w+"
    r")"
)

_ARTICLE_PATH_RE = re.compile(
    r"(?i)/(?:20\d{2}|news|article|story|video|politics|world|incident|conflict)/[^/?#]{4,}"
)


def _news_items_cap_env(name: str, *, default: int, hard_max: int) -> int:
    try:
        n = int((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        n = default
    return max(5, min(n, hard_max))


def _news_max_items() -> int:
    return _news_items_cap_env("NEWS_DIRECT_MAX_ITEMS", default=7, hard_max=12)


def _news_snippet_max_chars() -> int:
    return _news_items_cap_env("NEWS_SNIPPET_MAX_CHARS", default=520, hard_max=900)


def _news_brief_hint_max_chars() -> int:
    return _news_items_cap_env("NEWS_BRIEF_HINT_MAX_CHARS", default=5500, hard_max=8000)


def _news_source_max_items() -> int:
    return _news_items_cap_env("NEWS_BRIEF_SOURCE_MAX_ITEMS", default=7, hard_max=10)


def _news_topic_from_query(user_query: str) -> str:
    """Тема из запроса без «какие новости» и прочего шума."""

    def _norm_topic(topic: str) -> str:
        t = _clip_words((topic or "").strip(), 52)
        low_t = t.lower()
        if low_t in {"мире", "мира", "мир", "в мире"}:
            return ""
        return t

    q = re.sub(r"\s+", " ", (user_query or "").strip()).strip("?!.")
    if not q:
        return ""
    low = q.lower()
    generic = {
        "какие новости",
        "новости",
        "что в новостях",
        "главные новости",
        "последние новости",
        "новости дня",
        "сводка новостей",
        "news",
    }
    if low in generic:
        return ""
    m = re.search(
        r"(?i)(?:какие|последние|главные|свежие|актуальные)\s+новости(?:\s+(?:про|о|об|в|из|на|по))?\s*(.+)$",
        q,
    )
    if m:
        topic = (m.group(1) or "").strip()
        if topic and topic.lower() not in {"какие", "сегодня", "сейчас"}:
            return _norm_topic(topic)
    m = re.search(r"(?i)новости(?:\s+(?:про|о|об|в|из|на|по))?\s+(.+)$", q)
    if m:
        topic = (m.group(1) or "").strip()
        if topic and topic.lower() not in {"какие", "сегодня", "сейчас"}:
            return _norm_topic(topic)
    if "новост" in low:
        m2 = re.search(r"(?i)(?:в|из|по)\s+(.+)$", q)
        if m2:
            return _norm_topic(m2.group(1).strip())
        return ""
    if len(q) <= 56:
        return _norm_topic(q)
    return ""


_RU_MONTH_GENITIVE = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _world_news_dated_header() -> str:
    """«Главные мировые новости на 30 мая 2026 года» — зона GEMMA_REPORT_TIMEZONE."""
    from datetime import datetime, timezone

    from core.report_timezone import get_report_tz

    loc = datetime.now(timezone.utc).astimezone(get_report_tz())
    month = _RU_MONTH_GENITIVE[loc.month] if 1 <= loc.month <= 12 else ""
    if month:
        return f"Главные мировые новости на {loc.day} {month} {loc.year} года\n\n"
    return "Главные мировые новости\n\n"


def _news_digest_header(user_query: str) -> str:
    """Короткий заголовок дайджеста без технич. метаданных."""
    q = (user_query or "").strip().lower()
    if any(k in q for k in ("в мире", "миров", "международ", "world news", "global", "какие новости")):
        return _world_news_dated_header()
    topic = _news_topic_from_query(user_query)
    if topic:
        return f"Новости — {topic}\n\n"
    return "Главные новости\n\n"


def _split_google_news_title(title: str, source_name: str = "") -> Tuple[str, str]:
    """Отделяет заголовок от названия издания (Google RSS: «… — Издание»)."""
    t = (title or "").strip()
    src = (source_name or "").strip()
    if not t:
        return "", src
    for sep in (" - ", " – ", " — ", " | "):
        if sep not in t:
            continue
        head, tail = t.rsplit(sep, 1)
        head, tail = head.strip(), tail.strip()
        if len(head) < 12 or len(tail) > 48:
            continue
        if src and tail.lower() != src.lower() and tail.lower() not in src.lower():
            if _jaccard(tail, src) < 0.45:
                continue
        return head, src or tail
    return t, src


def _publisher_label(row: Dict[str, Any]) -> str:
    name = str(row.get("source_name") or "").strip()
    if name:
        return name
    _, pub = _split_google_news_title(str(row.get("title") or ""), "")
    return pub


def _news_digest_show_snippets() -> bool:
    raw = (os.getenv("NEWS_DIGEST_SHOW_SNIPPETS") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _looks_like_disambiguation_snippet(snippet: str, title: str = "") -> bool:
    """Википедия/словари по слову из заголовка («Как: … см. Викисловарь»)."""
    s = (snippet or "").strip()
    if len(s) < 40:
        return False
    low = s.lower()
    if "викисловар" in low or ("википеди" in low and "см." in low):
        return True
    if re.match(r"(?i)^как\s*:", s[:64]):
        return True
    if s.count(" — ") >= 4 and ("см." in low or "род." in low or "сокр." in low):
        return True
    head = (title or "").strip().lower()
    if head.startswith("как ") and len(s) > 120 and s.count(" — ") >= 2:
        return True
    return False


def _format_news_item_block(
    n: int,
    *,
    title: str,
    snippet: str = "",
    publisher: str = "",
) -> str:
    headline = _clip_words((title or "").strip(), 220)
    if not headline:
        return ""
    lines = [f"{n}. {headline}"]
    sn = _clip_words((snippet or "").strip(), _news_snippet_max_chars())
    if (
        _news_digest_show_snippets()
        and sn
        and not _looks_like_disambiguation_snippet(sn, headline)
        and sn.lower() not in headline.lower()
        and not headline.lower().startswith(sn.lower()[:40])
    ):
        lines.append(f"   {sn}")
    pub = (publisher or "").strip()
    if pub and pub.lower() not in headline.lower():
        lines.append(f"   · {pub}")
    return "\n".join(lines)


def _domain_label(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    try:
        host = re.sub(r"^https?://(?:www\.)?", "", u.split("/")[2] if "//" in u else u).split("/")[0]
        return host.lower()
    except (IndexError, AttributeError):
        return ""


def _is_rubric_junk(title: str, url: str = "") -> bool:
    t = (title or "").strip().lower()
    u = (url or "").strip().lower()
    if len(t) < 12:
        return True
    if any(j in t for j in _RUBRIC_JUNK) or any(j in u for j in ("/rubric/", "/rubrics/")):
        return True
    if t.count("·") >= 3 or t.count("...") >= 2:
        return True
    return False


def _is_seo_kakie_listicle_title(title: str) -> bool:
    """SEO-заголовки «Какие данные…» / «Россиянам напомнили, какие…» — не новость."""
    t = (title or "").strip()
    if not t:
        return False
    if re.match(r"(?i)^какие\s", t):
        return True
    if re.search(r"(?i)\bкакие\s+(?:данные|виды|способ|робот|события|платформ)", t):
        return True
    if re.search(r"(?i)(?:напомнили|рассказал[io]?|рассказали|сообщили),?\s+какие\s", t):
        return True
    if re.search(r"(?i)^узнайте,?\s+какие\s", t):
        return True
    return False


def _host_is_non_news(url: str) -> bool:
    h = _domain_label(url)
    if not h:
        return False
    for part in _NON_NEWS_HOST_PARTS:
        if h == part or h.endswith("." + part):
            return True
    return False


def _url_looks_like_article(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http") or _url_is_portal_homepage(u) or _host_is_non_news(u):
        return False
    path = re.sub(r"^https?://(?:www\.)?[^/]+", "", u).strip("/").lower()
    if not path:
        return False
    if _ARTICLE_PATH_RE.search("/" + path + "/"):
        return True
    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2 and len(segs[-1]) >= 10:
        return True
    if len(segs) >= 3:
        return True
    return False


def _url_is_portal_homepage(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u.startswith("http"):
        return False
    if any(h in u for h in _PORTAL_URL_HOSTS):
        return True
    if "news.google.com/topics" in u:
        return True
    path = re.sub(r"^https?://(?:www\.)?[^/]+", "", u).strip("/").lower()
    if path in ("", "news", "ru", "en", "index.html", "index.php", "signin", "login", "allnews"):
        return True
    if path in ("world", "world/", "politics", "russia", "international"):
        return True
    if re.fullmatch(r"(?:ru|en|by|ua|world|news)(?:/index\.html)?", path):
        return True
    return False


def _snippet_is_seo_menu(snippet: str) -> bool:
    s = (snippet or "").strip()
    if len(s) < 80:
        return False
    if s.count("·") >= 2 or s.count("•") >= 3:
        return True
    caps = re.findall(r"[А-ЯЁ][а-яё]{3,}", s)
    return len(caps) >= 5 and len(s) > 180


def _title_looks_like_portal_branding(title: str) -> bool:
    """Заголовок раздела портала / соцсеть — не отдельная новость (часто без URL в SearX)."""
    t = (title or "").strip()
    if not t:
        return False
    if _PORTAL_BRANDING_TITLE_RE.search(t):
        return True
    if t.count("|") >= 2 and re.search(r"(?i)новост", t):
        return True
    if re.search(r"(?i)^(?:беларусь|россия|украина)\s*[\|·—]", t):
        return True
    if re.search(r"(?i)[\|·—]\s*новости\s+(?:беларуси|россии|украины|mail\s*ru)", t):
        return True
    if re.search(r"(?i)новости\s+об\s+общественно-политической", t):
        return True
    return False


def is_search_portal_junk(title: str, snippet: str = "", url: str = "") -> bool:
    """Главная портала / SEO-меню — не новость."""
    t = (title or "").strip()
    sn = (snippet or "").strip()
    if _title_looks_like_portal_branding(t):
        return True
    if _is_seo_kakie_listicle_title(t):
        return True
    if _host_is_non_news(url):
        return True
    if _is_rubric_junk(t, url):
        return True
    if _SEARCH_PORTAL_TITLE_RE.search(t):
        return True
    if _url_is_portal_homepage(url):
        return True
    if len(t) > 100 and t.lower().count("новост") >= 2:
        return True
    if _snippet_is_seo_menu(sn):
        return True
    if len(t) > 90 and ("события" in t.lower() or "картина дня" in t.lower()):
        return True
    return False


def _looks_like_news_story_row(title: str, snippet: str = "", url: str = "") -> bool:
    if is_search_portal_junk(title, snippet, url):
        return False
    t = (title or "").strip()
    sn = (snippet or "").strip()
    if _host_is_non_news(url):
        return False
    if re.search(r"(?i)\b(?:youtube|youtu\.be)\b", t) and re.search(
        r"(?i)\b(?:youtube|youtu\.be)\b", url
    ):
        return False
    if re.search(
        r"(?i)(главная\s*-|main page|sign[- ]?in|play now|русская служба|"
        r"новости\s+(?:мира|дня)\s*[-—|]|site:\s|meetup|reddit|r/[\w]+)",
        t,
    ):
        return False
    if re.match(r"(?i)^какие\s+(?:данные|виды|способы|платформ|робот|события)", t):
        return False
    if _is_seo_kakie_listicle_title(t):
        return False
    if _url_looks_like_article(url):
        return True
    if len(t) > 150:
        return False
    # Без URL статьи длинный SEO-сниппет главной портала не считаем новостью.
    if re.search(
        r"(?i)(сбил|удар|ранен|заявил|отменил|договор|переговор|кризис|"
        r"выбор|убит|арест|санкц|взрыв|обстрел|дрон|беспилот|лукашенко|"
        r"путин|санкци|договор|переговор)",
        t,
    ):
        return True
    if len(sn) >= 36 and not re.search(r"(?i)новости\s+об\s+", sn):
        if 28 <= len(t) <= 110 and not t.lower().startswith("новости"):
            return True
    return 28 <= len(t) <= 110 and not t.lower().startswith("новости")


def _clip_words(text: str, max_len: int) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) <= max_len:
        return s
    cut = s[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or s[: max_len - 1]) + "…"


def _split_line_to_entry(line: str) -> Tuple[str, str, str]:
    """title, snippet, url из строки веб-поиска."""
    raw = (line or "").strip()
    if not raw:
        return "", "", ""
    url_m = _URL_RE.search(raw)
    url = (url_m.group(1) if url_m else "").rstrip(").,;")
    text = _URL_RE.sub("", raw).strip(" -—:")
    if ";" in text and not url:
        parts = [p.strip() for p in text.split(";") if p.strip()]
        if len(parts) == 1:
            text = parts[0]
    if ":" in text:
        title, _, rest = text.partition(":")
        title, rest = title.strip(), rest.strip()
    else:
        title, rest = text, ""
    if rest:
        for sep in (" · ", " ... ", " | "):
            if sep in rest:
                rest = rest.split(sep, 1)[0].strip()
        rest = _clip_words(rest, _news_snippet_max_chars())
    title = _clip_words(title, 200)
    return title, rest, url


def build_news_llm_source_block(
    items: List[Dict[str, Any]],
    *,
    search_results: Optional[List[Dict[str, Any]]] = None,
    max_items: Optional[int] = None,
) -> str:
    """Сырые материалы для LLM: заголовок + сниппет + издание (не только «title; title»)."""
    cap = max_items if max_items is not None else _news_source_max_items()
    blocks: List[str] = []
    for row in items:
        if not isinstance(row, dict) or len(blocks) >= cap:
            continue
        raw_title = str(row.get("title") or "").strip()
        if not raw_title or _is_rubric_junk(raw_title, str(row.get("link") or "")):
            continue
        headline, _ = _split_google_news_title(raw_title, str(row.get("source_name") or ""))
        publisher = _publisher_label(row)
        snippet = _clip_words(
            str(row.get("snippet") or row.get("description") or "").strip(),
            _news_snippet_max_chars(),
        )
        n = len(blocks) + 1
        chunk = f"{n}. {headline}"
        if publisher:
            chunk += f"\n   Источник: {publisher}"
        if snippet:
            chunk += f"\n   Выдержка: {snippet}"
        else:
            chunk += "\n   Выдержка: (в ленте только заголовок — не дополняй фактами вне заголовка)"
        blocks.append(chunk)
    extra = search_results if isinstance(search_results, list) else []
    known_heads = [
        _split_google_news_title(str(r.get("title") or ""), str(r.get("source_name") or ""))[0].lower()
        for r in items
        if isinstance(r, dict)
    ]
    for row in extra:
        if len(blocks) >= cap + 2:
            break
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or row.get("content") or "").strip()
        if not title or not snippet:
            continue
        if any(_jaccard(title, h) >= 0.55 for h in known_heads if h):
            continue
        n = len(blocks) + 1
        blocks.append(
            f"{n}. {_clip_words(title, 200)}\n   Выдержка: {_clip_words(snippet, _news_snippet_max_chars())}"
        )
        known_heads.append(title.lower())
    return "\n\n".join(blocks).strip()


def enrich_news_items_with_snippets(
    items: List[Dict[str, Any]],
    search_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Подмешивает snippet из веб-поиска к RSS-заголовкам по пересечению слов."""
    if not items or not search_results:
        return items
    enriched: List[Dict[str, Any]] = []
    used: Set[int] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        out = dict(row)
        headline, _ = _split_google_news_title(
            str(row.get("title") or ""),
            str(row.get("source_name") or ""),
        )
        h_tokens = _token_set(headline)
        best_i = -1
        best_score = 0.0
        for i, sr in enumerate(search_results):
            if i in used or not isinstance(sr, dict):
                continue
            st = str(sr.get("title") or "").strip()
            if not st:
                continue
            score = _jaccard(headline, st)
            if h_tokens and _token_set(st):
                score = max(score, len(h_tokens & _token_set(st)) / max(len(h_tokens), 1))
            if score > best_score:
                best_score = score
                best_i = i
        try:
            min_score = float((os.getenv("NEWS_ENRICH_MIN_MATCH_SCORE") or "0.15").strip())
        except ValueError:
            min_score = 0.15
        if best_i >= 0 and best_score >= min_score:
            sn = str(search_results[best_i].get("snippet") or search_results[best_i].get("content") or "").strip()
            if sn and not _looks_like_disambiguation_snippet(sn, headline):
                out["snippet"] = _clip_words(sn, _news_snippet_max_chars())
            used.add(best_i)
        enriched.append(out)
    return enriched if enriched else items


_BY_NEWS_DOMAIN_BOOST = frozenset(
    {
        "news.example.com",
        "eng.news.example.com",
        "news3.example.com",
        "news2.example.com",
        "shop.example.com",
        "customs.gov.by",
        "gov.example.com",
    }
)

_RU_NEWS_DOMAIN_BOOST = frozenset(
    {
        "rbc.ru",
        "interfax.ru",
        "ria.ru",
        "tass.ru",
        "kommersant.ru",
    }
)

_SPORTS_DIGEST_RE = re.compile(
    r"(?i)(?:\bxi\b vs\b|champions league|predicted lineup|baseball in 20|"
    r"psg\b|arsenal fc|premier league|nfl\b|nba\b|super bowl|"
    r"football final|матч.*сегодня.*состав)",
)

_OFFTOPIC_DIGEST_RE = re.compile(
    r"(?i)(?:ps store|playstation store|sony about ps|"
    r"we might not have baseball|clickbait)",
)

_GENERIC_DIGEST_TITLE_RE = re.compile(
    r"(?i)^(?:главные\s+)?новости(?:\s+(?:дня|за\s+\d{1,2}\s+\w+))?\s*"
    r"(?:[\|·—-]\s*(?:четверг|пятниц|суббот|воскрес|понедель|вторник|сред|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday))?\s*[\|.:—-]*\s*$"
)

_FOREIGN_REGION_DIGEST_RE = re.compile(
    r"(?i)(?:казахстан|kazakhstan|tengrinews|vietnam\.vn|"
    r"канарск|canary islands|круиз.*вирус)",
)


def _cyrillic_ratio(text: str) -> float:
    s = (text or "").strip()
    if not s:
        return 0.0
    cyr = len(re.findall(r"[а-яё]", s, re.I))
    return cyr / max(len(s), 1)


def _looks_like_offtopic_digest_row(
    title: str,
    snippet: str = "",
    url: str = "",
    *,
    country: str = "",
    world_feed: bool = False,
) -> bool:
    t = (title or "").strip()
    sn = (snippet or "").strip()
    blob = f"{t} {sn}".lower()
    if _SPORTS_DIGEST_RE.search(t) or _SPORTS_DIGEST_RE.search(sn):
        if not re.search(r"(?i)(беларус|belarus|лукашенко|minsk|минск)", blob):
            return True
    if _OFFTOPIC_DIGEST_RE.search(t):
        return True
    if _GENERIC_DIGEST_TITLE_RE.match(t):
        return True
    co = (country or "").strip().upper()
    if co == "BY" and not world_feed:
        if _FOREIGN_REGION_DIGEST_RE.search(blob):
            if not re.search(r"(?i)(беларус|belarus|лукашенко|minsk|минск)", blob):
                return True
    if "wikinews.org" in (url or "").lower():
        if not re.search(r"(?i)(беларус|belarus|украин|ukraine|росси|russia)", blob):
            if "kazakhstan" in blob or "sony" in blob or "ps store" in blob:
                return True
    if co == "BY" and not world_feed:
        if _cyrillic_ratio(t) < 0.08 and _cyrillic_ratio(sn) < 0.08:
            pub = _domain_label(url)
            if pub not in _BY_NEWS_DOMAIN_BOOST and "belarus" not in blob and "беларус" not in blob:
                return True
    return False


def _news_row_relevance_score(
    title: str,
    snippet: str,
    url: str,
    *,
    country: str = "",
    world_feed: bool = False,
) -> float:
    score = 1.0
    t = (title or "").strip()
    sn = (snippet or "").strip()
    blob = f"{t} {sn}".lower()
    pub = _domain_label(url)
    co = (country or "").strip().upper()
    if co == "BY" and not world_feed:
        if pub in _BY_NEWS_DOMAIN_BOOST or pub.endswith(".by"):
            score += 4.0
        if "беларус" in blob or "belarus" in blob or "лукашенко" in blob or "lukashenko" in blob:
            score += 3.0
        score += _cyrillic_ratio(t) * 2.5 + _cyrillic_ratio(sn) * 1.5
        if pub in {"reuters.com", "eadaily.com"} and ("belarus" in blob or "беларус" in blob):
            score += 2.0
    elif co == "RU" and not world_feed:
        if pub in _RU_NEWS_DOMAIN_BOOST:
            score += 3.0
        score += _cyrillic_ratio(t) * 2.0
    elif world_feed:
        if pub in {"reuters.com", "bbc.com", "apnews.com", "news.un.org"}:
            score += 2.0
    if _url_looks_like_article(url):
        score += 1.5
    if len(sn) >= 80:
        score += 0.8
    if _is_seo_kakie_listicle_title(t):
        score -= 5.0
    return score


def collect_news_display_items_from_search(
    results: List[Dict[str, Any]],
    *,
    user_query: str = "",
    country: str = "",
    world_feed: bool = False,
    require_article_url: bool = False,
) -> List[Dict[str, Any]]:
    """Пункты дайджеста из UniversalSearch/SearX — без Google News RSS."""
    cap = _news_max_items()
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("link") or "").strip()
        snippet = str(row.get("snippet") or row.get("content") or "").strip()
        if not title or len(title) < 12:
            continue
        if require_article_url:
            if not url or not _url_looks_like_article(url):
                continue
        if not _looks_like_news_story_row(title, snippet, url):
            continue
        if _looks_like_offtopic_digest_row(
            title, snippet, url, country=country, world_feed=world_feed
        ):
            continue
        publisher = ""
        if url.startswith("http"):
            dm = re.match(r"^https?://(?:www\.)?([^/]+)", url, re.I)
            if dm:
                publisher = dm.group(1).lower()
        score = _news_row_relevance_score(
            title, snippet, url, country=country, world_feed=world_feed
        )
        if score < 0.5:
            continue
        item = {
            "title": _clip_words(title, 220),
            "publisher": publisher,
            "snippet": _clip_words(snippet, _news_snippet_max_chars()) if snippet else "",
            "link": url,
            "google_link": "",
            "source_url": url,
            "_score": score,
        }
        candidates.append((score, item))
    candidates.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    domain_counts: Dict[str, int] = {}
    max_per_domain = 2 if len(candidates) <= cap * 2 else 1
    for _sc, item in candidates:
        pub = str(item.get("publisher") or "")
        if pub and domain_counts.get(pub, 0) >= max_per_domain:
            continue
        if pub:
            domain_counts[pub] = domain_counts.get(pub, 0) + 1
        item = dict(item)
        item.pop("_score", None)
        item["index"] = len(out) + 1
        out.append(item)
        if len(out) >= cap:
            break
    return out


def collect_news_display_items(
    items: List[Dict[str, Any]],
    *,
    user_query: str = "",
) -> List[Dict[str, Any]]:
    """Пункты дайджеста в том же порядке и с теми же заголовками, что в Telegram (со ссылками)."""
    cap = _news_max_items()
    seen_publishers: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        raw_title = str(row.get("title") or "").strip()
        url = str(row.get("link") or row.get("url") or "").strip()
        if not raw_title or _is_rubric_junk(raw_title, url):
            continue
        headline, pub_from_title = _split_google_news_title(
            raw_title,
            str(row.get("source_name") or ""),
        )
        publisher = _publisher_label(row) or pub_from_title
        pub_key = publisher.lower()
        if pub_key and pub_key in seen_publishers:
            continue
        if pub_key:
            seen_publishers.add(pub_key)
        src_url = str(row.get("source") or "").strip()
        g_link = url
        fetch_url = g_link if g_link.startswith("http") else ""
        if src_url.startswith("http"):
            src_path = re.sub(r"^https?://(?:www\.)?[^/]+", "", src_url).strip("/")
            if len(src_path) > 12 and "/" in src_path:
                fetch_url = fetch_url or src_url
        out.append(
            {
                "index": len(out) + 1,
                "title": headline,
                "publisher": publisher,
                "snippet": str(row.get("snippet") or row.get("description") or "").strip(),
                "link": fetch_url or g_link or src_url,
                "google_link": g_link,
                "source_url": src_url,
            }
        )
        if len(out) >= cap:
            break
    return out


def format_news_from_displayed(displayed: List[Dict[str, Any]], *, user_query: str = "") -> str:
    """Дайджест из уже собранных пунктов (после enrich в stash)."""
    blocks: List[str] = []
    for row in displayed:
        if not isinstance(row, dict):
            continue
        block = _format_news_item_block(
            int(row.get("index") or len(blocks) + 1),
            title=str(row.get("title") or ""),
            snippet=str(row.get("snippet") or ""),
            publisher=str(row.get("publisher") or ""),
        )
        if block:
            blocks.append(block)
    if not blocks:
        return ""
    head = _news_digest_header(user_query)
    return _finish_news_digest(head + "\n\n".join(blocks))


def format_news_from_items(items: List[Dict[str, Any]], *, user_query: str = "") -> str:
    """Читаемый дайджест из Google News RSS: суть + издание, без URL."""
    displayed = collect_news_display_items(items, user_query=user_query)
    blocks: List[str] = []
    for row in displayed:
        block = _format_news_item_block(
            int(row.get("index") or len(blocks) + 1),
            title=str(row.get("title") or ""),
            snippet=str(row.get("snippet") or ""),
            publisher=str(row.get("publisher") or ""),
        )
        if block:
            blocks.append(block)
    if not blocks:
        return ""
    head = _news_digest_header(user_query)
    return _finish_news_digest(head + "\n\n".join(blocks))


def _news_brief_footer() -> str:
    raw = (os.getenv("NEWS_BRIEF_FOOTER") or "").strip()
    if raw.lower() in {"0", "false", "off", "no"}:
        return ""
    if raw:
        return raw
    return "Напишите номер пункта или «развёрнуто» — расскажу подробнее."


def _news_narrative_footer(*, world_feed: bool = False, user_query: str = "") -> str:
    """Футер при NEWS_DIGEST_FORMAT=narrative."""
    raw = (os.getenv("NEWS_NARRATIVE_FOOTER") or "").strip()
    if raw.lower() in {"0", "false", "off", "no"}:
        return ""
    if raw:
        return raw
    q = (user_query or "").strip().lower()
    if world_feed or any(
        k in q for k in ("в мире", "миров", "международ", "world news", "global", "какие новости")
    ):
        return "Составлено на основе информации открытых источников."
    return (
        "Если какая-то тема интересна глубже — напиши своими словами, например: "
        "«расскажи про беспилотник в Румынии». Или номер пункта / «развёрнуто»."
    )


def parse_numbered_news_digest_items(body: str) -> List[Dict[str, Any]]:
    """Разбор нумерованного дайджеста (1. заголовок / · издание) из текста ассистента."""
    text = (body or "").strip()
    if not text:
        return []
    items: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        num_m = re.match(r"^\s*(\d{1,2})\.\s+(.+)$", line)
        if num_m:
            if current:
                items.append(current)
            current = {
                "index": int(num_m.group(1)),
                "title": num_m.group(2).strip(),
                "publisher": "",
                "snippet": "",
            }
            continue
        if current is None:
            continue
        pub_m = re.match(r"^\s*[·•]\s+(.+)$", line)
        if pub_m:
            current["publisher"] = pub_m.group(1).strip()
            continue
        sn_m = re.match(r"^\s{2,}(.+)$", line)
        if sn_m:
            sn = sn_m.group(1).strip()
            low = sn.lower()
            if "напишите номер" in low or low.startswith("напишите «развёрнуто»"):
                continue
            if sn and not current.get("snippet"):
                current["snippet"] = sn
    if current:
        items.append(current)
    return items


def _finish_news_digest(body: str, *, add_brief_footer: bool = True) -> str:
    t = (body or "").strip()
    foot = _news_brief_footer() if add_brief_footer else ""
    if foot and foot.lower() not in t.lower():
        t = f"{t}\n\n{foot}"
    return t


def _summary_looks_like_reference_blob(summary: str) -> bool:
    """Wikipedia/словарная статья вместо заголовков — не дайджест."""
    s = (summary or "").strip()
    if not s:
        return False
    if re.match(r"(?i)^новости\s*[—:-]\s*информация", s):
        return True
    if "— информация, которая представляет" in s.lower():
        return True
    if s.count(";") < 1 and len(s) > 120 and " · " not in s and " - " not in s[:80]:
        if re.search(r"(?i)(?:это\s+|является\s+|представляет\s+собой)", s):
            return True
    return False


def _parse_summary_to_search_rows(summary: str) -> List[Dict[str, Any]]:
    """Строки сводки DDG → dict для collect_news_display_items_from_search."""
    body = (summary or "").strip()
    if not body or _summary_looks_like_reference_blob(body):
        return []
    parsed: List[Tuple[str, str, str]] = []
    if ";" in body and "\n" not in body:
        for chunk in body.split(";"):
            t, s, u = _split_line_to_entry(chunk)
            if t and not _is_rubric_junk(t, u):
                parsed.append((t, s, u))
    else:
        for ln in body.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            m = re.match(r"^(\d+)[.)]\s*(.+)$", ln)
            chunk = m.group(2).strip() if m else re.sub(r"^[-•*]\s*", "", ln).strip()
            t, s, u = _split_line_to_entry(chunk)
            if t and not _is_rubric_junk(t, u):
                parsed.append((t, s, u))
    rows: List[Dict[str, Any]] = []
    for title, snippet, url in parsed:
        headline, pub_from_title = _split_google_news_title(title, "")
        publisher = pub_from_title or _domain_label(url)
        rows.append(
            {
                "title": headline or title,
                "snippet": snippet,
                "url": url,
                "publisher": publisher,
            }
        )
    return rows


def format_news_from_search(
    summary: str,
    *,
    user_query: str = "",
    country: str = "",
    world_feed: bool = False,
) -> str:
    """Форматирует сводку веб-поиска без LLM — с теми же фильтрами, что и search digest."""
    body = (summary or "").strip()
    if not body:
        return ""
    if _summary_looks_like_reference_blob(body):
        return ""
    rows = _parse_summary_to_search_rows(body)
    if rows:
        displayed = collect_news_display_items_from_search(
            rows, user_query=user_query, country=country, world_feed=world_feed
        )
        if displayed:
            return format_news_from_displayed(displayed, user_query=user_query)
    return ""


def format_news_loose_from_summary(summary: str, *, user_query: str = "") -> str:
    """Brain prefetch / repair: сводка без strict collect (отказ LLM, tool leak)."""
    body = (summary or "").strip()
    if not body or _summary_looks_like_reference_blob(body):
        return ""
    chunks: List[str] = []
    if ";" in body and "\n" not in body:
        chunks = [c.strip() for c in body.split(";") if c.strip()]
    else:
        chunks = [ln.strip() for ln in body.splitlines() if ln.strip()]
    cap = _news_max_items()
    blocks: List[str] = []
    for chunk in chunks:
        if len(blocks) >= cap:
            break
        t, sn, u = _split_line_to_entry(chunk)
        headline = (t or "").strip()
        snippet = (sn or "").strip()
        publisher = _domain_label(u) or ""
        if headline and snippet and len(headline) < 24 and ":" not in headline:
            publisher = publisher or headline
            headline = snippet
            snippet = ""
        elif headline and snippet and len(headline) < 20:
            publisher = publisher or headline
            headline = snippet
            snippet = ""
        if not headline or len(headline) < 8:
            continue
        if _is_seo_kakie_listicle_title(headline) or is_search_portal_junk(headline, snippet, u):
            continue
        if not publisher:
            _, pub_from_title = _split_google_news_title(headline, "")
            publisher = pub_from_title
        block = _format_news_item_block(
            len(blocks) + 1,
            title=headline,
            snippet=snippet,
            publisher=publisher if publisher and not publisher.startswith("http") else "",
        )
        if block:
            blocks.append(block)
    if not blocks:
        return ""
    head = _news_digest_header(user_query)
    return _finish_news_digest(head + "\n\n".join(blocks))


def trim_hallucinated_news_bullets(text: str, *, max_items: Optional[int] = None) -> str:
    """Обрезает нумерованный список в ответе LLM (лишние пункты 8–12)."""
    s = (text or "").strip()
    if not s:
        return s
    cap = max_items if max_items is not None else int((os.getenv("NEWS_REPLY_MAX_ITEMS") or "7").strip() or "7")
    cap = max(3, min(cap, 15))
    lines = s.splitlines()
    out: List[str] = []
    bullet_n = 0
    for ln in lines:
        m = re.match(r"^(\d+)[.)]\s+", ln.strip())
        if m:
            bullet_n += 1
            if bullet_n > cap:
                continue
        out.append(ln)
    if bullet_n > cap and out:
        # Уже сказано в заголовке дайджеста; не дублируем «обрубок» внизу.
        pass
    return "\n".join(out).strip()


def _photo_dedup_ttl_sec() -> float:
    raw = (os.getenv("TELEGRAM_PHOTO_DEDUP_SEC") or "12").strip()
    try:
        return max(3.0, min(float(raw), 120.0))
    except ValueError:
        return 12.0


def should_skip_duplicate_photo_turn(user_id: str, chat_id: str, file_unique_id: str) -> bool:
    """Повтор того же file_unique_id за короткое окно (двойной update Telegram)."""
    if not _env_flag("TELEGRAM_PHOTO_DEDUP_ENABLED", True):
        return False
    fid = (file_unique_id or "").strip()
    if not fid:
        return False
    key = f"{user_id}:{chat_id}:{fid}"
    now = time.monotonic()
    ttl = _photo_dedup_ttl_sec()
    with _PHOTO_DEDUP_LOCK:
        prev = _RECENT_PHOTO_TURNS.get(key)
        _RECENT_PHOTO_TURNS[key] = now
        if len(_RECENT_PHOTO_TURNS) > 500:
            cutoff = now - ttl * 4
            for k in list(_RECENT_PHOTO_TURNS.keys()):
                if _RECENT_PHOTO_TURNS[k] < cutoff:
                    del _RECENT_PHOTO_TURNS[k]
    return prev is not None and (now - prev) < ttl


def dedupe_identical_text_outputs(outputs: List[Output]) -> List[Output]:
    """Два одинаковых текста подряд (например двойное «не распознан»)."""
    if not outputs:
        return outputs
    seen: List[str] = []
    kept: List[Output] = []
    for o in outputs:
        if o.type != "text":
            kept.append(o)
            continue
        body = str(o.payload or "").strip()
        if not body:
            kept.append(o)
            continue
        norm = body.lower()
        if any(_jaccard(norm, s) >= 0.92 for s in seen):
            continue
        seen.append(norm)
        kept.append(o)
    return kept if kept else outputs
