from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote as url_quote, quote, urlencode, urlparse, unquote

import aiohttp

# Public, keyless providers: Open-Meteo, Frankfurter, Wikipedia, DuckDuckGo, Google News RSS.
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=12)
USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "GemmaAgent/1.0 (+https://github.com/gemma-agent/gemma-agent; keyless external facts)",
)

_WMO_LABELS: Dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm, slight hail",
    99: "thunderstorm, heavy hail",
}

_WMO_LABELS_RU: Dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "слабая морось",
    53: "морось",
    55: "сильная морось",
    56: "слабая ледяная морось",
    57: "сильная ледяная морось",
    61: "слабый дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "слабый ледяной дождь",
    67: "сильный ледяной дождь",
    71: "слабый снег",
    73: "снег",
    75: "сильный снег",
    77: "снежные зёрна",
    80: "ливни",
    81: "умеренные ливни",
    82: "сильные ливни",
    85: "слабые снегопады",
    86: "сильные снегопады",
    95: "гроза",
    96: "гроза, слабый град",
    99: "гроза, сильный град",
}

_RU_MONTH_GEN = (
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


def _wmo_label(code: Optional[int], *, ru: bool = False) -> str:
    if code is None:
        return "неизвестно" if ru else "unknown"
    c = int(code)
    table = _WMO_LABELS_RU if ru else _WMO_LABELS
    return table.get(c, f"код {c}" if ru else f"code {c}")


def _norm_loc_token(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _admin1_matches_hint(admin1: str, hint: str) -> bool:
    """Сопоставить admin1 Open-Meteo с подсказкой (minsk / Example Region / …)."""
    h = _norm_loc_token((hint or "").replace("ё", "е"))
    a = _norm_loc_token((admin1 or "").replace("ё", "е"))
    if not h or not a:
        return False
    if h in ("minsk", "минск", "minsk region", "минская область"):
        return "minsk" in a or "минск" in a
    if h in ("mogilev", "могилев", "mogilev region", "могилевская область"):
        return "mogilev" in a or "могил" in a
    if h in ("grodno", "гродно"):
        return "grodno" in a or "гродн" in a
    if h in ("brest", "брест"):
        return "brest" in a or "брест" in a
    if h in ("vitebsk", "витебск"):
        return "vitebsk" in a or "витебск" in a
    if h in ("gomel", "гомель"):
        return "gomel" in a or "гомел" in a
    return h in a or a in h


def _pick_geo_result(
    results: List[Dict[str, Any]],
    *,
    country: str,
    admin1_hint: str,
) -> Optional[Dict[str, Any]]:
    if not results:
        return None

    def _pop(x: Dict[str, Any]) -> int:
        try:
            return int(x.get("population") or 0)
        except (TypeError, ValueError):
            return 0

    pool_cap = results[: min(10, len(results))]
    cc = (country or "").strip().upper()
    pool: List[Dict[str, Any]] = list(pool_cap)
    if len(cc) == 2:
        pool_cc = [r for r in pool_cap if str(r.get("country_code") or "").upper() == cc]
        if pool_cc:
            pool = pool_cc
    elif cc and len(cc) != 2:
        cl = cc.lower()
        pool_nm = [
            r
            for r in pool_cap
            if cl in str(r.get("country") or "").lower()
            or str(r.get("country") or "").lower().startswith(cl)
        ]
        if pool_nm:
            pool = pool_nm
    hint = (admin1_hint or "").strip()
    if hint:
        matched = [r for r in pool if _admin1_matches_hint(str(r.get("admin1") or ""), hint)]
        if matched:
            return max(matched, key=_pop)
    return max(pool, key=_pop)


def _weather_location_heading(loc_name: str, region: str, ctry: str) -> str:
    """Без дубля «Санкт-Петербург, Санкт-Петербург» если регион совпадает с городом."""
    name = (loc_name or "").strip()
    reg = (region or "").strip()
    country = (ctry or "").strip()
    nn, rr = _norm_loc_token(name), _norm_loc_token(reg)
    parts: List[str] = [name] if name else []
    if reg and rr != nn and rr not in nn and nn not in rr:
        parts.append(reg)
    if country:
        parts.append(country)
    return ", ".join(parts)


def _format_observation_clock_line(
    iso_time: Optional[str],
    tz_abbr: Optional[str],
    *,
    ru: bool,
) -> str:
    if not iso_time or not str(iso_time).strip():
        return ""
    raw = str(iso_time).strip().replace("Z", "")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    abbr = (tz_abbr or "").strip()
    if ru:
        mon = _RU_MONTH_GEN[dt.month] if 1 <= dt.month <= 12 else str(dt.month)
        tz_bit = f" ({abbr})" if abbr else " (местное время)"
        return f"Сейчас по месту: {dt.day} {mon} {dt.year}, {dt.strftime('%H:%M')}{tz_bit}"
    tz_bit = f" ({abbr})" if abbr else " (local)"
    return f"Local time: {dt.strftime('%b %d, %Y, %H:%M')}{tz_bit}"


def _format_weather_user_summary(
    *,
    loc_heading: str,
    iso_time: Optional[str],
    tz_abbr: Optional[str],
    temp: Any,
    feel: Any,
    hum: Any,
    wind: Any,
    wcode: Optional[int],
    ru: bool,
) -> str:
    cond = _wmo_label(wcode, ru=ru)
    lines: List[str] = []
    title = f"Погода — {loc_heading}" if ru else f"Weather — {loc_heading}"
    lines.append(title)
    clock = _format_observation_clock_line(iso_time, tz_abbr, ru=ru)
    if clock:
        lines.append(clock)
    lines.append("")  # пустая строка — визуальный блок
    if ru:
        lines.append(f"• Температура: {temp} °C (ощущается как {feel} °C)")
        lines.append(f"• Условия: {cond}")
        lines.append(f"• Влажность: {hum} %")
        lines.append(f"• Ветер: {wind} м/с")
    else:
        lines.append(f"• Temperature: {temp} °C (feels like {feel} °C)")
        lines.append(f"• Conditions: {cond}")
        lines.append(f"• Humidity: {hum} %")
        lines.append(f"• Wind: {wind} m/s")
    return "\n".join(lines).strip()


def _parse_iso_local(s: Optional[str]) -> Optional[datetime]:
    raw = (s or "").strip().replace("Z", "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _obs_local_date(iso_time: Optional[str]) -> Optional[date]:
    dt = _parse_iso_local(str(iso_time) if iso_time else "")
    return dt.date() if dt else None


def _format_daily_forecast_block(
    daily: Any,
    obs_iso: Optional[str],
    *,
    ru: bool,
    max_days: int = 3,
) -> str:
    """
    Суточный прогноз Open-Meteo (макс/мин и код погоды) — явно «сегодня / завтра», чтобы не путать с LLM.
    """
    if not isinstance(daily, dict):
        return ""
    times = daily.get("time")
    tmax = daily.get("temperature_2m_max")
    tmin = daily.get("temperature_2m_min")
    codes = daily.get("weather_code")
    if not isinstance(times, list) or not isinstance(tmax, list) or not isinstance(tmin, list):
        return ""
    n = min(len(times), len(tmax), len(tmin))
    if n < 1:
        return ""
    codes_list: List[Any]
    if isinstance(codes, list) and len(codes) >= n:
        codes_list = codes[:n]
    else:
        codes_list = [None] * n

    today_d = _obs_local_date(obs_iso)
    start_i = 0
    if today_d is not None:
        for i in range(n):
            raw = str(times[i]).strip()
            try:
                d_i = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            if d_i == today_d:
                start_i = i
                break

    lines: List[str] = []
    for off in range(max_days):
        i = start_i + off
        if i >= n:
            break
        raw = str(times[i]).strip()
        try:
            d_i = date.fromisoformat(raw[:10])
        except ValueError:
            continue
        if off == 0:
            rel = "today"
        elif off == 1:
            rel = "tomorrow"
        elif off == 2:
            rel = "day_after"
        else:
            rel = "later"
        mon = _RU_MONTH_GEN[d_i.month] if 1 <= d_i.month <= 12 else str(d_i.month)
        if ru:
            if rel == "today":
                head = f"Сегодня, {d_i.day} {mon}"
            elif rel == "tomorrow":
                head = f"Завтра, {d_i.day} {mon}"
            elif rel == "day_after":
                head = f"Послезавтра, {d_i.day} {mon}"
            else:
                head = f"{d_i.day} {mon}"
        else:
            if rel == "today":
                head = f"Today, {d_i.strftime('%b')} {d_i.day}"
            elif rel == "tomorrow":
                head = f"Tomorrow, {d_i.strftime('%b')} {d_i.day}"
            elif rel == "day_after":
                head = f"Day after tomorrow, {d_i.strftime('%b')} {d_i.day}"
            else:
                head = d_i.strftime("%b %d")
        try:
            wci = int(codes_list[i]) if codes_list[i] is not None else None
        except (TypeError, ValueError):
            wci = None
        cond = _wmo_label(wci, ru=ru)
        mx, mn = tmax[i], tmin[i]
        if ru:
            lines.append(f"• {head}: макс. {mx} °C, мин. {mn} °C; {cond}.")
        else:
            lines.append(f"• {head}: high {mx} °C, low {mn} °C; {cond}.")

    if not lines:
        return ""
    title = "По дням (прогноз Open-Meteo):" if ru else "Multi-day outlook (Open-Meteo):"
    return "\n".join([title] + lines)


def _format_hourly_24h_every_3h(
    hourly: Any,
    current_iso: Optional[str],
    *,
    ru: bool,
) -> str:
    """
    Из почасового прогноза Open-Meteo — 9 точек с шагом 3 ч (~сутки от первого слота).
    """
    if not isinstance(hourly, dict):
        return ""
    times = hourly.get("time")
    temps = hourly.get("temperature_2m")
    codes = hourly.get("weather_code")
    if not isinstance(times, list) or not isinstance(temps, list):
        return ""
    n = min(len(times), len(temps))
    if n < 4:
        return ""
    codes_list: List[Any]
    if isinstance(codes, list) and len(codes) >= n:
        codes_list = codes[:n]
    else:
        codes_list = [None] * n

    cur_dt = _parse_iso_local(str(current_iso) if current_iso else "")
    start_i = 0
    if cur_dt is not None:
        for i in range(n):
            tdt = _parse_iso_local(str(times[i]))
            if tdt is not None and tdt >= cur_dt:
                start_i = i
                break

    rows: List[str] = []
    num_slots = 9
    step = 3
    prev_day: Optional[date] = None
    cur_day0 = cur_dt.date() if cur_dt is not None else None
    for k in range(num_slots):
        i = start_i + k * step
        if i >= n:
            break
        tdt = _parse_iso_local(str(times[i]))
        if tdt is None:
            continue
        tval = temps[i]
        wc_raw = codes_list[i] if i < len(codes_list) else None
        try:
            wci = int(wc_raw) if wc_raw is not None else None
        except (TypeError, ValueError):
            wci = None
        lbl = _wmo_label(wci, ru=ru)
        hm = tdt.strftime("%H:%M")
        d = tdt.date()
        date_prefix = ""
        if prev_day is not None and d != prev_day:
            date_prefix = f"{d.day:02d}.{d.month:02d} " if ru else f"{d.strftime('%m/%d')} "
        elif prev_day is None and cur_day0 is not None and d != cur_day0:
            date_prefix = f"{d.day:02d}.{d.month:02d} " if ru else f"{d.strftime('%m/%d')} "
        prev_day = d
        rows.append(f"  {date_prefix}{hm} — {tval} °C, {lbl}")

    if not rows:
        return ""

    header = (
        "Ближайшие сутки (каждые 3 ч, локальное время):"
        if ru
        else "Next 24 hours (every 3 h, local time):"
    )
    return "\n".join([header] + rows)


def _strip_xml_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text_has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[а-яё]", s, re.IGNORECASE))


def _normalize_geo_query(q: str) -> str:
    """Убираем типичные префиксы адреса — геокодер ищет по названию населённого пункта."""
    s = (q or "").strip()
    if not s:
        return s
    low = s.lower()
    for pref in (
        "агрогородок ",
        "а.г. ",
        "аг. ",
        "аг ",
        "посёлок ",
        "поселок ",
        "д. ",
        "с. ",
        "г. ",
    ):
        if low.startswith(pref):
            s = s[len(pref) :].strip()
            low = s.lower()
    return s


async def _http_get_json(url: str) -> Tuple[int, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            if resp.status < 200 or resp.status >= 300:
                return resp.status, None
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, None


def _wttr_json_first_value(blob: Any) -> str:
    if isinstance(blob, list) and blob:
        el = blob[0]
        if isinstance(el, dict) and el.get("value") is not None:
            return str(el.get("value") or "").strip()
    return ""


def _wttr_pick_midday_hourly(hourly: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(hourly, list) or not hourly:
        return None
    for h in hourly:
        if isinstance(h, dict) and str(h.get("time") or "") == "1200":
            return h
    mid = len(hourly) // 2
    return hourly[mid] if isinstance(hourly[mid], dict) else None


def format_wttr_j1_summary_text(data: Dict[str, Any], *, forecast_day_index: int = 0, ru: bool = True) -> str:
    """
    Краткая сводка для пользователя из JSON wttr.in (?format=j1).
    forecast_day_index: 0 сегодня, 1 завтра, 2 послезавтра (обрезается по числу дней в ответе).
    """
    days = data.get("weather") if isinstance(data.get("weather"), list) else []
    if not days:
        return ""
    idx = max(0, min(int(forecast_day_index), len(days) - 1))
    na = (data.get("nearest_area") or [{}])[0] if isinstance(data.get("nearest_area"), list) else {}
    area = _wttr_json_first_value(na.get("areaName") if isinstance(na, dict) else None)
    region = _wttr_json_first_value(na.get("region") if isinstance(na, dict) else None)
    ctry = _wttr_json_first_value(na.get("country") if isinstance(na, dict) else None)
    loc_bits = [b for b in (area, region, ctry) if b]
    if ru:
        labels = ("Сегодня", "Завтра", "Послезавтра")
        day_lbl = labels[idx] if idx < len(labels) else f"День +{idx}"
        head = "Погода (wttr.in, запасной источник)"
    else:
        labels = ("Today", "Tomorrow", "Day after tomorrow")
        day_lbl = labels[idx] if idx < len(labels) else f"Day +{idx}"
        head = "Weather (wttr.in fallback)"
    lines: List[str] = [head]
    if loc_bits:
        lines.append(("Пункт: " if ru else "Location: ") + ", ".join(loc_bits))
    day = days[idx] if isinstance(days[idx], dict) else {}
    ddate = str(day.get("date") or "").strip()
    mx = day.get("maxtempC")
    mn = day.get("mintempC")
    avg = day.get("avgtempC")
    if ddate or mx is not None or mn is not None:
        if ru:
            chunk = f"{day_lbl} ({ddate}): макс {mx}°C, мин {mn}°C"
            if avg is not None and str(avg).strip():
                chunk += f", в среднем ~{avg}°C"
        else:
            chunk = f"{day_lbl} ({ddate}): high {mx}°C, low {mn}°C"
            if avg is not None and str(avg).strip():
                chunk += f", avg ~{avg}°C"
        lines.append(chunk)
    cur = (data.get("current_condition") or [{}])[0] if isinstance(data.get("current_condition"), list) else {}
    if idx == 0 and isinstance(cur, dict) and cur:
        t = cur.get("temp_C")
        feel = cur.get("FeelsLikeC")
        desc = _wttr_json_first_value(cur.get("weatherDesc"))
        if not desc and ru:
            desc = _wttr_json_first_value(cur.get("lang_ru"))
        hum = cur.get("humidity")
        wind = cur.get("windspeedKmph")
        if t is not None and str(t).strip():
            if ru:
                chunk2 = f"Сейчас: {t}°C"
                if feel is not None and str(feel).strip():
                    chunk2 += f", ощущается как {feel}°C"
                if desc:
                    chunk2 += f", {desc}"
                if hum is not None and str(hum).strip():
                    chunk2 += f", влажность {hum}%"
                if wind is not None and str(wind).strip():
                    chunk2 += f", ветер {wind} км/ч"
            else:
                chunk2 = f"Now: {t}°C"
                if feel is not None and str(feel).strip():
                    chunk2 += f", feels {feel}°C"
                if desc:
                    chunk2 += f", {desc}"
                if hum is not None and str(hum).strip():
                    chunk2 += f", humidity {hum}%"
                if wind is not None and str(wind).strip():
                    chunk2 += f", wind {wind} km/h"
            lines.append(chunk2)
    elif idx >= 1 and isinstance(day, dict):
        hslot = _wttr_pick_midday_hourly(day.get("hourly"))
        if isinstance(hslot, dict):
            t = hslot.get("tempC")
            desc = _wttr_json_first_value(hslot.get("lang_ru")) or _wttr_json_first_value(hslot.get("weatherDesc"))
            if t is not None and str(t).strip():
                lines.append(
                    (f"Ориентир днём (~12:00): ~{t}°C, {desc}" if ru else f"Midday (~12:00): ~{t}°C, {desc}").rstrip(
                        ", "
                    )
                )
    return "\n".join(lines).strip()


async def fetch_wttr_in_j1_summary(
    city: str,
    country: str,
    *,
    forecast_day_index: int = 0,
) -> Optional[str]:
    """HTTP JSON wttr.in → короткий текст; None при сетевой/разборной ошибке."""
    from core.brain.text_helpers import normalize_weather_city_country, wttr_in_j1_url

    url = wttr_in_j1_url(city, country)
    if not url:
        return None
    status, payload = await _http_get_json(url)
    if status != 200 or not isinstance(payload, dict):
        return None
    ru = bool(re.search(r"[а-яё]", f"{city}{country}", re.IGNORECASE))
    text = format_wttr_j1_summary_text(payload, forecast_day_index=forecast_day_index, ru=ru)
    if not text.strip():
        return None
    cn, _ = normalize_weather_city_country((city or "").strip(), (country or "").strip())
    if cn == "Минск" and not _wttr_summary_matches_city(text, "minsk", "минск"):
        retry_url = wttr_in_j1_url("Minsk", "BY")
        if retry_url and retry_url != url:
            st2, payload2 = await _http_get_json(retry_url)
            if st2 == 200 and isinstance(payload2, dict):
                text2 = format_wttr_j1_summary_text(
                    payload2, forecast_day_index=forecast_day_index, ru=ru
                )
                if text2.strip() and _wttr_summary_matches_city(text2, "minsk", "минск"):
                    return text2.strip()
    return text.strip()


def _wttr_summary_matches_city(summary: str, *needles: str) -> bool:
    low = (summary or "").lower()
    return any(n in low for n in needles if n)


async def _http_get_text(url: str) -> Tuple[int, str]:
    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            return resp.status, text


async def _http_get_html(url: str, *, timeout_total: float = 20.0) -> Tuple[int, str]:
    """HTML-страницы (DuckDuckGo и т.п.) — чуть дольше таймаут."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_total)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            return resp.status, text


def _duckduckgo_decode_href(href: str) -> str:
    """Раскодировать редирект DDG //duckduckgo.com/l/?uddg=… → целевой URL."""
    raw = (href or "").strip()
    if not raw:
        return raw
    if raw.startswith("//"):
        raw = f"https:{raw}"
    try:
        if "uddg=" not in raw:
            return raw
        qs = parse_qs(urlparse(raw).query)
        udg = (qs.get("uddg") or [""])[0]
        if udg:
            return unquote(udg)
    except Exception:
        pass
    return raw


def _class_attr_contains(token: str):
    def _check(cls_val: Any) -> bool:
        if not cls_val:
            return False
        if isinstance(cls_val, (list, tuple)):
            s = " ".join(str(x) for x in cls_val)
        else:
            s = str(cls_val)
        return token in s

    return _check


def _duckduckgo_html_is_bot_wall(html: str) -> bool:
    """DDG отдаёт 200 HTML с капчей «Unfortunately, bots use…» — парсер видит 0 результатов."""
    h = (html or "").lower()
    if "anomaly-modal__title" in h or "anomaly-modal" in h:
        return True
    if "unfortunately, bots use duckduckgo" in h:
        return True
    if "cc=botnet" in h and "challenge-form" in h:
        return True
    return False


def _parse_duckduckgo_html_results(html: str, *, max_items: int) -> List[Dict[str, str]]:
    """Разбор HTML-выдачи html.duckduckgo.com (разметка может меняться — держим селекторы простыми)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, str]] = []
    for a in soup.select("a.result__a"):
        if len(out) >= max_items:
            break
        title = a.get_text(strip=True)
        href = _duckduckgo_decode_href(a.get("href") or "")
        snippet = ""
        body = a.find_parent("div", class_=_class_attr_contains("result__body"))
        if body:
            sn = body.select_one(".result__snippet, a.result__snippet")
            if sn:
                snippet = sn.get_text(" ", strip=True)
        if title or href:
            out.append({"title": title, "url": href, "snippet": snippet})
    return out


class WeatherAPIClient:
    """Open-Meteo (no API key): geocoding + current conditions."""

    def __init__(self) -> None:
        self._geo_base = os.getenv(
            "OPEN_METEO_GEO_URL", "https://geocoding-api.open-meteo.com/v1/search"
        ).rstrip("/")
        self._forecast_base = os.getenv(
            "OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast"
        ).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._geo_base and self._forecast_base)

    async def _forecast_summary_at(
        self,
        lat: float,
        lon: float,
        *,
        loc_name: str,
        region: str,
        ctry: str,
        lang: str = "ru",
    ) -> Dict[str, Any]:
        fc_params = urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "timezone": "auto",
                "forecast_days": "3",
                # Open-Meteo 2026+: явный current=time,… → HTTP 400; time всё равно в JSON current.
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "hourly": "temperature_2m,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            }
        )
        fs, fc = await _http_get_json(f"{self._forecast_base}?{fc_params}")
        if fs != 200 or not isinstance(fc, dict):
            return {"configured": False, "error": "forecast failed"}
        cur = fc.get("current") or {}
        obs_time = cur.get("time")
        temp = cur.get("temperature_2m")
        hum = cur.get("relative_humidity_2m")
        feel = cur.get("apparent_temperature")
        wcode = cur.get("weather_code")
        wind = cur.get("wind_speed_10m")
        tz_abbr = str(fc.get("timezone_abbreviation") or "").strip() or None
        loc_heading = _weather_location_heading(loc_name, region, ctry)
        ru = lang == "ru"
        summary = _format_weather_user_summary(
            loc_heading=loc_heading,
            iso_time=str(obs_time) if obs_time else None,
            tz_abbr=tz_abbr,
            temp=temp,
            feel=feel,
            hum=hum,
            wind=wind,
            wcode=int(wcode) if wcode is not None else None,
            ru=ru,
        )
        daily_extra = _format_daily_forecast_block(
            fc.get("daily"),
            str(obs_time) if obs_time else None,
            ru=ru,
        )
        if daily_extra:
            summary = f"{summary}\n\n{daily_extra}"
        hourly_extra = _format_hourly_24h_every_3h(
            fc.get("hourly"),
            str(obs_time) if obs_time else None,
            ru=ru,
        )
        if hourly_extra:
            summary = f"{summary}\n\n{hourly_extra}"
        return {
            "configured": True,
            "current": cur,
            "hourly": fc.get("hourly"),
            "summary": summary,
            "resolved": {
                "name": loc_name,
                "latitude": lat,
                "longitude": lon,
                "country": ctry,
                "admin1": region,
            },
        }

    async def get_current_at_coords(
        self,
        latitude: float,
        longitude: float,
        *,
        label: str = "",
        admin1: str = "",
        country: str = "",
    ) -> Dict[str, Any]:
        if not self.is_configured():
            return {"configured": False, "error": "open-meteo URLs not set"}
        lang = "ru" if re.search(r"[а-яё]", f"{label}{admin1}{country}", re.IGNORECASE) else "en"
        out = await self._forecast_summary_at(
            float(latitude),
            float(longitude),
            loc_name=(label or "место").strip(),
            region=(admin1 or "").strip(),
            ctry=(country or "").strip(),
            lang=lang,
        )
        if out.get("configured"):
            out["coords"] = True
        return out

    async def get_current(
        self,
        city: str = "",
        country: str = "",
        *,
        admin1_hint: str = "",
    ) -> Dict[str, Any]:
        q = _normalize_geo_query((city or "").strip())
        if not q:
            return {"configured": False, "error": "city required for weather"}
        if not self.is_configured():
            return {"configured": False, "error": "open-meteo URLs not set"}

        cc_raw = (country or "").strip()
        lang = "ru" if re.search(r"[а-яё]", q, re.IGNORECASE) else "en"

        async def _geo_search(name: str) -> Tuple[int, Any]:
            geo_q = urlencode({"name": name, "count": 10, "language": lang})
            return await _http_get_json(f"{self._geo_base}?{geo_q}")

        status, geo = await _geo_search(q)
        if status != 200 or not isinstance(geo, dict):
            return {"configured": False, "error": "geocoding failed"}
        results: List[Dict[str, Any]] = geo.get("results") or []
        cc_low = cc_raw.lower()
        belarus_hint = cc_raw.upper() in {"BY", "BLR"} or "беларус" in cc_low or "belarus" in cc_low
        if not results and belarus_hint:
            status, geo = await _geo_search(f"{q}, Belarus")
            if status == 200 and isinstance(geo, dict):
                results = geo.get("results") or []
        if not results and _text_has_cyrillic(q):
            status, geo = await _geo_search(f"{q}, Беларусь")
            if status == 200 and isinstance(geo, dict):
                results = geo.get("results") or []
        if not results:
            return {"configured": False, "error": f"no location for {q!r}"}

        pick = _pick_geo_result(results, country=country, admin1_hint=admin1_hint)

        lat = pick.get("latitude")
        lon = pick.get("longitude")
        if lat is None or lon is None:
            return {"configured": False, "error": "invalid geocoding result"}

        loc_name = pick.get("name") or q
        region = pick.get("admin1") or ""
        ctry = pick.get("country") or ""
        out = await self._forecast_summary_at(
            float(lat),
            float(lon),
            loc_name=str(loc_name),
            region=str(region),
            ctry=str(ctry),
            lang=lang,
        )
        if not out.get("configured"):
            return out
        out["city"] = q
        out["country"] = country
        return out


class CurrencyAPIClient:
    """Frankfurter ECB rates (no API key)."""

    def __init__(self) -> None:
        custom = os.getenv("CURRENCY_API_ENDPOINT", "").strip()
        self._base = (custom or "https://api.frankfurter.app").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._base)

    async def get_rate(self, base: str = "USD", quote: str = "EUR") -> Dict[str, Any]:
        b = (base or "USD").strip().upper()[:3] or "USD"
        q = (quote or "EUR").strip().upper()[:3] or "EUR"
        if len(b) != 3 or len(q) != 3:
            return {"configured": False, "error": "invalid currency code"}
        if not self.is_configured():
            return {"configured": False, "error": "currency endpoint not set"}

        path = f"{self._base}/latest?from={url_quote(b)}&to={url_quote(q)}"
        status, data = await _http_get_json(path)
        if status != 200 or not isinstance(data, dict):
            return {"configured": False, "error": "rate request failed"}
        rates = data.get("rates") or {}
        rate = rates.get(q)
        if rate is None and b == q:
            rate = 1.0
        if rate is None:
            return {"configured": False, "error": f"no rate for {b}/{q}"}
        amt = data.get("amount", 1.0)
        summary = f"1 {b} = {rate} {q} (as of {data.get('date', '?')}, ECB via Frankfurter)"
        return {
            "configured": True,
            "base": b,
            "quote": q,
            "rate": rate,
            "amount": amt,
            "date": data.get("date"),
            "summary": summary,
        }


_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


class NewsAPIClient:
    """Headlines via Google News RSS (no API key; topic search).

    Locale logic (priority order):
      1. env NEWS_GOOGLE_RSS_LOCALE — full override, e.g. ``hl=en&gl=US&ceid=US:en``
      2. explicit ``country`` parameter — ``gl`` and ``ceid`` derived from it
      3. world-news keywords — ru feed if запрос/страна CIS (см. ``NEWS_WORLD_RSS_LANG``)
      4. fallback → ``hl=ru&gl=RU&ceid=RU:ru``
    """

    _WORLD_KW = (
        "мировые новости",
        "новости мира",
        "новости в мире",
        "в мире",
        "по миру",
        "world news",
        "международн",
        "global",
        "international",
        "мир сегодня",
        "world today",
        "новости планеты",
        "what's happening in the world",
    )

    # Источники-мусорки (сатира, кликбейт, откровенные фейки) — блокируются на уровне домена.
    # Расширяется через env NEWS_BLOCKED_DOMAINS (через запятую).
    _JUNK_DOMAINS: set = {
        "fathomjournal.org",
        "fathomjournal.com",
        "thenewsglobe.net",
        "thenewsglobe.com",
        "worldnewsdailyreport.com",
        "dailyworldupdate.net",
        "newspunch.com",
        "yournewswire.com",
        "beforeitsnews.com",
        "infowars.com",
        "theonion.com",
        "politifact.com",  # фактчекинг, не источник
        "snopes.com",
    }

    def __init__(self) -> None:
        self._blocked: set[str] | None = None  # ленивая загрузка

    def is_configured(self) -> bool:
        return True

    @classmethod
    def wants_world_news(cls, topic: str) -> bool:
        """Запрос про мир/международку — не привязывать к стране из профиля."""
        low = (topic or "").strip().lower()
        if not low:
            return True
        return any(k in low for k in cls._WORLD_KW)

    @classmethod
    def _world_rss_lang(cls, topic: str, country: str) -> str:
        """Язык мировой ленты: ru по умолчанию для кириллицы/CIS; en — NEWS_WORLD_RSS_LANG=en."""
        forced = (os.getenv("NEWS_WORLD_RSS_LANG") or "").strip().lower()
        if forced in ("en", "ru"):
            return forced
        if _CYRILLIC_RE.search(topic or ""):
            return "ru"
        c = (country or "").strip().upper()
        if c in ("RU", "BY", "KZ", "UA"):
            return "ru"
        if c in ("US", "GB", "AU", "CA", "IE"):
            return "en"
        return "ru"

    @classmethod
    def _world_locale(cls, topic: str, country: str) -> str:
        lang = cls._world_rss_lang(topic, country)
        if lang == "en":
            return "hl=en&gl=US&ceid=US:en"
        c = (country or "").strip().upper()
        if c in ("BY", "KZ", "UA") and re.fullmatch(r"[A-Z]{2}", c):
            return f"hl=ru&gl={c}&ceid={c}:ru"
        return "hl=ru&gl=RU&ceid=RU:ru"

    @classmethod
    def _locale_env_override(cls, topic: str, country: str) -> str:
        """
        NEWS_GOOGLE_RSS_LOCALE — жёсткий override. По умолчанию не применяется к кириллице/CIS:
        иначе на RU-боте в .env часто остаётся hl=en&gl=US и все дайджесты на английском.
        NEWS_GOOGLE_RSS_LOCALE_FORCE=true — всегда использовать env.
        """
        env = (os.getenv("NEWS_GOOGLE_RSS_LOCALE") or "").strip()
        if not env:
            return ""
        force = (os.getenv("NEWS_GOOGLE_RSS_LOCALE_FORCE") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if force:
            return env
        if _CYRILLIC_RE.search(topic or ""):
            return ""
        c = (country or "").strip().upper()
        if c in ("RU", "BY", "KZ", "UA"):
            return ""
        return env

    @classmethod
    def _locale_for_topic(cls, topic: str, country: str, *, topic_was_empty: bool = False) -> str:
        """Pick the Google News RSS locale string based on topic and country."""
        env_loc = cls._locale_env_override(topic, country)
        if env_loc:
            return env_loc

        # Мировые/общие — отдельная лента (не только «новости Беларуси»), но на русском для RU-запросов.
        if topic_was_empty or cls.wants_world_news(topic):
            return cls._world_locale(topic, country)

        c = (country or "").strip().upper()
        if c and re.fullmatch(r"[A-Z]{2}", c):
            lang = "ru" if c in ("RU", "BY", "KZ", "UA") else "en"
            return f"hl={lang}&gl={c}&ceid={c}:{lang}"

        return "hl=ru&gl=RU&ceid=RU:ru"

    @classmethod
    def _rss_search_topic(cls, topic: str, *, topic_was_empty: bool = False, country: str = "") -> str:
        """Упрощённый запрос в Google News RSS для мировой ленты."""
        if topic_was_empty or cls.wants_world_news(topic):
            custom = (os.getenv("NEWS_WORLD_RSS_QUERY") or "").strip()
            if custom:
                return custom
            if cls._world_rss_lang(topic, country) == "ru":
                return "международные новости"
            return "world news"
        return (topic or "").strip() or "главные новости"

    def _is_junk_source(self, link: str) -> bool:
        """Проверяет домен ссылки по чёрному списку."""
        if not link:
            return False
        try:
            domain = re.sub(r"^https?://(?:www\.)?", "", link.split("/")[2] if "//" in link else link).split("/")[0].lower()
        except (IndexError, AttributeError):
            return False
        blocked = self._blocked
        if blocked is None:
            raw = (os.getenv("NEWS_BLOCKED_DOMAINS") or "").strip()
            extra = set() if not raw else {d.strip().lower() for d in raw.split(",") if d.strip()}
            blocked = self._JUNK_DOMAINS | extra
            self._blocked = blocked
        return any(domain == b or domain.endswith("." + b) for b in blocked)

    async def headlines(self, topic: str = "", country: str = "") -> Dict[str, Any]:
        raw_topic = (topic or "").strip()
        topic_was_empty = not raw_topic
        # Для мировой ленты country не сужает тему, но задаёт gl/язык (BY → ru&gl=BY).
        co_profile = (country or "").strip()
        co = "" if self.wants_world_news(raw_topic) else co_profile
        t = self._rss_search_topic(
            raw_topic, topic_was_empty=topic_was_empty, country=co_profile or co
        )

        locale = self._locale_for_topic(
            raw_topic or t, co_profile or co, topic_was_empty=topic_was_empty
        )
        q = url_quote(t, safe="")
        url = f"https://news.google.com/rss/search?q={q}&{locale}"
        status, xml_text = await _http_get_text(url)
        if status != 200:
            return {"configured": False, "error": "news rss failed"}

        items: List[Dict[str, str]] = []
        try:
            root = ET.fromstring(xml_text)
            for el in root.iter():
                if _strip_xml_ns(el.tag) != "item":
                    continue
                title_el = link_el = src_el = None
                for child in el:
                    tag = _strip_xml_ns(child.tag)
                    if tag == "title":
                        title_el = child
                    elif tag == "link":
                        link_el = child
                    elif tag == "source":
                        src_el = child
                if title_el is not None and title_el.text:
                    link = (link_el.text or "").strip() if link_el is not None else ""
                    # source url — реальный домен публикации (Google RSS даёт редиректы в <link>)
                    source_url = (src_el.get("url") or "").strip() if src_el is not None else ""
                    source_name = (src_el.text or "").strip() if src_el is not None else ""
                    if self._is_junk_source(link) or self._is_junk_source(source_url):
                        continue
                    items.append(
                        {
                            "title": title_el.text.strip(),
                            "link": link,
                            "source": source_url,
                            "source_name": source_name,
                        }
                    )
        except ET.ParseError:
            return {"configured": False, "error": "news rss parse error"}

        if not items:
            return {"configured": False, "error": "no headlines parsed"}

        try:
            cap = max(5, min(15, int((os.getenv("NEWS_RSS_MAX_ITEMS") or "12").strip())))
        except ValueError:
            cap = 12
        diversified: List[Dict[str, str]] = []
        seen_dom: set[str] = set()
        for row in items:
            link = str(row.get("link") or "")
            src_url = str(row.get("source") or "")
            dom = ""
            try:
                raw = src_url or link
                dom = re.sub(r"^https?://(?:www\.)?", "", raw.split("/")[2] if "//" in raw else raw).split("/")[0].lower()
            except (IndexError, AttributeError):
                dom = ""
            if dom and dom in seen_dom:
                continue
            if dom:
                seen_dom.add(dom)
            diversified.append(row)
            if len(diversified) >= cap:
                break
        if len(diversified) < cap:
            have = {id(r) for r in diversified}
            for row in items:
                if id(row) in have:
                    continue
                diversified.append(row)
                have.add(id(row))
                if len(diversified) >= cap:
                    break
        top = diversified if diversified else items[:cap]
        summary = "; ".join(x["title"] for x in top)
        return {
            "configured": True,
            "topic": t,
            "query_topic": raw_topic or t,
            "country": co or country,
            "locale": locale,
            "world_feed": self.wants_world_news(raw_topic or t),
            "items": top,
            "summary": summary,
        }


class WikipediaClient:
    """MediaWiki extracts API (no key for reasonable use)."""

    def __init__(self, *, lang: Optional[str] = None) -> None:
        custom = os.getenv("WIKIPEDIA_API_ENDPOINT", "").strip()
        override = (str(lang).strip().lower() if lang is not None and str(lang).strip() else "")
        if override and re.fullmatch(r"[a-z]{2,12}", override):
            self._lang = override
            self.endpoint = f"https://{self._lang}.wikipedia.org/w/api.php"
            return
        env_lang = (os.getenv("WIKIPEDIA_LANG", "en") or "en").strip() or "en"
        self._lang = env_lang
        self.endpoint = custom or f"https://{env_lang}.wikipedia.org/w/api.php"

    def is_configured(self) -> bool:
        return bool(self.endpoint)

    def wiki_lang(self) -> str:
        if self._lang:
            return str(self._lang).lower()
        m = re.match(r"https?://([a-z]{2,12})\.wikipedia\.org", self.endpoint or "", re.I)
        return (m.group(1) if m else "en").lower()

    def page_url_for_title(self, title: str) -> str:
        t = (title or "").strip()
        slug = t.replace(" ", "_")
        return f"https://{self.wiki_lang()}.wikipedia.org/wiki/{quote(slug, safe=':_/()%')}"

    async def opensearch(self, search: str, limit: int = 5) -> List[str]:
        s = (search or "").strip()
        if not s or not self.is_configured():
            return []
        lim = min(max(1, limit), 20)
        params = urlencode(
            {
                "action": "opensearch",
                "format": "json",
                "search": s,
                "limit": lim,
                "namespace": "0",
            }
        )
        url = f"{self.endpoint}?{params}"
        status, data = await _http_get_json(url)
        if status != 200 or not isinstance(data, list) or len(data) < 2:
            return []
        titles = data[1]
        if not isinstance(titles, list):
            return []
        return [str(x).strip() for x in titles if str(x).strip()]

    async def article_extract(
        self,
        title: str,
        *,
        intro_only: bool = False,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Полный текст (plain) или только вступление; обрезка по WIKIPEDIA_MAX_EXTRACT_CHARS."""
        t = (title or "").strip()
        if not t:
            return {"configured": False, "error": "title required"}
        if not self.is_configured():
            return {"configured": False, "error": "wikipedia endpoint not set"}
        try:
            default_cap = int(os.getenv("WIKIPEDIA_MAX_EXTRACT_CHARS", "20000"))
        except ValueError:
            default_cap = 20000
        cap = default_cap if max_chars is None else int(max_chars)
        cap = max(400, min(cap, 50000))

        params = urlencode(
            {
                "action": "query",
                "format": "json",
                "redirects": "1",
                "prop": "extracts",
                "explaintext": "1",
                "exintro": "1" if intro_only else "0",
                "titles": t,
            }
        )
        url = f"{self.endpoint}?{params}"
        status, data = await _http_get_json(url)
        if status != 200 or not isinstance(data, dict):
            return {"configured": False, "error": "wikipedia request failed"}

        q = data.get("query") or {}
        pages = q.get("pages") or {}
        extract = None
        out_title = t
        for _pid, page in pages.items():
            if not isinstance(page, dict):
                continue
            if page.get("missing") or "invalid" in page:
                continue
            extract = page.get("extract")
            out_title = page.get("title") or out_title
            break

        if not extract:
            return {"configured": False, "error": "no wikipedia extract"}

        excerpt = extract.strip()
        truncated = len(excerpt) > cap
        if truncated:
            excerpt = excerpt[: cap - 1] + "…"

        page_url = self.page_url_for_title(out_title)
        return {
            "configured": True,
            "topic": t,
            "title": out_title,
            "extract": excerpt,
            "truncated": truncated,
            "page_url": page_url,
            "intro_only": intro_only,
        }

    async def summary(self, topic: str) -> Dict[str, Any]:
        t = (topic or "").strip()
        if not t:
            return {"configured": False, "error": "topic required"}
        r = await self.article_extract(t, intro_only=True, max_chars=800)
        if not r.get("configured"):
            return r
        excerpt = r.get("extract") or ""
        title = r.get("title") or t
        summary = f"{title}: {excerpt}"
        return {
            "configured": True,
            "topic": t,
            "title": title,
            "summary": summary,
            "extract": excerpt,
        }


class GenericSearchClient:
    """DuckDuckGo: Instant Answer JSON, при пустом ответе — HTML-выдача (без ключа)."""

    def __init__(self) -> None:
        self._base = os.getenv(
            "DUCKDUCKGO_API_URL", "https://api.duckduckgo.com/"
        ).rstrip("/")
        self._html_base = os.getenv(
            "DUCKDUCKGO_HTML_URL", "https://html.duckduckgo.com/html/"
        ).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._base)

    def _html_fallback_enabled(self) -> bool:
        raw = os.getenv("DUCKDUCKGO_HTML_FALLBACK")
        if raw is None:
            return True
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    async def _search_html(self, query: str) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q or not self._html_base:
            return {"configured": False, "error": "html search not configured"}
        try:
            n = int(os.getenv("DUCKDUCKGO_HTML_MAX_RESULTS", "8"))
        except ValueError:
            n = 8
        n = max(2, min(n, 15))
        try:
            tmo = float((os.getenv("DUCKDUCKGO_HTML_TIMEOUT_SEC") or "20").strip())
        except ValueError:
            tmo = 20.0
        tmo = max(8.0, min(tmo, 45.0))
        params = urlencode({"q": q})
        url = f"{self._html_base}?{params}"
        status, html = await _http_get_html(url, timeout_total=tmo)
        if status < 200 or status >= 300 or not html:
            return {"configured": False, "error": f"duckduckgo html http={status}"}
        if _duckduckgo_html_is_bot_wall(html):
            return {
                "configured": False,
                "error": "duckduckgo_bot_challenge",
                "hint": (
                    "DuckDuckGo вернул антибот-страницу (часто у IP VPS/хостинга). "
                    "Обход без DDG: свой SearXNG (SEARXNG_INSTANCE_URL) — движки вы настраиваете на инстансе. "
                    "Опционально платные API (Tavily/Brave), если доступны вам юридически и по оплате."
                ),
            }
        rows = _parse_duckduckgo_html_results(html, max_items=n)
        if not rows:
            return {"configured": False, "error": "no html results from duckduckgo"}
        parts: List[str] = []
        for r in rows:
            line = (r.get("title") or "").strip()
            sn = (r.get("snippet") or "").strip()
            u = (r.get("url") or "").strip()
            if sn:
                line = f"{line}: {sn}" if line else sn
            if u:
                line = f"{line} ({u})" if line else u
            if line:
                parts.append(line)
        if not parts:
            return {"configured": False, "error": "duckduckgo html parse empty"}
        summary = " \n".join(parts[:n])
        if len(summary) > 9000:
            summary = summary[:8997] + "..."
        return {
            "configured": True,
            "query": q,
            "summary": summary,
            "source": "duckduckgo_html",
            "results": rows,
        }

    async def search(self, query: str) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"configured": False, "error": "query required"}
        if not self.is_configured():
            return {"configured": False, "error": "search endpoint not set"}

        params = urlencode({"q": q, "format": "json", "no_html": "1", "skip_disambig": "1"})
        url = f"{self._base}?{params}"
        status, data = await _http_get_json(url)
        if status < 200 or status >= 300 or not isinstance(data, dict):
            if self._html_fallback_enabled():
                return await self._search_html(q)
            return {"configured": False, "error": "search request failed"}

        abstract = (data.get("AbstractText") or data.get("Abstract") or "").strip()
        answer = (data.get("Answer") or "").strip()
        heading = (data.get("Heading") or "").strip()
        parts: List[str] = []
        if heading:
            parts.append(heading)
        if answer:
            parts.append(answer)
        if abstract:
            parts.append(abstract)
        for rt in data.get("RelatedTopics") or []:
            if isinstance(rt, dict) and rt.get("Text"):
                parts.append(str(rt["Text"]))
            if len(parts) >= 4:
                break

        if not parts:
            if self._html_fallback_enabled():
                html_res = await self._search_html(q)
                if html_res.get("configured"):
                    return html_res
                if str(html_res.get("error") or "") == "duckduckgo_bot_challenge":
                    return html_res
            return {"configured": False, "error": "no instant answer from duckduckgo"}

        summary = " — ".join(parts[:3])
        if len(summary) > 900:
            summary = summary[:897] + "..."
        return {"configured": True, "query": q, "summary": summary, "raw_heading": heading}

    async def search_variants(self, queries: List[str]) -> Dict[str, Any]:
        """Перебор формулировок: короче/с «рецепт» и т.д., пока DDG не даст instant answer или HTML."""
        last: Dict[str, Any] = {"configured": False, "error": "no search queries"}
        for raw in queries:
            qv = (raw or "").strip()
            if not qv:
                continue
            last = await self.search(qv)
            if last.get("configured"):
                return last
        return last
