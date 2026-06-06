"""
Человекочитаемый текст для Telegram (parse_mode=HTML).
"""
from __future__ import annotations

import logging

import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from core.report_i18n import (
    REPORT_GLOSSARY_FOOTER_HTML,
    anti_flood_label_ru,
    format_kv_table_pre,
    format_metrics_table_pre,
    format_ms_whole,
    llm_kind_ru,
    monitor_label_ru,
    p95_label_ru,
    planner_tags_ru,
    ru_bool,
    ru_status,
    runtime_component_label_ru,
)
from core.report_timezone import (
    format_health_snapshot_caption,
    format_operator_datetime_from_iso,
    format_usage_digest_slot_caption,
    report_time_uses_utc_wall,
    report_timezone_label,
    report_utc_offset_label,
)
from core.path_redaction import redact_public_path
from core.user_facts import FACT_FIELD_LABELS_PROFILE_RU, FACT_FIELD_LABELS_RU


logger = logging.getLogger(__name__)

def esc(x: Any) -> str:
    return html.escape(redact_public_path(x), quote=True)


_HEALTH_LABEL_RU = {
    "overall_status": "Общий статус",
    "mode": "Режим",
    "planner_engine": "Планировщик",
    "active_traces": "Активные трейсы",
    "flood_blocked_total": "Заблокировано флуда",
    "link_flagged_total": "Ссылок помечено",
    "link_dangerous_total": "Опасных ссылок",
    "security_warning_total": "Предупреждений безопасности",
    "security_high_risk_total": "Высокий риск (безопасность)",
}

_PASSPORT_FIELD_LABELS = {
    "mission": "Миссия",
    "evolution_vectors": "Векторы развития",
    "priorities": "Приоритеты",
    "kpi_targets": "Целевые KPI",
    "stop_rules": "Стоп-правила",
}

_INTEGRITY_ISSUE_RU = {
    "passport_file_missing": "нет файла паспорта разработки (см. /admin_passport)",
}


def code_block_html(body: str) -> str:
    """
    Блок «как тройные кавычки» в Telegram: моноширинный preformatted (parse_mode=HTML).
    В клиентах отображается как код с удобным копированием.
    """
    return f"<pre>{esc(body)}</pre>"


# Единые ширины колонок для <pre>-таблиц во всех отчётах (один ритм в Telegram).
_REPORT_KV_L = 22
_REPORT_KV_V = 28
_REPORT_KV_V_WIDE = 52
_REPORT_KV_V_LONG = 44
_REPORT_MET_L = 22
_REPORT_MET_N = 7


def report_pre_kv(
    rows: List[Tuple[str, str]],
    *,
    label_max: int = _REPORT_KV_L,
    value_max: int = _REPORT_KV_V,
) -> str:
    return f"<pre>{esc(format_kv_table_pre(rows, label_max=label_max, value_max=value_max))}</pre>"


def report_pre_metrics(
    rows: List[Tuple[str, int]],
    *,
    label_max: int = _REPORT_MET_L,
    num_width: int = _REPORT_MET_N,
) -> str:
    return f"<pre>{esc(format_metrics_table_pre(rows, label_max=label_max, num_width=num_width))}</pre>"


def _kv_block(title: str, items: Mapping[str, Any], *, empty: str = "— нет —") -> str:
    lines = [f"<b>{esc(title)}</b>"]
    if not items:
        lines.append(empty)
        return "\n".join(lines)
    for k, v in sorted(items.items(), key=lambda t: str(t[0])):
        if isinstance(v, (dict, list)):
            lines.append(f"• <code>{esc(k)}</code>: <i>{esc(v)}</i>")
        else:
            lines.append(f"• <b>{esc(k)}</b>: {esc(v)}")
    return "\n".join(lines)


_ME_PREFS_LABELS_RU = {
    "communication_style": "Стиль общения",
    "explanation_style": "Объяснения",
    "learning_explanation": "Пояснения при учёбе",
    "persona_name": "Персона",
    "tone": "Тон",
    "verbosity": "Длина ответов",
}

# Человекочитаемые подписи вместо сырых enum (как в настройках iOS).
_EXPLANATION_STYLE_RU = {
    "mixed": "Смешанный",
    "detailed": "Подробный",
    "step_by_step": "Пошаговый",
    "formal": "Формальный",
    "casual": "Неформальный",
    "simple": "Простой",
}
_TONE_STYLE_RU = {
    "balanced": "Умеренный",
    "friendly": "Дружелюбный",
    "didactic": "Наставнический",
}
_VERBOSITY_RU = {
    "concise": "Краткие",
    "structured": "Развёрнуто и по пунктам",
}
_COMMUNICATION_STYLE_RU = {
    "neutral": "Нейтральный",
    "formal": "Официальный",
    "informal": "Неформальный",
}
_LEARNING_SPEED_RU = {
    "slow": "Спокойный темп",
    "medium": "Обычный темп",
    "fast": "Быстрый темп",
}
_GROUP_BEHAVIOR_RU = {
    "balanced": "Сбалансированное",
    "active": "Активное",
    "passive": "Сдержанное",
}
_COLLABORATION_LEVEL_RU = {
    "low": "Низкий",
    "medium": "Средний",
    "high": "Высокий",
}
# Короткие подписи часовых поясов без IANA и без UTC±N (для строки «Часовой пояс» в фактах).
_TZ_FACT_FRIENDLY_RU = {
    "europe/minsk": "Минск",
    "europe/moscow": "Москва",
    "europe/kaliningrad": "Калининград",
    "europe/warsaw": "Варшава",
    "europe/kiev": "Киев",
    "europe/kyiv": "Киев",
    "utc": "UTC",
    "gmt": "UTC",
}


def _humanize_me_pref_value(key: str, raw: str) -> str:
    """Переводит значения настроек стиля с англ. slug на русскую подпись."""
    k = str(key or "").strip()
    v = (raw or "").strip()
    if not v:
        return raw
    low = v.lower()
    if k in ("explanation_style", "learning_explanation"):
        return _EXPLANATION_STYLE_RU.get(low, v)
    if k == "tone":
        return _TONE_STYLE_RU.get(low, v)
    if k == "verbosity":
        return _VERBOSITY_RU.get(low, v)
    if k == "communication_style":
        return _COMMUNICATION_STYLE_RU.get(low, v)
    if k == "persona_name":
        if low in {"neutral", "neutral_mode"}:
            return "Нейтральный"
        if low in {"friend_mode", "friend"}:
            return "Друг"
        if low in {"teacher_mode", "teacher"}:
            return "Учитель"
        return v
    return v


def _humanize_fact_value(fact_key: str, raw: str) -> str:
    """Упрощённое отображение отдельных фактов (часовой пояс без Europe/…)."""
    fk = str(fact_key or "").strip().lower()
    v = (raw or "").strip()
    if not v:
        return raw
    if fk == "timezone":
        low = v.lower().replace(" ", "_")
        if low in _TZ_FACT_FRIENDLY_RU:
            return _TZ_FACT_FRIENDLY_RU[low]
        if "/" in v:
            return v.split("/")[-1].replace("_", " ").title()
    return v


def _humanize_twin_field(field_key: str, raw: str) -> str:
    fk = str(field_key or "").strip()
    v = (raw or "").strip()
    if not v:
        return raw
    low = v.lower()
    if fk == "preferred_explanation_style":
        return _EXPLANATION_STYLE_RU.get(low, v)
    if fk == "learning_speed":
        return _LEARNING_SPEED_RU.get(low, v)
    if fk == "group_behavior":
        return _GROUP_BEHAVIOR_RU.get(low, v)
    if fk == "collaboration_level":
        return _COLLABORATION_LEVEL_RU.get(low, v)
    return v


def _ios_group_caption(title: str) -> str:
    """Заголовок секции в духе «группированных списков» (серый подзаголовок)."""
    return f"<i>{esc(title)}</i>"


_ME_FACT_KEYS_INTERESTS = frozenset({"interests"})
_ME_FACT_KEYS_HANDLED = frozenset(
    ("name", "country", "city", "timezone", "age", "language", "currency", "occupation")
) | _ME_FACT_KEYS_INTERESTS

_ME_PREFS_DISPLAY_ORDER = (
    "tone",
    "persona_name",
    "communication_style",
    "explanation_style",
    "learning_explanation",
    "verbosity",
)


def _me_fact_timezone_line(raw: str) -> str:
    """Часовой пояс для карточки: короткое имя + (UTC±…), без сырого IANA при известном сопоставлении."""
    v = (raw or "").strip()
    if not v:
        return "<i>не указано</i>"
    hv = _humanize_fact_value("timezone", v)
    hv_esc = esc(hv)
    off_html = ""
    try:
        zid = v.replace(" ", "_")
        if "/" in zid:
            z = ZoneInfo(zid)
            off = datetime.now(z).strftime("%z")
            if off and len(off) >= 5:
                sign = off[0]
                hh = int(off[1:3])
                mm = int(off[3:5])
                if mm == 0:
                    off_html = f" (UTC{sign}{hh})"
                else:
                    off_html = f" (UTC{sign}{hh}:{mm:02d})"
    except Exception as e:
        logger.debug('%s optional failed: %s', 'telegram_ui', e, exc_info=True)
    return f"{hv_esc}{off_html}" if off_html else hv_esc


def _me_core_profile_lines_html(user_id: str, facts: Mapping[str, Any]) -> List[str]:
    """Блок «Основная информация»: фиксированный порядок + опциональные поля, пустые — «не указано»."""
    lines: List[str] = []

    def disp_optional_fact(key: str) -> str:
        v = facts.get(key)
        if _me_is_empty_value(v):
            return "<i>не указано</i>"
        if isinstance(v, str):
            return _me_format_fact_value(str(key), v)
        return _me_format_value(v)

    name_v = facts.get("name")
    if _me_is_empty_value(name_v):
        name_disp = "<i>не указано</i>"
    else:
        name_disp = _me_format_fact_value("name", str(name_v)) if isinstance(name_v, str) else _me_format_value(name_v)
    lines.append(f"• <b>Имя</b>: {name_disp}")
    lines.append(f"• <b>Telegram ID</b>: <code>{esc(user_id)}</code>")
    lines.append(f"• <b>Страна</b>: {disp_optional_fact('country')}")
    lines.append(f"• <b>Город</b>: {disp_optional_fact('city')}")
    lines.append(f"• <b>Часовой пояс</b>: {_me_fact_timezone_line(str(facts.get('timezone') or ''))}")

    for key in ("age", "language", "currency", "occupation"):
        v = facts.get(key)
        if _me_is_empty_value(v):
            continue
        label = _me_fact_label_ru(str(key))
        disp = _me_format_fact_value(str(key), str(v)) if isinstance(v, str) else _me_format_value(v)
        lines.append(f"• <b>{esc(label)}</b>: {disp}")

    for k in sorted(facts.keys(), key=str):
        if k in _ME_FACT_KEYS_HANDLED:
            continue
        v = facts.get(k)
        if _me_is_empty_value(v):
            continue
        label = _me_fact_label_ru(str(k))
        disp = _me_format_fact_value(str(k), str(v)) if isinstance(v, str) else _me_format_value(v)
        lines.append(f"• <b>{esc(label)}</b>: {disp}")
    return lines


def _me_interests_section_html(facts: Mapping[str, Any]) -> List[str]:
    v = facts.get("interests")
    items: List[str] = []
    if isinstance(v, list):
        items = [str(x).strip() for x in v if str(x).strip()]
    elif isinstance(v, str) and v.strip():
        parts = re.split(r"[\n,;•]+", v)
        items = [p.strip() for p in parts if p.strip()]
    if not items:
        return ["<i>Пока не указано — расскажите, чем увлекаетесь.</i>"]
    return [f"• {esc(it)}" for it in items[:24]] + (["• …"] if len(items) > 24 else [])


def _me_prefs_bullets_html(prefs: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    for key in _ME_PREFS_DISPLAY_ORDER:
        if key not in prefs:
            continue
        v = prefs.get(key)
        if _me_is_empty_value(v):
            continue
        label = _ME_PREFS_LABELS_RU.get(str(key), str(key).replace("_", " ").title())
        if isinstance(v, str):
            hv = _humanize_me_pref_value(str(key), v)
            disp = esc(hv) if hv.strip() else "<i>не задано</i>"
        else:
            disp = _me_format_value(v)
        lines.append(f"• <b>{esc(label)}</b>: {disp}")
    if not lines:
        return ["<i>Используются значения по умолчанию.</i>"]
    return lines


def _psych_profile_bullets_html(profile: Dict[str, Any]) -> List[str]:
    """Компактные буллеты для блока «Аналитика» на странице /me."""
    if not profile:
        return ["<i>Пока нет данных — эвристика появится после нескольких реплик.</i>"]
    lines: List[str] = []
    _dt = _profile_datetime_compact_html
    la = profile.get("last_analysis") if isinstance(profile.get("last_analysis"), dict) else None
    if la:
        raw_sent = la.get("sentiment")
        sent_key = str(raw_sent) if raw_sent is not None else ""
        sent_disp = _PSYCH_SENTIMENT_RU.get(sent_key, sent_key or "—")
        lines.append(f"• <b>Тональность</b>: {esc(sent_disp)}")
        lines.append(f"• <b>Стресс в формулировках</b>: {_psych_bool_ru(la.get('stress_signals'))}")
        kw = la.get("keywords") or []
        if isinstance(kw, list) and kw:
            tags = ", ".join(esc(str(x)) for x in kw[:12])
            lines.append(f"• <b>Теги по тексту</b>: {tags}")
        else:
            lines.append("• <b>Теги по тексту</b>: <i>нет</i>")
        if la.get("analyzed_at"):
            lines.append(f"• <b>Последний разбор</b>: {_dt(la.get('analyzed_at'))}")
        if la.get("message_length") is not None:
            lines.append(f"• <b>Длина реплики</b>: {esc(la.get('message_length'))} симв.")
    if profile.get("stress_streak") is not None:
        lines.append(f"• <b>Стресс подряд</b> <i>(эвристика)</i>: {esc(profile.get('stress_streak'))}")
    if profile.get("updated_at"):
        lines.append(f"• <b>Обновлено</b>: {_dt(profile.get('updated_at'))}")
    shown = {"last_analysis", "stress_streak", "updated_at"}
    for k in sorted(profile.keys(), key=str):
        if k in shown:
            continue
        v = profile[k]
        if v is None or v == {} or v == []:
            continue
        label = str(k).replace("_", " ").strip().title()
        lines.append(f"• <b>{esc(label)}</b>: {_me_format_value(v)}")
    return lines or ["<i>Нет числовых метрик — продолжайте диалог.</i>"]


def _me_is_empty_value(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def _me_fact_label_ru(key: str) -> str:
    prof = FACT_FIELD_LABELS_PROFILE_RU.get(key)
    if prof:
        return prof
    lbl = FACT_FIELD_LABELS_RU.get(key)
    if lbl:
        return lbl[0].upper() + lbl[1:] if len(lbl) > 1 else lbl.upper()
    return str(key).replace("_", " ").strip().title()


def _me_format_fact_value(fact_key: str, v: Any) -> str:
    if _me_is_empty_value(v):
        return "<i>не задано</i>"
    if isinstance(v, str):
        hv = _humanize_fact_value(fact_key, v.strip())
        return esc(hv) if hv.strip() else "<i>не задано</i>"
    return _me_format_value(v)


def _me_format_value(v: Any) -> str:
    if _me_is_empty_value(v):
        return "<i>не задано</i>"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return esc(v)
    if isinstance(v, str):
        return esc(v.strip()) if v.strip() else "<i>не задано</i>"
    if isinstance(v, list):
        if all(not isinstance(x, (dict, list)) for x in v):
            return ", ".join(esc(str(x)) for x in v[:24]) + ("…" if len(v) > 24 else "")
        return code_block_html(json_pretty(v)[:3500])
    if isinstance(v, dict):
        if not v:
            return "<i>не задано</i>"
        inner: List[str] = []
        for sk, sv in sorted(v.items(), key=lambda t: str(t[0])):
            inner.append(f"{_me_fact_label_ru(sk)} — {_me_format_value(sv)}")
        return "<br/>".join(inner)
    return esc(v)


_PSYCH_SENTIMENT_RU = {
    "neutral": "нейтральная",
    "stressed": "напряжённая (есть маркеры усталости или стресса)",
}

_TWIN_SECTION_RU = {
    "location": "Локация",
    "learning_profile": "Учебный профиль",
    "interaction_history": "История взаимодействий",
    "interests": "Интересы",
    "created_at": "Создан",
}

# Эмодзи секций «как в карточке профиля» (отдельная иконка у каждого блока).
_TWIN_SECTION_EMOJI = {
    "location": "📍",
    "learning_profile": "📚",
    "interests": "🎯",
    "interaction_history": "📜",
    "created_at": "🕐",
}

_TWIN_FIELD_RU = {
    "city": "Город",
    "country": "Страна / регион",
    "interests": "Интересы",
    "strengths": "Сильные стороны",
    "weaknesses": "Зоны роста",
    "learning_speed": "Темп усвоения",
    "preferred_explanation_style": "Стиль объяснений",
    "social_profile": "Социальный профиль",
    "group_behavior": "Поведение в группе",
    "collaboration_level": "Уровень кооперации",
    "goals": "Цели",
    "last_updated": "Обновлён",
    "user_id": "ID пользователя",
}


def _psych_bool_ru(val: Any) -> str:
    return "да" if val else "нет"


def _psych_tags_block_html(keywords: Any) -> str:
    if not isinstance(keywords, list) or not keywords:
        return "<b>Теги по тексту</b>\n<i>нет</i>"
    return "<b>Теги по тексту</b>\n" + ", ".join(f"<code>{esc(str(x))}</code>" for x in keywords[:12])


def _telegram_datetime_line_html(value: Any) -> str:
    """Время для отчётов в Telegram: без сырого ISO; зона из GEMMA_REPORT_TIMEZONE (если задана)."""
    cap = format_operator_datetime_from_iso(value)
    if not cap:
        return "—"
    if report_time_uses_utc_wall():
        return f"<b>{esc(cap)}</b> · <i>UTC</i>"
    zn = report_timezone_label()
    off = report_utc_offset_label()
    return f"<b>{esc(cap)}</b> · <i>{esc(zn)} ({esc(off)})</i>"


def _profile_datetime_compact_html(value: Any) -> str:
    """Дата/время для карточки профиля: только локальное время из GEMMA_REPORT_TIMEZONE, без IANA и UTC±N."""
    cap = format_operator_datetime_from_iso(value)
    return f"<b>{esc(cap)}</b>" if cap else "—"


def _psych_profile_body_lines(profile: Dict[str, Any], *, compact_time: bool = False) -> List[str]:
    """Текст блока психопрофиля без заголовка «🧠» (для /me и /psych)."""
    lines: List[str] = []
    _dt = _profile_datetime_compact_html if compact_time else _telegram_datetime_line_html
    la = profile.get("last_analysis") if isinstance(profile.get("last_analysis"), dict) else None
    if la:
        lines.append("<b>Последний разбор реплики</b>")
        raw_sent = la.get("sentiment")
        sent_key = str(raw_sent) if raw_sent is not None else ""
        sent_disp = _PSYCH_SENTIMENT_RU.get(sent_key, sent_key or "—")
        lines.append(f"<b>Тональность</b>\n{esc(sent_disp)}")
        lines.append(f"<b>Стресс в формулировках</b>\n{_psych_bool_ru(la.get('stress_signals'))}")
        lines.append(_psych_tags_block_html(la.get("keywords")))
        if la.get("analyzed_at"):
            lines.append(f"<b>Время анализа</b>\n{_dt(la.get('analyzed_at'))}")
        if la.get("message_length") is not None:
            lines.append(f"<b>Длина сообщения</b>\n{esc(la.get('message_length'))} симв.")
        lines.append("")
    if profile.get("stress_streak") is not None:
        lines.append(f"<b>Стресс подряд</b> <i>(эвристика)</i>\n{esc(profile.get('stress_streak'))}")
    if profile.get("updated_at"):
        lines.append(f"<b>Обновлено</b>\n{_dt(profile.get('updated_at'))}")
    shown = {"last_analysis", "stress_streak", "updated_at"}
    for k in sorted(profile.keys(), key=str):
        if k in shown:
            continue
        v = profile[k]
        if v is None or v == {} or v == []:
            continue
        label = str(k).replace("_", " ").strip().title()
        lines.append(f"• <b>{esc(label)}</b>: {_me_format_value(v)}")
    return lines


def format_psych_html(profile: Dict[str, Any]) -> str:
    if not profile:
        return (
            "🧠 <b>Психологический профиль</b>\n\n"
            "<blockquote><i>Пока нет данных — напишите несколько сообщений в диалог.</i></blockquote>"
        )
    body = "\n".join(_psych_profile_body_lines(profile, compact_time=True)).strip()
    foot = ""
    if report_time_uses_utc_wall():
        foot = (
            "\n\n<i>Время — UTC. Для отображения в вашей зоне задайте "
            "<code>GEMMA_REPORT_TIMEZONE</code> в .env.</i>"
        )
    return (
        "🧠 <b>Психологический профиль</b>\n\n"
        "🔍 <b>Сводка</b>\n"
        f"<blockquote>{body}</blockquote>{foot}"
    )


def format_me_html(data: Dict[str, Any]) -> str:
    uid = str(data.get("user_id", "") or "")
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    prefs = data.get("preferences") if isinstance(data.get("preferences"), dict) else {}
    psych = data.get("psychology") if isinstance(data.get("psychology"), dict) else {}
    twin = data.get("digital_twin") if isinstance(data.get("digital_twin"), dict) else {}
    parts: List[str] = [
        "👤 <b>Профиль пользователя</b>",
        "",
        "📋 <b>Основная информация</b>",
        "<blockquote>" + "\n".join(_me_core_profile_lines_html(uid, facts)) + "</blockquote>",
        "",
        "🎮 <b>Интересы</b>",
        "<blockquote>" + "\n".join(_me_interests_section_html(facts)) + "</blockquote>",
        "",
        "💬 <b>Стиль общения</b>",
        "<blockquote>" + "\n".join(_me_prefs_bullets_html(prefs)) + "</blockquote>",
        "",
        "📊 <b>Аналитика</b>",
        "<blockquote>" + "\n".join(_psych_profile_bullets_html(psych)) + "</blockquote>",
    ]
    if twin:
        parts.extend(["", format_twin_html(twin, for_me_page=True)])
    return "\n".join(parts)


def _twin_leaf_html(val: Any, *, field_key: str = "") -> str:
    if val is None:
        return "<i>нет данных</i>"
    if isinstance(val, bool):
        return "да" if val else "нет"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return esc(val)
    if isinstance(val, str):
        s = val.strip()
        if s and field_key:
            s = _humanize_twin_field(field_key, s)
        return esc(s) if s else "<i>нет данных</i>"
    if isinstance(val, list):
        if not val:
            return "<i>пусто</i>"
        if all(not isinstance(x, (dict, list)) for x in val):
            return ", ".join(esc(str(x)) for x in val[:16]) + ("…" if len(val) > 16 else "")
        return f"<i>{len(val)} записей</i>"
    if isinstance(val, dict):
        inner = ", ".join(f"{esc(str(sk))}: {esc(json.dumps(sv, ensure_ascii=False)[:80])}" for sk, sv in list(val.items())[:4])
        return inner + ("…" if len(val) > 4 else "")
    return esc(val)


def _twin_nested_dict_lines(d: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for sk, sv in sorted(d.items(), key=lambda t: str(t[0])):
        label = _TWIN_FIELD_RU.get(sk, sk.replace("_", " "))
        if isinstance(sv, dict):
            out.append(f"<b>{esc(label)}</b>")
            for ssk, ssv in sorted(sv.items(), key=lambda t: str(t[0])):
                sl = _TWIN_FIELD_RU.get(ssk, ssk.replace("_", " "))
                out.append(f"{esc(sl)}\n{_twin_leaf_html(ssv, field_key=str(ssk))}")
        else:
            out.append(f"<b>{esc(label)}</b>\n{_twin_leaf_html(sv, field_key=str(sk))}")
    return out


def _twin_interaction_history_line(items: Any) -> List[str]:
    if not isinstance(items, list) or not items:
        return ["<i>нет записей</i>"]
    last_ts = ""
    last = items[-1]
    if isinstance(last, dict):
        last_ts = str(last.get("timestamp") or "")
    tail = f"\n<i>Последняя запись:</i> <code>{esc(last_ts)}</code>" if last_ts else ""
    return [f"{len(items)} событий{tail}"]


def format_twin_html(twin: Dict[str, Any], *, for_me_page: bool = False) -> str:
    if not twin:
        empty = "<i>Пока пусто — данные появятся при сохранении интересов и учебного профиля.</i>"
        return f"🪞 <b>Цифровой двойник</b>\n\n<blockquote>{empty}</blockquote>"
    lines: List[str] = ["🪞 <b>Цифровой двойник</b>", ""]
    uid = twin.get("user_id")
    if not for_me_page and uid is not None and str(uid):
        lines.append("🆔 <b>ID в данных</b>")
        lines.append(f"<blockquote><code>{esc(str(uid))}</code></blockquote>")
        lines.append("")
    section_order = ("location", "learning_profile", "interests", "interaction_history", "created_at")
    seen: Set[str] = set()
    section_chunks: List[str] = []

    def emit_section(title: str, inner_body: List[str], emoji: str = "📎") -> None:
        if not inner_body:
            return
        section_chunks.append(f"{emoji} <b>{esc(title)}</b>")
        section_chunks.append("<blockquote>" + "\n\n".join(inner_body) + "</blockquote>")

    for key in section_order:
        if key not in twin:
            continue
        seen.add(key)
        v = twin[key]
        label = _TWIN_SECTION_RU.get(key, key.replace("_", " "))
        emoji = _TWIN_SECTION_EMOJI.get(key, "📎")
        inner: List[str] = []
        if key == "interaction_history":
            inner.extend(_twin_interaction_history_line(v))
        elif isinstance(v, dict) and v:
            inner.extend(_twin_nested_dict_lines(v))
        elif isinstance(v, list):
            inner.append(_twin_leaf_html(v, field_key=key))
        elif v is not None and v != "":
            inner.append(_twin_leaf_html(v, field_key=key))
        emit_section(label, inner, emoji)

    for key in sorted(twin.keys(), key=str):
        if key in seen or key == "user_id":
            continue
        v = twin[key]
        if v is None or v == {} or v == []:
            continue
        title = str(key).replace("_", " ")
        inner: List[str] = []
        if isinstance(v, dict):
            inner.extend(_twin_nested_dict_lines(v))
        else:
            inner.append(_twin_leaf_html(v, field_key=str(key)))
        emit_section(title, inner, "📎")

    lines.extend(section_chunks)
    return "\n\n".join(lines).rstrip()


def format_mem0_facts_html(facts: Any) -> str:
    head = "🧩 <b>Факты Mem0</b>"
    if facts is None:
        return head + "\n\n<blockquote><i>Нет ответа от хранилища.</i></blockquote>"
    if not isinstance(facts, list):
        inner = "<i>Неожиданный формат данных.</i>\n" + code_block_html(json_pretty(facts))
        return head + "\n\n<blockquote>" + inner + "</blockquote>"
    if len(facts) == 0:
        return head + "\n\n<blockquote><i>Записей пока нет.</i></blockquote>"
    rows: List[str] = []
    for i, it in enumerate(facts, 1):
        if isinstance(it, dict):
            content = it.get("content") if it.get("content") is not None else it.get("memory")
            mid = it.get("id")
            blob = esc(str(content or "").strip() or "—")
            if mid:
                rows.append(f"{i}. {blob}")
                rows.append(f"   <code>{esc(str(mid))}</code>")
            else:
                rows.append(f"{i}. {blob}")
        else:
            rows.append(f"{i}. {esc(it)}")
    return head + "\n\n🗂️ <b>Записи</b>\n\n<blockquote>" + "\n".join(rows) + "</blockquote>"


def json_pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


_FACT_META_SOURCE_RU = {
    "message_extract": "авто · из сообщения",
    "user_confirm": "подтверждение",
    "manual": "вручную",
    "import": "импорт",
}
_FACT_META_KNOWN = frozenset(
    {"updated_at", "expires_at", "revoked", "revoked_at", "source", "confidence"}
)


def _facts_confidence_pct_html(val: Any) -> str:
    try:
        c = float(val)
        if c > 1.0 + 1e-9:
            return esc(f"{int(round(c))}%")
        pct = int(round(c * 100))
        return esc(f"{pct}%")
    except (TypeError, ValueError):
        return esc(str(val))


def _facts_meta_datetime_html(val: Any) -> str:
    cap = format_operator_datetime_from_iso(val)
    return esc(cap) if cap else "—"


def _facts_meta_source_ru(src: Any) -> str:
    s = str(src or "").strip()
    if not s:
        return "—"
    return esc(_FACT_META_SOURCE_RU.get(s, s.replace("_", " ")))


def _facts_value_display_html(key: str, v: Any) -> str:
    k = str(key or "").strip()
    if k == "interests":
        if isinstance(v, list):
            items = [str(x).strip() for x in v if str(x).strip()]
            return ", ".join(esc(x) for x in items) if items else "<i>не задано</i>"
        if isinstance(v, str) and v.strip():
            parts = re.split(r"[\n,;•\[\]'\"]+", v)
            items = [p.strip() for p in parts if p.strip() and p.strip() not in {"[", "]"}]
            if items:
                return ", ".join(esc(x) for x in items)
            return _me_format_fact_value(k, v)
    if k == "timezone" and isinstance(v, str):
        return _me_fact_timezone_line(v.strip())
    if isinstance(v, str):
        return _me_format_fact_value(k, v)
    return _me_format_value(v)


def _facts_values_lines_html(
    facts: Mapping[str, Any],
    *,
    empty_line: str = "<i>Пока нет сохранённых фактов.</i>",
) -> List[str]:
    if not facts:
        return [empty_line]
    lines: List[str] = []
    for fk in sorted(facts.keys(), key=str):
        v = facts.get(fk)
        if _me_is_empty_value(v):
            continue
        label = _me_fact_label_ru(str(fk))
        disp = _facts_value_display_html(str(fk), v)
        lines.append(f"• <b>{esc(label)}</b>: {disp}")
    return lines or ["<i>Нет заполненных полей.</i>"]


def _facts_meta_field_lines(field: str, m: Any) -> List[str]:
    label = _me_fact_label_ru(str(field))
    if not isinstance(m, dict):
        return [f"<b>{esc(label)}</b>", f"• {_me_format_value(m)}"]
    out: List[str] = [f"<b>{esc(label)}</b>"]
    if m.get("revoked") is True:
        out.append("• Статус: <i>отозвано</i>")
        if m.get("revoked_at"):
            out.append(f"• Снято: {_facts_meta_datetime_html(m['revoked_at'])}")
    if m.get("updated_at"):
        out.append(f"• Обновлено: {_facts_meta_datetime_html(m['updated_at'])}")
    if m.get("expires_at"):
        out.append(f"• Действует до: {_facts_meta_datetime_html(m['expires_at'])}")
    src = m.get("source")
    if src is not None and str(src).strip():
        out.append(f"• Источник: {_facts_meta_source_ru(src)}")
    if m.get("confidence") is not None and m.get("revoked") is not True:
        out.append(f"• Уверенность: {_facts_confidence_pct_html(m.get('confidence'))}")
    for ek, ev in sorted(m.items(), key=lambda t: str(t[0])):
        if ek in _FACT_META_KNOWN:
            continue
        lk = _me_fact_label_ru(str(ek))
        out.append(f"• <b>{esc(lk)}</b>: {_me_format_value(ev)}")
    if len(out) == 1:
        out.append("• <i>нет служебных данных</i>")
    return out


def format_facts_html(data: Dict[str, Any]) -> str:
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    meta = data.get("facts_meta") if isinstance(data.get("facts_meta"), dict) else {}
    parts = [
        "📝 <b>Ваши факты</b>",
        "",
        "📌 <b>Что запомнили</b>",
        "<blockquote>" + "\n".join(_facts_values_lines_html(facts)) + "</blockquote>",
    ]
    if meta:
        chunks: List[str] = []
        limit = 30
        keys = sorted(meta.keys(), key=str)[:limit]
        for k in keys:
            chunks.append("\n".join(_facts_meta_field_lines(str(k), meta.get(k))))
        if len(meta) > limit:
            chunks.append(f"<i>… ещё полей в метаданных: {len(meta) - limit}</i>")
        parts.extend(["", "🔖 <b>Подробности записи</b>", "<blockquote>" + "\n\n".join(chunks) + "</blockquote>"])
    return "\n".join(parts)


def format_facts_refresh_html(data: Dict[str, Any]) -> str:
    before = data.get("before") if isinstance(data.get("before"), dict) else {}
    after = data.get("after") if isinstance(data.get("after"), dict) else {}
    parts = ["🔄 <b>Факты обновлены</b>", ""]
    parts.append(f"⏮️ <b>Было</b> ({len(before)} полей)")
    parts.append(
        "<blockquote>"
        + "\n".join(_facts_values_lines_html(before, empty_line="<i>нет полей</i>"))
        + "</blockquote>"
    )
    parts.append("")
    parts.append(f"✅ <b>Стало</b> ({len(after)} полей)")
    parts.append(
        "<blockquote>"
        + "\n".join(_facts_values_lines_html(after, empty_line="<i>нет полей</i>"))
        + "</blockquote>"
    )
    return "\n".join(parts)


_CORPUS_KIND_RU: Dict[str, str] = {
    "book": "книга",
    "law_act": "НПА",
    "shared_ingest": "общая база",
}


def format_corpus_catalog_html(data: Dict[str, Any]) -> str:
    """Сводка списка из corpus_catalog для /corpus_books и /corpus_docs."""
    if not data.get("ok"):
        err = esc(str(data.get("error") or "ошибка"))
        return "\n".join(["📚 <b>Корпус документов</b>", "", f"<blockquote><i>{err}</i></blockquote>"])
    mode = str(data.get("mode") or "")
    total = int(data.get("total") or 0)
    offset = int(data.get("offset") or 0)
    limit = int(data.get("limit") or 0)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    truncated = bool(data.get("truncated"))
    page_cmd = str(data.get("page_command") or "corpus_docs")

    if mode == "books":
        head = "📚 <b>Книги в корпусе</b>"
        sub_html = "Проиндексированные книги (BooksRAG и др.) — id для <code>/corpus_doc</code>."
    elif mode == "documents":
        head = "📄 <b>Документы в корпусе</b>"
        sub_html = "НПА, материалы общей базы и прочее (не книги)."
    else:
        head = "📑 <b>Все записи корпуса</b>"
        sub_html = "Книги и документы в одном списке."

    parts: List[str] = [head, f"<i>{sub_html}</i>", f"<i>Всего: {total}</i>", ""]
    if not items:
        parts.append(
            "<blockquote><i>Пусто. Книги: загрузка через /add_book; акты — после кэша LawSearch; общая база — кнопка «Общая база» у вложений.</i></blockquote>"
        )
        return "\n".join(parts)

    lines: List[str] = []
    for idx, it in enumerate(items):
        n = offset + idx + 1
        doc_id = esc(str(it.get("id") or ""))
        tit = (str(it.get("title") or "")).strip()
        kind = str(it.get("kind") or "")
        kr = esc(_CORPUS_KIND_RU.get(kind, kind or "—"))
        nc = int(it.get("chunks") or 0)
        title_bit = f" — {esc(tit)}" if tit else ""
        if mode == "documents":
            lines.append(f"{n}. <code>{doc_id}</code> · <i>{kr}</i>{title_bit} · {nc} чанк.")
        elif mode == "all":
            lines.append(f"{n}. <code>{doc_id}</code> · <i>{kr}</i>{title_bit} · {nc} чанк.")
        else:
            lines.append(f"{n}. <code>{doc_id}</code>{title_bit} · {nc} чанк.")
    parts.append("<blockquote>" + "\n".join(lines) + "</blockquote>")
    parts.append("")
    parts.append("<i>Скачать файл:</i> <code>/corpus_doc &lt;id&gt;</code>")
    if truncated:
        nxt = offset + limit
        parts.append(f"<i>Далее:</i> <code>/{esc(page_cmd)} {nxt}</code>")
    return "\n".join(parts)


def _c_plugins_cmd(cmd: str) -> str:
    return f"<code>{esc(cmd.strip())}</code>"


def _plugins_status_table_pre(rows: List[Tuple[str, str]]) -> str:
    """Два столбца (название · статус) моноширинным блоком для Telegram <pre>."""
    if not rows:
        return ""
    w_name = max(10, min(24, max(len(n) for n, _ in rows)))
    sts = [s for _, s in rows]
    w_stat = max(12, min(44, max(len(s) for s in sts)))
    out_ln: List[str] = []
    out_ln.append(f"{'Название'.ljust(w_name)}  {'Статус'}")
    out_ln.append(f"{'─' * w_name}  {'─' * w_stat}")
    for name, st in rows:
        n = name if len(name) <= w_name else name[: max(1, w_name - 1)] + "…"
        s = st if len(st) <= w_stat else st[: max(1, w_stat - 1)] + "…"
        out_ln.append(f"{n.ljust(w_name)}  {s}")
    return "\n".join(out_ln)


def format_plugins_status_html(items: List[Dict[str, Any]]) -> str:
    """
    Сводка по плагинам для /plugins.
    items: name, type, loaded (bool), status (healthy|failed|disabled|...), error (optional str).
    """
    head = "📦 <b>Плагины</b> <i>(реестр)</i>"
    if not items:
        return "\n".join(
            [
                head,
                "",
                "📭 <b>Реестр пуст</b>",
                "<blockquote><i>Нет зарегистрированных модулей — проверьте каталог <code>modules</code> и <code>MODULES_PATH</code>.</i></blockquote>",
            ]
        )
    legend = "🟢 Активен · 🟡 ошибка / деградация · 🔴 выключен · 🔵 ожидает / иной статус"
    table_rows: List[Tuple[str, str]] = []
    for it in items:
        name = str(it.get("name") or "").strip()
        if not name:
            name = "—"
        typ = str(it.get("type") or "").strip()
        loaded = bool(it.get("loaded"))
        st = str(it.get("status") or "").strip()
        err = str(it.get("error") or "").strip().replace("\n", " ")
        ver = it.get("version")
        ver_s = f" v{ver}" if ver else ""
        label = f"{name}{ver_s}"
        if typ:
            label = f"{label} ({typ})"
        if not loaded:
            status_cell = "🔴 Выключен"
        elif st == "healthy":
            status_cell = "🟢 Активен"
        elif st == "degraded":
            tail = f": {err[:36]}" if err else ""
            status_cell = f"🟡 Деградация{tail}"
        elif st == "failed":
            tail = f": {err[:36]}" if err else ""
            status_cell = f"🟡 Ошибка{tail}"
        elif st == "disabled":
            status_cell = "🔴 Выключен"
        else:
            hint = f" · {st}" if st else ""
            status_cell = f"🔵 Ожидает{hint}"
        table_rows.append((label, status_cell))
    table_txt = _plugins_status_table_pre(table_rows)
    foot = (
        f"<i>Всего: {len(items)}. Справка: {_c_plugins_cmd('/plugins_help')} · "
        f"каталог slash-команд: {_c_plugins_cmd('/help')} → «Плагины».</i>"
    )
    return "\n".join(
        [
            head,
            "",
            "📖 <b>Условные обозначения</b>",
            f"<blockquote>{legend}</blockquote>",
            "",
            "📋 <b>Список</b>",
            f"<pre>{esc(table_txt)}</pre>",
            "",
            "🔗 <b>Навигация</b>",
            f"<blockquote>{foot}</blockquote>",
        ]
    )


def format_plugins_help_html() -> str:
    """Краткая справка для /plugins_help."""
    sec_intro = "\n".join(
        [
            f"{_c_plugins_cmd('/plugins')} — список модулей с индикатором состояния:",
            "🟢 экземпляр создан, модуль включён;",
            "🟡 ошибка экземпляра или статус деградации (см. текст или логи);",
            "🔴 модуль в реестре, но выключен (не в списке активных).",
            "",
            f"Полный перечень slash-команд из манифестов: раздел «Плагины» в {_c_plugins_cmd('/help')} <i>(кнопка внизу)</i>.",
            f"Общее состояние системы: {_c_plugins_cmd('/status')} или {_c_plugins_cmd('/system_state')}.",
        ]
    )
    sec_reason = "\n".join(
        [
            f"{_c_plugins_cmd('/solution_explorer')} оптимизировать обработку инцидентов без потери качества",
            f"{_c_plugins_cmd('/reason_timeline')} 2026-05-07 10:00 | status=OPEN\\n2026-05-07 10:30 | status=CLOSED",
            f"{_c_plugins_cmd('/reason_fsm')} start=INIT target=CLOSED INIT->OPEN OPEN->READY READY->CLOSED forbid ERROR",
            f"{_c_plugins_cmd('/reason_consistency')} conditions: must:ready forbid:closed || answer: system is closed",
        ]
    )
    sec_local = "\n".join(
        [
            f"{_c_plugins_cmd('/local_text')} op=normalize_spaces || text=hello   world",
            f"{_c_plugins_cmd('/local_regex')} pattern=\\\\d+ || text=order_123_ok",
            f"{_c_plugins_cmd('/local_math')} expr=(17**7+17)*1234567",
            f"{_c_plugins_cmd('/local_parse')} fmt=json || text={{\"a\":1,\"b\":2}}",
            f"{_c_plugins_cmd('/local_fs')} op=read || path=README.md",
            f"{_c_plugins_cmd('/local_tokenize')} text=hello world",
            f"{_c_plugins_cmd('/local_diff')} a=line1 || b=line2",
            f"{_c_plugins_cmd('/local_cache')} get || key=auto_reasoning_last",
            f"{_c_plugins_cmd('/context_reduce')} длинный текст для сжатия контекста...",
            f"{_c_plugins_cmd('/benchmark_run')} quick",
            f"{_c_plugins_cmd('/benchmark_run')} full suites=attention,logic,routing threshold=100",
            f"{_c_plugins_cmd('/benchmark_run')} nightly compare=1 save=1",
        ]
    )
    sec_school = "\n".join(
        [
            f"{_c_plugins_cmd('/explain')} математика производная",
            f"{_c_plugins_cmd('/solve')} математика 2x+4=10",
            f"{_c_plugins_cmd('/check')} математика 25% от 80 || 20",
            f"{_c_plugins_cmd('/quiz')} физика механика",
        ]
    )
    sec_img = "\n".join(
        [
            "<i>Прикрепите фото и напишите задачу текстом:</i>",
            "• оставь лицо, замени фон на студию",
            "• сделай ч/б в цвет / сделай black and white",
            "• улучши качество, убери шум",
            "• сделай стиль аниме / рисунок",
            "• удали предмет справа / поменяй позу",
        ]
    )
    return "\n\n".join(
        [
            "🧩 <b>Справка: плагины</b>",
            "",
            "📖 <b>Общее</b>",
            f"<blockquote>{sec_intro}</blockquote>",
            "🧪 <b>Примеры reasoning-команд</b>",
            f"<blockquote>{sec_reason}</blockquote>",
            "🔧 <b>Примеры Local*</b>",
            f"<blockquote>{sec_local}</blockquote>",
            "🎓 <b>Учёба (School Assistant)</b>",
            f"<blockquote>{sec_school}</blockquote>",
            "🖼 <b>Редактирование фото (ImageSkill)</b>",
            f"<blockquote>{sec_img}</blockquote>",
        ]
    )


def format_plugin_health_html(payload: Dict[str, Any]) -> str:
    if not payload or not payload.get("ok"):
        err = esc((payload or {}).get("error") or "нет данных")
        return (
            "🧩 <b>Здоровье плагинов</b>\n\n"
            "⚠️ <b>Ошибка</b>\n"
            f"<blockquote><i>{err}</i></blockquote>"
        )
    s = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    sum_rows = [
        ("Зарегистрировано", str(s.get("registered_total", 0))),
        ("Загружено", str(s.get("loaded_total", 0))),
        ("Сбойных загруж.", str(s.get("failed_loaded_total", 0))),
        ("Конфликтов cmd", str(s.get("collision_tokens_total", 0))),
        ("Без cmd", str(s.get("loaded_without_commands_total", 0))),
    ]
    body_main: List[str] = [
        "<i>Показывает, что реально зарегистрировано, загружено и какие slash-команды видит роутер.</i>",
        "",
        report_pre_kv(sum_rows),
    ]
    failed = payload.get("failed_loaded") if isinstance(payload.get("failed_loaded"), list) else []
    if failed:
        body_main.append(
            f"⚠️ Сбойные загруженные: <code>{esc(', '.join(str(x) for x in failed[:20]))}</code>"
        )
    nocmd = payload.get("loaded_without_commands") if isinstance(payload.get("loaded_without_commands"), list) else []
    if nocmd:
        body_main.append(
            f"ℹ️ Загружены без slash-команд: <code>{esc(', '.join(str(x) for x in nocmd[:20]))}</code>"
        )
    parts: List[str] = [
        "🧩 <b>Здоровье плагинов</b>",
        "",
        "📊 <b>Сводка</b>",
        "<blockquote>" + "\n".join(body_main) + "</blockquote>",
    ]
    col = payload.get("command_collisions") if isinstance(payload.get("command_collisions"), dict) else {}
    if col:
        col_lines = []
        for tok, owners in list(col.items())[:20]:
            if isinstance(owners, list):
                col_lines.append(f"• <code>/{esc(tok)}</code> → <code>{esc(', '.join(str(x) for x in owners))}</code>")
        if col_lines:
            parts.extend(["", "⚡ <b>Конфликты slash-токенов</b>", "<blockquote>" + "\n".join(col_lines) + "</blockquote>"])
    parts.extend(["", "📎 <b>Машинный отчёт</b>", "<blockquote><i>JSON: <code>/admin_plugins_health_json</code></i></blockquote>"])
    return "\n".join(parts)


def format_reasoning_quality_html(payload: Dict[str, Any]) -> str:
    if not payload:
        return (
            "🧠 <b>Качество reasoning</b>\n\n"
            "📭 <b>Данные</b>\n"
            "<blockquote><i>нет данных</i></blockquote>"
        )
    if not payload.get("ok"):
        return (
            "🧠 <b>Качество reasoning</b>\n\n"
            "⚠️ <b>Статус degraded</b>\n"
            "<blockquote>"
            f"<i>{esc(payload.get('error') or 'нет валидного snapshot')}</i>\n\n"
            "<i>JSON: <code>/admin_reasoning_quality_json</code></i>"
            "</blockquote>"
        )
    rows = [
        ("intent", str(payload.get("intent") or "")),
        ("module", str(payload.get("module") or "")),
        ("outcome", str(payload.get("outcome") or "")),
        ("final_answer_present", ru_bool(payload.get("final_answer_present"))),
        ("reasoning_completed", ru_bool(payload.get("reasoning_completed"))),
        ("no_meta_text", ru_bool(payload.get("no_meta_text"))),
    ]
    body = [
        "<i>OK означает: финальный ответ есть, reasoning завершён, meta-текста нет.</i>",
        "",
        report_pre_kv(rows),
    ]
    excerpt = str(payload.get("assistant_excerpt") or "").strip()
    if excerpt:
        if len(excerpt) > 380:
            excerpt = excerpt[:377] + "..."
        body.append(f"Фрагмент ответа: <code>{esc(excerpt)}</code>")
    parts = [
        "🧠 <b>Качество reasoning</b>",
        "",
        "📊 <b>Метрики</b>",
        "<blockquote>" + "\n".join(body) + "</blockquote>",
        "",
        "📎 <b>Машинный отчёт</b>",
        "<blockquote><i>JSON: <code>/admin_reasoning_quality_json</code></i></blockquote>",
    ]
    return "\n".join(parts)


def format_system_state_html(info: Dict[str, Any]) -> str:
    mods = info.get("modules") or []
    n = len(mods) if isinstance(mods, list) else 0
    failed = sum(1 for m in (mods if isinstance(mods, list) else []) if isinstance(m, dict) and m.get("status") == "failed")
    parts: List[str] = ["⚙️ <b>Состояние системы</b>", ""]

    overview: List[str] = [
        f"Общий статус: <b>{esc(ru_status(info.get('overall_status')))}</b>",
        f"Модулей в реестре: <b>{n}</b>",
    ]
    mode = info.get("mode")
    if mode is not None and str(mode):
        overview.append(f"Режим: <code>{esc(mode)}</code>")
    if failed:
        overview.append(
            f"⚠️ Модулей в ошибке: <b>{failed}</b> <i>(внутренний статус <code>failed</code>)</i>"
        )
    parts.append("📊 <b>Обзор</b>")
    parts.append("<blockquote>" + "\n".join(overview) + "</blockquote>")

    ke = info.get("knowledge_engine") if isinstance(info.get("knowledge_engine"), dict) else {}
    pool = ke.get("context_pool_rows")
    if pool is None and ke.get("entries") is not None:
        pool = ke.get("entries")
    if pool is not None:
        ke_lines = [
            f"Пул контекста для ответа (движок знаний): <b>{esc(pool)}</b> строк",
            "<i>Учитываются факты, фрагменты Mem0, темы и хвост диалога с последней сборки — "
            "это не размер архива и не полный счётчик облачной памяти.</i>",
        ]
        parts.extend(["", "🧠 <b>Контекст и знания</b>", "<blockquote>" + "\n".join(ke_lines) + "</blockquote>"])

    ums = info.get("user_memory_snapshot") if isinstance(info.get("user_memory_snapshot"), dict) else {}
    ums_lines: List[str] = []
    if ums.get("knowledge_archive_entries") is not None:
        ums_lines.append(f"Ваш архив знаний (сохранённые тексты): <b>{esc(ums.get('knowledge_archive_entries'))}</b>")
    if ums.get("mem0_facts") is not None:
        ums_lines.append(
            f"Фактов в Mem0 (долговременная память, до 100 в списке API): <b>{esc(ums.get('mem0_facts'))}</b>"
        )
    if ums_lines:
        parts.extend(["", "💭 <b>Память в снимке</b>", "<blockquote>" + "\n".join(ums_lines) + "</blockquote>"])

    rs = info.get("reasoning_snapshot") if isinstance(info.get("reasoning_snapshot"), dict) else {}
    if rs:
        score = rs.get("score_percent")
        passed = rs.get("passed_cases")
        total = rs.get("total_cases")
        ok = rs.get("ok")
        status = "ok" if ok else "degraded"
        rs_line = (
            f"Reasoning: <b>{esc(status)}</b> · score: <code>{esc(score)}%</code> · "
            f"cases: <code>{esc(passed)}/{esc(total)}</code>"
        )
        parts.extend(["", "🧪 <b>Бенчмарк reasoning</b>", "<blockquote>" + rs_line + "</blockquote>"])

    ar = info.get("auto_reasoning") if isinstance(info.get("auto_reasoning"), dict) else {}
    if ar:
        en = "on" if ar.get("enabled") else "off"
        md = ar.get("mode") or "legacy"
        pc = ar.get("plugins_count")
        avg = ar.get("avg_plugins_per_task")
        avg_routed = ar.get("avg_routed_per_task")
        avg_local = ar.get("avg_local_calls_per_task")
        ts = ar.get("timeout_sec")
        eff = ar.get("token_efficiency_percent")
        saved_total = ar.get("estimated_saved_tokens_total")
        base_total = ar.get("estimated_baseline_tokens_total")
        ar_lines = [
            (
                f"Auto reasoning: <b>{esc(en)}</b> · mode: <code>{esc(md)}</code> · plugins: <code>{esc(pc)}</code> · "
                f"avg run: <code>{esc(avg)}</code> · avg routed: <code>{esc(avg_routed)}</code> · "
                f"avg local: <code>{esc(avg_local)}</code> · timeout: <code>{esc(ts)}s</code>"
            ),
            (
                f"Token efficiency: <b>{esc(eff)}%</b> · saved est: <code>{esc(saved_total)}</code> · "
                f"baseline est: <code>{esc(base_total)}</code>"
            ),
        ]
        parts.extend(["", "🤖 <b>Auto reasoning</b>", "<blockquote>" + "\n".join(ar_lines) + "</blockquote>"])

    af = info.get("anti_flood") if isinstance(info.get("anti_flood"), dict) else {}
    if af:
        on = "вкл" if af.get("enabled") else "выкл"
        lim = af.get("max_msg_per_10s")
        af_line = f"Антифлуд: <b>{on}</b> · лимит сообщений / 10 с: <code>{esc(lim)}</code>"
        parts.extend(["", "🛡️ <b>Антифлуд</b>", "<blockquote>" + af_line + "</blockquote>"])

    mon = info.get("monitoring") if isinstance(info.get("monitoring"), dict) else {}
    ctr = mon.get("counters") if isinstance(mon.get("counters"), dict) else {}
    if ctr:
        try:
            fb = int(ctr.get("planner_fallback_total", 0))
            sus = int(ctr.get("telegram_reply_suspect_incomplete_total", 0))
        except (TypeError, ValueError):
            fb, sus = 0, 0
        if fb or sus:
            sig_lines = [
                f"• Fallback планировщика: <b>{fb}</b> <i>(часть запросов не в чат-модуль)</i>",
                f"• Подозрение на обрыв ответа: <b>{sus}</b> <i>(лог: <code>[turn]</code>, <code>suspect_incomplete</code>)</i>",
            ]
            parts.extend(["", "📉 <b>Сигналы мониторинга</b>", "<blockquote>" + "\n".join(sig_lines) + "</blockquote>"])

    res = info.get("resilience") if isinstance(info.get("resilience"), dict) else {}
    if res:
        sm = res.get("safe_mode") or {}
        if isinstance(sm, dict) and sm.get("active"):
            parts.extend(
                ["", "🔒 <b>Устойчивость</b>", "<blockquote>Безопасный режим <b>активен</b></blockquote>"]
            )
    return "\n".join(parts)


def format_anti_flood_html(d: Dict[str, Any]) -> str:
    if not d:
        return (
            "🛡️ <b>Антифлуд</b>\n\n"
            "📭 <b>Данные</b>\n"
            "<blockquote><i>нет данных</i></blockquote>"
        )
    af_rows = [(anti_flood_label_ru(k), str(v)) for k, v in sorted(d.items())]
    return "\n".join(
        [
            "🛡️ <b>Антифлуд</b>",
            "",
            "ℹ️ <b>Назначение</b>",
            "<blockquote><i>Ограничения на частоту сообщений и команд.</i></blockquote>",
            "",
            "📊 <b>Параметры</b>",
            "<blockquote>" + report_pre_kv(af_rows) + "</blockquote>",
        ]
    )


def format_governance_html(d: Dict[str, Any]) -> str:
    parts: List[str] = ["📋 <b>Хранение и очистка данных</b>", ""]
    _gov_labels = {
        "retention_days_logs": "Логи, дн.",
        "retention_days_behavior": "Поведение, дн.",
    }
    gov_rows: List[Tuple[str, str]] = []
    for k in ("retention_days_logs", "retention_days_behavior"):
        if k in d:
            gov_rows.append((_gov_labels.get(k, k), str(d[k])))
    if gov_rows:
        parts.append("⏱️ <b>Ретеншн</b>")
        parts.append("<blockquote>" + report_pre_kv(gov_rows) + "</blockquote>")
    rk = d.get("redact_keys")
    if isinstance(rk, list) and rk:
        parts.extend(
            [
                "",
                "🔐 <b>Маскирование</b>",
                "<blockquote>"
                + f"• Маскируемые поля: <code>{esc(', '.join(str(x) for x in rk[:12]))}</code>"
                + "</blockquote>",
            ]
        )
    return "\n".join(parts)


def format_pulse_html(snap: Dict[str, Any], *, embedded: bool = False) -> str:
    """Снимок live_pulse: метрики, p95, воркер, хост, хвост решений планировщика."""
    out: List[str] = []
    if not embedded:
        out.extend(
            [
                "🫀 <b>Пульс системы</b>",
                "",
                "<blockquote><i>Живые счётчики, задержки и последние решения маршрутизатора.</i></blockquote>",
                "",
            ]
        )
    else:
        out.extend(["🫀 <b>Пульс</b> (снимок)", ""])

    mon = snap.get("monitoring") or {}
    if isinstance(mon, dict) and mon:
        mon_rows: List[Tuple[str, int]] = []
        for k in sorted(mon.keys()):
            try:
                mon_rows.append((monitor_label_ru(k), int(mon[k])))
            except (TypeError, ValueError):
                mon_rows.append((monitor_label_ru(k), 0))
        out.extend(
            ["📈 <b>Счётчики</b>", "", "<blockquote>", report_pre_metrics(mon_rows), "</blockquote>", ""]
        )

    obs = snap.get("observability") if isinstance(snap.get("observability"), dict) else {}
    p95 = obs.get("p95_ms") if isinstance(obs.get("p95_ms"), dict) else {}
    p95_rows: List[Tuple[str, int]] = []
    for pk in ("telegram_pipeline", "openrouter_completion"):
        if pk in p95 and p95.get(pk) is not None:
            try:
                p95_rows.append((p95_label_ru(pk), int(format_ms_whole(p95.get(pk)))))
            except (TypeError, ValueError):
                p95_rows.append((p95_label_ru(pk), 0))
    try:
        tr_n = int(obs.get("active_traces") or 0)
    except (TypeError, ValueError):
        tr_n = 0
    lat_inner: List[str] = []
    if p95_rows:
        lat_inner.append(report_pre_metrics(p95_rows))
    else:
        lat_inner.append("<i>Нет данных p95.</i>")
    lat_inner.append(report_pre_kv([("Трасс активно", str(tr_n))]))
    out.extend(
        ["⏱ <b>Задержки (p95) и трассы</b>", "", "<blockquote>", *lat_inner, "</blockquote>", ""]
    )

    hw = snap.get("heavy_worker") if isinstance(snap.get("heavy_worker"), dict) else {}
    hw_rows = [
        ("Очередь", f"{hw.get('queue_depth')}/{hw.get('queue_max')}"),
        ("Параллельно", str(hw.get("max_concurrency") if hw.get("max_concurrency") is not None else "")),
        ("Таймаут, с", str(hw.get("timeout_sec") if hw.get("timeout_sec") is not None else "")),
    ]
    out.extend(
        ["⚙️ <b>Тяжёлые задачи</b>", "", "<blockquote>", report_pre_kv(hw_rows), "</blockquote>", ""]
    )

    hr = snap.get("host_resources") if isinstance(snap.get("host_resources"), dict) else {}
    out.append("🖥 <b>Сервер</b>")
    out.append("")
    if hr.get("available"):
        mem = hr.get("memory") if isinstance(hr.get("memory"), dict) else {}
        pr = hr.get("pressure") if isinstance(hr.get("pressure"), dict) else {}
        srv_rows = [
            ("CPU %", str(hr.get("cpu_percent"))),
            ("RAM %", str(mem.get("percent"))),
            ("Нагрузка", str(pr.get("level") or "—")),
        ]
        out.extend(["<blockquote>", report_pre_kv(srv_rows), "</blockquote>", ""])
    else:
        out.extend(["<blockquote>", f"<i>{esc(hr.get('error', 'нет данных'))}</i>", "</blockquote>", ""])

    res = snap.get("resilience") if isinstance(snap.get("resilience"), dict) else {}
    res_rows = [
        ("Модуль", ru_status(res.get("enabled"))),
        ("Безоп. режим", ru_status(res.get("safe_mode_active"))),
    ]
    out.extend(
        ["🛡 <b>Устойчивость</b>", "", "<blockquote>", report_pre_kv(res_rows), "</blockquote>", ""]
    )

    boot = snap.get("boot") if isinstance(snap.get("boot"), dict) else {}
    _boot_start = format_operator_datetime_from_iso(boot.get("origin_utc")) or str(boot.get("origin_utc", ""))[:19]
    _bstate = boot.get("boot_state") if isinstance(boot.get("boot_state"), dict) else {}
    _prev_boot = format_operator_datetime_from_iso(_bstate.get("previous_start_utc")) or str(
        _bstate.get("previous_start_utc", "")
    )[:19]
    boot_rows = [
        ("Старт", _boot_start),
        ("Перезапуск", "да" if _bstate.get("restart_detected") else "нет"),
        ("Прошлый", _prev_boot if _prev_boot else "—"),
        ("Запуск №", str(_bstate.get("boot_count") or "—")),
        ("Метка", str(boot.get("last_mark_name") or "—")),
        ("Δ, мс", format_ms_whole(boot.get("last_mark_delta_ms"))),
    ]
    out.extend(
        ["🚀 <b>Запуск бота</b>", "", "<blockquote>", report_pre_kv(boot_rows), "</blockquote>", ""]
    )

    pr = snap.get("planner_recent")
    plan_lines: List[str] = []
    if isinstance(pr, list) and pr:
        for row in list(reversed(pr))[:12]:
            if not isinstance(row, dict):
                continue
            tags: List[str] = []
            if row.get("safe_mode"):
                tags.append("safe")
            if row.get("fallback"):
                tags.append("fallback")
            if row.get("maintenance_ran"):
                tags.append("maint")
            tag_s = ""
            if tags:
                tag_s = f" · <i>{esc(planner_tags_ru(tags))}</i>"
            _pts = format_operator_datetime_from_iso(row.get("ts")) or str(row.get("ts", ""))[:19]
            plan_lines.append(
                f"• <code>{esc(_pts)}</code> · <code>{esc(row.get('intent'))}</code> → "
                f"<b>{esc(row.get('module'))}</b>{tag_s}\n"
                f"  <i>{esc(str(row.get('reason', ''))[:72])}</i>"
            )
        if len(pr) > 12:
            plan_lines.append(
                f"<i>В буфере до {len(pr)} записей — полностью: <code>/admin_pulse_json</code></i>"
            )
    else:
        plan_lines.append("<i>После перезапуска ещё не было диалогов.</i>")
    out.extend(
        ["🧠 <b>Последние решения планировщика</b>", "", "<blockquote>", *plan_lines, "</blockquote>", ""]
    )

    if not embedded:
        out.extend(
            [
                "<blockquote><i>Машиночитаемо: <code>/admin_pulse_json</code> · размер хвоста: "
                "<code>LIVE_PULSE_PLANNER_TAIL</code> в .env</i></blockquote>",
                "",
                REPORT_GLOSSARY_FOOTER_HTML,
            ]
        )
    return "\n".join(out).strip()


def format_xray_html(payload: Dict[str, Any]) -> str:
    """HTML для /admin_xray: аномалии + ключевые блоки pulse/errors."""
    lines = [
        "🩻 <b>Рентген системы (XRAY)</b>",
        "",
        "<blockquote><i>Аномалии задержек и узкие места.</i></blockquote>",
        "",
    ]
    anomalies = payload.get("anomalies")
    anom_lines: List[str] = []
    if isinstance(anomalies, list) and anomalies:
        for row in anomalies[:12]:
            if not isinstance(row, dict):
                continue
            sev = str(row.get("severity") or "info").upper()
            mark = "🔴" if sev == "HIGH" else "🟠" if sev == "WARN" else "ℹ️"
            sev_ru = {"HIGH": "серьёзно", "WARN": "внимание", "INFO": "инфо"}.get(sev, sev)
            anom_lines.append(
                f"{mark} <b>{esc(sev_ru)}</b> <code>{esc(row.get('code'))}</code>: {esc(row.get('detail'))}"
            )
    else:
        anom_lines.append("<i>Аномалии не обнаружены.</i>")
    lines.extend(["⚠️ <b>Аномалии</b>", "", "<blockquote>", *anom_lines, "</blockquote>", ""])

    pulse = payload.get("pulse") if isinstance(payload.get("pulse"), dict) else {}
    lines.append(format_pulse_html(pulse, embedded=True))
    lines.append("")

    errs = payload.get("errors") if isinstance(payload.get("errors"), dict) else {}
    lines.append(format_errors_compact_html(errs))
    ul = payload.get("usage_learning") if isinstance(payload.get("usage_learning"), dict) else {}
    ui = payload.get("usage_insights") if isinstance(payload.get("usage_insights"), list) else []
    habit_inner: List[str] = [f"Всего событий: <b>{esc(ul.get('total_events'))}</b>"]
    top_hours = ul.get("top_hours_utc") if isinstance(ul.get("top_hours_utc"), list) else []
    if top_hours:
        h_rows: List[Tuple[str, int]] = []
        for x in top_hours[:8]:
            if isinstance(x, dict):
                h_rows.append((f"{int(x.get('hour', 0)):02d}:00", int(x.get("count") or 0)))
        if h_rows:
            habit_inner.append("")
            habit_inner.append("<b>Часы пика</b>")
            habit_inner.append(report_pre_metrics(h_rows))
            habit_inner.append("<i>Календарный час в данных журнала.</i>")
    top_intents = ul.get("top_intents") if isinstance(ul.get("top_intents"), list) else []
    if top_intents:
        i_rows: List[Tuple[str, int]] = []
        for x in top_intents[:8]:
            if isinstance(x, dict):
                i_rows.append((str(x.get("intent") or "?"), int(x.get("count") or 0)))
        if i_rows:
            habit_inner.append("")
            habit_inner.append("<b>Намерения (intent)</b>")
            habit_inner.append(report_pre_metrics(i_rows))
    top_queries = ul.get("top_queries") if isinstance(ul.get("top_queries"), list) else []
    if top_queries:
        q_rows: List[Tuple[str, int]] = []
        for row in top_queries[:8]:
            if isinstance(row, dict):
                q = str(row.get("query") or "").replace("\n", " ").strip()
                if len(q) > 22:
                    q = q[:21] + "…"
                q_rows.append((q or "—", int(row.get("count") or 0)))
        if q_rows:
            habit_inner.append("")
            habit_inner.append("<b>Формулировки</b>")
            habit_inner.append(report_pre_metrics(q_rows))
    if ui:
        habit_inner.append("")
        habit_inner.append("<b>Автоматические выводы</b>")
        for row in ui[:4]:
            habit_inner.append(f"• {esc(row)}")
    lines.extend(
        [
            "📈 <b>Привычки использования</b> <i>(локальная статистика)</i>",
            "",
            "<blockquote>",
            *habit_inner,
            "</blockquote>",
            "",
            "<blockquote><i>JSON: <code>/admin_xray_json</code> · архив: <code>/admin_diagnostic</code></i></blockquote>",
        ]
    )
    return "\n".join(lines)


def format_usage_digest_html(payload: Dict[str, Any]) -> str:
    """HTML для /admin_usage_digest и автоматического утреннего/вечернего дайджеста."""
    lamp = payload.get("lamp") or ""
    title = "📊 <b>Дайджест активности</b>"
    if lamp:
        title = f"{lamp} {title}"
    slot_raw = payload.get("slot")
    snap = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    head_inner = [
        f"Интервал: <b>{esc(format_usage_digest_slot_caption(slot_raw))}</b>",
        f"Всего событий (накопительно): <b>{esc(snap.get('total_events'))}</b>",
        f"К прошлой сводке: <b>+{esc(payload.get('delta_events'))}</b>",
    ]
    lines = [title, "", "<blockquote>", *head_inner, "</blockquote>", ""]
    trends = payload.get("trends") if isinstance(payload.get("trends"), list) else []
    tr_inner: List[str]
    if trends:
        tr_inner = ["<b>Изменения к прошлому дайджесту</b>", ""] + [esc(t) for t in trends[:12]]
    else:
        tr_inner = ["<i>Сравнение появится после второго дайджеста.</i>"]
    lines.extend(["📉 <b>Динамика</b>", "", "<blockquote>", *tr_inner, "</blockquote>", ""])
    ui = payload.get("insights") if isinstance(payload.get("insights"), list) else []
    if ui:
        ins_lines = ["<b>Короткие выводы</b>", ""] + [f"• {esc(t)}" for t in ui[:5]]
        lines.extend(["💡 <b>Выводы</b>", "", "<blockquote>", *ins_lines, "</blockquote>", ""])
    lines.append(
        "<blockquote><i>Подробнее: <code>/admin_xray</code> · JSON: <code>/admin_usage_digest_json</code></i></blockquote>"
    )
    return "\n".join(lines)


def _format_llm_period_days(val: Any) -> str:
    try:
        x = float(val)
    except (TypeError, ValueError):
        return str(val or "")
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


def format_llm_usage_html(
    agg: Dict[str, Any],
    *,
    session_cost_usd: Optional[float] = None,
    top_rows: Optional[List[Dict[str, Any]]] = None,
    sort_label: str = "date",
) -> str:
    """Сводка по журналу OpenRouter (/admin_llm_usage)."""
    from core.llm_usage_store import unicode_sparkline

    lines = [
        "📉 <b>LLM · OpenRouter</b>",
        "",
        "<blockquote><i>Локальный журнал успешных/неуспешных completion (поле cost из ответа API).</i></blockquote>",
        "",
    ]

    def _int_cell(key: str) -> str:
        try:
            return str(int(agg.get(key, 0) or 0))
        except (TypeError, ValueError):
            v = agg.get(key)
            return "0" if v in (None, "") else str(v)

    sum_rows: List[Tuple[str, str]] = [
        ("Окно, дн.", _format_llm_period_days(agg.get("period_days"))),
        ("Записей в окне", _int_cell("window_records")),
        ("Успешных compl.", _int_cell("completions_ok")),
        ("Ошибок compl.", _int_cell("completions_fail")),
    ]
    lines.extend(["📋 <b>Сводка окна</b>", "", "<blockquote>", report_pre_kv(sum_rows), "</blockquote>", ""])
    lp = agg.get("log_path")
    if lp:
        lines.extend(
            [
                "📁 <b>Путь к журналу</b>",
                "",
                "<blockquote>",
                report_pre_kv([("Журнал", str(lp))], value_max=_REPORT_KV_V_WIDE),
                "</blockquote>",
                "",
            ]
        )
    tt = int(agg.get("total_tokens") or 0)
    avg = float(agg.get("avg_tokens_per_ok") or 0.0)
    pt = int(agg.get("prompt_tokens") or 0)
    ct = int(agg.get("completion_tokens") or 0)
    tok_rows: List[Tuple[str, str]] = [
        ("Токены (успех)", str(tt)),
        ("Средн. на запрос", str(round(avg, 1))),
    ]
    if pt or ct:
        tok_rows.extend([("из них prompt", str(pt)), ("из них completion", str(ct))])
    lines.extend(["🔢 <b>Токены</b>", "", "<blockquote>", report_pre_kv(tok_rows), "</blockquote>", ""])
    cost_sum = float(agg.get("cost_sum") or 0.0)
    paid_n = int(agg.get("paid_completions") or 0)
    free_n = int(agg.get("free_completions") or 0)
    d_cost = float(agg.get("daily_avg_cost") or 0.0)
    m_cost = float(agg.get("monthly_est_cost") or 0.0)
    d_tok = float(agg.get("daily_avg_tokens") or 0.0)
    m_tok = float(agg.get("monthly_est_tokens") or 0.0)
    cost_rows: List[Tuple[str, str]] = [
        ("Сумма за окно, $", str(round(cost_sum, 6))),
        ("Платных compl.", str(paid_n)),
        ("Бесплатных (≈0)", str(free_n)),
        ("$/день (экстр.)", str(round(d_cost, 6))),
        ("~30 дн., $", str(round(m_cost, 4))),
        ("Ток./день", str(int(round(d_tok)))),
        ("Ток./~30 дн.", str(int(round(m_tok)))),
    ]
    if session_cost_usd is not None and session_cost_usd > 0:
        cost_rows.append(("Сессия MONITOR, $", str(round(session_cost_usd, 6))))
    lines.extend(
        ["💵 <b>Оплата (cost в ответе API)</b>", "", "<blockquote>", report_pre_kv(cost_rows), "</blockquote>", ""]
    )

    by_kind = agg.get("by_kind") if isinstance(agg.get("by_kind"), dict) else {}
    if by_kind:
        bk_pre: List[Tuple[str, str]] = []
        for k in sorted(by_kind.keys()):
            bk = by_kind[k] if isinstance(by_kind[k], dict) else {}
            kr = llm_kind_ru(str(k))
            n = int(bk.get("n") or 0)
            toks = int(bk.get("tokens") or 0)
            cst = float(bk.get("cost") or 0.0)
            bk_pre.append((f"{kr} · вызовы", str(n)))
            bk_pre.append((f"{kr} · токены", str(toks)))
            bk_pre.append((f"{kr} · $", str(round(cst, 6))))
        lines.extend(["🏷 <b>По типу (kind)</b>", "", "<blockquote>", report_pre_kv(bk_pre), "</blockquote>", ""])

    st = agg.get("sparkline_tokens") if isinstance(agg.get("sparkline_tokens"), list) else []
    sc = agg.get("sparkline_cost") if isinstance(agg.get("sparkline_cost"), list) else []
    days_lbl = agg.get("sparkline_days") if isinstance(agg.get("sparkline_days"), list) else []
    if st:
        sl_tok = unicode_sparkline([float(x) for x in st])
        sp_rows: List[Tuple[str, str]] = [("График", sl_tok)]
        if days_lbl:
            sp_rows.insert(0, ("Даты", f"{days_lbl[0]} … {days_lbl[-1]}"))
        lines.extend(
            [
                "📈 <b>7 дней: токены/день</b> <i>▁▂▃▄▅▆▇</i>",
                "",
                "<blockquote>",
                report_pre_kv(sp_rows, value_max=_REPORT_KV_V_LONG),
                "</blockquote>",
                "",
            ]
        )
    if sc and any(float(x) > 0 for x in sc):
        sl_c = unicode_sparkline([float(x) for x in sc])
        sc_rows: List[Tuple[str, str]] = [("График", sl_c)]
        if days_lbl:
            sc_rows.insert(0, ("Даты", f"{days_lbl[0]} … {days_lbl[-1]}"))
        lines.extend(
            [
                "📈 <b>7 дней: cost/день ($)</b>",
                "",
                "<blockquote>",
                report_pre_kv(sc_rows, value_max=_REPORT_KV_V_LONG),
                "</blockquote>",
                "",
            ]
        )

    if top_rows:
        from core.llm_usage_store import format_row_ts_for_report

        sort_ru = {"date": "по дате", "cost": "по стоимости", "tokens": "по токенам"}.get(sort_label, sort_label)
        rec_lines: List[str] = [f"<b>Последние записи</b> <i>(сортировка: {esc(sort_ru)})</i>", ""]
        for r in top_rows:
            ts = format_row_ts_for_report(r)
            ok = "✓" if r.get("ok") else "✗"
            kind = str(r.get("kind") or "?")
            tag = str(r.get("tag") or "")
            model = str(r.get("requested_model") or "")[:40]
            tot = r.get("total_tokens")
            cst = r.get("cost")
            cpt = r.get("cached_prompt_tokens")
            cwt = r.get("cache_write_tokens")
            tail = f" tag={tag}" if tag else ""
            cache_tail = ""
            try:
                if cpt is not None and int(cpt) > 0:
                    cache_tail += f" cached_tok={cpt}"
            except (TypeError, ValueError):
                pass
            try:
                if cwt is not None and int(cwt) > 0:
                    cache_tail += f" cache_wr={cwt}"
            except (TypeError, ValueError):
                pass
            rec_lines.append(
                f"<code>{esc(ts)}</code> {ok} <code>{esc(kind)}</code>{esc(tail)} · {esc(model)} · tok={esc(tot)} cost={esc(cst)}{esc(cache_tail)}"
            )
        lines.extend(["📎 <b>Хвост журнала</b>", "", "<blockquote>", *rec_lines, "</blockquote>", ""])

    lines.append(
        "<blockquote><i>Параметры: <code>days=30</code> <code>sort=date|cost|tokens</code> <code>limit=25</code> · JSON: <code>/admin_llm_usage_json</code></i></blockquote>"
    )
    lines.append("")
    lines.append(REPORT_GLOSSARY_FOOTER_HTML)
    return "\n".join(lines)


def format_efficiency_html(payload: Dict[str, Any]) -> str:
    lines = [
        "⚡ <b>Эффективность системы</b>",
        "",
        "<blockquote><i>Экономия токенов, успех плагинов и качество маршрутизации.</i></blockquote>",
        "",
    ]
    ts = payload.get("token_saving") if isinstance(payload.get("token_saving"), dict) else {}
    pl = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else {}
    ph = pl.get("health") if isinstance(pl.get("health"), dict) else {}
    pr = payload.get("planner") if isinstance(payload.get("planner"), dict) else {}
    llm = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}

    llm_rows = [
        ("Токены (окно)", str(llm.get("total_tokens", 0))),
        ("Ток./день", str(int(float(llm.get("daily_avg_tokens") or 0)))),
        ("Cost $ (окно)", str(round(float(llm.get("cost_sum") or 0.0), 6))),
        ("~30 дн. $", str(round(float(llm.get("monthly_est_cost") or 0.0), 4))),
    ]
    lines.extend(
        ["💵 <b>Токены и стоимость</b>", "", "<blockquote>", report_pre_kv(llm_rows), "</blockquote>", ""]
    )
    ts_rows = [
        ("Saved est", str(ts.get("estimated_saved_tokens_total", 0))),
        ("Baseline est", str(ts.get("estimated_baseline_tokens_total", 0))),
        ("Эффективность %", str(ts.get("efficiency_percent", 0.0))),
    ]
    lines.extend(
        ["♻️ <b>Экономия токенов</b>", "", "<blockquote>", report_pre_kv(ts_rows), "</blockquote>", ""]
    )
    pl_rows = [
        ("Выполнений", str(pl.get("exec_total", 0))),
        ("Успешно", str(pl.get("exec_ok", 0))),
        ("Ошибок", str(pl.get("exec_fail", 0))),
        ("Успех %", str(pl.get("exec_success_percent", 0.0))),
        ("Healthy", str(ph.get("healthy", 0))),
        ("Failed", str(ph.get("failed", 0))),
        ("Disabled", str(ph.get("disabled", 0))),
    ]
    lines.extend(["🧩 <b>Плагины</b>", "", "<blockquote>", report_pre_kv(pl_rows), "</blockquote>", ""])
    pr_rows = [
        ("Решений", str(pr.get("decisions_total", 0))),
        ("Fallback", str(pr.get("fallback_total", 0))),
        ("Route success %", str(pr.get("route_success_percent", 0.0))),
    ]
    lines.extend(
        ["🧭 <b>Маршрутизация</b>", "", "<blockquote>", report_pre_kv(pr_rows), "</blockquote>", ""]
    )
    lines.append("<blockquote><i>JSON: <code>/admin_efficiency_json</code></i></blockquote>")
    return "\n".join(lines)


def format_errors_compact_html(err: Dict[str, Any]) -> str:
    if not err:
        return "\n".join(
            [
                "📌 <b>Журнал ошибок</b>",
                "",
                "<blockquote><i>Нет сводки.</i></blockquote>",
            ]
        )
    total = err.get("total")
    tall = err.get("total_all")
    inner: List[str] = [f"Учитывается в устойчивости: <b>{esc(total)}</b>"]
    if tall is not None:
        inner.append(f"Всего строк в выборке: <b>{esc(tall)}</b>")
    byc = err.get("by_component") or {}
    if isinstance(byc, dict) and byc:
        top = sorted(byc.items(), key=lambda x: -int(x[1]))[:8]
        rows_lc = [(runtime_component_label_ru(str(c)), int(cnt)) for c, cnt in top]
        inner.extend(["", "<b>Подсистемы (топ)</b>", report_pre_metrics(rows_lc)])
    return "\n".join(
        ["📌 <b>Журнал ошибок</b>", "", "<blockquote>", *inner, "</blockquote>", ""]
    )


def format_health_short_html(h: Dict[str, Any]) -> str:
    out: List[str] = ["💚 <b>Состояние (кратко)</b>", ""]
    kv: List[Tuple[str, str]] = []
    for k in ("overall_status", "mode", "planner_engine", "active_traces"):
        if k in h:
            label = _HEALTH_LABEL_RU.get(k, k)
            val = h[k]
            if k == "overall_status":
                val = ru_status(val)
            kv.append((str(label), str(val)))
    if kv:
        out.extend(["<blockquote>", report_pre_kv(kv), "</blockquote>", ""])
    sec = h.get("security")
    if isinstance(sec, dict) and sec:
        sec_rows = [(_HEALTH_LABEL_RU.get(k, k), str(v)) for k, v in list(sec.items())[:10]]
        out.extend(
            [
                "🔒 <b>Безопасность</b>",
                "",
                "<blockquote>",
                report_pre_kv(sec_rows),
                "</blockquote>",
                "",
            ]
        )
    ip = h.get("input_pipeline")
    if isinstance(ip, dict) and ip:
        ip_rows = [("Пропуски без actor", str(ip.get("skipped_no_actor_total", 0)))]
        out.extend(
            [
                "📥 <b>Входной конвейер</b>",
                "",
                "<blockquote>",
                report_pre_kv(ip_rows),
                "</blockquote>",
                "",
            ]
        )
    return "\n".join(out).strip()


def format_purge_result_html(d: Dict[str, Any]) -> str:
    ok = d.get("ok")
    mode = d.get("mode") or "?"
    mode_ru = "полная очистка" if mode == "full" else "по сроку хранения"
    err = d.get("error")
    pr_rows: List[Tuple[str, str]] = [
        ("Режим", mode_ru),
        ("Успех", str(ok)),
        ("Удалено", str(d.get("removed", ""))),
        ("Осталось", str(d.get("kept", ""))),
    ]
    inner = [report_pre_kv(pr_rows)]
    if err:
        inner.append(f"<i>Ошибка: {esc(err)}</i>")
    if d.get("safe_mode_cleared"):
        inner.append("<i>Safe mode снят (полная очистка журнала).</i>")
    return "\n".join(
        ["🧹 <b>Очистка журнала ошибок</b>", "", "<blockquote>", *inner, "</blockquote>"]
    )


def format_admin_user_facts_html(data: Dict[str, Any]) -> str:
    if data.get("error"):
        return f"⚠️ {esc(data.get('error'))}"
    uid = esc(data.get("user_id", ""))
    facts = data.get("facts") or {}
    parts = [f"👤 <b>Факты пользователя</b> <code>{uid}</code>", ""]
    parts.append("<blockquote>" + _kv_block("Факты", facts if isinstance(facts, dict) else {}) + "</blockquote>")
    meta = data.get("facts_meta") or {}
    if isinstance(meta, dict) and meta:
        parts.extend(["", "<blockquote>" + _kv_block("Мета", meta) + "</blockquote>"])
    return "\n".join(parts)


def _disk_rows_html(disk: Any, *, limit: int = 4) -> List[str]:
    lines: List[str] = []
    if not isinstance(disk, list):
        return lines
    for d in disk[:limit]:
        if not isinstance(d, dict):
            continue
        label = esc(d.get("label") or d.get("path") or "?")
        pct = d.get("used_percent")
        free = d.get("free_gb")
        lines.append(f"• {label}: занято <b>{esc(pct)}%</b>, свободно <b>{esc(free)}</b> GB")
    if len(disk) > limit:
        lines.append(f"<i>… ещё томов: {len(disk) - limit}</i>")
    return lines


def format_unified_health_html(
    snap: Dict[str, Any],
    *,
    max_backup_rows: int = 8,
    footer_line: Optional[str] = None,
) -> str:
    """Компактный HTML для build_unified_health_snapshot (без многокилобайтного JSON)."""
    ts_line = format_health_snapshot_caption(snap.get("ts"))
    parts: List[str] = [
        f"🏥 <b>Сводка состояния</b> · {esc(ts_line)}",
        "",
    ]
    integrity = snap.get("integrity") if isinstance(snap.get("integrity"), dict) else {}
    iok = integrity.get("ok")
    integ_lines: List[str] = [
        f"Статус: {'✅' if iok else '⚠️'} <b>{esc(ru_bool(iok))}</b>",
    ]
    issues = integrity.get("issues") or []
    if isinstance(issues, list) and issues:
        for iss in issues[:12]:
            iss_s = str(iss)
            human = _INTEGRITY_ISSUE_RU.get(iss_s, iss_s)
            integ_lines.append(f"• {esc(human)}")
        if len(issues) > 12:
            integ_lines.append(f"<i>… ещё {len(issues) - 12}</i>")
    parts.extend(["🔍 <b>Целостность данных</b>", "", "<blockquote>", *integ_lines, "</blockquote>", ""])

    ext = snap.get("external_services") if isinstance(snap.get("external_services"), dict) else {}
    ext_fail = ext.get("failures") if isinstance(ext.get("failures"), list) else []
    if ext_fail:
        ext_lines: List[str] = ["<b>Сбои (последняя проверка)</b>", ""]
        for row in ext_fail[:10]:
            svc = row.get("service") or "?"
            role = row.get("role")
            label = f"{svc} ({role})" if role else str(svc)
            msg = row.get("user_message") or row.get("error_code") or "—"
            src = row.get("source") or ""
            ext_lines.append(f"• <b>{esc(label)}</b> [{esc(src)}]: {esc(msg)}")
        ext_lines.append(
            "<i>Полная проверка: <code>/admin_connectivity</code> · JSON: <code>/admin_connectivity_json</code></i>"
        )
        parts.extend(["🌐 <b>Внешние API</b>", "", "<blockquote>", *ext_lines, "</blockquote>", ""])
    elif ext.get("last_full_at"):
        lf_ok = ext.get("last_full_ok")
        lf_lbl = "в порядке" if lf_ok else "есть сбои"
        lf_caption = format_health_snapshot_caption(ext.get("last_full_at"))
        ext_ok_lines = [
            f"Последняя проверка: <b>{esc(lf_lbl)}</b> · {esc(lf_caption)}",
        ]
        if ext.get("last_full_summary"):
            ext_ok_lines.append(f"<i>{esc(ext.get('last_full_summary'))}</i>")
        ext_ok_lines.append("<i>Обновить: <code>/admin_connectivity</code></i>")
        parts.extend(["🌐 <b>Внешние сервисы</b>", "", "<blockquote>", *ext_ok_lines, "</blockquote>", ""])
    elif ext.get("by_service"):
        parts.extend(
            [
                "🌐 <b>Внешние API</b>",
                "",
                "<blockquote><i>Точечные проверки при старте; полная сводка: <code>/admin_connectivity</code></i></blockquote>",
                "",
            ]
        )
    else:
        parts.extend(
            [
                "🌐 <b>Внешние API</b>",
                "",
                "<blockquote><i>Ещё не проверялись. Запусти <code>/admin_connectivity</code> (Telegram, OpenRouter, Mem0).</i></blockquote>",
                "",
            ]
        )

    ev = snap.get("evaluate") if isinstance(snap.get("evaluate"), dict) else {}
    if ev and "error" not in ev:
        mod_st = ev.get("modules_overall") or ev.get("overall_status")
        eth = ev.get("error_thresholds") if isinstance(ev.get("error_thresholds"), dict) else {}
        deg_at = eth.get("degraded_at")
        crit_at = eth.get("critical_at")
        ev_u_rows: List[Tuple[str, str]] = [
            ("Модули", f"{ru_status(mod_st)} ({mod_st})"),
            ("Ошибок в журнале", str(ev.get("error_total", ""))),
            ("Сбойных модулей", str(ev.get("failed_modules", ""))),
        ]
        if deg_at is not None and crit_at is not None:
            ev_u_rows.append(("Порог деград.", str(deg_at)))
            ev_u_rows.append(("Порог крит.", str(crit_at)))
        ev_u_rows.extend(
            [
                ("KPI ок", ru_bool(ev.get("kpi_ok"))),
                ("Деградация", ru_bool(ev.get("degraded"))),
                ("Критично", ru_bool(ev.get("critical"))),
            ]
        )
        hr = ev.get("host_resources") if isinstance(ev.get("host_resources"), dict) else {}
        if hr.get("available"):
            mem = hr.get("memory") if isinstance(hr.get("memory"), dict) else {}
            pr = hr.get("pressure") if isinstance(hr.get("pressure"), dict) else {}
            ev_u_rows.append(("CPU %", str(hr.get("cpu_percent"))))
            ev_u_rows.append(("RAM %", str(mem.get("percent"))))
            ev_u_rows.append(
                ("RAM MB", f"{mem.get('used_mb')}/{mem.get('total_mb')}")
            )
            if pr.get("level"):
                ev_u_rows.append(("Нагрузка", ru_status(pr.get("level"))))
        ev_extra: List[str] = [report_pre_kv(ev_u_rows, value_max=36)]
        if (ev.get("degraded") or ev.get("critical")) and str(mod_st).lower() in ("healthy", "ok", "full"):
            ev_extra.append(
                "<i>«Всё в норме» по модулям не отменяет предупреждение по журналу: там могут быть старые записи. "
                "Очистка: <code>/admin_purge_logs all</code> при необходимости.</i>"
            )
        parts.extend(["📊 <b>Оценка устойчивости</b>", "", "<blockquote>", *ev_extra, "</blockquote>", ""])
    elif isinstance(ev, dict) and ev.get("error"):
        parts.extend(
            [
                "📊 <b>Оценка устойчивости</b>",
                "",
                "<blockquote>",
                f"<i>{esc(ev.get('error'))}</i>",
                "</blockquote>",
                "",
            ]
        )

    deg = snap.get("degradation_summary") if isinstance(snap.get("degradation_summary"), dict) else {}
    if deg:
        _deg_ru = {
            "degraded": "Деградация",
            "critical": "Критично",
            "kpi_ok": "KPI в норме",
            "error_total": "Ошибок в журнале",
            "resource_pressure": "Ресурсы",
        }
        d_rows: List[Tuple[str, str]] = []
        for k in ("degraded", "critical", "kpi_ok", "error_total", "resource_pressure"):
            if k in deg:
                dv = deg[k]
                if k in ("degraded", "critical", "kpi_ok"):
                    dv = ru_bool(dv)
                d_rows.append((_deg_ru.get(k, k), str(dv)))
        if d_rows:
            parts.extend(
                [
                    "⚠️ <b>Деградация (сводка)</b>",
                    "",
                    "<blockquote>",
                    report_pre_kv(d_rows),
                    "</blockquote>",
                    "",
                ]
            )

    res = snap.get("resilience") if isinstance(snap.get("resilience"), dict) else {}
    if res:
        res_lines: List[str] = []
        sm = res.get("safe_mode")
        if isinstance(sm, dict):
            if sm:
                res_lines.append(report_pre_kv([("Безоп. режим", ru_bool(sm.get("active")))]))
                if sm.get("reason"):
                    res_lines.append(f"<i>Причина: {esc(sm.get('reason'))}</i>")
            else:
                res_lines.append("<i>Безопасный режим: не активен / нет данных</i>")
        aw = res.get("allowlist")
        if isinstance(aw, list) and aw:
            sm_active = isinstance(sm, dict) and bool(sm.get("active"))
            label = (
                "Разрешённые модули (safe mode активен)"
                if sm_active
                else "Запасной allowlist при safe mode (сейчас выключен)"
            )
            shown = ", ".join(str(x) for x in aw[:12])
            res_lines.append(f"• {label}: <code>{esc(shown)}</code>")
            if len(aw) > 12:
                res_lines.append(f"<i>… +{len(aw) - 12}</i>")
        parts.extend(["🛡 <b>Устойчивость (снимок)</b>", "", "<blockquote>", *res_lines, "</blockquote>", ""])

    hr_top = snap.get("host_resources") if isinstance(snap.get("host_resources"), dict) else {}
    if hr_top and hr_top.get("available") and not (ev and ev.get("host_resources")):
        mem = hr_top.get("memory") if isinstance(hr_top.get("memory"), dict) else {}
        host_inner: List[str] = [
            report_pre_kv(
                [
                    ("CPU %", str(hr_top.get("cpu_percent"))),
                    ("RAM %", str(mem.get("percent"))),
                ]
            ),
            *_disk_rows_html(hr_top.get("disk")),
        ]
        parts.extend(["🖥 <b>Хост (снимок)</b>", "", "<blockquote>", *host_inner, "</blockquote>", ""])

    aut = snap.get("autonomy") if isinstance(snap.get("autonomy"), dict) else {}
    backups = snap.get("backups_recent")
    if isinstance(backups, list) and backups:
        bu_lines: List[str] = [
            f"<i>До {max_backup_rows} записей, новые сверху.</i>",
            "",
        ]
        ret = aut.get("retention")
        be = aut.get("backup_every_n_maintenance")
        if ret is not None or be is not None:
            bu_lines.append(
                "<i>Автономия: ротация "
                f"<code>{esc(ret)}</code> · снимок каждые "
                f"<code>{esc(be)}</code> циклов обслуживания</i>"
            )
            bu_lines.append("")
        for b in backups[:max_backup_rows]:
            if not isinstance(b, dict):
                continue
            bid = esc(b.get("id") or "?")
            lab = esc(b.get("label") or "")
            _ca = b.get("created_at")
            cat = esc(format_operator_datetime_from_iso(_ca) or str(_ca or "")[:19])
            bu_lines.append(f"• <code>{bid}</code> · {lab} · <i>{cat}</i>")
        if len(backups) > max_backup_rows:
            bu_lines.append(f"<i>… ещё в каталоге (вся выборка снапшота): {len(backups)}</i>")
        parts.extend(["💾 <b>Бэкапы критичных данных</b>", "", "<blockquote>", *bu_lines, "</blockquote>", ""])
    else:
        lb = aut.get("last_bundles")
        if isinstance(lb, list) and lb:
            lb_lines: List[str] = []
            for b in lb[:max_backup_rows]:
                if isinstance(b, dict):
                    lb_lines.append(f"• <code>{esc(b.get('id'))}</code> · {esc(b.get('label'))}")
            parts.extend(
                ["💾 <b>Бэкапы (автономия)</b>", "", "<blockquote>", *lb_lines, "</blockquote>", ""]
            )

    parts.append(
        "<blockquote>"
        + (
            footer_line
            if footer_line is not None
            else "<i>Полный JSON: <code>/admin_health_json</code></i>"
        )
        + "</blockquote>"
    )
    return "\n".join(parts)


def format_resilience_detail_html(snapshot: Dict[str, Any], evaluate: Dict[str, Any]) -> str:
    out: List[str] = ["🛡️ <b>Устойчивость — подробно</b>", ""]
    snap_lines: List[str]
    if not snapshot:
        snap_lines = ["<i>нет</i>"]
    else:
        snap_lines = []
        for k, v in list(snapshot.items())[:20]:
            if isinstance(v, (dict, list)):
                snap_lines.append(f"• <code>{esc(k)}</code>: <i>{esc(str(v)[:200])}</i>")
            else:
                snap_lines.append(f"• <code>{esc(k)}</code>: {esc(v)}")
        if len(snapshot) > 20:
            snap_lines.append(f"<i>… ещё ключей: {len(snapshot) - 20}</i>")
    out.extend(["📦 <b>Снимок</b>", "", "<blockquote>", *snap_lines, "</blockquote>", ""])

    eval_inner: List[str]
    if not evaluate:
        eval_inner = ["<i>нет</i>"]
    elif evaluate.get("error"):
        eval_inner = [f"<i>{esc(evaluate.get('error'))}</i>"]
    else:
        _ev_ru = {
            "kpi_ok": "KPI в норме",
            "error_total": "Ошибок в журнале",
            "failed_modules": "Сбойных модулей",
            "overall_status": "Общий статус",
            "degraded": "Деградация",
            "critical": "Критично",
        }
        ev_rows: List[Tuple[str, str]] = []
        for k in ("kpi_ok", "error_total", "failed_modules", "overall_status", "degraded", "critical"):
            if k in evaluate:
                val = evaluate[k]
                if k == "overall_status":
                    val = ru_status(val)
                elif k in ("kpi_ok", "degraded", "critical"):
                    val = ru_bool(val)
                ev_rows.append((_ev_ru.get(k, k), str(val)))
        eval_inner = []
        if ev_rows:
            eval_inner.append(report_pre_kv(ev_rows))
        hr = evaluate.get("host_resources") if isinstance(evaluate.get("host_resources"), dict) else {}
        if hr.get("available"):
            mem = hr.get("memory") if isinstance(hr.get("memory"), dict) else {}
            eval_inner.append(
                report_pre_kv(
                    [
                        ("CPU %", str(hr.get("cpu_percent"))),
                        ("RAM %", str(mem.get("percent"))),
                    ]
                )
            )
    out.extend(["📊 <b>Оценка</b>", "", "<blockquote>", *eval_inner, "</blockquote>", ""])
    out.append("<blockquote><i>JSON: <code>/admin_resilience_json</code></i></blockquote>")
    return "\n".join(out)


def format_operator_panel_html(snap: Dict[str, Any]) -> str:
    lines = ["🎛 <b>Консоль оператора</b>", ""]
    h = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    lines.append(format_health_short_html(h))
    lines.append("")
    cv = snap.get("config_validation") if isinstance(snap.get("config_validation"), dict) else {}
    if cv:
        cv_in = [f"ok=<b>{esc(cv.get('ok'))}</b>"]
        if cv.get("errors"):
            cv_in.append(f"ошибки: <pre>{esc(cv.get('errors'))}</pre>")
        if cv.get("warnings"):
            cv_in.append(f"предупреждения: <pre>{esc(cv.get('warnings'))}</pre>")
        lines.extend(["⚙️ <b>Конфиг</b>", "", "<blockquote>", *cv_in, "</blockquote>", ""])
    voice = snap.get("voice_stt") if isinstance(snap.get("voice_stt"), dict) else {}
    if voice:
        v_rows = [(str(k), str(v)) for k, v in list(voice.items())[:14]]
        lines.extend(
            [
                "🎙 <b>Голос (STT)</b>",
                "",
                "<blockquote>",
                report_pre_kv(v_rows),
                "</blockquote>",
                "",
            ]
        )
    mem0 = snap.get("mem0") if isinstance(snap.get("mem0"), dict) else {}
    if mem0:
        loc = " · локальный API" if mem0.get("mem0_local_standalone") else ""
        m0_lines = [
            f"• HTTP Mem0: <b>{esc(mem0.get('mem0_http_enabled'))}</b>"
            f"{loc} · облачный ключ: <b>{esc(mem0.get('mem0_cloud_enabled'))}</b>"
            f" · scheme: <code>{esc(mem0.get('auth_scheme'))}</code>",
            f"• primary URL: <code>{esc(mem0.get('primary_api_url'))}</code>",
        ]
        pk = mem0.get("primary_key") if isinstance(mem0.get("primary_key"), dict) else {}
        if pk.get("configured"):
            m0_lines.append(f"• primary ключ: len=<b>{esc(pk.get('key_len'))}</b>")
        mk = mem0.get("mirror_key") if isinstance(mem0.get("mirror_key"), dict) else {}
        if mk.get("configured"):
            m0_lines.append(f"• mirror URL: <code>{esc(mem0.get('mirror_api_url'))}</code>")
            m0_lines.append(f"• mirror ключ: len=<b>{esc(mk.get('key_len'))}</b>")
        m0_lines.append(f"• MEM0_MIRROR_WRITE: <b>{esc(mem0.get('mirror_write'))}</b>")
        for hint in (mem0.get("hints") or [])[:5]:
            if hint:
                m0_lines.append(f"• <i>{esc(hint)}</i>")
        lines.extend(
            ["☁️ <b>Mem0</b> <i>(без полного ключа)</i>", "", "<blockquote>", *m0_lines, "</blockquote>", ""]
        )
    notes = snap.get("operator_notes") if isinstance(snap.get("operator_notes"), dict) else {}
    if notes:
        n_lines = [f"• <code>{esc(k)}</code>: <i>{esc(v)}</i>" for k, v in list(notes.items())[:6]]
        lines.extend(["📝 <b>Заметки</b>", "", "<blockquote>", *n_lines, "</blockquote>", ""])
    lines.append("<blockquote><i>JSON: <code>/admin_operator_json</code></i></blockquote>")
    return "\n".join(lines)


def format_backup_list_html(rows: Any) -> str:
    head = ["💾 <b>Список бэкапов</b>", ""]
    if not isinstance(rows, list) or not rows:
        head.extend(
            [
                "<blockquote><i>пусто</i></blockquote>",
                "",
                "<blockquote><i>JSON: <code>/admin_backup_list_json</code></i></blockquote>",
            ]
        )
        return "\n".join(head)
    inner: List[str] = [f"Записей: <b>{len(rows)}</b>", ""]
    for r in rows[:25]:
        if isinstance(r, dict):
            rid = esc(r.get("id") or r.get("path") or "?")
            lab = esc(r.get("label") or "")
            _cr = r.get("created_at")
            cat = esc(format_operator_datetime_from_iso(_cr) or str(_cr or "")[:22])
            iok = r.get("integrity_ok")
            inner.append(f"• <code>{rid}</code> · {lab} · <i>{cat}</i> · ok=<b>{esc(iok)}</b>")
        else:
            inner.append(f"• <code>{esc(r)}</code>")
    if len(rows) > 25:
        inner.append(f"<i>… +{len(rows) - 25}</i>")
    head.extend(
        [
            "<blockquote>",
            *inner,
            "</blockquote>",
            "",
            "<blockquote><i>JSON: <code>/admin_backup_list_json</code></i></blockquote>",
        ]
    )
    return "\n".join(head)


def format_action_result_html(title: str, payload: Dict[str, Any]) -> str:
    inner: List[str] = []
    for k, v in sorted(payload.keys(), key=str):
        if isinstance(v, (dict, list)):
            inner.append(f"• <code>{esc(k)}</code>:")
            raw = str(v)
            if len(raw) > 800:
                raw = raw[:797] + "…"
            inner.append(f"<pre>{esc(raw)}</pre>")
        else:
            inner.append(f"• <code>{esc(k)}</code>: {esc(v)}")
    return "\n".join(
        [f"✅ <b>{esc(title)}</b>", "", "<blockquote>", *inner, "</blockquote>"]
    )


def format_auto_suggestions_html(rows: List[str]) -> str:
    head = ["🤖 <b>Автономия — подсказки</b>", ""]
    if not rows:
        head.append("<blockquote><i>пусто</i></blockquote>")
        return "\n".join(head)
    inner = [f"• {esc(r)}" for r in rows[:40]]
    if len(rows) > 40:
        inner.append(f"<i>… +{len(rows) - 40}</i>")
    head.extend(["<blockquote>", *inner, "</blockquote>"])
    return "\n".join(head)


def format_auto_review_html(payload: Dict[str, Any]) -> str:
    lines = ["🤖 <b>Автообзор</b>", ""]
    diag = payload.get("diagnostics")
    if isinstance(diag, dict) and diag:
        d_lines: List[str] = []
        for k, v in list(diag.items())[:24]:
            if isinstance(v, (dict, list)):
                d_lines.append(f"• <code>{esc(k)}</code>: <i>{esc(str(v)[:120])}</i>")
            else:
                d_lines.append(f"• <code>{esc(k)}</code>: {esc(v)}")
        if len(diag) > 24:
            d_lines.append(f"<i>… +{len(diag) - 24} полей</i>")
        lines.extend(["🔧 <b>Диагностика</b>", "", "<blockquote>", *d_lines, "</blockquote>", ""])
    hints = payload.get("optimize_hints")
    if hints is not None:
        lines.extend(
            [
                "💡 <b>Подсказки по оптимизации</b>",
                "",
                "<blockquote>",
                f"<pre>{esc(str(hints)[:3000])}</pre>",
                "</blockquote>",
                "",
            ]
        )
    sug = payload.get("suggestions")
    if isinstance(sug, list) and sug:
        s_lines = [f"• {esc(s)}" for s in sug[:30]]
        lines.extend(["📌 <b>Рекомендации</b>", "", "<blockquote>", *s_lines, "</blockquote>"])
    return "\n".join(lines)


def format_auto_idea_html(data: Any) -> str:
    return "\n".join(
        ["💡 <b>Идея</b>", "", "<blockquote>", code_block_html(str(data)[:3800]), "</blockquote>"]
    )


def format_admin_logs_header_html(
    n: int,
    *,
    log_path: str = "",
    file_mtime_utc: str = "",
    file_exists: bool = True,
    newest_ts: str = "",
    component_filter: str = "",
) -> str:
    title = f"📜 <b>runtime_errors.jsonl</b> — последние <code>{esc(n)}</code>"
    body: List[str] = []
    if component_filter:
        body.append(f"Фильтр: <code>{esc(component_filter)}</code> (скан хвоста журнала)")
    if log_path:
        body.append(f"Файл: <code>{esc(log_path)}</code>")
    if not file_exists:
        body.append("<i>Файл журнала ещё не создан — после первой записи появится путь и время.</i>")
    elif file_mtime_utc:
        _mt = format_operator_datetime_from_iso(file_mtime_utc) or esc(file_mtime_utc)
        body.append(f"Файл на диске (время изменения): <code>{_mt}</code>")
    if newest_ts:
        _nt = format_operator_datetime_from_iso(newest_ts) or esc(newest_ts)
        body.append(f"Самая свежая в этом списке: <code>{_nt}</code>")
    if file_exists and (file_mtime_utc or newest_ts):
        hint = (
            "<i>mtime — когда файл менялся на диске; «самая свежая в списке» — поле <code>ts</code> "
            "только среди показанных строк."
        )
        if component_filter:
            hint += " При фильтре компонента в журнал могли писать другие подсистемы — mtime тогда часто новее."
        hint += "</i>"
        body.append(hint)
    body.append(
        "Порядок: <b>новые сверху</b> · в строках: <code>[ошибка]</code> и русское имя подсистемы · "
        "<code>/admin_logs 50 voice</code> — только компонент <code>voice</code>"
    )
    return title + "\n\n" + "<blockquote>" + "\n".join(body) + "</blockquote>"


def format_admin_logs_html(tail_text: str, n: int) -> str:
    """Заголовок + блок кода (<pre>), как у JSON-ответов."""
    return format_admin_logs_header_html(n) + "\n\n" + code_block_html(tail_text)


def format_development_passport_block_html(block: Dict[str, Any]) -> str:
    parts = ["📜 <b>Паспорт разработки</b>", ""]
    for key in ("mission", "evolution_vectors", "priorities", "kpi_targets", "stop_rules"):
        if key not in block:
            continue
        val = block[key]
        raw = str(val)
        if len(raw) > 1200:
            raw = raw[:1197] + "…"
        label = _PASSPORT_FIELD_LABELS.get(key, key)
        parts.extend(
            [
                f"📌 <b>{esc(label)}</b>",
                "",
                "<blockquote>",
                f"<pre>{esc(raw)}</pre>",
                "</blockquote>",
                "",
            ]
        )
    src = block.get("source")
    if src is not None:
        parts.extend(["<blockquote>", f"<i>источник: {esc(src)}</i>", "</blockquote>", ""])
    extra_keys = [k for k in block.keys() if k not in {"mission", "evolution_vectors", "priorities", "kpi_targets", "stop_rules", "source"}]
    if extra_keys:
        parts.extend(
            [
                "<blockquote>",
                f"<i>ещё ключи:</i> <code>{esc(', '.join(str(k) for k in extra_keys[:20]))}</code>",
                "</blockquote>",
                "",
            ]
        )
    parts.append("<blockquote><i>JSON: <code>/admin_passport_json</code></i></blockquote>")
    return "\n".join(parts)


def split_html_message(text: str, limit: int = 4000) -> List[str]:
    """Разбивает HTML без обрезки посередине тега (грубо — по абзацам)."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    buf = ""
    for para in text.split("\n\n"):
        block = para if not buf else "\n\n" + para
        if len(buf) + len(block) > limit and buf:
            chunks.append(buf)
            buf = para
        else:
            buf += block
    if buf:
        chunks.append(buf)
    if len(chunks) <= 1 and len(text) > limit:
        from core.telegram_util import chunk_text

        chunks = chunk_text(text, limit)

    # Склеиваем куски с незакрытым <blockquote> (разбиение по \n\n рвёт тег → Telegram 400).
    def _open_bq(s: str) -> int:
        return len(re.findall(r"(?i)<blockquote\b", s))

    def _close_bq(s: str) -> int:
        return len(re.findall(r"(?i)</blockquote>", s))

    if len(chunks) > 1:
        merged: List[str] = []
        acc = ""
        for ch in chunks:
            acc = ch if not acc else acc + "\n\n" + ch
            if _open_bq(acc) <= _close_bq(acc):
                merged.append(acc)
                acc = ""
        if acc:
            merged.append(acc)
        chunks = merged if merged else chunks

    def _open_pre(s: str) -> int:
        return len(re.findall(r"(?i)<pre\b", s))

    def _close_pre(s: str) -> int:
        return len(re.findall(r"(?i)</pre>", s))

    if len(chunks) > 1:
        merged_pre: List[str] = []
        accp = ""
        for ch in chunks:
            accp = ch if not accp else accp + "\n\n" + ch
            if _open_pre(accp) <= _close_pre(accp):
                merged_pre.append(accp)
                accp = ""
        if accp:
            merged_pre.append(accp)
        chunks = merged_pre if merged_pre else chunks

    return chunks
