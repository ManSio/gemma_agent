"""
Plain-text ответы для плагинов: в Telegram для module Output обычно нет parse_mode=HTML,
поэтому здесь без тегов — только строки, списки и эмодзи.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping
from core.path_redaction import redact_public_path

_PSYCH_SENTIMENT_RU = {
    "neutral": "нейтральная",
    "stressed": "напряжённая (маркеры усталости/стресса)",
}

_TWIN_SECTION_RU = {
    "location": "Локация",
    "learning_profile": "Учебный профиль",
    "interaction_history": "История взаимодействий",
    "interests": "Интересы",
    "created_at": "Создан",
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

_PSYCH_PLUGIN_ANALYSIS_RU = {
    "user_id": "Пользователь",
    "timestamp": "Время",
    "message": "Фрагмент сообщения",
    "emotional_tone": "Эмоциональный тон",
    "confidence_level": "Уверенность",
    "anxiety_level": "Тревожность",
    "communication_style": "Стиль общения",
    "learning_style": "Стиль обучения",
    "engagement_level": "Вовлечённость",
    "fatigue_level": "Усталость",
    "social_behavior": "Социальное поведение",
    "recommendation": "Рекомендация",
}


def _plain_leaf(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "да" if val else "нет"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, str):
        vv = redact_public_path(val)
        return vv if vv else "—"
    if isinstance(val, list):
        if not val:
            return "пусто"
        if all(not isinstance(x, (dict, list)) for x in val):
            return ", ".join(redact_public_path(x) for x in val[:16]) + ("…" if len(val) > 16 else "")
        return f"{len(val)} записей"
    if isinstance(val, dict):
        parts = []
        for sk, ssv in list(val.items())[:5]:
            parts.append(f"{sk}: {redact_public_path(json.dumps(ssv, ensure_ascii=False)[:60])}")
        tail = "…" if len(val) > 5 else ""
        return "; ".join(parts) + tail
    return redact_public_path(val)


def _twin_nested_plain(d: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for sk, sv in sorted(d.items(), key=lambda t: str(t[0])):
        label = _TWIN_FIELD_RU.get(sk, sk.replace("_", " "))
        if isinstance(sv, dict):
            out.append(f"• {label}")
            for ssk, ssv in sorted(sv.items(), key=lambda t: str(t[0])):
                sl = _TWIN_FIELD_RU.get(ssk, ssk.replace("_", " "))
                out.append(f"  ◦ {sl}: {_plain_leaf(ssv)}")
        else:
            out.append(f"• {label}: {_plain_leaf(sv)}")
    return out


def _twin_history_plain(items: Any) -> List[str]:
    if not isinstance(items, list) or not items:
        return ["• Записей: нет"]
    last = items[-1]
    last_ts = ""
    if isinstance(last, dict):
        last_ts = str(last.get("timestamp") or "")
    tail = f" (последняя: {last_ts})" if last_ts else ""
    return [f"• Событий в истории: {len(items)}{tail}"]


def format_twin_plain(twin: Dict[str, Any]) -> str:
    if not twin:
        return "🪞 Цифровой двойник\n\nПока пусто."
    lines: List[str] = ["🪞 Цифровой двойник", ""]
    uid = twin.get("user_id")
    if uid is not None and str(uid):
        lines.append(f"Пользователь: {uid}")
        lines.append("")
    section_order = ("location", "learning_profile", "interests", "interaction_history", "created_at")
    seen = set()
    for key in section_order:
        if key not in twin:
            continue
        seen.add(key)
        v = twin[key]
        label = _TWIN_SECTION_RU.get(key, key.replace("_", " "))
        if key == "interaction_history":
            lines.append(label)
            lines.extend(_twin_history_plain(v))
            lines.append("")
            continue
        if isinstance(v, dict) and v:
            lines.append(label)
            lines.extend(_twin_nested_plain(v))
            lines.append("")
        elif isinstance(v, list):
            lines.append(label)
            lines.append(f"• {_plain_leaf(v)}")
            lines.append("")
        elif v is not None and v != "":
            lines.append(label)
            lines.append(f"• {_plain_leaf(v)}")
            lines.append("")
    for key in sorted(twin.keys(), key=str):
        if key in seen or key == "user_id":
            continue
        v = twin[key]
        if v is None or v == {} or v == []:
            continue
        lines.append(str(key))
        if isinstance(v, dict):
            lines.extend(_twin_nested_plain(v))
        else:
            lines.append(f"• {_plain_leaf(v)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_psych_core_plain(profile: Dict[str, Any]) -> str:
    """Схема core.psychology_engine (last_analysis, stress_streak, …)."""
    if not profile:
        return "🧠 Психологический профиль\n\nПока нет данных."
    lines = ["🧠 Психологический профиль", ""]
    la = profile.get("last_analysis") if isinstance(profile.get("last_analysis"), dict) else None
    if la:
        lines.append("Последний анализ")
        raw_sent = la.get("sentiment")
        sk = str(raw_sent) if raw_sent is not None else ""
        sent_disp = _PSYCH_SENTIMENT_RU.get(sk, sk or "—")
        lines.append(f"• Тональность: {sent_disp}")
        lines.append(f"• Сигналы стресса в тексте: {'да' if la.get('stress_signals') else 'нет'}")
        kw = la.get("keywords") or []
        if isinstance(kw, list) and kw:
            lines.append("• Теги: " + ", ".join(str(x) for x in kw[:12]))
        else:
            lines.append("• Теги: нет")
        if la.get("analyzed_at"):
            lines.append(f"• Время анализа: {la.get('analyzed_at')}")
        if la.get("message_length") is not None:
            lines.append(f"• Длина сообщения: {la.get('message_length')} симв.")
        lines.append("")
    if profile.get("stress_streak") is not None:
        lines.append(f"Счётчик «стресс» подряд: {profile.get('stress_streak')}")
    if profile.get("updated_at"):
        lines.append(f"Профиль обновлён: {profile.get('updated_at')}")
    shown = {"last_analysis", "stress_streak", "updated_at"}
    for k in sorted(profile.keys(), key=str):
        if k in shown:
            continue
        v = profile[k]
        if v is None or v == {} or v == []:
            continue
        lines.append(f"• {k}: {v}")
    return "\n".join(lines)


_PSYCH_PLUGIN_ANALYSIS_ORDER = (
    "user_id",
    "timestamp",
    "message",
    "emotional_tone",
    "confidence_level",
    "anxiety_level",
    "communication_style",
    "learning_style",
    "engagement_level",
    "fatigue_level",
    "social_behavior",
    "recommendation",
)


def _psych_plugin_analysis_body_lines(analysis: Mapping[str, Any], *, indent: str = "") -> List[str]:
    lines: List[str] = []
    for key in _PSYCH_PLUGIN_ANALYSIS_ORDER:
        if key not in analysis:
            continue
        label = _PSYCH_PLUGIN_ANALYSIS_RU.get(key, key)
        lines.append(f"{indent}• {label}: {analysis[key]}")
    for key in sorted(analysis.keys(), key=str):
        if key in _PSYCH_PLUGIN_ANALYSIS_ORDER:
            continue
        lines.append(f"{indent}• {key}: {analysis[key]}")
    return lines


def format_psych_plugin_analysis_plain(analysis: Dict[str, Any]) -> str:
    lines = ["🧠 Анализ сообщения", ""]
    lines.extend(_psych_plugin_analysis_body_lines(analysis))
    return "\n".join(lines)


def format_psych_plugin_profile_plain(profile: Dict[str, Any]) -> str:
    lines = ["🧠 Психологический профиль", ""]
    if profile.get("user_id"):
        lines.append(f"Пользователь: {profile.get('user_id')}")
    if profile.get("created_at"):
        lines.append(f"Создан: {profile.get('created_at')}")
    if profile.get("updated_at"):
        lines.append(f"Обновлён: {profile.get('updated_at')}")
    hist = profile.get("analysis_history")
    if isinstance(hist, list):
        lines.append(f"Записей в истории анализов: {len(hist)}")
        if hist and isinstance(hist[-1], dict):
            lines.append("")
            lines.append("Последний анализ:")
            lines.extend(_psych_plugin_analysis_body_lines(hist[-1], indent="  "))
    summ = profile.get("summary") if isinstance(profile.get("summary"), dict) else {}
    if summ:
        lines.append("")
        lines.append("Сводка по истории:")
        for k, v in sorted(summ.items(), key=lambda t: str(t[0])):
            lines.append(f"  • {k}: {v}")
    return "\n".join(lines)


def format_persona_plugin_plain(persona: Dict[str, Any]) -> str:
    """Человекочитаемый блок без сырого JSON: фиксированный порядок полей."""
    lines = ["🎭 Текущий персонаж", ""]
    uid = persona.get("user_id")
    pid = persona.get("persona")
    title = persona.get("name")
    desc = persona.get("description")
    traits = persona.get("traits")
    ts = persona.get("timestamp")
    if uid is not None:
        lines.append(f"• Пользователь: {_plain_leaf(uid)}")
    if pid is not None:
        lines.append(f"• Ключ режима: {_plain_leaf(pid)}")
    if title is not None:
        lines.append(f"• Имя: {_plain_leaf(title)}")
    if desc:
        lines.append(f"• Описание: {_plain_leaf(desc)}")
    if traits:
        lines.append(f"• Черты: {_plain_leaf(traits)}")
    if ts is not None:
        lines.append(f"• Обновлено: {_plain_leaf(ts)}")
    shown = {"user_id", "persona", "name", "description", "traits", "timestamp"}
    extra = {k: v for k, v in persona.items() if k not in shown}
    if extra:
        lines.append("")
        lines.append("Дополнительно:")
        for k, v in sorted(extra.items(), key=lambda t: str(t[0])):
            lines.append(f"  • {k}: {_plain_leaf(v)}")
    return "\n".join(lines)


def format_user_record_plain(user_data: Dict[str, Any]) -> str:
    lines = ["👤 Информация о пользователе", ""]
    for k, v in sorted(user_data.items(), key=lambda t: str(t[0])):
        lines.append(f"• {k}: {_plain_leaf(v)}")
    return "\n".join(lines)


def format_books_search_plain(results: List[Any]) -> str:
    lines: List[str] = ["📚 Результаты поиска", ""]
    if not results:
        lines.append("Ничего не найдено.")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        if not isinstance(r, dict):
            lines.append(f"{i}. {r}")
            lines.append("")
            continue
        if r.get("error"):
            lines.append(f"Ошибка: {r.get('error')}")
            continue
        if r.get("message"):
            lines.append(str(r.get("message")))
            if r.get("query"):
                lines.append(f"Запрос: {r.get('query')}")
            lines.append("")
            continue
        content = str(r.get("content") or "").strip()
        preview = content[:2000] + ("…" if len(content) > 2000 else "")
        cid = r.get("chunk_id")
        mt = r.get("match_type") or ""
        head = f"Фрагмент #{cid}" + (f" ({mt})" if mt else "")
        lines.append(f"{i}. {head}")
        lines.append(preview)
        lines.append("")
    return "\n".join(lines).rstrip()


def format_group_behavior_plain(result: Dict[str, Any]) -> str:
    lines = ["👥 Результат обработки в группе", ""]
    lines.append(f"Группа: {result.get('group_id', '—')}")
    lines.append(f"Тип: {result.get('group_type', '—')}")
    msg = result.get("message")
    if msg:
        lines.append(f"Сообщение (фрагмент): {msg}")
    if result.get("timestamp"):
        lines.append(f"Время: {result.get('timestamp')}")
    beh = result.get("behavior_analysis") if isinstance(result.get("behavior_analysis"), dict) else {}
    if beh:
        lines.append("")
        lines.append("Поведение:")
        lines.append(f"  • Социальные маркеры: {'да' if beh.get('social_cues') else 'нет'}")
        lines.append(f"  • Процессные маркеры: {'да' if beh.get('process_indicators') else 'нет'}")
        lines.append(f"  • Вовлечённость: {beh.get('engagement_level', '—')}")
        lines.append(f"  • Поток диалога: {beh.get('conversation_flow', '—')}")
    lines.append("")
    lines.append(f"Вмешаться: {'да' if result.get('should_intervene') else 'нет'}")
    tpl = result.get("response_template")
    if isinstance(tpl, str):
        lines.append(f"Шаблон ответа: {tpl}")
    elif isinstance(tpl, list) and tpl:
        lines.append("Примеры ответов: " + "; ".join(str(x) for x in tpl[:3]))
    return "\n".join(lines)


def format_schedule_plain(schedule: Any) -> str:
    lines = ["📅 Расписание", ""]
    if isinstance(schedule, dict):
        for k, v in sorted(schedule.items(), key=lambda t: str(t[0])):
            lines.append(f"• {k}:")
            if isinstance(v, list):
                for item in v[:30]:
                    lines.append(f"    ◦ {_plain_leaf(item)}")
                if len(v) > 30:
                    lines.append(f"    … ещё {len(v) - 30}")
            else:
                lines.append(f"    {_plain_leaf(v)}")
    else:
        lines.append(str(schedule))
    return "\n".join(lines)


def format_quiz_plain(quiz: Dict[str, Any]) -> str:
    if quiz.get("error"):
        return f"Тест\n\nОшибка: {quiz.get('error')}"
    lines = ["📝 Тест", ""]
    if quiz.get("subject"):
        lines.append(f"Предмет: {quiz.get('subject')}")
    if quiz.get("title"):
        lines.append(f"Тема/название: {quiz.get('title')}")
    if quiz.get("generated_at"):
        lines.append(f"Создан: {quiz.get('generated_at')}")
    lines.append("")
    questions = quiz.get("questions") or []
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            lines.append(f"{i}. {q}")
            continue
        lines.append(f"{i}. {q.get('question', '—')}")
        for opt in q.get("options") or []:
            lines.append(f"   • {opt}")
        if q.get("correct") is not None:
            lines.append(f"   (верный вариант: {q.get('correct')})")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_parent_reports_plain(reports: Dict[str, Any]) -> str:
    if reports.get("error"):
        return f"Отчёты\n\nОшибка: {reports.get('error')}"
    lines = ["👪 Отчёты", ""]
    if reports.get("user_id"):
        lines.append(f"Пользователь: {reports.get('user_id')}")
    if reports.get("timestamp"):
        lines.append(f"Время отчёта: {reports.get('timestamp')}")
    cp = reports.get("child_progress") if isinstance(reports.get("child_progress"), dict) else {}
    if cp:
        lines.append("")
        lines.append("Прогресс:")
        lines.append(f"  • Балл обучения: {cp.get('learning_score', '—')}")
        lines.append(f"  • Посещаемость: {cp.get('attendance', '—')}")
        lines.append(f"  • Заданий выполнено: {cp.get('assignments_completed', '—')}")
        lines.append(f"  • Последняя активность: {cp.get('last_activity', '—')}")
        rec = cp.get("recommended_actions")
        if isinstance(rec, list) and rec:
            lines.append("  Рекомендации:")
            for x in rec:
                lines.append(f"    ◦ {x}")
    pr = reports.get("parent_recommendations")
    if isinstance(pr, list) and pr:
        lines.append("")
        lines.append("Советы родителю:")
        for x in pr:
            lines.append(f"  • {x}")
    si = reports.get("schedule_info") if isinstance(reports.get("schedule_info"), dict) else {}
    up = si.get("upcoming_activities") if isinstance(si.get("upcoming_activities"), list) else []
    if up:
        lines.append("")
        lines.append("Ближайшие события:")
        for act in up:
            if isinstance(act, dict):
                lines.append(
                    f"  • {act.get('activity', '—')} — {act.get('date', '')} {act.get('time', '')}"
                )
            else:
                lines.append(f"  • {act}")
    return "\n".join(lines)
