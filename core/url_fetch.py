"""
Безопасная загрузка текстового содержимого по HTTP(S) для инструментов мозга.
Защита от SSRF: только публичные адреса после DNS, ручные редиректы с проверкой каждого шага.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from html.parser import HTMLParser
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse, urljoin

import aiohttp

logger = logging.getLogger(__name__)

_FALLBACK_BOT_UA = "GemmaAgent/1.0 (+https://github.com/gemma-agent/gemma-agent; url fetch)"
_DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _effective_user_agent() -> str:
    """UA из HTTP_USER_AGENT; при URL_FETCH_BROWSER_COMPAT (по умолчанию вкл.) — Chrome UA, если не задан свой и не GemmaAgent."""
    raw = os.getenv("HTTP_USER_AGENT", "").strip()
    if _env_flag("URL_FETCH_BROWSER_COMPAT", default=True):
        if not raw or raw.lower().startswith("gemmabot/"):
            return _DEFAULT_BROWSER_UA
    if raw:
        return raw
    return _FALLBACK_BOT_UA


def _fetch_request_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": _effective_user_agent(),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    }
    if _env_flag("URL_FETCH_BROWSER_COMPAT", default=True):
        lang = (os.getenv("URL_FETCH_ACCEPT_LANGUAGE") or "").strip()
        headers["Accept-Language"] = lang or "en-US,en;q=0.9,ru;q=0.8,en;q=0.7"
        headers["Cache-Control"] = "no-cache"
        headers["Upgrade-Insecure-Requests"] = "1"
    return headers


def _max_bytes() -> int:
    try:
        return max(4096, int(os.getenv("URL_FETCH_MAX_BYTES", "2097152")))
    except ValueError:
        return 2097152


def _max_redirects() -> int:
    try:
        return max(0, min(15, int(os.getenv("URL_FETCH_MAX_REDIRECTS", "5"))))
    except ValueError:
        return 5


def _timeout() -> aiohttp.ClientTimeout:
    try:
        sec = max(3.0, float(os.getenv("URL_FETCH_TIMEOUT_SEC", "15")))
    except ValueError:
        sec = 15.0
    return aiohttp.ClientTimeout(total=sec)


def _max_chars_out() -> int:
    try:
        return max(2000, int(os.getenv("URL_FETCH_MAX_CHARS_RESPONSE", "12000")))
    except ValueError:
        return 12000


def _allowlist_hosts() -> Set[str]:
    raw = os.getenv("URL_FETCH_ALLOWLIST", "").strip()
    if not raw:
        return set()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _blocked_hostnames() -> Set[str]:
    raw = os.getenv("URL_FETCH_BLOCKLIST_HOSTS", "").strip()
    defaults = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
        "169.254.169.254",
    }
    if not raw:
        return defaults
    extra = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return defaults | extra


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved:
        return True
    if addr.version == 6:
        mapped = getattr(addr, "ipv4_mapped", None)
        if mapped is not None and ipaddress.ip_address(mapped).is_private:
            return True
    if addr.version == 4:
        if int(addr) == int(ipaddress.IPv4Address("169.254.169.254")):
            return True
    return False


def _dns_all_public(hostname: str) -> Tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        return False, f"dns error: {e}"
    seen: Set[str] = set()
    for info in infos:
        ip = info[4][0]
        if ip in seen:
            continue
        seen.add(ip)
        if _is_private_ip(ip):
            return False, f"blocked address {ip}"
    if not seen:
        return False, "no addresses"
    return True, ""


def _validate_http_url(url: str) -> Tuple[bool, str]:
    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, f"parse error: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, "only http/https"
    if parsed.username is not None or parsed.password is not None:
        return False, "credentials in url not allowed"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "missing host"
    if host in _blocked_hostnames():
        return False, "host blocked"
    if host.endswith(".localhost") or host.endswith(".local"):
        return False, "local host blocked"
    allow = _allowlist_hosts()
    if allow:
        if not any(host == a or host.endswith("." + a) for a in allow):
            return False, "host not in URL_FETCH_ALLOWLIST"
    ok, err = _dns_all_public(host)
    if not ok:
        return False, err
    return True, ""


class _HTMLToText(HTMLParser):
    _SKIP = frozenset(
        {
            "script",
            "style",
            "noscript",
            "template",
            "head",
            "meta",
            "link",
            "svg",
            "iframe",
            "object",
            "embed",
            "audio",
            "video",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._suppress_stack: List[bool] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        from core.untrusted_content_sanitize import html_attrs_hidden

        t = tag.lower()
        parent = self._suppress_stack[-1] if self._suppress_stack else False
        here = parent or t in self._SKIP or html_attrs_hidden(attrs)
        self._suppress_stack.append(here)
        if not here and t in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._suppress_stack:
            self._suppress_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._suppress_stack and self._suppress_stack[-1]:
            return
        if data:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n{3,}", "\n\n", raw)).strip()


def _http_status_from_error_message(err: str) -> int:
    m = re.match(r"http\s+(\d{3})\s*$", (err or "").strip(), re.I)
    if m:
        return int(m.group(1))
    return 0


def _mirror_fallback_statuses() -> Set[int]:
    raw = (os.getenv("URL_FETCH_MIRROR_HTTP_STATUSES") or "401,403,429,451,503").strip()
    out: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out or {401, 403, 429, 451, 503}


def _mirror_reader_wrap_url(target: str) -> str:
    """Jina Reader: https://r.jina.ai/https://example.com/path — target уже прошёл SSRF-проверку."""
    base = (os.getenv("URL_FETCH_MIRROR_BASE") or "https://r.jina.ai/").strip()
    if not base.endswith("/"):
        base += "/"
    t = (target or "").strip()
    if not t:
        return ""
    return f"{base}{t}"


def _mirror_fallback_eligible(original_url: str) -> bool:
    if not _env_flag("URL_FETCH_MIRROR_FALLBACK", default=True):
        return False
    host = (urlparse(original_url).hostname or "").lower()
    if not host or host == "r.jina.ai" or host.endswith(".jina.ai"):
        return False
    return True


async def _safe_fetch_raw_once(url: str) -> Dict[str, Any]:
    """
    Один HTTP-цикл без зеркал. Возвращает:
    {ok, url, raw, content_type, http_status} или {error, url?, ...}.
    """
    url = (url or "").strip()
    if not url:
        return {"error": "url required"}
    ok, err = _validate_http_url(url)
    if not ok:
        return {"error": err, "url": url}

    headers = _fetch_request_headers()
    current = url
    max_r = _max_redirects()
    limit = _max_bytes()
    timeout = _timeout()
    final_status = 0
    ctype = ""
    raw = b""

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for _ in range(max_r + 2):
                ok, err = _validate_http_url(current)
                if not ok:
                    return {"error": f"redirect target invalid: {err}", "url": current}
                async with session.get(current, allow_redirects=False) as resp:
                    final_status = resp.status
                    if resp.status in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("Location") or ""
                        if not loc:
                            return {"error": "redirect without Location", "url": current}
                        current = urljoin(str(resp.url), loc)
                        continue
                    ctype = resp.headers.get("Content-Type", "") or ""
                    if resp.status != 200:
                        snippet = (await resp.text())[:500]
                        return {
                            "error": f"http {resp.status}",
                            "url": current,
                            "snippet": snippet,
                        }
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(65536):
                        buf.extend(chunk)
                        if len(buf) >= limit:
                            break
                    raw = bytes(buf[:limit])
                    current = str(resp.url)
                    break
            else:
                return {"error": "too many redirects", "url": current}
    except aiohttp.ClientError as e:
        logger.warning("[url_fetch] client error: %s", e)
        return {"error": str(e), "url": url}
    except Exception as e:
        detail = str(e).strip() or repr(e)
        logger.exception("[url_fetch] unexpected: %s url=%s", detail, url)
        return {"error": detail, "url": url}

    return {
        "ok": True,
        "url": current,
        "raw": raw,
        "content_type": ctype,
        "http_status": final_status,
    }


async def safe_fetch_raw(url: str) -> Dict[str, Any]:
    """
    Безопасная загрузка. При 401/403/429/… у исходного хоста (настраивается) — повтор через
    публичный reader-прокси (по умолчанию r.jina.ai), чтобы обойти часть антибот-ограничений.
    """
    original = (url or "").strip()
    first = await _safe_fetch_raw_once(original)
    if first.get("ok"):
        return first
    if not _mirror_fallback_eligible(original):
        return first
    code = _http_status_from_error_message(str(first.get("error") or ""))
    if code not in _mirror_fallback_statuses():
        return first
    wrapped = _mirror_reader_wrap_url(original)
    ok_w, err_w = _validate_http_url(wrapped)
    if not ok_w:
        logger.debug("[url_fetch] mirror url invalid: %s", err_w)
        return first
    second = await _safe_fetch_raw_once(wrapped)
    if not second.get("ok"):
        return first
    logger.info("[url_fetch] mirror fallback ok for %s (was http %s)", original, code)
    second["mirror_used"] = "jina_reader"
    second["original_request_url"] = original
    return second


def _max_article_images() -> int:
    try:
        n = int((os.getenv("URL_FETCH_MAX_ARTICLE_IMAGES") or "3").strip())
    except ValueError:
        n = 3
    return max(0, min(n, 6))


def _normalize_image_url(src: str, base_url: str) -> str:
    s = (src or "").strip()
    if not s or s.startswith("data:"):
        return ""
    try:
        u = urljoin(base_url, s)
    except Exception:
        return ""
    if not u.startswith("http"):
        return ""
    low = u.lower()
    if any(x in low for x in ("pixel", "tracker", "1x1", "spacer", "logo.svg", "icon.")):
        return ""
    if low.endswith((".svg", ".ico", ".gif")) and "og:image" not in low:
        return ""
    return u


def _extract_image_urls_from_html(html: str, base_url: str) -> List[str]:
    """og:image и крупные img со страницы статьи."""
    cap = _max_article_images()
    if cap <= 0 or not (html or "").strip():
        return []
    out: List[str] = []
    seen: Set[str] = set()
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        og = soup.find("meta", attrs={"property": "og:image"})
        if og:
            u = _normalize_image_url(str(og.get("content") or ""), base_url)
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        tw = soup.find("meta", attrs={"name": re.compile(r"^twitter:image", re.I)})
        if tw and len(out) < cap:
            u = _normalize_image_url(str(tw.get("content") or ""), base_url)
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        for img in soup.find_all("img"):
            if len(out) >= cap:
                break
            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
                or ""
            )
            u = _normalize_image_url(str(src), base_url)
            if not u or u in seen:
                continue
            try:
                w = int(str(img.get("width") or "0"))
                h = int(str(img.get("height") or "0"))
            except ValueError:
                w, h = 0, 0
            if w and h and (w < 120 or h < 80):
                continue
            seen.add(u)
            out.append(u)
    except Exception as e:
        logger.debug("extract_image_urls: %s", e)
    return out[:cap]


def _body_to_text(content: bytes, content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    charset = "utf-8"
    if "charset=" in (content_type or "").lower():
        m = re.search(r"charset=([\w\-]+)", content_type, re.I)
        if m:
            charset = m.group(1).strip() or charset
    try:
        text = content.decode(charset, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")

    if "html" in ct or text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:500].lower():
        from core.untrusted_content_sanitize import sanitize_untrusted_text, strip_html_comments

        text = strip_html_comments(text)
        parser = _HTMLToText()
        try:
            parser.feed(text)
            parser.close()
            plain = parser.text()
        except Exception:
            plain = re.sub(r"<[^>]+>", " ", text)
        plain, _ = sanitize_untrusted_text(plain, source="url_fetch")
        return plain
    return text


class UrlFetchModule:
    """Инструмент: UrlFetch.fetch_page — текст страницы по URL (документация, статьи)."""

    async def fetch_page(self, url: str, user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        # city/country/… — LLM часто передаёт лишние поля; URL здесь не строим из города.
        include_images = bool(kwargs.get("include_images"))
        if not _env_flag("URL_FETCH_ENABLED", True):
            return {"error": "url fetch disabled (URL_FETCH_ENABLED=false)"}

        url = (url or "").strip()
        if not url:
            return {"error": "url required"}

        ok, err = _validate_http_url(url)
        if not ok:
            return {"error": err, "url": url}

        if _env_flag("URL_FETCH_USE_SITE_RECIPE", False) and os.getenv(
            "SITE_RECIPE_ENABLED", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            from core.site_recipe_module import SiteRecipeModule

            alt = await SiteRecipeModule().parse_with_recipe(url, str(user_id or ""))
            if alt.get("ok"):
                return {
                    "ok": True,
                    "url": alt.get("url"),
                    "title": alt.get("title") or "",
                    "content_type": alt.get("content_type", ""),
                    "bytes_read": alt.get("bytes_read", 0),
                    "http_status": alt.get("http_status", 0),
                    "text": alt.get("text", ""),
                    "truncated": bool(alt.get("truncated")),
                    "hint": alt.get("hint")
                    or "Через SiteRecipe: запрос URL + рецепт (см. mode в оркестраторе при необходимости).",
                    "site_recipe_mode": alt.get("mode"),
                    "host": alt.get("host"),
                    "recipe_source": alt.get("recipe_source"),
                }
            if alt.get("error"):
                return alt

        got = await safe_fetch_raw(url)
        if got.get("error"):
            out = dict(got)
            try:
                host = (urlparse(url).hostname or "").strip().lower()
            except Exception:
                host = ""
            site_part = f"site:{host} " if host else ""
            err_s = str(got.get("error") or "")
            out["hint"] = (
                f"Загрузка не удалась ({err_s}). Прямой запрос и при необходимости зеркало уже использованы. "
                f"Дальше: UniversalSearch.search с query «{site_part}…» по сути вопроса; "
                "повторный UrlFetch с другим URL из выдачи; бинарный файл пользователю — /filefrom с прямой https-ссылкой."
            )
            return out
        raw = got.get("raw")
        if raw is None:
            logger.error(
                "[url_fetch] fetch_page: response missing raw body keys=%s url=%s",
                list(got.keys()),
                url,
            )
            return {
                "error": "internal: response missing raw body",
                "url": url,
                "http_status": got.get("http_status"),
            }
        ctype = str(got.get("content_type") or "")
        current = str(got.get("url") or url)
        try:
            final_status = int(got.get("http_status") or 0)
        except (TypeError, ValueError):
            final_status = 0

        text = _body_to_text(raw, ctype)
        max_out = _max_chars_out()
        truncated = len(text) > max_out
        text_out = text[:max_out] + "\n… [truncated]" if truncated else text

        title = ""
        head = raw[:65536].decode("utf-8", errors="ignore")
        m = re.search(r"<title[^>]*>([^<]{1,300})", head, re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()

        images: List[str] = []
        if include_images and "html" in (ctype or "").lower():
            images = _extract_image_urls_from_html(head, current)

        from core.untrusted_content_sanitize import untrusted_external_hint

        hint = untrusted_external_hint() + " Используй text как источник фактов."
        if got.get("mirror_used"):
            hint = (
                "Текст через зеркало чтения (прямой запрос отклонён). "
                + untrusted_external_hint()
                + " Для юридических формулировок — сверка в браузере."
            )

        out: Dict[str, Any] = {
            "ok": True,
            "url": current,
            "title": title,
            "content_type": (ctype.split(";")[0] or "").strip(),
            "bytes_read": len(raw),
            "http_status": final_status,
            "text": text_out,
            "truncated": truncated,
            "hint": hint,
            "images": images,
        }
        if got.get("mirror_used"):
            out["mirror_used"] = got.get("mirror_used")
            out["original_request_url"] = got.get("original_request_url") or ""
        return out
